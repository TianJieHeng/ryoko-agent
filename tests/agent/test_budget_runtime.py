"""BE03 real AIAgent, durable SessionDB, and the actual OpenAI HTTP dispatch seam."""
import contextvars
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import openai
import pytest

from agent.budget_account import BudgetBlocked, BudgetPolicyError, parse_budget_policy, actor_for
from agent.identity_lifecycle import agent_runtime_scope
from agent.runtime_commands import (bind_runtime_run, claim_turn_command, prepare_turn_command,
                                   reset_runtime_run, finish_turn_command)
from agent.turn_facade_lease import admit_durable_turn_lease
from hermes_state import SessionDB


BASE_URL = "https://budget.invalid/v1"
MODEL = "gpt-4.1-mini"


def policy(*, attempts=8, slots=2, mode="tokens"):
    return {"schema_version": 1, "mode": mode,
            "limits": {"tokens": 500000, "attempts": attempts,
                       "cost_micros": None if mode == "tokens" else 500000,
                       "wall_ms": 120000, "provider_slots": slots, "executor_slots": 2},
            "deadline_seconds": 120, "request_timeout_ms": 10000,
            "routes": [{"model": MODEL, "base_url": BASE_URL, "max_input_tokens": 100000,
                        "max_output_tokens": 64, "input_overhead_tokens": 128, "output_token_parameter": "max_tokens",
                        "input_cost_micros_per_million": None if mode == "tokens" else 1000000,
                        "output_cost_micros_per_million": None if mode == "tokens" else 2000000,
                        "bounds_verified": True}]}


def recipient_plan():
    return {"schema_version": 1, "envelope": "declared", "grants": [
        {"recipient_id": "fixture", "purpose": purpose, "endpoint": BASE_URL, "transport": "httpx"}
        for purpose in ("main_model", "aux_model")]}


def guarded_http(handler, *, purpose="main_model", async_mode=False):
    from tools.egress_policy import prepare_recipient, wrap_httpx_transport
    authorization = prepare_recipient(purpose, BASE_URL)
    cls = httpx.AsyncClient if async_mode else httpx.Client
    client = cls(transport=wrap_httpx_transport(httpx.MockTransport(handler), authorization, async_mode=async_mode),
                 trust_env=False)
    client._hermes_recipient_authorization = authorization
    return client


def config(budget=None):
    return {"runtime_budget": budget or policy(), "agent_identity": {
        "schema_version": 1, "principal_id": "owner", "profile_id": "fixture",
        "primary_agent_id": "primary", "active_agent_id": "primary",
        "agents": {"primary": {"policy_version": 1, "role": "primary", "memory_backend": "personal_mcp",
                                "recipient_plan": recipient_plan(), "secret_refs": ["OPENAI_API_KEY"], "allowed_tools": ["todo_list", "delegate_task", "terminal"]}},
        "child_policy": {"policy_version": 1, "role": "child", "memory_backend": "builtin",
                         "recipient_plan": recipient_plan(), "secret_refs": ["OPENAI_API_KEY"], "allowed_tools": ["todo_list"]}}}


def response(tool=False, usage=True):
    msg = {"role": "assistant", "content": "bounded answer"}
    if tool:
        msg = {"role": "assistant", "content": None, "tool_calls": [{"id": "todo_1", "type": "function",
               "function": {"name": "todo_list", "arguments": '{"todos":[{"id":"1","content":"done","status":"completed"}]}'}}]}
    data = {"id": "fixture", "object": "chat.completion", "created": 1, "model": MODEL,
            "choices": [{"index": 0, "finish_reason": "tool_calls" if tool else "stop", "message": msg}]}
    if usage:
        data["usage"] = {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14}
    return data


