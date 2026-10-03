"""Offline, descriptive decision metrics. This module never fits or promotes a model.

The version-one input/output schema and metric populations are documented in
``evals/decisions/README.md``. Inputs contain labels and probabilities, not state
packets. Provenance checks detect declared split overlap; they cannot establish
that an upstream model or dataset was honestly produced.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections import defaultdict
from collections.abc import Mapping
from typing import Any

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_POINT = re.compile(r"DP(?:0[1-9]|1[0-6])\Z")
_ROW_FIELDS = {
    "row_id", "episode_id", "point_id", "question_id", "options", "distribution",
    "label", "fallback", "split",
}
_PROVENANCE_FIELDS = {
    "dataset_id", "dataset_sha256", "domain", "split", "frozen", "synthetic",
    "label_source", "model_digest", "calibration_digest", "contract_digest",
    "calibration_dataset", "training_datasets",
}
_SPLIT_FIELDS = {"dataset_id", "dataset_sha256", "episode_ids"}
_COST_FIELDS = {"point_id", "question_id", "true_label", "predicted_label", "cost"}
_LABEL_SOURCES = {"human", "independent_teacher", "outcome", "synthetic_fixture"}


class EvaluationError(ValueError):
    """Malformed observations, unsupported provenance, or declared split leakage."""


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise EvaluationError(f"{field} must be a nonempty bounded string")
    return value


def _fields(value: Any, expected: set[str], field: str) -> None:
    if not isinstance(value, Mapping) or set(value) != expected:
        raise EvaluationError(f"{field} must have exactly the documented fields")


def _number(value: Any, field: str, *, maximum: float = 1.0) -> float:
    if type(value) not in (int, float) or not 0 <= value <= maximum or not math.isfinite(value):
        raise EvaluationError(f"{field} must be a finite number in [0, {maximum}]")
    return float(value)


def _digest(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise EvaluationError(f"{field} must be a lowercase SHA-256 hex digest")
    return value


def _identifiers(value: Any, field: str) -> set[str]:
    if not isinstance(value, list):
        raise EvaluationError(f"{field} must be a list")
    result = {_text(item, field) for item in value}
    if len(result) != len(value):
        raise EvaluationError(f"{field} cannot contain duplicates")
    return result


def dataset_sha256(rows: list[dict[str, Any]]) -> str:
    """Hash canonical JSON rows sorted by row_id; not the surrounding manifest."""
    try:
        ordered = sorted(rows, key=lambda row: row["row_id"])
        payload = json.dumps(ordered, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (KeyError, TypeError, ValueError) as exc:
        raise EvaluationError("Rows must be finite JSON objects with sortable row_id values") from exc
    return hashlib.sha256(payload).hexdigest()


def _validate_rows(rows: Any) -> list[dict[str, Any]]:
    if not isinstance(rows, list) or len(rows) > 100_000:
        raise EvaluationError("rows must be a list of at most 100000 observations")
    seen = set()
    validated = []
    for row in rows:
        _fields(row, _ROW_FIELDS, "row")
        for field in ("row_id", "episode_id", "point_id", "question_id", "label"):
            _text(row[field], field)
        if row["row_id"] in seen:
            raise EvaluationError("row_id must be unique within a holdout")
        seen.add(row["row_id"])
        if not _POINT.fullmatch(row["point_id"]):
            raise EvaluationError("point_id must be DP01 through DP16")
        if row["split"] != "holdout":
            raise EvaluationError("Only holdout rows can be evaluated")
        if type(row["fallback"]) is not bool:
            raise EvaluationError("fallback must be an explicit boolean")
        options = _identifiers(row["options"], "options")
        if not 2 <= len(options) <= 128 or "unclear" not in options:
            raise EvaluationError("options must contain 2–128 closed choices including unclear")
        if row["label"] not in options:
            raise EvaluationError("label must belong to the closed options")
        distribution = row["distribution"]
        if distribution is None:
            if not row["fallback"]:
                raise EvaluationError("Missing distributions require an explicit fallback")
            prediction, confidence, brier = None, None, None
        else:
            _fields(distribution, options, "distribution")
            probabilities = {key: _number(distribution[key], "probability") for key in sorted(options)}
            if not math.isclose(math.fsum(probabilities.values()), 1.0, rel_tol=0.0, abs_tol=1e-9):
                raise EvaluationError("distribution must sum to one without renormalization")
            # Stable across dict/menu ordering, including a tied unclear outcome.
            prediction = min(options, key=lambda key: (-probabilities[key], key))
            confidence = probabilities[prediction]
            brier = math.fsum((probabilities[key] - int(key == row["label"])) ** 2 for key in sorted(options))
        validated.append({**row, "prediction": prediction, "confidence": confidence,
                          "correct": prediction == row["label"] if prediction is not None else None,
                          "brier": brier})
    return sorted(validated, key=lambda row: row["row_id"])


def _validate_provenance(provenance: Any, rows: list[dict[str, Any]], digest: str) -> dict:
    _fields(provenance, _PROVENANCE_FIELDS, "provenance")
    for field in ("dataset_id", "domain"):
        _text(provenance[field], field)
    for field in ("dataset_sha256", "model_digest", "calibration_digest", "contract_digest"):
        _digest(provenance[field], field)
    if provenance["dataset_sha256"] != digest:
        raise EvaluationError("dataset_sha256 does not match the canonical holdout rows")
    if provenance["split"] != "holdout" or provenance["frozen"] is not True:
        raise EvaluationError("Evaluation requires an explicitly frozen holdout")
    if (type(provenance["synthetic"]) is not bool or not isinstance(provenance["label_source"], str)
            or provenance["label_source"] not in _LABEL_SOURCES):
        raise EvaluationError("synthetic and independent label_source must be explicit")
    if provenance["label_source"] == "synthetic_fixture" and not provenance["synthetic"]:
        raise EvaluationError("Synthetic fixture labels must be explicitly synthetic")
    if not isinstance(provenance["training_datasets"], list):
        raise EvaluationError("training_datasets must be an explicit list, possibly empty")
    ids = {provenance["dataset_id"]}
    digests = {digest}
    episodes = {row["episode_id"] for row in rows}
    manifests = [provenance["calibration_dataset"], *provenance["training_datasets"]]
    for manifest in manifests:
        _fields(manifest, _SPLIT_FIELDS, "split manifest")
        dataset_id = _text(manifest["dataset_id"], "split dataset_id")
        split_digest = _digest(manifest["dataset_sha256"], "split dataset_sha256")
        split_episodes = _identifiers(manifest["episode_ids"], "split episode_ids")
        if dataset_id in ids or split_digest in digests or episodes & split_episodes:
            raise EvaluationError("Holdout, calibration and training must have disjoint IDs, digests and episodes")
        ids.add(dataset_id)
        digests.add(split_digest)
        episodes.update(split_episodes)
    # A detached, canonical JSON copy prevents later caller mutation of the report.
    result = json.loads(json.dumps(provenance, sort_keys=True, allow_nan=False))
    result["calibration_dataset"]["episode_ids"].sort()
    for manifest in result["training_datasets"]:
        manifest["episode_ids"].sort()
    result["training_datasets"].sort(key=lambda item: item["dataset_id"])
    return result


def _validate_costs(error_costs: Any, rows: list[dict[str, Any]]) -> tuple[dict, list]:
    if error_costs is None:
        return {}, []
    if not isinstance(error_costs, list):
        raise EvaluationError("error_costs must be a list of scoped confusion-cost overrides")
    menus = defaultdict(list)
    for row in rows:
        menus[(row["point_id"], row["question_id"])].append(set(row["options"]))
    costs = {}
    for entry in error_costs:
        _fields(entry, _COST_FIELDS, "error cost")
        for field in _COST_FIELDS - {"cost"}:
            _text(entry[field], field)
        key = (entry["point_id"], entry["question_id"], entry["true_label"], entry["predicted_label"])
        if key in costs:
            raise EvaluationError("Duplicate error-cost override")
        if not any({key[2], key[3]} <= menu for menu in menus.get(key[:2], [])):
            raise EvaluationError("Error-cost override must refer to observed closed options")
        cost = _number(entry["cost"], "error cost", maximum=1e12)
        if key[2] == key[3] and cost != 0:
            raise EvaluationError("Correct predictions must have zero error cost")
        costs[key] = cost
    return costs, [dict(zip(("point_id", "question_id", "true_label", "predicted_label", "cost"), (*key, costs[key])))
                   for key in sorted(costs)]


def _confidence_auroc(rows: list[dict]) -> dict:
    correct = sum(row["correct"] for row in rows)
    incorrect = len(rows) - correct
    if not correct or not incorrect:
        reason = "no_predictions" if not rows else "all_correct" if correct else "all_incorrect"
        return {"value": None, "undefined_reason": reason, "correct_count": correct, "incorrect_count": incorrect}
    tied = defaultdict(lambda: [0, 0])
    for row in rows:
        tied[row["confidence"]][int(row["correct"])] += 1
    wins = 0.0
    lower_incorrect = 0
    for _, (tie_incorrect, tie_correct) in sorted(tied.items()):
        wins += tie_correct * (lower_incorrect + tie_incorrect / 2)
        lower_incorrect += tie_incorrect
    return {"value": wins / (correct * incorrect), "undefined_reason": None,
            "correct_count": correct, "incorrect_count": incorrect}


def _coverage(rows: list[dict], target: float) -> dict:
    eligible = [row for row in rows if row["prediction"] not in (None, "unclear") and not row["fallback"]]
    tied = defaultdict(list)
    for row in eligible:
        tied[row["confidence"]].append(row)
    result = {"target_precision": target, "coverage": 0.0 if rows else None,
              "accepted_count": 0, "eligible_count": len(eligible), "precision": None,
              "threshold": None, "undefined_reason": "no_threshold_meets_target"}
    if not rows:
        result["undefined_reason"] = "no_rows"
    elif not eligible:
        result["undefined_reason"] = "no_eligible_predictions"
    accepted = correct = 0
    for threshold, observations in sorted(tied.items(), reverse=True):
        accepted += len(observations)
        correct += sum(row["correct"] for row in observations)
        precision = correct / accepted
        if precision >= target:
            result.update(coverage=accepted / len(rows), accepted_count=accepted, precision=precision,
                          threshold=threshold, undefined_reason=None)
    return result


def _metrics(rows: list[dict], *, bins: int, target: float, costs: dict) -> dict:
    scored = [row for row in rows if row["prediction"] is not None]
    count = len(scored)
    calibration_bins = []
    bucket_rows = [[] for _ in range(bins)]
    losses = []
    for row in scored:
        bucket_rows[min(int(row["confidence"] * bins), bins - 1)].append(row)
        key = (row["point_id"], row["question_id"], row["label"], row["prediction"])
        losses.append(costs.get(key, float(not row["correct"])))
    for index, observations in enumerate(bucket_rows):
        n = len(observations)
        calibration_bins.append({"lower": index / bins, "upper": (index + 1) / bins, "count": n,
                                 "accuracy": sum(row["correct"] for row in observations) / n if n else None,
                                 "mean_confidence": math.fsum(row["confidence"] for row in observations) / n if n else None})
    ece = math.fsum(item["count"] * abs(item["accuracy"] - item["mean_confidence"])
                    for item in calibration_bins if item["count"]) / count if count else None
    fallback_count = sum(row["fallback"] for row in rows)
    return {
        "row_count": len(rows), "scored_count": count, "missing_distribution_count": len(rows) - count,
        "accuracy": sum(row["correct"] for row in scored) / count if count else None,
        "brier": math.fsum(row["brier"] for row in scored) / count if count else None,
        "ece": ece, "calibration_bins": calibration_bins,
        "confidence_auroc": _confidence_auroc(scored),
        "coverage_at_target_precision": _coverage(rows, target),
        "fallback_count": fallback_count, "fallback_rate": fallback_count / len(rows) if rows else None,
        "asymmetric_error_cost": {"total": math.fsum(losses), "mean": math.fsum(losses) / count if count else None,
                                  "scored_count": count, "basis": "candidate_top1_only"},
        "unscored_metrics_reason": "no_predictions" if not count else None,
    }


def evaluate(rows: list[dict[str, Any]], *, provenance: Mapping[str, Any],
             target_precision: float = .95, bins: int = 10,
             error_costs: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Validate a frozen holdout and return a deterministic, JSON-safe report.

    Classification metrics score the candidate, even when an incumbent fallback
    was used. Empirical coverage excludes fallbacks and unclear predictions.
    No threshold is fitted, installed, or qualified by this measurement function.
    """
    target = _number(target_precision, "target_precision")
    if target == 0:
        raise EvaluationError("target_precision must be greater than zero")
    if type(bins) is not int or not 1 <= bins <= 1000:
        raise EvaluationError("bins must be an integer in [1, 1000]")
    validated = _validate_rows(rows)
    checked_provenance = _validate_provenance(provenance, validated, dataset_sha256(rows))
    costs, cost_manifest = _validate_costs(error_costs, validated)
    grouped = defaultdict(list)
    for row in validated:
        grouped[(row["point_id"], row["question_id"], len(row["options"]))].append(row)
    groups = [{"point_id": point, "question_id": question, "option_count": count,
               "metrics": _metrics(observations, bins=bins, target=target, costs=costs)}
              for (point, question, count), observations in sorted(grouped.items())]
    return {"schema_version": 1, "purpose": "descriptive_frozen_holdout_evaluation",
            "provenance": checked_provenance,
            "settings": {"target_precision": target, "bins": bins, "error_costs": cost_manifest,
                         "default_correct_cost": 0.0, "default_error_cost": 1.0},
            "overall": _metrics(validated, bins=bins, target=target, costs=costs), "groups": groups}
