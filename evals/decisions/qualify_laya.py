"""Offline L07/L09 inspection of explicitly saved, redacted DP16 evidence.

    python -m evals.decisions.qualify_laya evidence.json --readiness \
        docs/build/laya-integration-readiness.json --output /tmp/laya-report.json

This module never loads credentials, imports a provider, or contacts a service.
Input must follow the closed schema demonstrated by laya_qualification_fixtures.json.
Episode/tool identifiers are SHA-256 digests, not raw text. Task-arm latency excludes
classifier time; task-arm cost includes all its billed input/output/cache/recovery.
Classifier batch usage is added exactly once, never once per question. Cache counters
partition task input tokens and are not additional tokens. Unknown usage stays unknown.
All bounds assume independent, representative episodes and predeclared hard bounds;
assertions in a saved file are not proof of those assumptions or deployment authority.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path
import re
from statistics import NormalDist
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.decisions.contracts import DecisionError, digest, number, require, sha256
from agent.decisions.planner_evaluation import evaluate_planner_pairs
from agent.decisions.release_gates import metric_gates

MAX_BYTES = 16 * 1024 * 1024
MAX_EPISODES = 10000
MIN_SAMPLES = 30
# Bonferroni over at most 32 reported statistical assertions. This is not model confidence.
ALPHA = .05 / 32
TOKEN_FIELDS = ("prompt_tokens", "output_tokens", "schema_tokens", "bridge_tokens", "recovery_tokens",
                "cache_hit_tokens", "cache_miss_tokens")
INPUT_FIELDS = ("prompt_tokens", "schema_tokens", "bridge_tokens", "recovery_tokens")
ARM_METRICS = (*TOKEN_FIELDS, "total_cost", "first_response_ms", "total_latency_ms")
SOURCES = {"human", "independent_teacher", "outcome", "synthetic_fixture"}
FALLBACKS = {"none", "abstain", "deadline", "queue_full", "transport_error", "unauthorized",
             "malformed", "receipt_error", "canceled", "superseded", "other"}
SCENARIOS = {"local_fixture", "warm", "cold", "loaded", "outage", "restart_without_login", "login_recovery"}
AUDITS = {"safety_violations", "budget_violations", "permission_leaks", "cache_prefix_mutations"}
EXTERNAL_GATES = ("actual_host_auth_and_route_acceptance", "loaded_checkpoint_tokenizer_export_identity",
                  "artifact_provenance_and_license", "cold_boot_without_login_and_recovery",
                  "representative_hardware_load_power_and_outage", "retention_and_key_custody_audit",
                  "private_data_consent_if_applicable", "independent_frozen_holdout_verification",
                  "exact_release_operator_approval")


def _object(value, keys, code):
    require(type(value) is dict and set(value) == set(keys), code)


def _integer(value, low=0, high=10**12, code="invalid_evidence_integer"):
    require(type(value) is int and low <= value <= high, code)
    return value


def _boolean(value):
    require(type(value) is bool, "invalid_evidence_boolean")


def _ids(values, maximum=MAX_EPISODES):
    require(type(values) is list and len(values) <= maximum, "invalid_episode_or_tool_ids")
    for value in values:
        sha256(value)
    require(len(values) == len(set(values)), "duplicate_episode_or_tool_id")


def _optional_number(value, maximum, *, integer=False):
    if value is not None:
        if integer:
            _integer(value, high=maximum)
        else:
            number(value, 0, maximum, "invalid_evidence_metric")


def _attestation(value):
    _object(value, {"source", "independent", "evidence_digest"}, "invalid_label_attestation")
    require(type(value["source"]) is str and value["source"] in SOURCES, "invalid_label_source")
    _boolean(value["independent"])
    sha256(value["evidence_digest"])


def _arm(arm, bounds):
    _object(arm, {*ARM_METRICS, "task_success", "outcome"}, "invalid_qualification_arm")
    _boolean(arm["task_success"])
    _attestation(arm["outcome"])
    for field in TOKEN_FIELDS:
        _optional_number(arm[field], bounds["tokens_per_arm"], integer=True)
    _optional_number(arm["total_cost"], bounds["cost_per_arm"])
    for field in ("first_response_ms", "total_latency_ms"):
        number(arm[field], 0, bounds["latency_ms_per_arm"], "invalid_evidence_latency")
    require(0 < arm["first_response_ms"] <= arm["total_latency_ms"], "invalid_evidence_latency")
    if all(arm[field] is not None for field in TOKEN_FIELDS):
        inputs = sum(arm[field] for field in INPUT_FIELDS)
        require(inputs == arm["cache_hit_tokens"] + arm["cache_miss_tokens"], "invalid_cache_partition")
        require(inputs + arm["output_tokens"] <= bounds["tokens_per_arm"], "token_bound_exceeded")


def _plan(plan, bounds, seen_batches):
    _object(plan, {"whole_plan_ms", "context_ms", "receipt_ms", "admission_ms", "completion", "fallback",
                   "queue_rejected", "scenario", "stages"}, "invalid_plan_observation")
    for field in ("whole_plan_ms", "context_ms", "receipt_ms", "admission_ms"):
        number(plan[field], 0, bounds["latency_ms_per_arm"], "invalid_evidence_latency")
    require(type(plan["completion"]) is str and plan["completion"] in {"completed", "failed", "unknown"},
            "invalid_completion_state")
    require(type(plan["fallback"]) is str and plan["fallback"] in FALLBACKS, "invalid_fallback_class")
    _boolean(plan["queue_rejected"])
    require(type(plan["scenario"]) is str and plan["scenario"] in SCENARIOS, "invalid_measurement_scenario")
    require(type(plan["stages"]) is list and len(plan["stages"]) <= 3, "invalid_plan_stages")
    for index, stage in enumerate(plan["stages"], 1):
        _object(stage, {"stage", "batch_id", "questions", "wall_ms", "queue_ms", "network_ms", "usage"},
                "invalid_stage_observation")
        _integer(stage["stage"], 1, 3)
        require(stage["stage"] == index, "noncausal_stage_order")
        sha256(stage["batch_id"])
        require(stage["batch_id"] not in seen_batches, "duplicate_batch_usage")
        seen_batches.add(stage["batch_id"])
        _integer(stage["questions"], 1, 64)
        for field in ("wall_ms", "queue_ms", "network_ms"):
            number(stage[field], 0, bounds["latency_ms_per_arm"], "invalid_stage_latency")
        require(stage["queue_ms"] + stage["network_ms"] <= stage["wall_ms"], "stage_latency_double_count")
        if stage["usage"] is not None:
            _object(stage["usage"], {"input_tokens", "output_tokens", "cost"}, "invalid_batch_usage")
            _optional_number(stage["usage"]["input_tokens"], bounds["tokens_per_arm"], integer=True)
            _optional_number(stage["usage"]["output_tokens"], bounds["tokens_per_arm"], integer=True)
            _optional_number(stage["usage"]["cost"], bounds["cost_per_arm"])
    accounted = sum(plan[key] for key in ("context_ms", "receipt_ms", "admission_ms"))
    accounted += sum(stage["wall_ms"] for stage in plan["stages"])
    require(accounted <= plan["whole_plan_ms"] + 1e-9, "incomplete_whole_plan_accounting")
    require(sum(stage["questions"] for stage in plan["stages"]) <= 62, "plan_question_cap_exceeded")
    if plan["completion"] == "completed" and plan["fallback"] == "none":
        require(bool(plan["stages"]), "completed_decision_requires_batch")
    if plan["completion"] != "completed":
        require(plan["fallback"] != "none", "incomplete_plan_without_fallback")


def _classifier(plan, metric):
    values = [stage["usage"][metric] if stage["usage"] is not None else None for stage in plan["stages"]]
    return None if any(value is None for value in values) else math.fsum(values)


def _task_tokens(arm):
    values = [arm[field] for field in (*INPUT_FIELDS, "output_tokens")]
    return None if any(value is None for value in values) else sum(values)


def _totals(row, arm):
    result = {"tokens": _task_tokens(row[arm]), "cost": row[arm]["total_cost"],
              "first_response_ms": row[arm]["first_response_ms"], "total_latency_ms": row[arm]["total_latency_ms"]}
    if arm == "candidate":
        classifier = [_classifier(row["plan"], key) for key in ("input_tokens", "output_tokens")]
        result["tokens"] = (result["tokens"] + sum(classifier) if result["tokens"] is not None
                            and all(value is not None for value in classifier) else None)
        cost = _classifier(row["plan"], "cost")
        result["cost"] = result["cost"] + cost if result["cost"] is not None and cost is not None else None
        for key in ("first_response_ms", "total_latency_ms"):
            result[key] += row["plan"]["whole_plan_ms"]
    return result


def validate_evidence(data):
    """Reject malformed records, split leakage and accounting ambiguity before analysis."""
    _object(data, {"schema_version", "redacted", "provenance", "candidate_sha", "model_digest", "contract_digest",
                   "calibration_digest", "environment", "units", "accounting", "bounds", "splits", "pairs",
                   "shadow", "audits"}, "invalid_qualification_evidence")
    _integer(data["schema_version"], 1, 1)
    require(data["redacted"] is True, "redacted_evidence_required")
    require(type(data["provenance"]) is str and data["provenance"] in {"synthetic_fixture", "real_candidate"},
            "invalid_evidence_provenance")
    require(type(data["candidate_sha"]) is str and re.fullmatch(r"[a-f0-9]{40}", data["candidate_sha"]),
            "invalid_candidate_sha")
    for key in ("model_digest", "contract_digest", "calibration_digest"):
        sha256(data[key])
    require(type(data["environment"]) is str and data["environment"] in
            {"local_fixture", "actual_ryoko_host", "unverified"}, "invalid_measurement_environment")
    require(data["units"] == {"latency": "ms", "tokens": "token", "cost": "USD"}, "explicit_units_required")
    require(data["accounting"] == {"task_latency": "excludes_classifier", "task_tokens": "disjoint_components",
                                   "cache_tokens": "input_partition", "task_cost": "all_task_billing"},
            "explicit_accounting_required")
    bounds = data["bounds"]
    _object(bounds, {"predeclared", "tokens_per_arm", "cost_per_arm", "latency_ms_per_arm"}, "invalid_evidence_bounds")
    _boolean(bounds["predeclared"])
    _integer(bounds["tokens_per_arm"], 1, 10**9)
    number(bounds["cost_per_arm"], 1e-12, 10**9, "invalid_evidence_bound")
    number(bounds["latency_ms_per_arm"], 1e-12, 10**9, "invalid_evidence_bound")
    splits = data["splits"]
    _object(splits, {"frozen", "thresholds_frozen", "training", "calibration", "holdout", "shadow"}, "invalid_splits")
    _boolean(splits["frozen"])
    _boolean(splits["thresholds_frozen"])
    seen = set()
    for key in ("training", "calibration", "holdout", "shadow"):
        _ids(splits[key])
        require(not seen.intersection(splits[key]), "episode_split_leakage")
        seen.update(splits[key])
    require(type(data["pairs"]) is list and len(data["pairs"]) <= MAX_EPISODES, "invalid_paired_episodes")
    require(type(data["shadow"]) is list and len(data["shadow"]) <= MAX_EPISODES, "invalid_shadow_episodes")
    seen_episodes, seen_batches = set(), set()
    for row in data["pairs"]:
        _object(row, {"episode_id", "labels", "needed_tools", "planned_tools", "need", "prediction_correct",
                      "prediction_confidence", "mode", "actual_misses", "recovered_misses", "recovery_evidence_digest",
                      "baseline", "candidate", "plan"}, "invalid_paired_episode")
        sha256(row["episode_id"])
        require(row["episode_id"] not in seen_episodes, "duplicate_episode")
        seen_episodes.add(row["episode_id"])
        require(row["episode_id"] in splits["holdout"], "episode_not_in_holdout")
        _attestation(row["labels"])
        _boolean(row["prediction_correct"])
        number(row["prediction_confidence"], 0, 1, "invalid_prediction_confidence")
        require(type(row["need"]) is str and row["need"] in {"no_tools", "needs_tools", "defer", "unclear"},
                "invalid_tool_need")
        require(type(row["mode"]) is str and row["mode"] in {"shadow", "reduced_bundle"}, "invalid_pair_mode")
        for key in ("needed_tools", "planned_tools", "actual_misses", "recovered_misses"):
            _ids(row[key], maximum=32)
        require(len(row["planned_tools"]) <= 12, "selected_tool_cap_exceeded")
        if row["need"] == "no_tools":
            require(not row["planned_tools"], "contradictory_no_tools_plan")
        require(set(row["recovered_misses"]) <= set(row["actual_misses"]), "recovery_without_actual_miss")
        require(set(row["actual_misses"]) <= set(row["needed_tools"]) - set(row["planned_tools"]), "invalid_actual_miss")
        if row["mode"] == "shadow":
            require(not row["actual_misses"] and not row["recovered_misses"]
                    and row["recovery_evidence_digest"] is None, "shadow_is_not_actual_recovery")
        if row["actual_misses"]:
            sha256(row["recovery_evidence_digest"])
        elif row["recovery_evidence_digest"] is not None:
            sha256(row["recovery_evidence_digest"])
        _plan(row["plan"], bounds, seen_batches)
        for arm in ("baseline", "candidate"):
            _arm(row[arm], bounds)
            totals = _totals(row, arm)
            for key, cap in (("tokens", "tokens_per_arm"), ("cost", "cost_per_arm"),
                             ("first_response_ms", "latency_ms_per_arm"), ("total_latency_ms", "latency_ms_per_arm")):
                require(totals[key] is None or totals[key] <= bounds[cap], "combined_arm_bound_exceeded")
    require(seen_episodes == set(splits["holdout"]), "missing_holdout_episode")
    for row in data["shadow"]:
        _object(row, {"episode_id", "plan"}, "invalid_shadow_episode")
        sha256(row["episode_id"])
        require(row["episode_id"] not in seen_episodes, "duplicate_episode")
        seen_episodes.add(row["episode_id"])
        require(row["episode_id"] in splits["shadow"], "episode_not_in_shadow_split")
        _plan(row["plan"], bounds, seen_batches)
    require(len(data["shadow"]) == len(splits["shadow"]), "missing_shadow_episode")
    if data["audits"] is not None:
        _object(data["audits"], {*AUDITS, "evidence_digest"}, "invalid_boundary_audit")
        sha256(data["audits"]["evidence_digest"])
        for key in AUDITS:
            _integer(data["audits"][key])
    return data


def wilson(successes, count):
    """Two-sided Bonferroni-adjusted Wilson interval on independent episodes."""
    _integer(count)
    _integer(successes, 0, count)
    if count == 0:
        return {"estimate": None, "lower": None, "upper": None, "n": 0, "method": "wilson"}
    p = successes / count
    z = NormalDist().inv_cdf(1 - ALPHA / 2)
    denominator = 1 + z * z / count
    center = (p + z * z / (2 * count)) / denominator
    radius = z * math.sqrt(p * (1 - p) / count + z * z / (4 * count * count)) / denominator
    return {"estimate": p, "lower": max(0., center - radius) if count >= MIN_SAMPLES else None,
            "upper": min(1., center + radius) if count >= MIN_SAMPLES else None, "n": count, "method": "wilson"}


def bounded_mean(values, low, high, *, alpha=ALPHA):
    """Distribution-free Hoeffding interval; retains uncertainty for constant samples."""
    number(low, -1e12, 1e12, "invalid_statistical_bound")
    number(high, low, 1e12, "invalid_statistical_bound")
    number(alpha, 1e-12, 1 - 1e-12, "invalid_statistical_alpha")
    require(type(values) is list and len(values) <= MAX_EPISODES, "invalid_statistical_samples")
    for value in values:
        number(value, low, high, "invalid_statistical_sample")
    count = len(values)
    estimate = math.fsum(values) / count if count else None
    radius = (high - low) * math.sqrt(math.log(2 / alpha) / (2 * count)) if count >= MIN_SAMPLES else None
    return {"estimate": estimate, "lower": max(low, estimate - radius) if radius is not None else None,
            "upper": min(high, estimate + radius) if radius is not None else None,
            "n": count, "method": "bounded_episode_hoeffding"}


def _count_ratio(numerators, denominators):
    """Micro tool recall/recovery without pretending tools within an episode are IID."""
    top, bottom = bounded_mean(numerators, 0, 32, alpha=ALPHA / 2), bounded_mean(denominators, 0, 32, alpha=ALPHA / 2)
    total = sum(denominators)
    lower = top["lower"] / bottom["upper"] if total and top["lower"] is not None and bottom["upper"] else None
    upper = min(1., top["upper"] / bottom["lower"]) if total and bottom["lower"] else (1. if lower is not None else None)
    return {"estimate": sum(numerators) / total if total else None, "lower": lower, "upper": upper,
            "n": len(numerators), "denominator_events": total, "method": "bounded_episode_count_ratio"}


def _latency_ratio(pairs, metric, cap):
    # Paired difference is primary. Dividing its bound by the bounded positive
    # baseline mean produces a ratio of means, never a mean of noisy ratios.
    baseline = [_totals(row, "baseline")[metric] for row in pairs]
    differences = [_totals(row, "candidate")[metric] - value for row, value in zip(pairs, baseline)]
    delta = bounded_mean(differences, -cap, cap, alpha=ALPHA / 2)
    base = bounded_mean(baseline, 0, cap, alpha=ALPHA / 2)
    estimate = 1 + delta["estimate"] / base["estimate"] if baseline else None
    upper = None
    if delta["upper"] is not None and base["lower"] > 0:
        upper = max(0., 1 + delta["upper"] / (base["lower"] if delta["upper"] >= 0 else base["upper"]))
    return {"estimate": estimate, "upper": upper, "n": len(pairs), "method": "paired_bounded_mean_ratio"}


def _percentile(values, fraction):
    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)] if values else None


def _timings(values, cap):
    count = len(values)
    upper = None
    if count >= MIN_SAMPLES:
        quantile = .95 + math.sqrt(math.log(2 / ALPHA) / (2 * count))
        upper = _percentile(values, quantile) if quantile <= 1 else cap
    return {"n": count, "p50": _percentile(values, .5), "p95": _percentile(values, .95),
            "p99": _percentile(values, .99), "maximum": max(values) if values else None,
            "p95_upper_bound": upper, "bound_method": "dkw_with_predeclared_cap", "units": "ms"}


def _ece(pairs):
    """One-sided bound on population ECE for ten bins fixed before evaluation.

    Let Z_i[b] = (correct_i - confidence_i) * 1[bin_i == b]. Empirical
    ECE is ||mean(Z_i)||_1. Jensen gives population ECE <= E[empirical ECE].
    Replacing one independent episode changes this statistic by at most 2/n,
    since ||Z_i||_1 <= 1. McDiarmid's lower tail therefore bounds population
    ECE by empirical ECE + sqrt(2*log(1/alpha)/n), with probability >= 1-alpha.
    This needs a frozen predictor/acceptance rule and representative independent
    episodes. It bounds this fixed-bin population quantity, not unbinned error.
    """
    if not pairs:
        return {"estimate": None, "upper": None, "n": 0, "method": "ten_fixed_bins_mcdiarmid"}
    bins = [[] for _ in range(10)]
    for row in pairs:
        bins[min(9, int(row["prediction_confidence"] * 10))].append(row)
    estimate = sum(abs(sum(row["prediction_correct"] - row["prediction_confidence"] for row in bucket))
                   for bucket in bins) / len(pairs)
    radius = math.sqrt(2 * math.log(1 / ALPHA) / len(pairs))
    return {"estimate": estimate, "upper": min(1., estimate + radius) if len(pairs) >= MIN_SAMPLES else None,
            "n": len(pairs), "method": "ten_fixed_bins_mcdiarmid"}


def _descriptive(data):
    rows = []
    for row in data["pairs"]:
        if row["plan"]["completion"] == "unknown":
            continue
        arms = {}
        for name in ("baseline", "candidate"):
            source, total = row[name], _totals(row, name)
            if any(value is None for value in (*total.values(), *(source[key] for key in TOKEN_FIELDS))):
                break
            arms[name] = {key: source[key] for key in (*INPUT_FIELDS, "cache_hit_tokens", "cache_miss_tokens", "task_success")}
            arms[name]["prompt_tokens"] += source["output_tokens"]
            if name == "candidate":
                arms[name]["prompt_tokens"] += int(_classifier(row["plan"], "input_tokens") + _classifier(row["plan"], "output_tokens"))
            arms[name].update(total_cost=total["cost"], first_response_ms=total["first_response_ms"],
                              total_latency_ms=total["total_latency_ms"])
        if len(arms) == 2:
            rows.append({"trial_id": row["episode_id"], "needed_tools": row["needed_tools"],
                         "planned_tools": row["planned_tools"], "need": row["need"],
                         "planner_misses": row["actual_misses"], "recovered_misses": row["recovered_misses"], **arms})
    if not rows:
        return None
    report = evaluate_planner_pairs(rows, provenance=data["provenance"],
        **{key: data[key] for key in ("model_digest", "contract_digest", "calibration_digest")})
    report["accounting_note"] = "Compatibility prompt_tokens includes task output and classifier input/output; not an input-only measure"
    censored = sum(row["plan"]["completion"] == "unknown" for row in data["pairs"])
    report["excluded_unknown_usage_pairs"] = len(data["pairs"]) - len(rows) - censored
    report["excluded_unknown_completion_pairs"] = censored
    return report


def _readiness_failures(readiness):
    if readiness is None:
        return ["readiness_not_supplied"]
    require(type(readiness) is dict and type(readiness.get("gates")) is list, "invalid_readiness")
    failures = []
    seen = set()
    for gate in readiness["gates"]:
        require(type(gate) is dict and type(gate.get("id")) is str
                and re.fullmatch(r"[a-z0-9_]{1,96}", gate["id"]), "invalid_readiness_gate")
        require(gate["id"] not in seen, "duplicate_readiness_gate")
        seen.add(gate["id"])
        require(type(gate.get("status")) is str and gate["status"] in
                {"blocked", "unmeasured", "pending", "failed", "complete", "passed"}, "invalid_readiness_status")
        if gate["status"] not in {"complete", "passed"}:
            failures.append("readiness:" + gate["id"] + ":" + gate["status"])
    return failures


def inspect_evidence(data, *, readiness=None):
    """Return descriptive measurements and every unmet gate, never a release grant."""
    validate_evidence(data)
    pairs, cap = data["pairs"], data["bounds"]["latency_ms_per_arm"]
    plans = [row["plan"] for row in (*pairs, *data["shadow"])]
    stages = [stage for plan in plans for stage in plan["stages"]]
    accepted = [row for row in pairs if row["need"] in {"no_tools", "needs_tools"}
                and row["plan"]["completion"] == "completed" and row["plan"]["fallback"] == "none"]
    no_tools = [row for row in accepted if row["need"] == "no_tools"]
    reduced = [row for row in pairs if row["mode"] == "reduced_bundle"]
    bounds = {
        "heldout_precision": wilson(sum(row["prediction_correct"] for row in accepted), len(accepted)),
        "no_tools_precision": wilson(sum(not row["needed_tools"] for row in no_tools), len(no_tools)),
        "needed_tool_recall": _count_ratio([len(set(row["needed_tools"]) & set(row["planned_tools"])) for row in pairs],
                                           [len(row["needed_tools"]) for row in pairs]),
        "recovery_success": _count_ratio([len(row["recovered_misses"]) for row in reduced],
                                         [len(row["actual_misses"]) for row in reduced]),
        "paired_success_delta": bounded_mean([int(row["candidate"]["task_success"]) - int(row["baseline"]["task_success"])
                                               for row in reduced], -1, 1),
        "calibration_ece": _ece(accepted),
        "first_response_latency_ratio": _latency_ratio(reduced, "first_response_ms", cap),
        "total_latency_ratio": _latency_ratio(reduced, "total_latency_ms", cap),
    }
    for metric, limit in (("token", "tokens_per_arm"), ("cost", "cost_per_arm")):
        key = "tokens" if metric == "token" else "cost"
        values = [(_totals(row, "baseline")[key], _totals(row, "candidate")[key]) for row in reduced]
        complete = all(left is not None and right is not None for left, right in values)
        bounds["net_" + metric + "_savings"] = bounded_mean([left - right for left, right in values] if complete else [],
                                                           -data["bounds"][limit], data["bounds"][limit])
    if not data["bounds"]["predeclared"]:
        for bound in bounds.values():
            if bound["method"] not in {"wilson", "ten_fixed_bins_mcdiarmid"}:
                for side in ("lower", "upper"):
                    if side in bound:
                        bound[side] = None
    unknown = sum(plan["completion"] == "unknown" for plan in plans)
    if any(row["plan"]["completion"] == "unknown" for row in reduced):
        for key in ("net_token_savings", "net_cost_savings", "first_response_latency_ratio", "total_latency_ratio"):
            bounds[key]["upper"] = None
            if "lower" in bounds[key]:
                bounds[key]["lower"] = None
    timing = _timings([plan["whole_plan_ms"] for plan in plans], cap)
    if not data["bounds"]["predeclared"]:
        timing["p95_upper_bound"] = None
    timing["measurement"] = "caller_observed_including_censored" if unknown else "whole_completed_operation"
    metrics = {key + "_lower_bound": bounds[key]["lower"] for key in
               ("heldout_precision", "no_tools_precision", "needed_tool_recall", "recovery_success", "paired_success_delta",
                "net_token_savings", "net_cost_savings")}
    metrics.update({key + "_upper_bound": bounds[key]["upper"] for key in
                    ("first_response_latency_ratio", "total_latency_ratio")})
    metrics.update(calibration_ece=bounds["calibration_ece"]["upper"], shadow_receipts=len(data["shadow"]),
                   p95_end_to_end_latency_ms=timing["p95_upper_bound"] if not unknown else None)
    if data["audits"] is not None:
        metrics.update({key: data["audits"][key] for key in AUDITS})
    reasons = _readiness_failures(readiness)
    if data["provenance"] != "real_candidate":
        reasons.append("synthetic_is_not_production_evidence")
    if data["environment"] != "actual_ryoko_host":
        reasons.append("actual_host_measurements_missing")
    if not data["bounds"]["predeclared"]:
        reasons.append("predeclared_population_bounds_required")
    if not data["splits"]["frozen"] or not data["splits"]["thresholds_frozen"]:
        reasons.append("frozen_splits_and_thresholds_required")
    if not data["splits"]["training"] or not data["splits"]["calibration"]:
        reasons.append("complete_training_and_calibration_provenance_required")
    if any(not item["independent"] or item["source"] == "synthetic_fixture"
           for row in pairs for item in (row["labels"], row["baseline"]["outcome"], row["candidate"]["outcome"])):
        reasons.append("independent_labels_and_outcomes_required")
    if len(reduced) != len(pairs):
        reasons.append("shadow_pairs_are_not_reduced_bundle_outcomes")
    if any(set(row["actual_misses"]) != set(row["needed_tools"]) - set(row["planned_tools"]) for row in reduced):
        reasons.append("unobserved_reduced_bundle_omissions_require_recovery_evidence")
    if unknown:
        reasons.append("unknown_completion_prevents_latency_and_cost_qualification")
    unknown_usage = sum(stage["usage"] is None or any(value is None for value in stage["usage"].values()) for stage in stages)
    if unknown_usage or any(value is None for row in pairs for arm in ("baseline", "candidate")
                            for value in (row[arm]["total_cost"], *(row[arm][key] for key in TOKEN_FIELDS))):
        reasons.append("unknown_usage_prevents_net_benefit_qualification")
    gate_checks = []
    for gate in metric_gates("DP16"):
        value = metrics.get(gate.name)
        status = "missing" if value is None else ("pass" if gate.accepts(value) else "failed")
        gate_checks.append({"name": gate.name, "value": value, "minimum": gate.minimum, "maximum": gate.maximum, "status": status})
        if status != "pass":
            reasons.append(status + "_metric:" + gate.name)
    reasons.extend("external_verification_required:" + key for key in EXTERNAL_GATES)
    usage = {}
    for key in ("input_tokens", "output_tokens", "cost"):
        values = [_classifier(plan, key) for plan in plans]
        usage[key] = {"known_subtotal": math.fsum(value for value in values if value is not None),
                      "total": math.fsum(values) if all(value is not None for value in values) else None,
                      "unknown_plans": sum(value is None for value in values)}
    return {"schema_version": 1, "purpose": "offline_laya_evidence_inspection", "provenance": data["provenance"],
            "evidence_digest": digest(data), "candidate_sha": data["candidate_sha"], "units": data["units"],
            "measurement_environment": data["environment"], "target_hardware": "unverified_by_offline_inspector",
            "qualifies_production": False, "L07_complete": False, "L09_release_qualified": False, "L10_authorized": False,
            "statistics": {"confidence_level": .95, "per_assertion_alpha": ALPHA, "simultaneous_assertion_budget": 32,
                           "minimum_independent_episodes": MIN_SAMPLES,
                           "assumptions": ["Independent representative episodes; within-episode tools are not independent trials",
                                           "Predeclared finite population bounds, not maxima selected after viewing results",
                                           "Wilson binomial intervals are approximate; Hoeffding, DKW and McDiarmid bounds are conservative",
                                           "ECE uses ten bins fixed before evaluation, a frozen predictor/acceptance rule and independent representative accepted episodes",
                                           "The ECE bound targets fixed-bin population ECE, not unbinned calibration error",
                                           "Saved attestations and numeric passes require independent external verification",
                                           "No model confidence is treated as a statistical confidence interval",
                                           "Shadow elapsed times can be censored; unknown completion blocks end-to-end latency qualification",
                                           "Savings units are absolute tokens per episode and USD per episode; latency ratios compare means"]},
            "pair_count": len(pairs), "accepted_prediction_count": len(accepted), "reduced_bundle_pair_count": len(reduced), "shadow_receipts": len(data["shadow"]),
            "bounds": bounds, "release_metrics": metrics, "gate_checks": gate_checks, "blocking_reasons": reasons,
            "observations": {"plans": len(plans), "batches": len(stages), "questions": sum(stage["questions"] for stage in stages),
                             "unknown_completion": unknown, "unknown_usage_batches": unknown_usage,
                             "unknown_completion_rate": unknown / len(plans) if plans else None,
                             "fallback_rate": sum(plan["fallback"] != "none" for plan in plans) / len(plans) if plans else None,
                             "queue_rejection_rate": sum(plan["queue_rejected"] for plan in plans) / len(plans) if plans else None,
                             "fallback_counts": dict(sorted(Counter(plan["fallback"] for plan in plans).items())),
                             "queue_rejections": sum(plan["queue_rejected"] for plan in plans),
                             "whole_plan": timing,
                             "scenario_whole_plan": {scenario: _timings([plan["whole_plan_ms"] for plan in plans
                                                                         if plan["scenario"] == scenario], cap)
                                                     for scenario in sorted({plan["scenario"] for plan in plans})},
                             "stage_wall": {str(index): _timings([stage["wall_ms"] for stage in stages
                                                                                       if stage["stage"] == index], cap)
                                                                     for index in (1, 2, 3)},
                             "queue_wait": _timings([plan["admission_ms"] + sum(stage["queue_ms"] for stage in plan["stages"])
                                                     for plan in plans], cap),
                             "network": _timings([sum(stage["network_ms"] for stage in plan["stages"]) for plan in plans], cap),
                             "classifier_usage": usage},
            "descriptive_paired_report": _descriptive(data)}


def load_redacted_json(path):
    """Bounded local input, rejecting duplicate keys and non-standard numeric tokens."""
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "duplicate_json_key")
            result[key] = value
        return result
    def constant(_):
        raise DecisionError("nonfinite_json_number")
    try:
        with Path(path).open("rb") as source:
            raw = source.read(MAX_BYTES + 1)
        require(len(raw) <= MAX_BYTES, "qualification_input_too_large")
        return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)
    except DecisionError:
        raise
    except (OSError, UnicodeError, ValueError, RecursionError):
        raise DecisionError("invalid_local_evidence_file") from None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path)
    parser.add_argument("--readiness", type=Path)
    parser.add_argument("--output", type=Path, help="Write a local report; existing files are never replaced")
    args = parser.parse_args(argv)
    try:
        report = inspect_evidence(load_redacted_json(args.evidence),
                                  readiness=load_redacted_json(args.readiness) if args.readiness else None)
        rendered = json.dumps(report, sort_keys=True, indent=2, allow_nan=False) + "\n"
        if args.output:
            require(args.output.resolve() not in {args.evidence.resolve(), args.readiness.resolve() if args.readiness else None},
                    "output_must_not_replace_evidence")
            with args.output.open("x", encoding="utf-8") as target:
                target.write(rendered)
        else:
            print(rendered, end="")
    except (DecisionError, OSError) as error:
        print(json.dumps({"error": error.code if isinstance(error, DecisionError) else "report_write_failed",
                          "qualifies_production": False}), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
