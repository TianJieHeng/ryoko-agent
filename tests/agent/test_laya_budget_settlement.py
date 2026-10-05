"""Planner stages share one real ledger reservation; uncertain work stays held."""
import json
import time

import pytest

from tests.agent.test_decision_planner_runtime import agents, execute


def test_complete_private_fallback_planner_is_one_operation_on_real_ledger(agents):
    _, agent, db, _ = agents("whole_planner_budget", budget=True)
    assert execute(agent, "bounded planner fixture")["final_response"] == "recorded answer"
    with db._runtime_read() as conn:
        rows = conn.execute("SELECT settlement_state,consumed_json FROM budget_reservations "
                            "WHERE operation_id LIKE 'decision_%'").fetchall()
    plans = [row for row in db.replay_runtime_events("session")["events"] if row["type"] == "decision.tool_plan"]
    assert len(plans) == 1
    # One front-door planner, one pre-tool observer and one tool-result observer.
    assert len(rows) == 3
    assert all(row["settlement_state"] == "settled" for row in rows)
    assert all(json.loads(row["consumed_json"])["attempts"] == 0 for row in rows)
    assert all(json.loads(row["consumed_json"])["wall_ms"] > 0 for row in rows)


def test_unknown_completion_settlement_retains_measured_uncertainty(agents):
    from agent.decisions.integration import _observe_with_budget
    from agent.runtime_commands import assert_runtime_dispatch
    _, agent, db, _ = agents("unknown_planner_budget", budget=True)
    observed = []

    def during_provider_request():
        run = assert_runtime_dispatch()
        result = _observe_with_budget(run, time.time() + .1, lambda: "fixture result",
                                      completion_unknown=lambda: True)
        assert result == "fixture result"
        with db._runtime_read() as conn:
            observed.extend(dict(row) for row in conn.execute(
                "SELECT settlement_state,unknown_usage,slots_released,held_json FROM budget_reservations "
                "WHERE operation_id LIKE 'decision_%' AND unknown_usage=1").fetchall())
    agent._fixture_on_request = during_provider_request
    execute(agent, "uncertain completion fixture")
    assert len(observed) == 1
    assert observed[0]["settlement_state"] == "unknown"
    assert observed[0]["unknown_usage"] == 1 and observed[0]["slots_released"] == 0
    assert json.loads(observed[0]["held_json"])["wall_ms"] > 0


def test_prequarantined_destination_does_not_hold_an_undispatched_budget(agents, monkeypatch):
    from agent.decisions.batching import SharedBatchAdmission
    from agent.decisions.client import DecisionClient
    from agent.decisions.contracts import ModelBundle
    from agent.decisions.planner_context import build_planner_context
    from agent.decisions.planner_runtime import prepare_front_door
    from agent.decisions.policy import PointPolicy
    from agent.decisions.receipts import JournalSink, scope_digest
    from agent.runtime_commands import assert_runtime_dispatch
    calls, inspected = [], []
    admission = SharedBatchAdmission()
    key = "synthetic-prequarantined-destination"
    admission.submit(key, lambda: None, timeout=1).result(timeout=1)
    admission.settle(key, failed=True, failure_limit=1, cooldown_seconds=30, remote_unknown=True)
    class Transport:
        protocol = "laya_systemone"
        admission_key = key
        def decide_many(self, *args):
            calls.append(args)
            pytest.fail("quarantined destination was dispatched")
    _, agent, db, _ = agents("prequarantined_budget", "off", budget=True)
    def context(run, **kwargs):
        assert kwargs["request"] == "synthetic quarantined planner"
        return build_planner_context(kwargs["request"], scope_digest=scope_digest(run.context), classification="synthetic")
    monkeypatch.setattr("agent.decisions.planner_runtime.owner_planner_context", context)
    def during_provider_request():
        run = assert_runtime_dispatch()
        client = DecisionClient(bundle=ModelBundle("1" * 64, "2" * 64, "3" * 64), transport=Transport(),
            policies={"DP16": PointPolicy("shadow", timeout_seconds=.5)}, sink=JournalSink(run), admission=admission)
        prepared = prepare_front_door(run, client, request_definitions=agent.tools,
                                      request="synthetic quarantined planner")
        assert prepared.plan.fallback == "remote_completion_unknown"
        assert prepared.plan.metrics.batch_count == 0 and not prepared.plan.metrics.remote_unknown
        with db._runtime_read() as conn:
            inspected.extend(dict(row) for row in conn.execute(
                "SELECT settlement_state,unknown_usage,slots_released,held_json FROM budget_reservations "
                "WHERE operation_id LIKE 'decision_%'").fetchall())
    agent._fixture_on_request = during_provider_request
    assert execute(agent, "prequarantined fixture request")["final_response"] == "recorded answer"
    assert not calls and admission.remote_unknown(key)
    assert len(inspected) == 1 and inspected[0]["settlement_state"] == "settled"
    assert inspected[0]["unknown_usage"] == 0 and inspected[0]["slots_released"] == 1
    assert not any(json.loads(inspected[0]["held_json"]).values())
