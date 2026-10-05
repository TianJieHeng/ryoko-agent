"""Production front-door owner + real local TLS with synthetic data and fake keys.

Only fixture-origin requests are explicitly marked synthetic. The ordinary
private path is exercised separately and never reaches the local server.
"""
from dataclasses import asdict
import json
import time

import pytest

from agent.decisions.contracts import DecisionError
from agent.decisions.laya_transport import LAYA_ENDPOINT, LayaDestinationManifest, LayaHttpsTransport
from tests.agent import test_decision_planner_runtime as legacy
from tests.agent.test_decision_planner_runtime import agents as base_agents, execute
from tests.agent.test_laya_destination_authorization import BUNDLE
from tests.agent.test_laya_transport import tls_server

CHOICES = {"need": "needs_tools", "effort": "one", "family": "yes", "include": "yes", "verify": "yes"}
TEXT = "synthetic observer request"


def enable_configuration(monkeypatch, *, mode="shadow", bridges=True):
    original = legacy.configuration
    def configuration(profile="fixture", ignored="shadow"):
        raw = original(profile, ignored)
        owner = raw["agent_identity"]["agents"]["primary"]
        owner["secret_refs"].append("LAYA_API_KEY")
        owner["recipient_plan"]["grants"].append({"recipient_id": "laya-observer", "purpose": "decision_inference",
            "endpoint": LAYA_ENDPOINT, "transport": "httpx"})
        if not bridges:
            owner["allowed_tools"] = ["todo_list"]
        raw["decisions"] = {"schema_version": 2, "protocol": "laya_systemone", "bundle": asdict(BUNDLE),
            "points": {"DP16": {"mode": mode, "timeout_seconds": 1}}, "destination": {
                "schema_version": 1, "endpoint": LAYA_ENDPOINT, "recipient_id": "laya-observer",
                "secret_ref": "LAYA_API_KEY", **{"expected_" + key: value for key, value in asdict(BUNDLE).items()}}}
        return raw
    monkeypatch.setattr(legacy, "configuration", configuration)


def use_local_tls(monkeypatch, server, *, synthetic=True, allowed=(TEXT,)):
    from agent.decisions import integration, planner_runtime_context
    from agent.decisions.planner_context import build_planner_context
    created = []
    def transport(raw):
        result = LayaHttpsTransport(LayaDestinationManifest.from_record(raw["destination"]),
                                   bundle=BUNDLE, synthetic_fixture=server.fixture)
        created.append(result)
        return result
    monkeypatch.setattr(integration, "_transport", transport)
    if synthetic:
        def context(request, **kwargs):
            assert request in allowed
            kwargs["classification"] = "synthetic"
            return build_planner_context(request, **kwargs)
        monkeypatch.setattr(planner_runtime_context, "build_planner_context", context)
    return created


@pytest.fixture
def agents(base_agents):
    def make(name, *args, **kwargs):
        suffix = name.replace("_", "-") if name.startswith("profile_") else "fixture"
        return base_agents(name, *args, secrets={"LAYA_API_KEY": "synthetic-laya-" + suffix}, **kwargs)
    return make


def plans(db):
    return [event["payload"] for event in db.replay_runtime_events("session")["events"]
            if event["type"] == "decision.tool_plan"]