@pytest.fixture
def factory(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / ".env").write_text("OPENAI_API_KEY=fixture-provider-key\n")
    monkeypatch.setattr("model_tools.check_toolset_requirements", lambda *a, **k: {})
    from tools.todo_tool import TODO_SCHEMA
    monkeypatch.setattr("model_tools.get_tool_definitions", lambda *a, **k: [{"type": "function", "function": TODO_SCHEMA}])
    monkeypatch.setattr("agent.process_bootstrap.OpenAI", MagicMock())
    db = SessionDB(tmp_path / "state.db")
    agents, clients = [], []

    def make(*, budget=None, session_id="session", handler=None):
        (tmp_path / "config.yaml").write_text(json.dumps(config(budget)))
        from run_agent import AIAgent
        agent = AIAgent(model=MODEL, provider="openai", api_key="fixture-provider-key", base_url=BASE_URL,
                        session_id=session_id, session_db=db, quiet_mode=True, skip_context_files=True,
                        skip_memory=True, max_iterations=5)
        calls = []
        def dispatch(request):
            calls.append(json.loads(request.content))
            return handler(request) if handler else httpx.Response(200, json=response())
        with agent_runtime_scope(agent.runtime_context):
            client = openai.OpenAI(api_key="fixture", base_url=BASE_URL,
                                  max_retries=7, http_client=guarded_http(dispatch))
        agent._test_dispatch = dispatch
        agent.client.close()
        clients.append(client)
        agent.client = client
        agent._cached_system_prompt = "Fixture system prompt."
        agent._use_prompt_caching = False
        agent._disable_streaming = False  # budget must choose bounded dispatch itself
        agent.tool_delay = 0
        agent.save_trajectories = False
        agent.compression_enabled = False
        monkeypatch.setattr(agent, "_create_request_openai_client", lambda **kwargs: client)
        monkeypatch.setattr(agent, "_close_request_openai_client", lambda *a, **k: None)
        monkeypatch.setattr(agent, "_cleanup_task_resources", lambda *a, **k: None)
        monkeypatch.setattr(agent, "_save_trajectory", lambda *a, **k: None)
        agents.append(agent)
        return agent, calls
    yield make, db, tmp_path
    for agent in agents:
        agent.close()
    for client in clients:
        client.close()
    db.close()


class active:
    def __init__(self, agent):
        self.agent = agent
    def __enter__(self):
        self.context = agent_runtime_scope(self.agent.runtime_context)
        self.context.__enter__()
        record = prepare_turn_command(self.agent, "test")
        admitted = admit_durable_turn_lease(self.agent, session_id=self.agent.session_id, relay_turn_id="budget-test",
            task_context={"session_id": self.agent.session_id, "platform": "cli"}, conversation_history=[])
        self.lease = admitted.lease
        self.run = claim_turn_command(self.agent, record, self.lease)
        assert self.run is not None
        self.token = bind_runtime_run(self.run)
        return self.run
    def __exit__(self, *exc):
        reset_runtime_run(self.token, self.run)
        self.lease.release()
        self.context.__exit__(*exc)


def test_real_agent_main_tool_aux_share_root_and_bound_wire(factory):
    make, db, _ = factory
    replies = iter([response(tool=True), response(), response()])
    agent, calls = make(handler=lambda r: httpx.Response(200, json=next(replies)))
    result = agent.run_conversation("Make a todo and answer")
    assert result["final_response"] == "bounded answer"
    assert len(calls) == 2
    assert all(c.get("max_tokens", c.get("max_completion_tokens")) == 64 and not c.get("stream") for c in calls)
    status = result["runtime_budget"]
    assert status["consumed"]["tokens"] == 28
    assert status["consumed"]["attempts"] == 2
    assert status["cost_tracking"] == "untracked" and status["invoice_guarantee"] is False
    assert status["reserved"]["executor_slots"] == status["reserved"]["provider_slots"] == 0
    assert any(m.get("role") == "tool" for m in result["messages"])
    # A continuation gets its own accepted run but not another root allocation.
    with active(agent) as run:
        from agent.auxiliary_client import _relay_sync_completion
        with openai.OpenAI(api_key="fixture", base_url=BASE_URL,
                           http_client=guarded_http(agent._test_dispatch, purpose="aux_model")) as auxiliary:
            _relay_sync_completion(auxiliary, {"model": MODEL, "messages": [{"role": "user", "content": "aux"}]},
                                   provider="openai", api_mode="chat_completions")
        root = db.get_budget_account(status["root_id"], actor_for(agent.runtime_context))
        assert run.budget.root_id == status["root_id"]
        assert root["consumed"]["attempts"] == 3


