"""Offline measurements never become mission completion or policy authority."""
import json
import subprocess
import sys

import pytest

from evals.mission_policy_comparison import ObservationError, compare_mission_policies


def observation(policy, **updates):
    return {"pair_id": "pair-one", "task_id": "two-linked-files", "policy": policy,
        "task_digest": "a" * 64, "acceptance_digest": "b" * 64, "provenance": "observed",
        "deterministic_passed": True, "agent_elapsed_ms": 200, "provider_cost_micros": 10,
        "cost_basis": "metered_estimate", "user_review_time_ms": 100,
        "review_observation": "measured_active", **updates}


def compare(*rows):
    return compare_mission_policies({"schema_version": 1, "observations": list(rows)})


def test_matched_metrics_include_actual_review_and_distinguish_cost_estimate():
    result = compare(observation("direct"), observation("reviewed", agent_elapsed_ms=250,
        provider_cost_micros=12, user_review_time_ms=75))
    pair = result["pairs"][0]
    assert (pair["agent_elapsed_delta_ms"], pair["provider_cost_delta_micros"], pair["user_review_delta_ms"]) == (50, 2, -25)
    assert pair["cost_basis"] == "metered_estimate"
    assert result["user_review_delta_total_ms"] == -25
    assert result["promotion_recommendation"] == "none_automatic"
    assert result["statistical_generalization"] == "not_established"
    assert not result["execution_performed"] and not result["completion_authority"]


@pytest.mark.parametrize("changes", [
    {"provenance": "synthetic"},
    {"user_review_time_ms": None, "review_observation": "unavailable"},
    {"review_observation": "reported"},
    {"deterministic_passed": False},
])
def test_missing_synthetic_reported_or_failed_evidence_cannot_establish_user_review_benefit(changes):
    result = compare(observation("direct", **changes), observation("reviewed", **changes))
    assert result["observed_review_pairs"] == 0
    assert result["user_review_delta_total_ms"] is None
    assert result["comparison_status"] == "observed_review_evidence_missing"


@pytest.mark.parametrize("field,value", [("task_digest", "c" * 64), ("acceptance_digest", "c" * 64),
                                        ("task_id", "different"), ("provenance", "synthetic")])
def test_different_tasks_or_provenance_are_not_matched(field, value):
    with pytest.raises(ObservationError, match="share exact"):
        compare(observation("direct"), observation("reviewed", **{field: value}))


def test_duplicate_missing_and_incomparable_measurements_remain_explicit():
    with pytest.raises(ObservationError, match="duplicate"):
        compare(observation("direct"), observation("direct"))
    assert compare(observation("direct"))["unmatched_pair_ids"] == ["pair-one"]
    result = compare(observation("direct"), observation("reviewed", cost_basis="provider_receipt",
                     review_observation="reported"))
    assert result["pairs"][0]["provider_cost_delta_micros"] is None
    assert result["pairs"][0]["user_review_delta_ms"] is None
    with pytest.raises(ObservationError):
        compare(observation("direct", agent_elapsed_ms=True))


def test_real_cli_reads_local_fixture_without_running_agent(tmp_path):
    path = tmp_path / "measurements.json"
    path.write_text(json.dumps({"schema_version": 1, "observations": [
        observation("direct", provenance="synthetic"), observation("reviewed", provenance="synthetic")]}))
    result = subprocess.run([sys.executable, "-m", "evals.mission_policy_comparison", str(path)],
                            check=True, text=True, capture_output=True, timeout=10)
    report = json.loads(result.stdout)
    assert report["matched_pairs"] == 1 and report["observed_review_pairs"] == 0
    assert report["execution_performed"] is False
