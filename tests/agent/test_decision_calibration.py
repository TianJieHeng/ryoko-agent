"""Offline BE15 metrics preserve closed labels, split isolation and honest denominators."""
import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from agent.decisions.calibration import EvaluationError, dataset_sha256, evaluate


def _digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def _row(number, confidence=.8, correct=True, *, fallback=False, point="DP06", question="risk"):
    return {"row_id": f"row-{number}", "episode_id": f"episode-{number}", "point_id": point,
            "question_id": question, "options": ["allow", "block", "unclear"],
            "distribution": {"allow": confidence, "block": 1 - confidence, "unclear": 0.0},
            "label": "allow" if correct else "block", "fallback": fallback, "split": "holdout"}


def _provenance(rows):
    return {"dataset_id": "test-holdout", "dataset_sha256": dataset_sha256(rows),
            "domain": "synthetic-risk-fixture", "split": "holdout", "frozen": True,
            "synthetic": True, "label_source": "synthetic_fixture",
            "model_digest": _digest("synthetic model descriptor"),
            "calibration_digest": _digest("synthetic calibration descriptor"),
            "contract_digest": _digest("synthetic contract"),
            "calibration_dataset": {"dataset_id": "test-calibration", "dataset_sha256": _digest("calibration rows"),
                                    "episode_ids": ["calibration-episode"]},
            "training_datasets": [{"dataset_id": "test-training", "dataset_sha256": _digest("training rows"),
                                   "episode_ids": ["training-episode"]}]}


def _evaluate(rows, **kwargs):
    return evaluate(rows, provenance=_provenance(rows), **kwargs)


def test_metrics_match_independent_arithmetic_and_full_multiclass_distribution():
    rows = [_row(1, .8, True), _row(2, .6, False)]
    metrics = _evaluate(rows, bins=2, target_precision=.95)["overall"]
    assert metrics["accuracy"] == .5
    # Brier is the sum over all closed classes, not only the winning probability.
    assert metrics["brier"] == pytest.approx(((.8 - 1) ** 2 + .2 ** 2 + .6 ** 2 + (.4 - 1) ** 2) / 2)
    assert metrics["ece"] == pytest.approx(abs((.8 + .6) / 2 - .5))
    assert metrics["confidence_auroc"]["value"] == 1
    assert metrics["coverage_at_target_precision"]["coverage"] == .5
    assert metrics["coverage_at_target_precision"]["precision"] == 1
    assert metrics["coverage_at_target_precision"]["threshold"] == .8
    assert sum(item["count"] for item in metrics["calibration_bins"]) == len(rows)


def test_auroc_uses_correctness_with_half_credit_for_confidence_ties():
    rows = [_row(1, .9, True), _row(2, .8, True), _row(3, .8, False), _row(4, .6, False)]
    positives = [.9, .8]
    negatives = [.8, .6]
    oracle = sum((a > b) + .5 * (a == b) for a in positives for b in negatives) / 4
    assert _evaluate(rows)["overall"]["confidence_auroc"]["value"] == oracle
    assert _evaluate([_row(1, .8, True), _row(2, .8, False)])["overall"]["confidence_auroc"]["value"] == .5


@pytest.mark.parametrize(("rows", "reason"), [([], "no_predictions"), ([_row(1)], "all_correct"),
                                              ([_row(1, correct=False)], "all_incorrect")])
def test_auroc_undefined_cases_are_explicit_json_null(rows, reason):
    metrics = _evaluate(rows)["overall"]
    assert metrics["confidence_auroc"]["value"] is None
    assert metrics["confidence_auroc"]["undefined_reason"] == reason
    json.dumps(metrics, allow_nan=False)
    if not rows:
        assert metrics["accuracy"] is metrics["brier"] is metrics["ece"] is metrics["fallback_rate"] is None
        assert metrics["coverage_at_target_precision"]["coverage"] is None


def test_coverage_does_not_cherry_pick_confidence_ties_and_can_recover_at_lower_threshold():
    rows = [_row(1, .9, True), _row(2, .9, False), _row(3, .8, True)]
    strict = _evaluate(rows, target_precision=.95)["overall"]["coverage_at_target_precision"]
    assert strict["accepted_count"] == 0
    assert strict["precision"] is strict["threshold"] is None
    assert strict["undefined_reason"] == "no_threshold_meets_target"
    loose = _evaluate(rows, target_precision=.6)["overall"]["coverage_at_target_precision"]
    assert loose["accepted_count"] == len(rows)
    assert loose["precision"] == pytest.approx(2 / 3)
    assert loose["threshold"] == .8


