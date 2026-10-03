"""Real admitted turn/hook/journal/replay paths with shadow enabled, no live model."""
from dataclasses import asdict
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
from openai import OpenAI
import pytest

from agent.decisions.contracts import ModelBundle
from agent.identity_lifecycle import agent_runtime_scope
from agent.runtime_commands import bind_submitted_command, submit_command
from hermes_state import SessionDB


def configuration(profile="fixture", mode="shadow"):
    return {"decisions": {"schema_version": 1, "bundle": asdict(ModelBundle("1"*64, "2"*64, "3"*64)),
        "points": {key: {"mode": mode} for key in ("DP06", "DP07", "DP16")}},
        "agent_identity": {"schema_version": 1, "principal_id": "owner", "profile_id": profile,
        "primary_agent_id": "primary", "active_agent_id": "primary", "agents": {"primary": {
            "policy_version": 1, "role": "primary", "memory_backend": "personal_mcp", "secret_refs": ["OPENAI_API_KEY"],
            "allowed_tools": ["todo_list"], "recipient_plan": {"schema_version": 1, "envelope": "declared", "grants": [{
                "recipient_id": "fixture", "purpose": "main_model", "endpoint": "https://fixture.invalid/v1", "transport": "httpx"}]}}},
        "child_policy": {"policy_version": 1, "role": "child", "memory_backend": "builtin", "secret_refs": [], "allowed_tools": []}}}


def response(tool):
    calls = [{"id": "tool_1", "type": "function", "function": {"name": "todo_list",
        "arguments": '{"todos":[{"id":"1","content":"synthetic work","status":"completed"}]}'}}] if tool else None
    return {"id": "fixture", "object": "chat.completion", "created": 1, "model": "fixture/model",
            "choices": [{"finish_reason": "tool_calls" if tool else "stop", "message": {
                "role": "assistant", "content": None if tool else "recorded answer", "tool_calls": calls}}]}


@pytest.fixture
def agents(tmp_path, monkeypatch):
    created = []
    from tools.todo_tool import TODO_SCHEMA
    monkeypatch.setattr("model_tools.get_tool_definitions", lambda *a, **k: [{"type": "function", "function": TODO_SCHEMA}])
    monkeypatch.setattr("model_tools.check_toolset_requirements", lambda *a, **k: {})
    def make(name, mode="shadow", *, budget=False):
        home = tmp_path / name
        home.mkdir()
        monkeypatch.setenv("HERMES_HOME", str(home))
        config = configuration(name, mode)
        if budget:
            config["runtime_budget"] = {"schema_version": 1, "mode": "tokens", "limits": {
                "tokens": 500000, "attempts": 8, "cost_micros": None, "wall_ms": 120000,
                "provider_slots": 2, "executor_slots": 2}, "deadline_seconds": 120, "request_timeout_ms": 10000,
                "routes": [{"model": "gpt-4.1-mini", "base_url": "https://fixture.invalid/v1", "max_input_tokens": 100000,
                    "max_output_tokens": 64, "input_overhead_tokens": 128, "output_token_parameter": "max_tokens",
                    "input_cost_micros_per_million": None, "output_cost_micros_per_million": None, "bounds_verified": True}]}
        (home / "config.yaml").write_text(json.dumps(config), encoding="utf-8")
        (home / ".env").write_text("OPENAI_API_KEY=synthetic-provider-key\n", encoding="utf-8")
        requests = []
        def respond(request):
            requests.append(json.loads(request.content))
            callback = getattr(agent, "_fixture_on_request", None)
            if callback is not None and len(requests) == 1:
                callback()
            data = response(len(requests) == 1)
            data["usage"] = {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14}
            return httpx.Response(200, json=data)
        def client(*args, **kwargs):
            from tools.egress_policy import prepare_recipient, wrap_httpx_transport
            authorization = prepare_recipient("main_model", "https://fixture.invalid/v1")
            http = httpx.Client(transport=wrap_httpx_transport(httpx.MockTransport(respond), authorization), trust_env=False)
            http._hermes_recipient_authorization = authorization
            return OpenAI(api_key="synthetic-provider-key", base_url="https://fixture.invalid/v1", max_retries=0, http_client=http)
        monkeypatch.setattr("tools.egress_policy.build_model_client", client)
        from run_agent import AIAgent
        db = SessionDB(home / "state.db")
        agent = AIAgent(model="gpt-4.1-mini", provider="openai", api_key="synthetic-provider-key",
            base_url="https://fixture.invalid/v1", session_id="session", session_db=db, quiet_mode=True,
            skip_context_files=True, skip_memory=True, max_iterations=4, enabled_toolsets=["todo"])
        agent._cached_system_prompt = "Fixed fixture system prompt."
        agent._use_prompt_caching = False
        agent._disable_streaming = True
        agent.tool_delay = 0
        agent.save_trajectories = False
        agent.compression_enabled = False
        monkeypatch.setattr(agent, "_create_request_openai_client", lambda **kwargs: agent.client)
        monkeypatch.setattr(agent, "_close_request_openai_client", lambda *a, **k: None, raising=False)
        monkeypatch.setattr(agent, "_cleanup_task_resources", lambda *a, **k: None)
        monkeypatch.setattr(agent, "_save_trajectory", lambda *a, **k: None)
        created.append((home, agent, db))
        return home, agent, db, requests
    yield make
    for home, agent, db in created:
        monkeypatch.setenv("HERMES_HOME", str(home))
        agent.close()
        db.close()


