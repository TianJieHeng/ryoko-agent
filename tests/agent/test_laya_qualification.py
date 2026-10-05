"""Offline evidence cannot turn fixtures, partial usage or shadow misses into release proof."""
from copy import deepcopy
import json
from pathlib import Path
import socket

import pytest

from agent.decisions.contracts import DecisionError, digest
from evals.decisions.qualify_laya import (
    MAX_EPISODES, MIN_SAMPLES, _ece, bounded_mean, inspect_evidence, load_redacted_json, main, validate_evidence, wilson,
)


def arm(**changes):
    return {"task_success": True, "prompt_tokens": 100, "output_tokens": 20, "schema_tokens": 100,
            "bridge_tokens": 0, "recovery_tokens": 0, "cache_hit_tokens": 100, "cache_miss_tokens": 100,
            "total_cost": .2, "first_response_ms": 100, "total_latency_ms": 300,
            "outcome": {"source": "synthetic_fixture", "independent": True, "evidence_digest": digest("outcome")}, **changes}


def evidence(count=1):
    rows = []
    for index in range(count):
        rows.append({"episode_id": digest(["episode", index]),
            "labels": {"source": "synthetic_fixture", "independent": True, "evidence_digest": digest(["labels", index])},
            "needed_tools": [digest("read")], "planned_tools": [], "need": "no_tools",
            "prediction_correct": False, "prediction_confidence": .99, "mode": "reduced_bundle",
            "actual_misses": [digest("read")], "recovered_misses": [digest("read")],
            "recovery_evidence_digest": digest(["recovery", index]), "baseline": arm(),
            "candidate": arm(schema_tokens=0, bridge_tokens=10, recovery_tokens=20, cache_hit_tokens=0,
                             cache_miss_tokens=130, first_response_ms=80, total_latency_ms=250, total_cost=.12),
            "plan": {"whole_plan_ms": 40, "context_ms": 5, "receipt_ms": 5, "admission_ms": 0,
                     "completion": "completed", "fallback": "none", "queue_rejected": False, "scenario": "local_fixture",
                     "stages": [{"stage": 1, "batch_id": digest(["batch", index]), "questions": 4,
                                 "wall_ms": 30, "queue_ms": 5, "network_ms": 10,
                                 "usage": {"input_tokens": 60, "output_tokens": 10, "cost": .09}}]}})
    return {"schema_version": 1, "redacted": True, "provenance": "synthetic_fixture", "candidate_sha": "a" * 40,
            "model_digest": digest("model"), "contract_digest": digest("contract"), "calibration_digest": digest("calibration"),
            "environment": "local_fixture", "units": {"latency": "ms", "tokens": "token", "cost": "USD"},
            "accounting": {"task_latency": "excludes_classifier", "task_tokens": "disjoint_components",
                           "cache_tokens": "input_partition", "task_cost": "all_task_billing"},
            "bounds": {"predeclared": True, "tokens_per_arm": 500, "cost_per_arm": 1, "latency_ms_per_arm": 1000},
            "splits": {"frozen": True, "thresholds_frozen": True, "training": [digest("training")],
                       "calibration": [digest("calibration_episode")], "holdout": [row["episode_id"] for row in rows], "shadow": []},
            "pairs": rows, "shadow": [], "audits": None}


def test_classifier_batches_are_charged_once_and_recovery_cache_and_network_count():
    report = inspect_evidence(evidence())
    observation = report["observations"]
    assert observation["batches"] == 1 and observation["questions"] == 4
    assert observation["classifier_usage"]["input_tokens"]["total"] == 60
    assert observation["network"]["p50"] == 10
    assert observation["queue_wait"]["p50"] == 5
    descriptive = report["descriptive_paired_report"]
    # 220 baseline versus 150 task + 70 classifier; cache partition adds nothing.
    assert descriptive["metrics"]["net_token_savings"] == 0
    assert descriptive["metrics"]["net_cost_savings"] == pytest.approx(-.01)
    assert descriptive["metrics"]["first_response_delta_ms"] == 20
    assert descriptive["metrics"]["total_latency_delta_ms"] == -10
    assert report["bounds"]["recovery_success"]["denominator_events"] == 1
    assert not descriptive["qualifies_production"]
    assert not report["qualifies_production"]
    assert report["target_hardware"] == "unverified_by_offline_inspector"