def test_sdk_retries_disabled_and_unknown_attempt_is_retained(factory):
    make, db, _ = factory
    agent, calls = make(budget=policy(attempts=1), handler=lambda r: httpx.Response(500, json={"error": {"message": "offline"}}))
    with active(agent) as run:
        from agent.budget_account import invoke_budgeted_completion
        with pytest.raises(openai.InternalServerError):
            invoke_budgeted_completion(agent.client, {"model": MODEL, "messages": [{"role": "user", "content": "hello"}]})
        account = db.get_budget_account(run.budget.account_id, run.budget.actor)
        assert account["unknown_usage"] and account["reserved"]["tokens"] > 0
        assert account["consumed"]["attempts"] + account["reserved"]["attempts"] == 1
        with pytest.raises(BudgetBlocked):
            invoke_budgeted_completion(agent.client, {"model": MODEL, "messages": []})
    assert len(calls) == 1


def test_unbounded_envelopes_and_opaque_tools_do_not_dispatch(factory):
    make, _, _ = factory
    agent, calls = make()
    with active(agent) as run:
        from agent.runtime_commands import invoke_runtime_operation
        ran = []
        with pytest.raises(BudgetBlocked, match="bounded execution"):
            invoke_runtime_operation("tool", lambda: ran.append(True), agent=agent, name="terminal")
        assert not ran and not calls
    other, other_calls = make(session_id="media")
    with active(other):
        from agent.budget_account import invoke_budgeted_completion
        with pytest.raises(BudgetBlocked, match="media"):
            invoke_budgeted_completion(other.client, {"model": MODEL, "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "x"}}]}]})
    assert not other_calls


def test_parallel_provider_slot_denial_and_cleanup(factory):
    make, db, _ = factory
    entered, release = threading.Event(), threading.Event()
    def handler(request):
        entered.set()
        assert release.wait(5)
        return httpx.Response(200, json=response())
    agent, calls = make(budget=policy(slots=1), handler=handler)
    with active(agent) as run:
        from agent.budget_account import invoke_budgeted_completion
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(contextvars.copy_context().run, invoke_budgeted_completion,
                                agent.client, {"model": MODEL, "messages": []})
            assert entered.wait(5)
            with pytest.raises(BudgetBlocked):
                invoke_budgeted_completion(agent.client, {"model": MODEL, "messages": []})
            release.set()
            # The denial closes this run to new work; already completed usage is still settled.
            with pytest.raises(BudgetBlocked):
                first.result(timeout=5)
        account = db.get_budget_account(run.budget.account_id, run.budget.actor)
        assert account["reserved"]["provider_slots"] == 0
        assert len(calls) == 1


def test_cost_mode_uses_declared_rates_not_vendor_invoice(factory):
    make, db, _ = factory
    agent, _ = make(budget=policy(mode="cost"))
    result = agent.run_conversation("hello")
    assert result["runtime_budget"]["cost_tracking"] == "bounded"
    assert result["runtime_budget"]["consumed"]["cost_micros"] == 18
    assert result["runtime_budget"]["invoice_guarantee"] is False


def test_policy_is_complete_and_invalid_cannot_disable():
    for value in (None, [], False, {"schema_version": 99}, {"enabled": False}):
        with pytest.raises(BudgetPolicyError):
            parse_budget_policy({"runtime_budget": value})
    assert parse_budget_policy({}) is None
    bad = policy(mode="cost")
    bad["routes"][0]["bounds_verified"] = False
    with pytest.raises(BudgetPolicyError, match="verified"):
        parse_budget_policy({"runtime_budget": bad})


def test_config_removal_cannot_disable_persisted_root(factory):
    make, _, home = factory
    agent, _ = make()
    agent.run_conversation("hello")
    data = config()
    data["runtime_budget"] = {}
    (home / "config.yaml").write_text(json.dumps(data))
    from agent.budget_account import install_budget_policy
    with pytest.raises(BudgetPolicyError, match="disabled"):
        install_budget_policy(SimpleNamespace(), data, session_db=agent._session_db, session_id=agent.session_id)


def test_trusted_parallel_children_inherit_one_root(factory, monkeypatch):
    make, db, _ = factory
    barrier = threading.Barrier(2)
    agent, calls = make(budget=policy(attempts=2), handler=lambda r: (barrier.wait(timeout=5), httpx.Response(200, json=response()))[1])
    from tools.delegate_tool import _build_child_agent
    children = []
    try:
        with active(agent) as run:
            for index in range(2):
                child = _build_child_agent(index, "answer", None, ["todo"], None, 2, 2, agent)
                children.append(child)
                child.client.close()
                with agent_runtime_scope(child.runtime_context):
                    child.client = openai.OpenAI(api_key="fixture", base_url=BASE_URL,
                                                http_client=guarded_http(agent._test_dispatch))
                child._cached_system_prompt = "child"
                child._disable_streaming = False
                child._use_prompt_caching = False
                child.save_trajectories = False
                child.compression_enabled = False
                monkeypatch.setattr(child, "_create_request_openai_client", lambda _client=child.client, **k: _client)
                monkeypatch.setattr(child, "_close_request_openai_client", lambda *a, **k: None)
                monkeypatch.setattr(child, "_cleanup_task_resources", lambda *a, **k: None)
                monkeypatch.setattr(child, "_save_trajectory", lambda *a, **k: None)
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(contextvars.copy_context().run, child.run_conversation, "answer") for child in children]
                results = [f.result(timeout=10) for f in futures]
            assert all(r["completed"] and r["runtime_budget"]["root_id"] == run.budget.root_id for r in results)
            assert len({r["runtime_budget"]["account_id"] for r in results}) == 2
            assert len(calls) == 2
            root = db.get_budget_account(run.budget.root_id, run.budget.actor)
            assert root["consumed"]["attempts"] == 2 and root["reserved"]["provider_slots"] == 0
            from agent.budget_account import invoke_budgeted_completion
            with pytest.raises(BudgetBlocked):
                invoke_budgeted_completion(agent.client, {"model": MODEL, "messages": []})
    finally:
        for child in children:
            child.close()


def test_restart_and_new_turn_cannot_reset_session_ceiling(factory):
    make, db, _ = factory
    agent, calls = make(budget=policy(attempts=1))
    first = agent.run_conversation("hello")
    root = first["runtime_budget"]["root_id"]
    # A newly constructed AIAgent over the same durable session is the restart seam.
    restored, new_calls = make(budget=policy(attempts=1))
    second = restored.run_conversation("continue")
    assert second["budget_blocked"] and second["completed"] is False
    assert second["runtime_budget"]["root_id"] == root
    assert new_calls == [] and len(calls) == 1
    assert db.get_budget_account(root, actor_for(agent.runtime_context))["consumed"]["attempts"] == 1


def test_cancel_unknown_remote_keeps_provider_slot_and_usage(factory):
    make, db, _ = factory
    entered, release = threading.Event(), threading.Event()
    def handler(request):
        entered.set()
        assert release.wait(5)
        raise httpx.ReadError("closed local transport", request=request)
    agent, calls = make(handler=handler)
    with active(agent) as run:
        from agent.budget_account import invoke_budgeted_completion
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(contextvars.copy_context().run, invoke_budgeted_completion,
                                 agent.client, {"model": MODEL, "messages": []})
            assert entered.wait(5)
            receipt = run.task_scope.request_cancel("stop")
            assert receipt["upstream_ack"] is None and not receipt["remote_effects_undone"]
            release.set()
            with pytest.raises(openai.APIConnectionError):
                future.result(timeout=5)
        account = db.get_budget_account(run.budget.account_id, run.budget.actor)
        assert account["unknown_usage"] and account["reserved"]["provider_slots"] == 1
        assert account["reserved"]["tokens"] > 0
        assert len(calls) == 1


def test_async_auxiliary_uses_same_adapter(factory):
    import asyncio
    make, db, _ = factory
    agent, _ = make()
    calls = []
    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json=response())
    async def invoke():
        from agent.auxiliary_client import _relay_async_completion
        client = openai.AsyncOpenAI(api_key="fixture", base_url=BASE_URL, max_retries=6,
            http_client=guarded_http(handler, purpose="aux_model", async_mode=True))
        try:
            return await _relay_async_completion(client, {"model": MODEL, "messages": []},
                provider="openai", api_mode="chat_completions")
        finally:
            await client.close()
    with active(agent) as run:
        assert asyncio.run(invoke()).choices[0].message.content == "bounded answer"
        account = db.get_budget_account(run.budget.account_id, run.budget.actor)
        assert account["consumed"]["attempts"] == 1
        assert account["reserved"]["provider_slots"] == 0
    assert len(calls) == 1 and calls[0]["max_tokens"] == 64


