"""BE04 attempt taxonomy, retry ceilings, account circuits and immutable pool scope."""
import asyncio
from types import SimpleNamespace
import time

import httpx
import openai
import pytest

from agent.attempt_policy import (AccountCircuits, AttemptController, Failure, FailureClass,
                                  classify_attempt_failure, client_scope_key)
from agent.budget_account import BudgetBlocked
from agent.retry_utils import parse_retry_after_seconds


def _error(status, code, message, **headers):
    request = httpx.Request("POST", "https://example.invalid/v1/chat/completions")
    return openai.APIStatusError(message, response=httpx.Response(status, request=request, headers=headers),
                                 body={"error": {"code": code, "message": message}})


@pytest.mark.parametrize("status,code,message,expected", [
    (401, "invalid_api_key", "Invalid API key", FailureClass.AUTH),
    (429, "insufficient_quota", "exceeded your current quota", FailureClass.QUOTA),
    (429, "rate_limit_exceeded", "too many requests", FailureClass.THROTTLE),
    (429, "overloaded", "server overloaded", FailureClass.OVERLOAD),
    (400, "context_length_exceeded", "maximum context length exceeded", FailureClass.CONTEXT),
    (404, "model_not_found", "model does not exist", FailureClass.UNSUPPORTED),
])
def test_distinct_refusals(status, code, message, expected):
    failure = classify_attempt_failure(_error(status, code, message))
    assert failure.reason is expected
    assert failure.remote_acceptance == "rejected"


@pytest.mark.parametrize("value", ["nan", "inf", "-inf", float("nan"), float("inf")])
def test_nonfinite_retry_after_is_not_a_sleep(value):
    assert parse_retry_after_seconds(value) is None


class _Budget:
    def __init__(self):
        self.agent = SimpleNamespace(_current_api_request_id="logical-request")
        self.deadline = time.time() + 120

    def check(self):
        pass

    def block(self, reason):
        raise BudgetBlocked(reason)


def test_circuit_account_scope_and_one_physical_ceiling(monkeypatch):
    now = [10.0]
    circuits = AccountCircuits(clock=lambda: now[0])
    controller = AttemptController(_Budget(), circuits=circuits, clock=lambda: now[0])
    monkeypatch.setattr("agent.attempt_policy.jittered_backoff", lambda *a, **k: 0.25)
    failure = Failure(FailureClass.THROTTLE, "rejected", 2.0)
    for index in range(3):
        attempt = controller.begin("account-a", f"reservation-{index}")
        assert attempt.attempt_id == attempt.reservation_id
        decision = controller.failed(attempt, failure)
        assert circuits.remaining("account-b") == 0
        if index < 2:
            assert decision.action == "retry" and decision.delay_seconds >= 2
            with pytest.raises(BudgetBlocked, match="cooling"):
                controller.begin("account-a", "too-early")
        else:
            assert decision.action == "stop"
        now[0] += 2
    # Changing key/pool does not reset the shared logical ceiling.
    with pytest.raises(BudgetBlocked):
        controller.begin("account-b", "amplified")


@pytest.mark.parametrize("failure", [Failure(FailureClass.THROTTLE, "rejected", 86400),
                                      Failure(FailureClass.OVERLOAD, "unknown"),
                                      Failure(FailureClass.AMBIGUOUS, "unknown"),
                                      Failure(FailureClass.CONTEXT, "rejected")])
def test_long_wait_ambiguity_and_context_do_not_enter_retry(failure):
    controller = AttemptController(_Budget(), circuits=AccountCircuits())
    attempt = controller.begin("account", "reservation")
    assert controller.failed(attempt, failure).action == "stop"