def test_synthetic_never_qualifies_and_every_failure_is_available():
    data = evidence(MIN_SAMPLES)
    data["bounds"]["predeclared"] = False
    data["splits"]["frozen"] = False
    readiness = {"gates": [{"id": "credential_provisioning", "status": "blocked"},
                           {"id": "loaded_artifacts", "status": "unmeasured"}]}
    report = inspect_evidence(data, readiness=readiness)
    reasons = report["blocking_reasons"]
    assert "synthetic_is_not_production_evidence" in reasons
    assert "readiness:credential_provisioning:blocked" in reasons
    assert "readiness:loaded_artifacts:unmeasured" in reasons
    assert "predeclared_population_bounds_required" in reasons
    assert "frozen_splits_and_thresholds_required" in reasons
    assert "missing_metric:safety_violations" in reasons
    assert "failed_metric:no_tools_precision_lower_bound" in reasons
    assert "failed_metric:shadow_receipts" in reasons
    assert "external_verification_required:cold_boot_without_login_and_recovery" in reasons
    assert not any(report[key] for key in ("L07_complete", "L09_release_qualified", "L10_authorized"))
    assert len(report["gate_checks"]) >= 15
    assert report["bounds"]["heldout_precision"]["upper"] > 0


@pytest.mark.parametrize("change,code", [
    (lambda data: data.update(schema_version=True), "invalid_evidence_integer"),
    (lambda data: data.update(redacted=1), "redacted_evidence_required"),
    (lambda data: data["units"].pop("cost"), "explicit_units_required"),
    (lambda data: data["units"].update(cost="unitless"), "explicit_units_required"),
    (lambda data: data["bounds"].update(cost_per_arm=float("nan")), "invalid_evidence_bound"),
    (lambda data: data["bounds"].update(predeclared=1), "invalid_evidence_boolean"),
    (lambda data: data["pairs"][0]["plan"].update(whole_plan_ms=float("inf")), "invalid_evidence_latency"),
    (lambda data: data["pairs"][0]["candidate"].update(prompt_tokens=True), "invalid_evidence_integer"),
    (lambda data: data["pairs"][0]["candidate"].update(total_cost=float("nan")), "invalid_evidence_metric"),
    (lambda data: data["pairs"][0].update(prediction_confidence=True), "invalid_prediction_confidence"),
    (lambda data: data["pairs"][0]["plan"]["stages"][0].update(questions=False), "invalid_evidence_integer"),
    (lambda data: data["pairs"][0]["plan"]["stages"][0].update(stage=True), "invalid_evidence_integer"),
    (lambda data: data["pairs"][0]["candidate"].update(cache_miss_tokens=999), "invalid_evidence_integer"),
    (lambda data: data["pairs"][0]["candidate"].update(cache_miss_tokens=100), "invalid_cache_partition"),
    (lambda data: data["pairs"][0]["plan"]["stages"][0].update(network_ms=29), "stage_latency_double_count"),
    (lambda data: data["pairs"][0]["plan"].update(whole_plan_ms=39), "incomplete_whole_plan_accounting"),
    (lambda data: data["pairs"][0]["plan"].update(raw_prompt="private data"), "invalid_plan_observation"),
    (lambda data: data["pairs"][0]["labels"].update(source="candidate_model"), "invalid_label_source"),
    (lambda data: data["pairs"][0]["plan"].update(completion="unknown"), "incomplete_plan_without_fallback"),
    (lambda data: data["pairs"][0]["candidate"].update(total_cost=.99), "combined_arm_bound_exceeded"),
])
def test_malformed_evidence_is_rejected(change, code):
    data = evidence()
    change(data)
    with pytest.raises(DecisionError, match=code):
        inspect_evidence(data)


def test_episode_splits_batches_and_recovery_cannot_be_recounted():
    data = evidence(2)
    data["pairs"][1]["episode_id"] = data["pairs"][0]["episode_id"]
    with pytest.raises(DecisionError, match="duplicate_episode"):
        validate_evidence(data)
    data = evidence()
    data["splits"]["training"].append(data["splits"]["holdout"][0])
    with pytest.raises(DecisionError, match="episode_split_leakage"):
        validate_evidence(data)
    data = evidence(2)
    data["pairs"][1]["plan"]["stages"][0]["batch_id"] = data["pairs"][0]["plan"]["stages"][0]["batch_id"]
    with pytest.raises(DecisionError, match="duplicate_batch_usage"):
        validate_evidence(data)
    data = evidence()
    data["pairs"][0]["mode"] = "shadow"
    with pytest.raises(DecisionError, match="shadow_is_not_actual_recovery"):
        validate_evidence(data)
    data["pairs"][0].update(actual_misses=[], recovered_misses=[], recovery_evidence_digest=None)
    report = inspect_evidence(data)
    assert report["bounds"]["recovery_success"]["estimate"] is None
    assert report["bounds"]["paired_success_delta"]["n"] == 0
    assert "shadow_pairs_are_not_reduced_bundle_outcomes" in report["blocking_reasons"]