def test_reference_matches_runtime_field_registry_and_config_loader(factory):
    from hermes_cli.config_defaults import DEFAULT_CONFIG
    from hermes_cli.config_effective import load_user_config_effective
    from scripts.gen_runtime_budget_reference import OUTPUT, render_reference
    make, _, _ = factory
    agent, _ = make()
    assert DEFAULT_CONFIG["runtime_budget"] == {}
    assert parse_budget_policy(load_user_config_effective(fail_closed=True)) == agent._runtime_budget_policy
    assert OUTPUT.read_text() == render_reference()


def test_large_declared_rate_uses_exact_integer_rounding():
    from agent.budget_account import _actual
    route = policy(mode="cost")["routes"][0]
    route["input_cost_micros_per_million"] = (1 << 53) + 123
    route["output_cost_micros_per_million"] = (1 << 53) + 321
    actual, unknown = _actual(response(), route, 1, "bounded")
    expected = (10 * route["input_cost_micros_per_million"] + 4 * route["output_cost_micros_per_million"] + 999999) // 1000000
    assert actual["cost_micros"] == expected and not unknown


@pytest.mark.parametrize("surface", ["sync_hook", "async_hook", "middleware", "execution"])
def test_opaque_plugin_callbacks_fail_before_execution(factory, monkeypatch, surface):
    import asyncio
    from hermes_cli.plugins import PluginManager
    make, _, _ = factory
    agent, _ = make()
    manager = PluginManager()
    ran = []
    callback = lambda **kwargs: ran.append(True)
    with active(agent):
        manager._hooks["pre_tool_call"] = [callback]
        manager._middleware["llm_call"] = [callback]
        with pytest.raises(BudgetBlocked, match="callback"):
            if surface == "sync_hook":
                manager.invoke_hook("pre_tool_call")
            elif surface == "async_hook":
                asyncio.run(manager.ainvoke_hook("pre_tool_call"))
            elif surface == "middleware":
                manager.invoke_middleware("llm_call")
            else:
                monkeypatch.setattr("hermes_cli.plugins._delivery_manager", lambda: manager)
                from hermes_cli.middleware import _run_execution_chain
                _run_execution_chain("llm_call", lambda value: ran.append("terminal"), request={})
        assert ran == []