def test_fallback_and_unclear_exclusions_keep_all_attempts_in_coverage_denominator():
    rows = [_row(1), _row(2, fallback=True), _row(3, fallback=True), _row(4)]
    rows[2]["distribution"] = None
    rows[3].update(distribution={"allow": .05, "block": .05, "unclear": .9}, label="unclear")
    metrics = _evaluate(rows)["overall"]
    assert metrics["row_count"] == len(rows)
    assert metrics["scored_count"] == 3
    assert metrics["missing_distribution_count"] == 1
    assert metrics["accuracy"] == 1
    assert metrics["fallback_rate"] == .5
    assert metrics["coverage_at_target_precision"]["eligible_count"] == 1
    assert metrics["coverage_at_target_precision"]["coverage"] == .25
    only_missing = _evaluate([rows[2]])["overall"]
    assert only_missing["accuracy"] is None
    assert only_missing["asymmetric_error_cost"]["mean"] is None
    assert only_missing["coverage_at_target_precision"]["undefined_reason"] == "no_eligible_predictions"


def test_groups_are_by_point_question_and_option_count_and_costs_keep_their_direction():
    rows = [_row(1, .8, False), _row(2, .8, False, point="DP12"), _row(3, question="another"), _row(4)]
    rows[3]["options"].append("ask")
    rows[3]["distribution"]["ask"] = 0.0
    costs = [{"point_id": "DP06", "question_id": "risk", "true_label": "block", "predicted_label": "allow", "cost": 20}]
    report = _evaluate(rows, error_costs=costs)
    groups = {(g["point_id"], g["question_id"], g["option_count"]): g["metrics"] for g in report["groups"]}
    assert sum(g["row_count"] for g in groups.values()) == len(rows)
    assert groups[("DP06", "risk", 3)]["asymmetric_error_cost"]["total"] == 20
    assert groups[("DP12", "risk", 3)]["asymmetric_error_cost"]["total"] == 1
    assert groups[("DP06", "risk", 4)]["asymmetric_error_cost"]["total"] == 0
    assert report["overall"]["asymmetric_error_cost"]["mean"] == 21 / len(rows)


def test_top1_ties_ece_boundaries_and_permutations_are_deterministic_without_mutation():
    rows = [_row(1, .5), _row(2, 1), _row(3, .75)]
    before = copy.deepcopy(rows)
    provenance = _provenance(rows)
    report = evaluate(rows, provenance=provenance, bins=4)
    permuted = copy.deepcopy(list(reversed(rows)))
    for row in permuted:
        row["distribution"] = dict(reversed(list(row["distribution"].items())))
    assert dataset_sha256(rows) == dataset_sha256(permuted)
    assert report == evaluate(permuted, provenance=provenance, bins=4)
    assert rows == before
    assert report["overall"]["accuracy"] == 1  # Lexical allow wins the .5/.5 tie.
    buckets = report["overall"]["calibration_bins"]
    assert buckets[2]["count"] == 1  # Left-inclusive boundary .5.
    assert buckets[3]["count"] == 2  # Final bin includes probability 1.
    provenance["training_datasets"][0]["episode_ids"].append("later-mutation")
    assert "later-mutation" not in report["provenance"]["training_datasets"][0]["episode_ids"]


@pytest.mark.parametrize("change", [
    {"distribution": {"allow": .8, "block": .2}},
    {"distribution": {"allow": .8, "block": .1, "unclear": .1, "unknown": 0}},
    {"distribution": {"allow": .8, "block": .8, "unclear": 0}},
    {"distribution": {"allow": float("nan"), "block": .2, "unclear": 0}},
    {"distribution": {"allow": float("inf"), "block": .2, "unclear": 0}},
    {"distribution": {"allow": True, "block": 0, "unclear": 0}},
    {"distribution": {"allow": -1, "block": 2, "unclear": 0}},
    {"distribution": {"allow": 10 ** 1000, "block": 0, "unclear": 0}},
    {"distribution": None}, {"label": "foreign"}, {"fallback": "false"}, {"split": "calibration"},
    {"options": ["allow", "allow", "unclear"]}, {"options": ["allow", "block"]},
    {"point_id": "DP17"}, {"private_packet": "must not be accepted or echoed"},
])
def test_invalid_predictions_and_labels_are_rejected_instead_of_silently_repaired(change):
    rows = [_row(1)]
    provenance = _provenance(rows)
    rows[0].update(change)
    with pytest.raises(EvaluationError):
        evaluate(rows, provenance=provenance)


