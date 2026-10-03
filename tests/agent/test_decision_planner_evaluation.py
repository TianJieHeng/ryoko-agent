"""Paired fixture accounting is descriptive and never release evidence."""
from copy import deepcopy

import pytest

from agent.decisions.contracts import DecisionError
from agent.decisions.planner_evaluation import evaluate_planner_pairs


def arm(success=True, **kwargs):
    return {"task_success": success, "prompt_tokens": 100, "schema_tokens": 100, "bridge_tokens": 0,
            "recovery_tokens": 0, "cache_hit_tokens": 100, "cache_miss_tokens": 100,
            "total_cost": .2, "first_response_ms": 100, "total_latency_ms": 500, **kwargs}


def trial():
    return {"trial_id": "synthetic-one", "needed_tools": ["read"], "planned_tools": [], "need": "no_tools",
            "planner_misses": ["read"], "recovered_misses": ["read"], "baseline": arm(),
            "candidate": arm(schema_tokens=0, bridge_tokens=30, recovery_tokens=90,
                             cache_hit_tokens=0, cache_miss_tokens=220, total_cost=.3, first_response_ms=140, total_latency_ms=650)}


def evaluate(rows):
    return evaluate_planner_pairs(rows, provenance="synthetic_fixture", model_digest="1" * 64,
                                  contract_digest="2" * 64, calibration_digest="3" * 64)


def test_bridge_recovery_and_cache_costs_can_erase_apparent_schema_savings():
    report = evaluate([trial()])
    assert report["metrics"]["net_token_savings"] == -20
    assert report["metrics"]["net_cost_savings"] == pytest.approx(-.1)
    assert report["metrics"]["no_tools_precision"] == 0
    assert report["metrics"]["needed_tool_recall"] == 0
    assert report["metrics"]["recovery_success"] == 1
    assert report["metrics"]["paired_task_success_delta"] == 0
    assert report["metrics"]["first_response_delta_ms"] == 40
    assert report["metrics"]["total_latency_delta_ms"] == 150
    assert not report["qualifies_production"]
    assert report["cost_components"]["candidate"]["cache_miss_tokens"] == 220


def test_undefined_populations_are_not_perfect_scores_and_malformed_pairs_fail():
    row = trial()
    row.update(needed_tools=[], planner_misses=[], recovered_misses=[], need="defer")
    report = evaluate([row])
    assert report["metrics"]["no_tools_precision"] is None
    assert report["metrics"]["needed_tool_recall"] is None
    assert report["metrics"]["recovery_success"] is None
    bad = deepcopy(row)
    bad["recovered_misses"] = ["secret"]
    with pytest.raises(DecisionError, match="recovery_without_miss"):
        evaluate([bad])
    with pytest.raises(DecisionError, match="invalid_trial_id"):
        evaluate([row, row])