def test_opaque_event_rejection_does_not_strand_later_legacy_events(factory, monkeypatch):
    from hermes_cli.plugins import PluginManager
    make, _, _ = factory
    agent, _ = make()
    manager = PluginManager()
    seen = []
    manager._subscribe_event("test", "fixture", lambda **kw: seen.append(kw["value"]))
    with active(agent):
        with pytest.raises(BudgetBlocked):
            manager._dispatch_event("fixture", {"value": "blocked"})
    # A missing context in the same strict profile is a denial, not legacy authority.
    from tools.egress_policy import EgressDenied
    with pytest.raises(EgressDenied):
        manager._dispatch_event("fixture", {"value": "missing-context"})
    monkeypatch.setattr("agent.identity_lifecycle.identity_config", lambda: {})
    assert manager._dispatch_event("fixture", {"value": "legacy"}) == 1
    assert manager._wait_for_event_dispatch(timeout=3)
    assert seen == ["legacy"]


def test_startup_hook_is_blocked_before_callback_without_admitted_run(factory):
    from hermes_cli.plugins import PluginManager
    make, _, _ = factory
    agent, _ = make()
    manager = PluginManager()
    called = []
    manager._hooks["on_agent_init"] = [lambda **kwargs: called.append(True)]
    with agent_runtime_scope(agent.runtime_context):
        with pytest.raises(BudgetBlocked, match="admitted run"):
            manager.invoke_hook("on_agent_init")
    assert called == []