@pytest.mark.parametrize("usage", [None, {"input_tokens": None, "output_tokens": 10, "cost": None}])
def test_unknown_usage_is_not_zero_or_excluded_from_benefit_bounds(usage):
    data = evidence(MIN_SAMPLES)
    data["pairs"][0]["plan"]["stages"][0]["usage"] = usage
    report = inspect_evidence(data)
    assert report["observations"]["classifier_usage"]["input_tokens"]["total"] is None
    assert report["observations"]["classifier_usage"]["input_tokens"]["known_subtotal"] == (MIN_SAMPLES - 1) * 60
    assert report["bounds"]["net_token_savings"]["lower"] is None
    assert report["bounds"]["net_cost_savings"]["lower"] is None
    assert report["descriptive_paired_report"]["excluded_unknown_usage_pairs"] == 1
    assert "unknown_usage_prevents_net_benefit_qualification" in report["blocking_reasons"]


def test_unknown_completion_and_audit_gaps_remain_visible():
    data = evidence(MIN_SAMPLES)
    data["pairs"][0]["plan"].update(completion="unknown", fallback="deadline")
    data["pairs"][0]["plan"]["stages"][0]["usage"] = None
    data["audits"] = {"evidence_digest": digest("audit"), "safety_violations": 0, "budget_violations": 1,
                      "permission_leaks": 2, "cache_prefix_mutations": 3}
    report = inspect_evidence(data)
    assert report["observations"]["unknown_completion"] == 1
    assert report["release_metrics"]["p95_end_to_end_latency_ms"] is None
    assert report["bounds"]["total_latency_ratio"]["upper"] is None
    assert report["bounds"]["net_cost_savings"]["lower"] is None
    assert report["descriptive_paired_report"]["excluded_unknown_completion_pairs"] == 1
    assert report["observations"]["fallback_counts"]["deadline"] == 1
    assert "unknown_completion_prevents_latency_and_cost_qualification" in report["blocking_reasons"]
    assert "failed_metric:budget_violations" in report["blocking_reasons"]
    assert "failed_metric:permission_leaks" in report["blocking_reasons"]
    assert "failed_metric:cache_prefix_mutations" in report["blocking_reasons"]


def test_intervals_do_not_use_model_confidence_or_assume_constant_samples_are_certain():
    data = evidence(MIN_SAMPLES)
    for row in data["pairs"]:
        row["prediction_correct"] = True
    report = inspect_evidence(data)
    data2 = deepcopy(data)
    for row in data2["pairs"]:
        row["prediction_confidence"] = .1
    report2 = inspect_evidence(data2)
    assert report["bounds"]["heldout_precision"] == report2["bounds"]["heldout_precision"]
    assert report["bounds"]["calibration_ece"]["estimate"] != report2["bounds"]["calibration_ece"]["estimate"]
    assert wilson(1, 1)["lower"] is None
    assert 0 < wilson(1000, 1000)["lower"] < 1
    assert bounded_mean([0] * MIN_SAMPLES, -1, 1)["lower"] < 0
    assert report["observations"]["whole_plan"]["p95_upper_bound"] > report["observations"]["whole_plan"]["p95"]
    assert report["bounds"]["paired_success_delta"]["lower"] < 0


def test_real_candidate_claim_and_passing_readiness_are_not_external_authority():
    data = evidence()
    data.update(provenance="real_candidate", environment="actual_ryoko_host")
    for row in data["pairs"]:
        for item in (row["labels"], row["baseline"]["outcome"], row["candidate"]["outcome"]):
            item["source"] = "human"
    report = inspect_evidence(data, readiness={"gates": [{"id": "production_rollout", "status": "complete"}]})
    assert not report["qualifies_production"]
    assert "external_verification_required:exact_release_operator_approval" in report["blocking_reasons"]
    assert "external_verification_required:loaded_checkpoint_tokenizer_export_identity" in report["blocking_reasons"]


def test_strict_local_cli_and_existing_report_preservation(tmp_path, capsys):
    path, output = tmp_path / "evidence.json", tmp_path / "report.json"
    path.write_text(json.dumps(evidence()), encoding="utf-8")
    assert main([str(path), "--output", str(output)]) == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert not report["qualifies_production"]
    assert report["observations"]["batches"] == 1
    original = output.read_bytes()
    assert main([str(path), "--output", str(output)]) == 2
    assert output.read_bytes() == original
    assert main([str(path), "--output", str(path)]) == 2
    assert "output_must_not_replace_evidence" in capsys.readouterr().err
    for raw, code in (("{\"a\":1,\"a\":2}", "duplicate_json_key"),
                      ("{\"a\":NaN}", "nonfinite_json_number"),
                      ("{\"a\":Infinity}", "nonfinite_json_number")):
        path.write_text(raw, encoding="utf-8")
        with pytest.raises(DecisionError, match=code):
            load_redacted_json(path)