def test_real_observer_local_tls_three_stages_and_shadow_provider_bytes(agents, monkeypatch, tmp_path):
    _, baseline, _, baseline_requests = agents("baseline", "off")
    baseline_result = execute(baseline, TEXT)
    enable_configuration(monkeypatch)
    with tls_server(tmp_path, choices=CHOICES) as server:
        created = use_local_tls(monkeypatch, server)
        home, candidate, db, requests = agents("candidate")

        original_tools = json.dumps(candidate.tools, sort_keys=True)
        result = execute(candidate, TEXT)
        assert result["final_response"] == baseline_result["final_response"]
        assert requests == baseline_requests
        assert json.dumps(candidate.tools, sort_keys=True) == original_tools
        assert len(created) == 1 and len(server.received) == 3
        observed = plans(db)
        assert len(observed) == 1 and observed[0]["fallback"] is None
        assert observed[0]["protocol_version"] == 2 and observed[0]["verified_tool_ids"] == ["todo_list"]
        assert observed[0]["metrics"]["batch_count"] == 3
        assert observed[0]["metrics"]["input_tokens"] == 30
        assert observed[0]["metrics"]["output_tokens"] == 6
        assert observed[0]["renderer_version"] == "dp16-systemone-v1"
        assert observed[0]["authorization_status"] == "transport_attempted"
        assert len(observed[0]["decision_receipt_ids"]) == 5
        events = [event for event in db.replay_runtime_events("session")["events"] if event["type"].startswith("decision.")]
        assert TEXT not in json.dumps(events) and "synthetic-laya-fixture" not in json.dumps(events)
        first_state = json.loads(json.loads(server.received[0][2])["state"])
        assert first_state["context"]["request"] == TEXT
        assert first_state["catalog"]["projection"] == "family_summaries_v1"
        assert first_state["catalog"]["tools"] == []
        assert all(b"synthetic-laya-fixture" not in row[2] for row in server.received)


@pytest.mark.parametrize("mode,synthetic,bridges,expected", [
    ("off", True, True, None),
    ("shadow", False, True, "privacy_not_qualified"),
    ("shadow", True, False, "authorized_bridge_required"),
])
def test_disabled_private_and_missing_bridges_send_zero_bytes(agents, monkeypatch, tmp_path, mode, synthetic, bridges, expected):
    enable_configuration(monkeypatch, mode=mode, bridges=bridges)
    with tls_server(tmp_path, choices=CHOICES) as server:
        created = use_local_tls(monkeypatch, server, synthetic=synthetic)
        home, agent, db, _ = agents("blocked")

        execute(agent, TEXT)
        assert server.received == []
        if expected is None:
            assert created == [] and plans(db) == []
        else:
            assert len(plans(db)) == 1 and plans(db)[0]["fallback"] == expected


def test_retry_tool_round_and_duplicate_hook_reuse_only_current_turn(agents, monkeypatch, tmp_path):
    from agent.decisions import integration
    from agent.runtime_commands import assert_runtime_dispatch
    enable_configuration(monkeypatch)
    with tls_server(tmp_path, choices=CHOICES) as server:
        use_local_tls(monkeypatch, server)
        home, agent, db, _ = agents("once")

        def on_request():
            run = assert_runtime_dispatch()
            kwargs = {"request": {"body": {"tools": agent.tools}}, "user_message": TEXT,
                "conversation_history": [], "turn_id": agent._current_turn_id, "api_call_count": 1, "retry_count": 0}
            saved = integration._observe_lifecycle("pre_api_request", **kwargs)
            assert saved.bundle_id == plans(db)[0]["bundle_id"]
            assert integration._observe_lifecycle("pre_api_request", **(kwargs | {"retry_count": 1})) is None
            assert integration._observe_lifecycle("pre_api_request", **(kwargs | {"api_call_count": 2})) is None
            assert len(server.received) == 3
            assert agent._decision_prepared_frontdoor.run_id == run.run_id
        agent._fixture_on_request = on_request
        execute(agent, TEXT)
        assert len(server.received) == 3 and len(plans(db)) == 1