def test_scope_partitions_profile_secret_endpoint_proxy_tls_and_loop(tmp_path, monkeypatch):
    from agent.secret_scope import set_secret_scope, reset_secret_scope
    from agent.process_bootstrap import _shared_transport_key
    from agent.auxiliary_client import _client_cache_key
    home_a, home_b = tmp_path / "a", tmp_path / "b"
    home_a.mkdir(); home_b.mkdir()
    options = {"api_key": "secret-a", "base_url": "https://a.invalid/v1", "ssl_verify": True}
    scopes = []
    for home, secret in ((home_a, "secret-a"), (home_b, "secret-b"), (home_a, "secret-a")):
        monkeypatch.setenv("HERMES_HOME", str(home))
        token = set_secret_scope({"OPENAI_API_KEY": secret}, profile_home=str(home))
        try:
            scopes.append((client_scope_key(None, options), _shared_transport_key(options["base_url"], True, None),
                           _client_cache_key("openai", async_mode=False, base_url=options["base_url"])))
        finally:
            reset_secret_scope(token)
    assert scopes[0] == scopes[2] and scopes[0] != scopes[1]
    assert "secret-a" not in repr(scopes) and "OPENAI_API_KEY" not in repr(scopes)
    baseline = client_scope_key(None, options)
    assert baseline != client_scope_key(None, {**options, "ssl_verify": False})
    assert baseline != client_scope_key(None, {**options, "base_url": "https://b.invalid/v1"})
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:1234")
    assert baseline != client_scope_key(None, options)
    async def key():
        return client_scope_key(None, options)
    assert asyncio.run(key()) != asyncio.run(key())


def test_strict_same_profile_identities_partition_clients_but_share_account_circuit(tmp_path, monkeypatch):
    from agent.agent_identity import resolve_agent_context
    from agent.identity_lifecycle import agent_runtime_scope
    from agent.attempt_policy import provider_account_ref
    from agent.process_bootstrap import _shared_transport_key
    from agent.auxiliary_client import _client_cache_key
    config = {"agent_identity": {
        "schema_version": 1, "principal_id": "owner", "profile_id": "fixture",
        "primary_agent_id": "primary", "active_agent_id": "primary",
        "agents": {
            "primary": {"policy_version": 1, "role": "primary", "memory_backend": "personal_mcp", "secret_refs": ["OPENAI_API_KEY"]},
            "specialist": {"policy_version": 1, "role": "specialist", "memory_backend": "builtin", "secret_refs": ["OPENAI_API_KEY"]}},
    }}
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / ".env").write_text("OPENAI_API_KEY=fixture-key\n")
    a = resolve_agent_context(config, profile_home=tmp_path, session_id="a")
    config["agent_identity"]["active_agent_id"] = "specialist"
    b = resolve_agent_context(config, profile_home=tmp_path, session_id="b")
    client = SimpleNamespace(base_url="https://example.invalid/v1", api_key="fixture-key")
    keys, accounts = [], []
    for context in (a, b, a):
        agent = SimpleNamespace(runtime_context=context)
        with agent_runtime_scope(context):
            keys.append((client_scope_key(agent, vars(client)), _shared_transport_key(client.base_url, True, None),
                         _client_cache_key("openai", async_mode=False, base_url=client.base_url)))
            accounts.append(provider_account_ref(SimpleNamespace(agent=agent), client))
    assert keys[0] == keys[2] and keys[0] != keys[1]
    assert accounts[0] == accounts[1] == accounts[2]
    with agent_runtime_scope(b):
        with pytest.raises(PermissionError, match="owning identity"):
            client_scope_key(SimpleNamespace(runtime_context=a), vars(client))


def test_no_header_retry_delay_reopens_its_own_circuit(monkeypatch):
    now = [10.0]
    circuit = AccountCircuits(clock=lambda: now[0])
    controller = AttemptController(_Budget(), circuits=circuit, clock=lambda: now[0])
    monkeypatch.setattr("agent.attempt_policy.jittered_backoff", lambda *a, **k: 0.5)
    attempt = controller.begin("account", "one")
    decision = controller.failed(attempt, Failure(FailureClass.THROTTLE, "rejected"))
    now[0] += decision.delay_seconds
    assert controller.begin("account", "two").reservation_id == "two"
