"""Real admitted turn/hook/journal/replay paths with shadow enabled, no live model."""
from dataclasses import asdict
import json

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
            "allowed_tools": ["todo_list", "tool_search", "tool_describe", "tool_call"], "recipient_plan": {"schema_version": 1, "envelope": "declared", "grants": [{
                "recipient_id": "fixture", "purpose": "main_model", "endpoint": "https://fixture.invalid/v1", "transport": "httpx"}]}}},
        "child_policy": {"policy_version": 1, "role": "child", "memory_backend": "builtin", "secret_refs": [], "allowed_tools": []}}}


def response(tool):
    calls = [{"id": "tool_1", "type": "function", "function": {"name": "tool_call",
        "arguments": '{"calls":[{"name":"todo_list","arguments":{"todos":[{"id":"1","content":"synthetic work","status":"completed"}]}}]}'}}] if tool else None
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


def execute(agent, text="planner-private-canary"):
    with agent_runtime_scope(agent.runtime_context):
        receipt = submit_command(agent, {"schema_version": 1, "command_id": text, "idempotency_key": text,
            "expected_revision": None, "operation": "submit", "payload": {"text": text}})
    with bind_submitted_command(agent, receipt):
        return agent.run_conversation(text)


def test_real_lifecycle_two_stage_observer_keeps_prefix_and_records_closed_plan(agents):
    _, off, _, baseline_requests = agents("baseline", "off")
    baseline = execute(off)
    _, candidate, db, requests = agents("candidate")
    tools_before = json.dumps(candidate.tools, sort_keys=True)
    prompt_before = candidate._cached_system_prompt
    result = execute(candidate)
    assert result["final_response"] == baseline["final_response"]
    assert [row["tools"] for row in requests] == [row["tools"] for row in baseline_requests]
    assert json.dumps(candidate.tools, sort_keys=True) == tools_before and candidate._cached_system_prompt == prompt_before
    events = db.replay_runtime_events("session")["events"]
    plans = [event for event in events if event["type"] == "decision.tool_plan"]
    assert len(plans) == 1
    assert plans[0]["payload"]["fallback"] == "privacy_not_qualified"
    assert plans[0]["payload"]["mode"] == "shadow"
    receipts = [event for event in events if event["type"] == "decision.observed" and event["payload"]["point_id"] == "DP16"]
    assert {event["payload"]["question_id"] for event in receipts} == {"need", "effort", "family"}
    assert all(event["payload"]["distribution"] is None for event in receipts)
    metadata = [event for event in events if event["type"].startswith("decision.")]
    assert "planner-private-canary" not in json.dumps(metadata)
    from tui_gateway.methods_runtime import _runtime_event_projection
    from tui_gateway.contracts.runtime_v1 import RuntimeEventEnvelope
    serialized = [RuntimeEventEnvelope.model_validate(_runtime_event_projection(event)).model_dump(mode="json") for event in metadata]
    assert next(event for event in serialized if event["type"] == "decision.tool_plan")["payload"]["decision_tool_plan"]["bundle_id"] == plans[0]["payload"]["bundle_id"]
    assert {event["payload"]["decision_policy"]["point_id"] for event in serialized if event["type"] == "decision.policy"} == {"DP06", "DP07", "DP16"}


def test_real_planner_a_b_a_scope_and_projection_rejects_extra_fields(agents, monkeypatch):
    home_a, a, db_a, _ = agents("profile_a")
    execute(a, "first-a")
    _, b, db_b, _ = agents("profile_b")
    execute(b, "first-b")
    monkeypatch.setenv("HERMES_HOME", str(home_a))
    execute(a, "second-a")
    plans_a = [event for event in db_a.replay_runtime_events("session")["events"] if event["type"] == "decision.tool_plan"]
    plans_b = [event for event in db_b.replay_runtime_events("session")["events"] if event["type"] == "decision.tool_plan"]
    assert len(plans_a) == 2 and len(plans_b) == 1
    assert {event["payload"]["scope_digest"] for event in plans_a} != {event["payload"]["scope_digest"] for event in plans_b}
    from tui_gateway.methods_runtime import _runtime_event_projection
    plans_a[0]["payload"]["raw_private_state"] = "must-not-forward"
    assert _runtime_event_projection(plans_a[0])["payload"] == {}


def test_actual_describe_owner_records_shadow_omission_and_denies_private_name(agents):
    from dataclasses import replace
    from agent.runtime_commands import assert_runtime_dispatch
    from tools.todo_tool import TODO_SCHEMA
    from tools.tool_search import dispatch_tool_describe
    from agent.decisions.contracts import digest
    captured = []
    denied_name = "mcp__another_primary__private"

    def describe_during_admitted_turn():
        run = assert_runtime_dispatch()
        run_id, plan, prefix = run.agent._decision_tool_plan
        # A controlled candidate omission exercises the real bridge without
        # transporting private packets or pretending this fixture is a model.
        candidate = replace(plan, need="no_tools", verified_tool_ids=(), fallback=None)
        run.agent._decision_tool_plan = (run_id, candidate, prefix)
        before = json.dumps(run.agent.tools, sort_keys=True)
        output = json.loads(dispatch_tool_describe({"names": ["todo_list", denied_name]},
            current_tool_defs=[{"type": "function", "function": TODO_SCHEMA}]))
        assert json.dumps(run.agent.tools, sort_keys=True) == before
        captured.append(output)

    _, agent, db, _ = agents("bridge")
    agent._fixture_on_request = describe_during_admitted_turn
    execute(agent)
    assert "todo_list" in captured[0]["tools"] and denied_name not in captured[0]["tools"]
    events = [event for event in db.replay_runtime_events("session")["events"] if event["type"] == "decision.planner_miss"]
    assert len(events) == 2
    by_digest = {event["payload"]["tool_digest"]: event["payload"] for event in events}
    assert by_digest[digest("todo_list")]["recovered"] is True
    assert by_digest[digest(denied_name)]["recovered"] is False
    assert all(event["payload"]["observation_only"] for event in events)
    assert denied_name not in json.dumps(events)
    from tui_gateway.methods_runtime import _runtime_event_projection
    from tui_gateway.contracts.runtime_v1 import RuntimeEventEnvelope
    for event in events:
        result = RuntimeEventEnvelope.model_validate(_runtime_event_projection(event))
        assert result.payload.decision_planner_miss.observation_only