def test_receipt_failure_preserves_incumbent_and_stops_dependent_stages(agents, monkeypatch, tmp_path):
    enable_configuration(monkeypatch)
    with tls_server(tmp_path, choices=CHOICES) as server:
        use_local_tls(monkeypatch, server)
        home, agent, db, requests = agents("sink_failure")

        append = db.append_runtime_event
        def failing(session, event_type, payload, **kwargs):
            if event_type == "decision.observed":
                raise OSError("synthetic-sensitive-storage-error")
            return append(session, event_type, payload, **kwargs)
        monkeypatch.setattr(db, "append_runtime_event", failing)
        assert execute(agent, TEXT)["final_response"] == "recorded answer"
        assert len(server.received) == 1 and len(requests) == 2
        assert plans(db)[0]["fallback"] == "receipt_unavailable"
        assert "synthetic-sensitive-storage-error" not in json.dumps(plans(db))


def test_revoked_run_after_tls_result_cannot_publish_plan(agents, monkeypatch, tmp_path):
    from agent.decisions import integration
    from agent.runtime_commands import assert_runtime_dispatch
    captured = []
    enable_configuration(monkeypatch)
    with tls_server(tmp_path, choices=CHOICES) as server:
        use_local_tls(monkeypatch, server)
        original = integration._transport
        def transport(raw):
            result = original(raw)
            run = assert_runtime_dispatch()
            native = result.decide_many
            def infer(requests, timeout):
                answer = native(requests, timeout)
                run.dispatch_blocked.set()
                captured.append(run)
                return answer
            result.decide_many = infer
            return result
        monkeypatch.setattr(integration, "_transport", transport)
        home, agent, db, _ = agents("cancelled")

        try:
            execute(agent, TEXT)
        except Exception as exc:
            from agent.runtime_commands import RuntimeFenceError
            assert isinstance(exc, RuntimeFenceError)
        assert captured and len(server.received) == 1
        assert plans(db) == []
        assert getattr(agent, "_decision_prepared_frontdoor", None) is None


def test_profile_a_b_a_never_reuses_scope_or_secret(agents, monkeypatch, tmp_path):
    enable_configuration(monkeypatch)
    with tls_server(tmp_path, choices=CHOICES) as server:
        use_local_tls(monkeypatch, server, allowed=("synthetic request a", "synthetic request b", "synthetic request a second"))
        home_a, a, db_a, _ = agents("profile_a")

        execute(a, "synthetic request a")
        home_b, b, db_b, _ = agents("profile_b")

        execute(b, "synthetic request b")
        monkeypatch.setenv("HERMES_HOME", str(home_a))
        execute(a, "synthetic request a second")
        assert [row[1] for row in server.received] == (["Bearer synthetic-laya-profile-a"] * 3
            + ["Bearer synthetic-laya-profile-b"] * 3 + ["Bearer synthetic-laya-profile-a"] * 3)
        assert {plan["scope_digest"] for plan in plans(db_a)}.isdisjoint({plan["scope_digest"] for plan in plans(db_b)})


def test_owner_context_projects_statuses_without_raw_tool_results(monkeypatch):
    from agent.decisions.planner_runtime_context import _recent_items, _visible_request
    from agent.decisions.planner_context import build_planner_context
    monkeypatch.setattr("tools.agent_policy_gate.authorize_tool", lambda *a, **k: None)
    history = [{"role": "system", "content": "system-private-canary"},
        {"role": "assistant", "content": "I am reading", "reasoning": "reasoning-private-canary",
         "tool_calls": [{"id": "call", "function": {"name": "todo_list"}}]},
        {"role": "tool", "tool_call_id": "call", "content": json.dumps({"status": "failed",
            "error": {"code": "read_failed", "message": "raw-private-canary"}})}]
    messages, outcomes = _recent_items(history, TEXT, "fixture-turn")
    context = build_planner_context(TEXT, scope_digest="a" * 64, messages=messages, tool_outcomes=outcomes)
    assert context.fallback is None and context.classification == "private"
    assert context.values["tool_outcomes"][0]["status"] == "error"
    assert "read_failed" in context.state_json
    assert "private-canary" not in context.state_json
    with pytest.raises(DecisionError, match="planner_attachment_bound"):
        _visible_request([{"type": "text", "text": "synthetic"}] * 65, "fixture-turn")