def execute(agent, text="seeded-private-canary"):
    with agent_runtime_scope(agent.runtime_context):
        receipt = submit_command(agent, {"schema_version": 1, "command_id": "command", "idempotency_key": "command",
            "expected_revision": None, "operation": "submit", "payload": {"text": text}})
    with bind_submitted_command(agent, receipt):
        return agent.run_conversation(text)


def test_real_turn_shadow_preserves_prompt_tools_result_and_replay(agents):
    _, off, _, off_requests = agents("off", "off")
    baseline = execute(off)
    _, agent, db, requests = agents("shadow")
    before_tools = json.dumps(agent.tools, sort_keys=True)
    before_prompt = agent._cached_system_prompt
    result = execute(agent)
    assert result["final_response"] == baseline["final_response"] == "recorded answer"
    assert len(requests) == len(off_requests) == 2
    assert [row["tools"] for row in requests] == [row["tools"] for row in off_requests]
    assert agent._cached_system_prompt == before_prompt and json.dumps(agent.tools, sort_keys=True) == before_tools
    events = db.replay_runtime_events("session")["events"]
    observed = [row for row in events if row["type"] == "decision.observed"]
    assert {row["payload"]["point_id"] for row in observed} == {"DP06", "DP07", "DP16"}
    assert len([row for row in observed if row["payload"]["point_id"] == "DP16"]) == 1
    assert all(row["payload"]["fallback"] == "privacy_not_qualified" for row in observed)
    assert "seeded-private-canary" not in json.dumps(observed)
    from tui_gateway.methods_runtime import _runtime_event_projection
    from tui_gateway.contracts.runtime_v1 import RuntimeEventEnvelope
    serialized = [RuntimeEventEnvelope.model_validate(_runtime_event_projection(row)).model_dump(mode="json") for row in observed]
    assert all(row["payload"]["decision_receipt"]["raw_state_retained"] is False for row in serialized)
    assert {row["payload"]["decision_receipt"]["receipt_id"] for row in serialized} == {row["payload"]["receipt_id"] for row in observed}


def test_profile_a_b_a_receipts_never_reuse_scope_or_client(agents, monkeypatch):
    home_a, a, db_a, _ = agents("a")
    execute(a)
    first_client = a._typed_decision_client
    first = [row["payload"] for row in db_a.replay_runtime_events("session")["events"] if row["type"] == "decision.observed"]
    _, b, db_b, _ = agents("b")
    execute(b)
    second = [row["payload"] for row in db_b.replay_runtime_events("session")["events"] if row["type"] == "decision.observed"]
    monkeypatch.setenv("HERMES_HOME", str(home_a))
    assert a._typed_decision_client is first_client and a._typed_decision_client is not b._typed_decision_client
    assert first[0]["scope_digest"] != second[0]["scope_digest"]
    assert db_a.replay_runtime_events("session")["events"] != db_b.replay_runtime_events("session")["events"]


def test_config_enforce_and_malformed_replay_are_closed(agents):
    from agent.decisions.integration import parse_settings
    from agent.decisions.contracts import DecisionError
    raw = configuration()["decisions"]
    raw["points"]["DP16"]["mode"] = "enforce"
    with pytest.raises(DecisionError, match="point_gate_required"):
        parse_settings(raw)
    _, agent, db, _ = agents("redaction")
    execute(agent)
    row = next(row for row in db.replay_runtime_events("session")["events"] if row["type"] == "decision.observed")
    row["payload"]["raw_state"] = "must-not-forward"
    from tui_gateway.methods_runtime import _runtime_event_projection
    assert "must-not-forward" not in json.dumps(_runtime_event_projection(row))


def test_receipt_labels_replay_and_observer_wall_budget_use_real_ledger(agents):
    _, agent, db, requests = agents("budget", budget=True)
    def annotate():
        from agent.decisions.receipts import JournalSink
        from agent.runtime_commands import assert_runtime_dispatch
        from agent.decisions.contracts import DecisionError
        run = assert_runtime_dispatch()
        row = next(row["payload"] for row in db.replay_runtime_events("session")["events"]
                   if row["type"] == "decision.observed")
        sink = JournalSink(run)
        with pytest.raises(DecisionError, match="unknown_receipt_or_label"):
            sink.annotate(row["receipt_id"], label="grant_admin", outcome="correct", source_digest="8"*64)
        sink.annotate(row["receipt_id"], label="needs_tools", outcome="correct", source_digest="8"*64)
    agent._fixture_on_request = annotate
    assert execute(agent)["final_response"] == "recorded answer" and len(requests) == 2
    with db._runtime_read() as conn:
        rows = conn.execute("SELECT operation_id,consumed_json,settlement_state FROM budget_reservations "
                            "WHERE operation_id LIKE 'decision_%'").fetchall()
    assert len(rows) >= 3
    assert all(row["settlement_state"] == "settled" and json.loads(row["consumed_json"])["wall_ms"] > 0 for row in rows)
    assert all(json.loads(row["consumed_json"])["attempts"] == 0 for row in rows)
    from tui_gateway.methods_runtime import _runtime_event_projection
    from tui_gateway.contracts.runtime_v1 import RuntimeEventEnvelope
    event = next(row for row in db.replay_runtime_events("session")["events"] if row["type"] == "decision.outcome")
    rendered = RuntimeEventEnvelope.model_validate(_runtime_event_projection(event)).model_dump(mode="json")
    assert rendered["payload"]["decision_outcome"]["label"] == "needs_tools"
