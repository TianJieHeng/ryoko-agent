"""Single-attempt Responses transport for an admitted, recipient-bound owner.

Consumer OAuth does not accept a request output cap. Hard-budget runs therefore
remain unsupported rather than treating a local stream close as a remote cap.
Login/refresh happens before admission; an expired grant never triggers hidden
network work or account rotation inside a command.
"""
from __future__ import annotations

import json
import time
import uuid
from contextvars import ContextVar

_ATTEMPT = ContextVar("owned_codex_physical_attempt", default=None)


def credential_is_granted(context, values):
    """Bind the virtual OAuth secret reference at the trusted constructor."""
    from hmac import compare_digest
    from agent.identity_lifecycle import identity_config
    from hermes_cli.auth_constants import DEFAULT_CODEX_BASE_URL
    if ("CODEX_CLI_OAUTH" not in context.policy.secret_refs
            or identity_config().get("codex_auth_source") != "codex_cli"
            or values.get("provider") != "openai-codex"
            or str(values.get("base_url", "")).rstrip("/") != DEFAULT_CODEX_BASE_URL.rstrip("/")):
        return False
    from hermes_cli.auth_codex import resolve_codex_cli_credentials
    return compare_digest(values.get("api_key") or "", resolve_codex_cli_credentials()["api_key"])


def authorize_responses_request(request):
    """Transport boundary: only the exact prepared body, once, in its owner."""
    from agent.runtime_commands import assert_runtime_dispatch
    from tools.egress_policy import EgressDenied
    attempt = _ATTEMPT.get()
    if (attempt is None or attempt["sent"] or assert_runtime_dispatch() is not attempt["run"]
            or request.headers.get("authorization") != "Bearer " + attempt["credential"]
            or json.loads(request.content) != attempt["body"]):
        raise EgressDenied("recipient_responses_operation_unowned")
    attempt["sent"] = True


def is_owned_codex_route(agent):
    return (getattr(agent, "api_mode", None) == "codex_responses"
            and getattr(agent, "provider", None) == "openai-codex")


def run_owned_codex_stream(agent, kwargs, client, *, on_first_delta=None):
    from agent.budget_account import BudgetBlocked
    from agent.codex_runtime import _consume_codex_event_stream
    from agent.runtime_commands import assert_runtime_dispatch, RuntimeFenceError, _record_operation_event
    from tools.egress_policy import assert_provider_client, EgressDenied

    run = assert_runtime_dispatch(agent)
    if run is None or not is_owned_codex_route(agent):
        raise RuntimeFenceError("OAuth Responses requires its admitted runtime owner")
    if run.budget is not None:
        raise BudgetBlocked("Codex OAuth has no verified remote output cap; hard token budgets are unsupported")
    assert_provider_client(client, purpose="main_model")
    # A request-local credential/recipient is immutable for the entire attempt.
    if client.api_key != agent.api_key or str(client.base_url).rstrip("/") != str(agent.base_url).rstrip("/"):
        raise RuntimeFenceError("OAuth request credentials or endpoint changed")
    request = dict(kwargs)
    allowed = {"model", "input", "instructions", "tools", "tool_choice", "parallel_tool_calls",
               "reasoning", "include", "text", "store", "stream", "prompt_cache_key",
               "extra_headers", "timeout"}
    if set(request) - allowed or request.get("store") is not False or not isinstance(request.get("input"), list):
        raise EgressDenied("recipient_responses_request_unsupported")
    if request.get("model") != agent.model:
        raise RuntimeFenceError("OAuth request model changed")
    headers = request.get("extra_headers", {})
    if not isinstance(headers, dict) or any(k.lower() not in {"session_id", "x-client-request-id"} for k in headers):
        raise EgressDenied("recipient_responses_headers_unsupported")
    if any(not isinstance(tool, dict) or tool.get("type") not in {"function", "custom"}
           for tool in request.get("tools", [])):
        raise EgressDenied("recipient_responses_hosted_tools_unsupported")
    # Media/remote fetches and opaque server continuations need separate contracts.
    def check_input(value):
        if isinstance(value, dict):
            if value.get("type") in {"input_image", "input_audio", "input_file", "item_reference"}:
                raise EgressDenied("recipient_responses_input_unsupported")
            for item in value.values():
                check_input(item)
        elif isinstance(value, list):
            for item in value:
                check_input(item)
    check_input(request["input"])
    # Finite local wall time, independent of a peer keeping an SSE connection alive.
    timeout = request.pop("timeout", 120)
    if type(timeout) not in {int, float} or not 0 < timeout <= 120:
        timeout = 120
    request.update(stream=True, timeout=timeout)
    deadline = time.monotonic() + timeout
    terminal = False
    received = 0
    agent._codex_streamed_text_parts = []

    def check():
        assert_runtime_dispatch(agent)
        assert_provider_client(client, purpose="main_model")
        if agent._interrupt_requested:
            raise InterruptedError("OAuth Responses cancelled; remote outcome may be unknown")
        if time.monotonic() >= deadline:
            raise TimeoutError("OAuth Responses deadline exceeded; remote outcome may be unknown")

    def events(stream):
        nonlocal terminal, received
        for event in stream:
            check()
            received += len(json.dumps(event.model_dump(mode="json"), ensure_ascii=False).encode())
            if received > 16 * 1024 * 1024:
                raise RuntimeFenceError("OAuth Responses exceeded the local response limit")
            if event.type == "response.completed":
                terminal = True
            yield event

    def text_delta(value):
        agent._codex_streamed_text_parts.append(value)
        callback = getattr(agent, "_fire_stream_delta", None)
        if callback:
            callback(value)

    check()
    operation_id = uuid.uuid4().hex
    correlation = {"run_id": run.run_id, "operation_id": operation_id}
    _record_operation_event(run, "model.started", {"phase": "oauth_physical_started"}, **correlation)
    attempt_token = _ATTEMPT.set({"run": run, "sent": False, "credential": client.api_key,
        "body": {key: value for key, value in request.items() if key not in {"timeout", "extra_headers"}}})
    try:
        # SDK retries and the legacy zero-event retry are both deliberately absent.
        with client.with_options(max_retries=0, timeout=timeout).responses.create(**request) as stream:
            final = _consume_codex_event_stream(events(stream), model=agent.model,
                on_text_delta=text_delta, on_first_delta=on_first_delta)
        check()
        if not terminal or final.status != "completed":
            raise RuntimeFenceError("OAuth Responses did not complete; no partial result may execute tools")
        usage = getattr(final, "usage", None)
        fields = ("input_tokens", "output_tokens")
        values = {key: usage.get(key) if isinstance(usage, dict) else getattr(usage, key, None) for key in fields}
        if any(type(value) is not int or value < 0 for value in values.values()):
            raise RuntimeFenceError("OAuth Responses finished without authoritative usage")
        _record_operation_event(run, "model.completed", {"phase": "oauth_physical_completed",
            **values, "cost_tracking": "untracked"}, **correlation)
        return final
    except BaseException:
        # Neither a local close nor an HTTP failure proves the server did no work.
        run.dispatch_blocked.set()
        _record_operation_event(run, "model.failed", {"phase": "oauth_physical_failed",
            "outcome_uncertain": True}, **correlation)
        raise
    finally:
        _ATTEMPT.reset(attempt_token)