def test_repository_fixture_cli_remains_offline_and_reports_synthetic_provenance(monkeypatch, capsys):
    def forbid_network(*args, **kwargs):
        raise AssertionError("offline inspection must not open sockets")
    monkeypatch.setattr(socket, "socket", forbid_network)
    fixture = Path(__file__).resolve().parents[2] / "evals/decisions/laya_qualification_fixtures.json"
    assert main([str(fixture)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["provenance"] == "synthetic_fixture"
    assert report["observations"]["classifier_usage"]["input_tokens"]["total"] == 60
    assert "synthetic_is_not_production_evidence" in report["blocking_reasons"]


def test_shadow_counts_cannot_be_reused_as_independent_holdout_or_recovery_trials():
    data = evidence()
    shadow = {"episode_id": digest("shadow episode"), "plan": deepcopy(data["pairs"][0]["plan"])}
    shadow["plan"]["stages"][0]["batch_id"] = digest("shadow batch")
    shadow["plan"]["scenario"] = "cold"
    data["shadow"] = [shadow]
    data["splits"]["shadow"] = [shadow["episode_id"]]
    report = inspect_evidence(data)
    assert report["shadow_receipts"] == 1
    assert report["bounds"]["recovery_success"]["n"] == 1
    assert report["observations"]["scenario_whole_plan"]["cold"]["n"] == 1
    assert report["observations"]["classifier_usage"]["input_tokens"]["total"] == 120
    data["shadow"][0]["episode_id"] = data["pairs"][0]["episode_id"]
    with pytest.raises(DecisionError, match="duplicate_episode"):
        inspect_evidence(data)


def test_three_stage_trace_uses_one_usage_record_per_batch_and_shared_wall_time():
    data = evidence()
    plan = data["pairs"][0]["plan"]
    for index in (2, 3):
        stage = deepcopy(plan["stages"][0])
        stage.update(stage=index, batch_id=digest(["batch", index]), questions=1)
        plan["stages"].append(stage)
    plan["whole_plan_ms"] += 60
    report = inspect_evidence(data)
    assert report["observations"]["batches"] == 3
    assert report["observations"]["questions"] == 6
    assert report["observations"]["classifier_usage"]["input_tokens"]["total"] == 180
    assert report["descriptive_paired_report"]["metrics"]["net_token_savings"] == -140
    assert report["descriptive_paired_report"]["metrics"]["first_response_delta_ms"] == 80
    assert report["observations"]["stage_wall"]["3"]["p95"] == 30
    plan["stages"][1]["stage"] = 3
    with pytest.raises(DecisionError, match="noncausal_stage_order"):
        inspect_evidence(data)


def test_posthoc_bounds_and_unobserved_misses_cannot_satisfy_confidence_gates():
    data = evidence(MIN_SAMPLES)
    data["bounds"]["predeclared"] = False
    data["pairs"][0].update(actual_misses=[], recovered_misses=[], recovery_evidence_digest=None)
    report = inspect_evidence(data)
    assert report["bounds"]["net_token_savings"]["lower"] is None
    assert report["release_metrics"]["p95_end_to_end_latency_ms"] is None
    assert "unobserved_reduced_bundle_omissions_require_recovery_evidence" in report["blocking_reasons"]
    assert "predeclared_population_bounds_required" in report["blocking_reasons"]


@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), -2, 2])
def test_statistical_helper_rejects_invalid_or_out_of_bound_samples(value):
    with pytest.raises(DecisionError, match="invalid_statistical_sample"):
        bounded_mean([value], -1, 1)


def test_fixed_bin_ece_bound_can_support_gate_and_retains_observed_error():
    perfect = [{"prediction_correct": True, "prediction_confidence": 1.} for _ in range(MAX_EPISODES)]
    bound = _ece(perfect)
    assert bound["estimate"] == 0
    assert 0 < bound["upper"] < .05
    assert bound["method"] == "ten_fixed_bins_mcdiarmid"
    # Opposite residual signs in different fixed bins must not cancel each other.
    residuals = [{"prediction_correct": index % 2 == 0,
                  "prediction_confidence": .9 if index % 2 == 0 else .1}
                 for index in range(MAX_EPISODES)]
    nonzero = _ece(residuals)
    assert nonzero["estimate"] == pytest.approx(.1)
    assert nonzero["upper"] == pytest.approx(bound["upper"] + .1)


def test_fixed_bin_ece_bound_preserves_sample_floor_and_probability_ceiling():
    rows = [{"prediction_correct": False, "prediction_confidence": 1.} for _ in range(MIN_SAMPLES)]
    assert _ece([])["upper"] is None
    assert _ece(rows[:-1])["estimate"] == 1
    assert _ece(rows[:-1])["upper"] is None
    assert _ece(rows)["upper"] == 1
