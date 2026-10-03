"""Finite host-owned reads through the existing authenticated connector client.

No model tool, credential store, account chooser, search or mutation dispatcher.
Schemas and execution use the same declared gateway recipient and source scope.
"""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
import json
import math
import time

from agent.connected_sources import (MAX_BYTES, TOOLS, ConnectedSourceError, arguments,
                                     canonical, check_schema, materialize, require, selection, sha)

_READ = ContextVar("connected_source_read", default=None)
HTTP_TIMEOUT = 15.0


@dataclass(frozen=True)
class _Read:
    run: object
    project_id: str
    selection_json: bytes
    deadline: float


def require_source_read():
    """Egress admission is available only inside this concrete read adapter."""
    from agent.artifact_commands import assert_artifact_dispatch
    from agent.project_context import authorize_project
    from tools.connectors.gateway.names import format_connector_name
    read = _READ.get()
    require(isinstance(read, _Read), "source_read_control_required")
    run = assert_artifact_dispatch(read.run)
    require(time.monotonic() < read.deadline, "source_timeout")
    selected = json.loads(read.selection_json)
    connector, tool = TOOLS[selected["kind"]]
    require(run.context.policy.allows_tool(format_connector_name(connector, tool)), "source_tool_not_granted")
    authorize_project(run.context, read.project_id, "read")
    authorize_project(run.context, read.project_id, "write")
    record = run.db.read_runtime_command(run.session_id, run.command_id)
    payload = record["command"]["payload"]
    require(payload.get("mode") == "connected_source" and payload.get("project_id") == read.project_id
            and payload.get("selection_sha256") == sha(read.selection_json), "source_read_control_required")
    from agent.budget_account import parse_budget_policy
    from agent.identity_lifecycle import identity_config
    require(run.budget is not None and run.budget.policy == parse_budget_policy(identity_config()),
            "source_budget_required")
    # No verified monetary price or upstream retry ceiling is provided by the
    # gateway. Cost mode cannot be certified. Token mode explicitly leaves spend
    # untracked; attempt accounting covers each physical gateway HTTP request.
    require(run.budget.policy.cost_tracking == "untracked", "source_cost_contract_unsupported")
    run.budget.check()
    return read


class _Response:
    def __init__(self, status, headers, payload):
        self.status_code, self.headers, self._payload = status, headers, payload

    def json(self):
        return self._payload


class _Transport:
    def __init__(self, authorization, *, http_transport=None):
        self.authorization, self.http_transport = authorization, http_transport
        self.count = 0
        self.pending = None

    def finish(self, confirmed=False):
        if self.pending is not None:
            budget, operation, elapsed = self.pending
            self.pending = None
            budget.settle(operation, {"attempts": 1, "wall_ms": elapsed},
                          unknown=not confirmed, slots_released=confirmed)

    def request(self, method, url, *, headers=None, json=None, timeout=None):
        import httpx
        from tools.egress_policy import wrap_httpx_transport
        read = require_source_read()
        require(self.count < 2 and method == "POST", "source_request_limit")
        self.authorization.check_url(url)
        budget = read.run.budget
        seconds = min(float(timeout), HTTP_TIMEOUT, read.deadline - time.monotonic(),
                      budget.deadline - time.time() - 1,
                      (budget.policy.record["request_timeout_ms"] - 1000) / 1000)
        require(seconds > 0, "source_timeout")
        maximum_ms = math.ceil(seconds * 1000) + 1000
        operation = budget.reserve({"attempts": 1, "wall_ms": maximum_ms, "provider_slots": 1})
        started, dispatched = time.monotonic(), False
        try:
            require_source_read()
            budget.dispatched(operation)
            dispatched = True
            self.count += 1
            inner = self.http_transport or httpx.HTTPTransport(retries=0)
            guarded = wrap_httpx_transport(inner, self.authorization)
            with httpx.Client(transport=guarded, trust_env=False, follow_redirects=False,
                              timeout=httpx.Timeout(seconds, read=min(seconds, 1.0))) as client:
                with client.stream(method, url, headers={**(headers or {}), "Accept-Encoding": "identity"}, json=json) as response:
                    require(response.headers.get("content-encoding", "identity").lower() == "identity", "source_encoding_unsupported")
                    chunks, size = [], 0
                    for chunk in response.iter_bytes(chunk_size=65536):
                        require_source_read()
                        require(time.monotonic() - started <= seconds, "source_timeout")
                        size += len(chunk)
                        require(size <= MAX_BYTES, "source_byte_limit")
                        chunks.append(chunk)
                    require(time.monotonic() - started <= seconds, "source_timeout")
                    def pairs(items):
                        result = {}
                        for key, value in items:
                            require(key not in result, "source_schema_mismatch")
                            result[key] = value
                        return result
                    import json as codec
                    payload = codec.loads(b"".join(chunks), object_pairs_hook=pairs,
                        parse_constant=lambda _value: require(False, "source_schema_mismatch"))
                    require_source_read()
                    result = _Response(response.status_code, dict(response.headers), payload)
        except BaseException:
            if dispatched:
                budget.settle(operation, {"attempts": 1, "wall_ms": math.ceil((time.monotonic() - started) * 1000)},
                              unknown=True, slots_released=False)
            else:
                budget.db.release_budget_reservation(budget.account_id, budget.actor, operation, **budget.fence)
            raise
        elapsed = math.ceil((time.monotonic() - started) * 1000)
        if url.endswith("/execute") and response.status_code not in {400, 401, 403, 404, 429}:
            # A transport return is not a successful tool result. Keep the
            # remote slot until the exact finite response has been validated.
            self.pending = (budget, operation, elapsed)
        else:
            budget.settle(operation, {"attempts": 1, "wall_ms": elapsed})
        require_source_read()
        return result


