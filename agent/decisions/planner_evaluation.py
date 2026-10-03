"""Offline paired DP16 measurements, including bridge, cache and recovery costs.

No model is called and no gate is promoted. Finite fixtures test accounting;
production confidence bounds and release proof require a real frozen candidate.
"""
from __future__ import annotations

from math import fsum

from agent.decisions.contracts import digest, number, require, sha256

_FIELDS = {"trial_id", "needed_tools", "planned_tools", "need", "planner_misses", "recovered_misses",
           "baseline", "candidate"}
_ARM_FIELDS = {"task_success", "prompt_tokens", "schema_tokens", "bridge_tokens", "recovery_tokens",
               "cache_hit_tokens", "cache_miss_tokens", "total_cost", "first_response_ms", "total_latency_ms"}
_TOKEN_FIELDS = {"prompt_tokens", "schema_tokens", "bridge_tokens", "recovery_tokens", "cache_hit_tokens", "cache_miss_tokens"}


def evaluate_planner_pairs(rows, *, provenance, model_digest, contract_digest, calibration_digest):
    require(provenance in {"synthetic_fixture", "real_candidate"}, "invalid_evaluation_provenance")
    for value in (model_digest, contract_digest, calibration_digest):
        sha256(value)
    require(type(rows) is list and 0 < len(rows) <= 100000, "invalid_paired_trials")
    seen = set()
    for row in rows:
        require(type(row) is dict and set(row) == _FIELDS, "invalid_paired_trial")
        require(type(row["trial_id"]) is str and 0 < len(row["trial_id"]) <= 128 and row["trial_id"] not in seen, "invalid_trial_id")
        seen.add(row["trial_id"])
        require(row["need"] in {"no_tools", "needs_tools", "defer", "unclear"}, "invalid_tool_need")
        for key in ("needed_tools", "planned_tools", "planner_misses", "recovered_misses"):
            values = row[key]
            require(type(values) is list and all(type(value) is str and 0 < len(value) <= 96 for value in values)
                    and len(values) == len(set(values)), "invalid_trial_tools")
        require(set(row["recovered_misses"]) <= set(row["planner_misses"]), "recovery_without_miss")
        require(set(row["planner_misses"]) <= set(row["needed_tools"]) - set(row["planned_tools"]), "invalid_planner_miss")
        for key in ("baseline", "candidate"):
            arm = row[key]
            require(type(arm) is dict and set(arm) == _ARM_FIELDS, "invalid_trial_arm")
            require(type(arm["task_success"]) is bool, "invalid_trial_success")
            for metric in _ARM_FIELDS - {"task_success"}:
                number(arm[metric], 0, 1e12, "invalid_trial_metric")
            require(all(type(arm[field]) is int for field in _TOKEN_FIELDS), "invalid_trial_tokens")
            require(arm["first_response_ms"] <= arm["total_latency_ms"], "invalid_trial_latency")
    rows = sorted(rows, key=lambda row: row["trial_id"])
    count = len(rows)
    no_tools = [row for row in rows if row["need"] == "no_tools"]
    needed = sum(len(row["needed_tools"]) for row in rows)
    recalled = sum(len(set(row["needed_tools"]) & set(row["planned_tools"])) for row in rows)
    misses = sum(len(row["planner_misses"]) for row in rows)
    recovered = sum(len(row["recovered_misses"]) for row in rows)
    def mean(arm, metric):
        return fsum(row[arm][metric] for row in rows) / count
    def all_tokens(row, arm):
        # Cache hit/miss are a billing partition, not additional prompt tokens.
        return sum(row[arm][key] for key in ("prompt_tokens", "schema_tokens", "bridge_tokens", "recovery_tokens"))
    metrics = {
        "no_tools_precision": sum(not row["needed_tools"] for row in no_tools) / len(no_tools) if no_tools else None,
        "needed_tool_recall": recalled / needed if needed else None,
        "planner_miss_rate": sum(bool(row["planner_misses"]) for row in rows) / count,
        "recovery_success": recovered / misses if misses else None,
        "paired_task_success_delta": mean("candidate", "task_success") - mean("baseline", "task_success"),
        "net_token_savings": fsum(all_tokens(row, "baseline") - all_tokens(row, "candidate") for row in rows) / count,
        "net_cost_savings": mean("baseline", "total_cost") - mean("candidate", "total_cost"),
        "first_response_delta_ms": mean("candidate", "first_response_ms") - mean("baseline", "first_response_ms"),
        "total_latency_delta_ms": mean("candidate", "total_latency_ms") - mean("baseline", "total_latency_ms"),
    }
    return {"schema_version": 1, "purpose": "descriptive_paired_planner_evaluation", "provenance": provenance,
            "trial_count": count, "trials_digest": digest(rows), "model_digest": model_digest,
            "contract_digest": contract_digest, "calibration_digest": calibration_digest,
            "metrics": metrics, "cost_components": {arm: {field: mean(arm, field) for field in sorted(_ARM_FIELDS)}
                                                     for arm in ("baseline", "candidate")},
            "qualifies_production": False,
            "qualification_pending": ["independent_labels", "frozen_disjoint_holdout", "confidence_bounds",
                                      "live_shadow", "point_release_gate", "operator_approval"]}