@pytest.mark.parametrize("new_policy", ["widened", "disabled"])
def test_crash_after_unqueued_acceptance_cannot_replace_policy(factory, new_policy):
    make, db, home = factory
    agent, calls = make(budget=policy(attempts=1))
    from agent.runtime_commands import submit_command, bind_submitted_command
    with agent_runtime_scope(agent.runtime_context):
        receipt = submit_command(agent, {"schema_version": 1, "command_id": "accepted-before-crash",
            "idempotency_key": "accepted-before-crash", "expected_revision": None,
            "operation": "submit", "payload": {"text": "hello"}})
    original = db.read_runtime_command(agent.session_id, receipt["command_id"])["budget_policy_json"]
    assert original == agent._runtime_budget_policy.snapshot
    # No root exists yet. A reconstructed agent sees changed configuration.
    restored, new_calls = make(budget=policy(attempts=100))
    if new_policy == "disabled":
        from agent.budget_account import install_budget_policy
        install_budget_policy(restored, {"agent_identity": config()["agent_identity"], "runtime_budget": {}})
    with bind_submitted_command(restored, receipt):
        result = restored.run_conversation("hello")
    assert result["budget_blocked"] and result["runtime_status"] == "blocked"
    assert new_calls == calls == []
    assert db.read_runtime_command(agent.session_id, receipt["command_id"])["budget_policy_json"] == original


def test_budget_metadata_column_migrates_old_unbudgeted_command(tmp_path):
    db = SessionDB(tmp_path / "migration.db")
    db._execute_write(lambda conn: conn.execute("ALTER TABLE runtime_commands DROP COLUMN budget_policy_json"))
    db.close()
    migrated = SessionDB(tmp_path / "migration.db")
    try:
        with migrated._runtime_read() as conn:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(runtime_commands)")}
        assert "budget_policy_json" in columns
    finally:
        migrated.close()


def test_bounded_retry_refusals_are_physical_and_no_sdk_amplification(factory, monkeypatch):
    make, db, _ = factory
    replies = iter([httpx.Response(429, headers={"Retry-After": "0"}, json={"error": {"message": "rate limit"}}),
                    httpx.Response(200, json=response())])
    agent, calls = make(handler=lambda r: next(replies))
    monkeypatch.setattr("agent.attempt_policy.jittered_backoff", lambda *a, **k: 0)
    result = agent.run_conversation("hello")
    assert result["final_response"] == "bounded answer"
    assert len(calls) == 2
    events = db.replay_runtime_events(agent.session_id)["events"]
    physical = [e for e in events if "physical_attempt" in e["payload"]]
    starts = [e for e in physical if e["type"] == "model.started"]
    finishes = [e for e in physical if e["type"] != "model.started"]
    assert len(starts) == len(finishes) == 2
    for event in physical:
        item = event["payload"]["physical_attempt"]
        assert event["operation_id"] == item["reservation_id"] == item["attempt_id"]
        assert "fixture" not in item["provider_account_ref"]
    assert finishes[0]["payload"]["physical_attempt"]["remote_acceptance"] == "rejected"
    assert finishes[1]["payload"]["physical_attempt"]["remote_acceptance"] == "accepted"
    assert result["runtime_budget"]["consumed"]["attempts"] + result["runtime_budget"]["reserved"]["attempts"] == 2
    assert result["runtime_budget"]["unknown_usage"]
    assert result["runtime_budget"]["reserved"]["tokens"] > 0
    assert result["runtime_budget"]["pending_provider_requests"] == 0