def read_source(run, project_id, selected, *, _http_transport=None):
    """Actual client/wire path. Only the low-level transport is synthetic in tests."""
    from tools.connectors.gateway import config
    from tools.connectors.gateway.client import ConnectorClient
    from tools.connectors.gateway.errors import ToolGatewayError
    from tools.egress_policy import EgressDenied, prepare_recipient
    from tools.managed_gateway_auth import connector_gateway_origin, managed_gateway_auth_headers
    from tools.managed_tool_gateway import peek_nous_access_token

    selected = selection(selected)
    read = _Read(run, project_id, canonical(selected), time.monotonic() + 2 * HTTP_TIMEOUT + 2)
    token = _READ.set(read)
    transport = None
    try:
        require_source_read()
        require(config.connectors_available(), "source_connector_unavailable")
        endpoint = connector_gateway_origin()
        authorization = prepare_recipient("connected_source", endpoint)
        require(authorization is not None, "source_recipient_required")
        transport = _Transport(authorization, http_transport=_http_transport)

        def headers(url):
            require_source_read()
            authorization.check_url(url)
            # Reuse the existing profile account, but never perform an implicit
            # OAuth refresh, guest registration, login or token mint here.
            return managed_gateway_auth_headers(url, token_reader=peek_nous_access_token)

        client = ConnectorClient(transport=transport, endpoint_resolver=lambda: endpoint, header_provider=headers)
        connector, tool = TOOLS[selected["kind"]]
        schema = client.read_schema(tool, timeout=HTTP_TIMEOUT)
        schema_digest = check_schema(schema, selected)
        require_source_read()
        result = client.pinned_read(connector, tool, arguments(selected), account=selected["account_id"], timeout=HTTP_TIMEOUT)
        require_source_read()
        observed = time.time()
        identity = run.context.identity
        scope = {key: getattr(identity, key) for key in ("principal_id", "profile_id", "agent_id")}
        scope.update(project_id=project_id, policy_digest=identity.policy_digest)
        original, projection = materialize(result, selected, observed, schema_digest, scope)
        transport.finish(confirmed=True)
        original["gateway_http_attempts"] = transport.count
        original["connector"] = connector
        original["tool"] = tool
        original["gateway_recipient_id"] = authorization.grant.recipient_id
        original["gateway_endpoint_sha256"] = sha(endpoint.encode())
        original["arguments_sha256"] = sha(canonical(arguments(selected)))
        original["upstream_retry_count"] = "not_attested"
        original["cost_tracking"] = "untracked"
        return original, projection
    except EgressDenied as exc:
        raise ConnectedSourceError("source_egress_denied") from exc
    except ToolGatewayError as exc:
        if exc.status == 400:
            code = "source_pinned_request_rejected"
        elif exc.status in {401, 403}:
            code = "source_authorization_unavailable"
        elif exc.code == "INVALID_RESPONSE":
            code = "source_schema_mismatch"
        else:
            code = "source_gateway_unavailable"
        raise ConnectedSourceError(code) from exc
    finally:
        try:
            if transport is not None:
                transport.finish()
        finally:
            _READ.reset(token)