@pytest.mark.parametrize("split_name", ["calibration_dataset", "training_datasets"])
@pytest.mark.parametrize("collision", ["dataset_id", "dataset_sha256", "episode_ids"])
def test_holdout_split_overlap_is_rejected_by_identity_digest_or_episode(split_name, collision):
    rows = [_row(1)]
    provenance = _provenance(rows)
    target = provenance[split_name] if split_name == "calibration_dataset" else provenance[split_name][0]
    target[collision] = [rows[0]["episode_id"]] if collision == "episode_ids" else provenance[collision]
    with pytest.raises(EvaluationError, match="disjoint"):
        evaluate(rows, provenance=provenance)


def test_training_calibration_overlap_duplicate_rows_and_digest_tampering_are_rejected():
    rows = [_row(1)]
    provenance = _provenance(rows)
    provenance["training_datasets"][0]["episode_ids"] = provenance["calibration_dataset"]["episode_ids"]
    with pytest.raises(EvaluationError, match="disjoint"):
        evaluate(rows, provenance=provenance)
    with pytest.raises(EvaluationError, match="unique"):
        _evaluate(rows + rows)
    provenance = _provenance(rows)
    rows[0]["label"] = "block"
    with pytest.raises(EvaluationError, match="does not match"):
        evaluate(rows, provenance=provenance)


@pytest.mark.parametrize("change", [{"frozen": False}, {"frozen": 1}, {"split": "train"},
    {"label_source": "candidate_model"}, {"label_source": []}, {"model_digest": "unversioned-model"},
    {"synthetic": False}, {"calibration_dataset": None}, {"training_datasets": None}, {"domain": ""}])
def test_provenance_must_be_explicit_independent_frozen_and_versioned(change):
    rows = [_row(1)]
    provenance = _provenance(rows)
    provenance.update(change)
    with pytest.raises(EvaluationError):
        evaluate(rows, provenance=provenance)


def test_synthetic_origin_is_distinct_from_independent_label_source():
    rows = [_row(1)]
    provenance = _provenance(rows)
    provenance["label_source"] = "human"
    assert evaluate(rows, provenance=provenance)["provenance"]["synthetic"] is True
    provenance["synthetic"] = False
    assert evaluate(rows, provenance=provenance)["provenance"]["label_source"] == "human"


@pytest.mark.parametrize("kwargs", [{"bins": 0}, {"bins": True}, {"bins": 1.5}, {"bins": 1001},
                                      {"target_precision": 0}, {"target_precision": 1.1},
                                      {"target_precision": float("nan")}, {"target_precision": True}])
def test_metric_configuration_is_finite_and_bounded(kwargs):
    with pytest.raises(EvaluationError):
        _evaluate([_row(1)], **kwargs)


@pytest.mark.parametrize("change", [{"cost": -1}, {"cost": float("nan")}, {"cost": True},
                                     {"true_label": "foreign"}, {"point_id": "DP12"},
                                     {"predicted_label": "block", "cost": 5}])
def test_asymmetric_costs_cannot_be_negative_unscoped_or_charge_correct_predictions(change):
    entry = {"point_id": "DP06", "question_id": "risk", "true_label": "block", "predicted_label": "allow", "cost": 10}
    entry.update(change)
    with pytest.raises(EvaluationError):
        _evaluate([_row(1)], error_costs=[entry])


def test_bundled_fixture_cli_runs_from_unrelated_directory_and_is_reproducible(tmp_path):
    root = Path(__file__).resolve().parents[2]
    script = root / "evals" / "decisions" / "evaluate.py"
    fixture = json.loads(script.with_name("synthetic_holdout.json").read_text())
    working = tmp_path / "unrelated-working-directory"
    working.mkdir()
    command = [sys.executable, str(script), "--bins", "5"]
    first = subprocess.run(command, cwd=working, text=True, capture_output=True, check=True)
    second = subprocess.run(command, cwd=working, text=True, capture_output=True, check=True)
    assert first.stdout == second.stdout
    actual = json.loads(first.stdout)
    expected = evaluate(fixture["rows"], provenance=fixture["provenance"], bins=5, error_costs=fixture["error_costs"])
    assert actual["overall"] == expected["overall"]
    assert actual["provenance"]["synthetic"] is True
    assert actual["provenance"]["dataset_sha256"] == dataset_sha256(fixture["rows"])
    assert "Not the existing user intention-routing dataset" in actual["fixture_notice"]
    assert not list(working.iterdir())  # CLI writes only its stdout result.
    rejected = subprocess.run([*command, "--dataset", "private.json"], cwd=working, text=True, capture_output=True)
    assert rejected.returncode != 0