@pytest.mark.parametrize("status,message,expected", [
    (401, "invalid API key", "auth_failure"),
    (429, "insufficient_quota", "quota_exhausted"),
    (400, "maximum context length exceeded", "context_overflow"),
    (404, "model does not exist", "unsupported_capability"),
    (500, "upstream reset after possible acceptance", "ambiguous_transport"),
    (503, "server overloaded", "overloaded"),
])
def test_bounded_terminal_failure_cannot_enter_legacy_recovery(factory, monkeypatch, status, message, expected):
    make, db, _ = factory
    agent, calls = make(handler=lambda r: httpx.Response(status, json={"error": {"message": message}}))
    def forbidden(*args, **kwargs):
        pytest.fail("bounded failure entered legacy recovery")
    monkeypatch.setattr("agent.turn_api_error.recover_before_classification", forbidden)
    monkeypatch.setattr(agent, "_try_activate_fallback", forbidden)
    result = agent.run_conversation("hello")
    assert len(calls) == 1 and result["failure_reason"] == expected
    if status in (500, 503):
        assert result["outcome_uncertain"]
        assert result["runtime_budget"]["unknown_usage"]
        assert result["runtime_budget"]["pending_provider_requests"] == 1


def test_strict_unbudgeted_fallback_and_aux_discovery_do_not_resolve(factory, monkeypatch):
    make, _, _ = factory
    agent, _ = make()
    agent._runtime_budget_policy = None  # identity enforcement is independent of budget opt-in
    agent._fallback_chain = [{"provider": "custom", "model": "private", "base_url": "https://foreign.invalid/v1"}]
    from agent.chat_completion_helpers import try_activate_fallback
    from agent import auxiliary_client as aux
    def forbidden(*a, **k):
        pytest.fail("unauthorized fallback tried to resolve credentials or a provider")
    monkeypatch.setattr(aux, "resolve_provider_client", forbidden)
    monkeypatch.setattr(aux, "_get_provider_chain", forbidden)
    with agent_runtime_scope(agent.runtime_context):
        assert not try_activate_fallback(agent)
        assert "recipient_purpose" in agent._fallback_refusal_reason
        assert aux._try_configured_fallback_chain("compression", "openai") == (None, None, "")
        assert aux._try_main_fallback_chain("compression") == (None, None, "")
        assert aux._try_payment_fallback("openai") == (None, None, "")
        assert aux._try_main_agent_model_fallback("openai") == (None, None, "")
        assert aux._try_discovery_chain() == (None, None, "")


def test_three_refusals_are_total_ceiling_even_with_large_legacy_retries(factory, monkeypatch):
    make, _, _ = factory
    agent, calls = make(handler=lambda r: httpx.Response(429, headers={"Retry-After": "0"},
                                                        json={"error": {"message": "rate limit"}}))
    agent._api_max_retries = 99
    agent._auto_recovery_cycles = 99
    monkeypatch.setattr("agent.attempt_policy.jittered_backoff", lambda *a, **k: 0)
    result = agent.run_conversation("hello")
    assert len(calls) == 3
    assert result["failure_reason"] == "throttled"
    assert result["runtime_budget"]["consumed"]["attempts"] + result["runtime_budget"]["reserved"]["attempts"] == 3
    assert result["runtime_budget"]["pending_provider_requests"] == 0


def test_async_timeout_is_one_unknown_physical_attempt(factory):
    import asyncio
    from agent.budget_account import invoke_budgeted_completion_async
    make, db, _ = factory
    agent, _ = make()
    requests = []
    def handler(request):
        requests.append(request)
        raise httpx.ReadTimeout("fixture timeout", request=request)
    async def invoke():
        async with openai.AsyncOpenAI(api_key="fixture", base_url=BASE_URL, max_retries=8,
                http_client=guarded_http(handler, purpose="aux_model", async_mode=True)) as client:
            with pytest.raises(openai.APITimeoutError):
                await invoke_budgeted_completion_async(client, {"model": MODEL, "messages": []})
            with pytest.raises(BudgetBlocked):
                await invoke_budgeted_completion_async(client, {"model": MODEL, "messages": []})
    with active(agent) as run:
        asyncio.run(invoke())
        status = run.budget.status()
        assert len(requests) == 1 and status["unknown_usage"]
        assert status["pending_provider_requests"] == 1
    physical = [e["payload"]["physical_attempt"] for e in db.replay_runtime_events(agent.session_id)["events"]
                if e["type"] == "model.failed" and "physical_attempt" in e["payload"]]
    assert len(physical) == 1
    assert physical[0]["reason"] == "ambiguous_transport" and physical[0]["remote_acceptance"] == "unknown"


