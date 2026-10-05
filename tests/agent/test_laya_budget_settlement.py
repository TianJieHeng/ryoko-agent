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