def test_strict_auxiliary_pool_refresh_and_healing_refuse_before_callbacks(factory, monkeypatch):
    make, _, _ = factory
    agent, _ = make()
    agent._runtime_budget_policy = None
    from agent import auxiliary_client as aux
    def forbidden(*a, **k):
        pytest.fail("strict auxiliary recovery touched an opaque credential pool or OAuth network")
    monkeypatch.setattr(aux, "load_pool", forbidden)
    monkeypatch.setattr(aux, "_refresh_nous_recommended_model", forbidden)
    monkeypatch.setattr(aux, "_CREDENTIAL_REFRESHERS", {"anthropic": forbidden})
    error = RuntimeError("fixture rejection")
    with agent_runtime_scope(agent.runtime_context):
        assert aux._load_pool_with_credentials("anthropic") is None
        assert aux._peek_pool_entry("anthropic") is None
        with pytest.raises(PermissionError, match="opaque credential pool"):
            aux._peek_pool_entry("anthropic", SimpleNamespace(current=forbidden, peek=forbidden))
        assert aux._recover_provider_pool("anthropic", error) is False
        assert aux._refresh_provider_credentials("anthropic", failed_api_key="fixture") is False
        for rung in (aux._ladder_nous_rungs, aux._ladder_credential_rungs):
            iterator = rung(error, SimpleNamespace(), {}, True)
            with pytest.raises(StopIteration) as stopped:
                next(iterator)
            assert stopped.value.value == (None, error)


def test_budgeted_auxiliary_recovery_refuses_before_parameter_or_auth_ladders(factory, monkeypatch):
    make, _, _ = factory
    agent, _ = make()
    from agent import auxiliary_client as aux
    def forbidden(*a, **k):
        pytest.fail("bounded auxiliary failure entered an independent recovery ladder")
    for name in ("_ladder_parameter_rungs", "_ladder_nous_rungs", "_ladder_credential_rungs"):
        monkeypatch.setattr(aux, name, forbidden)
    error = RuntimeError("fixture refusal")
    with active(agent):
        iterator = aux._aux_recovery_ladder(error, client=object(), kwargs={}, task="compression",
            async_mode=False, base_info=BASE_URL, resolved_provider="openai", resolved_model=MODEL,
            resolved_base_url=BASE_URL, resolved_api_key="fixture", resolved_api_mode="chat_completions",
            final_model=MODEL, max_tokens=64, main_runtime=None, route_info=None)
        with pytest.raises(RuntimeError, match="fixture refusal") as caught:
            next(iterator)
        assert caught.value is error


def test_strict_pool_entry_denies_before_disk_oauth_or_named_custom_lookup(factory, monkeypatch):
    make, _, _ = factory
    agent, _ = make()
    from agent import credential_pool as pools, auxiliary_client as aux
    def forbidden(*a, **k):
        pytest.fail("strict identity loaded or healed an ambient credential pool")
    monkeypatch.setattr(pools, "read_credential_pool", forbidden)
    monkeypatch.setattr(pools.auth_mod, "heal_forked_single_use_oauth_grants", forbidden)
    monkeypatch.setattr(pools, "custom_provider_pool_key_candidates", lambda *a: ["custom:https://private.invalid"])
    with agent_runtime_scope(agent.runtime_context):
        for provider in ("anthropic", "openai", "custom:https://private.invalid"):
            with pytest.raises(PermissionError, match="identity-aware account grants"):
                pools.load_pool(provider)
        # Optional named-custom lookup may be unavailable, but cannot bypass the
        # shared gate or borrow a pool; an explicitly scoped key stays usable.
        assert aux._named_custom_api_key({}, "custom:private", "https://private.invalid") == "no-key-required"
        assert aux._named_custom_api_key({"key_env": "OPENAI_API_KEY"}, "custom:private", BASE_URL) == "fixture-provider-key"
