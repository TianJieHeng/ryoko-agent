"""Bounded, exact, advisory decision records for BE10 scenario adapters.

``DecisionRecord.from_dict`` accepts JSON-shaped data. Each option/criterion cell
has exactly one fact, assumption, or explicitly subjective score. Facts require
an evidence reference; assumptions and subjective scores require a rationale.
Each criterion separately records a user priority and a nonnegative weight.
Criterion bounds define a fixed normalization scale, not data-derived extrema.

Numbers accept integers, Decimal, Fraction, or decimal/rational strings; binary
floats and bools are rejected. All arithmetic uses Fraction and serialized
numbers are exact rational strings. Ranges are scenario bounds, not statistical
confidence intervals. Weight ranges are unnormalized, independent bounds; the
all-zero vector is excluded. Per-option score ranges are exact for these bounds,
while robust-winner detection conservatively compares the resulting intervals.

This module performs no I/O and executes no expressions. Recommendations and
stored acceptance are advisory data, never authority to take an external action.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from fractions import Fraction
import json
import re
from typing import Any


MAX_OPTIONS = 64
MAX_CRITERIA = 32
MAX_INPUTS = 1024
MAX_SCENARIOS = 32
MAX_NUMBER_BITS = 256
MAX_NUMBER_TEXT = 128
MAX_TEXT = 4096
MAX_RECORD_TEXT = 1_000_000
MAX_RESULT_BITS = 8192
MAX_PACKAGE_BYTES = 4 * 1024 * 1024
_DECIMAL = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z")
_RATIONAL = re.compile(r"[+-]?[0-9]+/[0-9]+\Z")
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}\Z")


class DecisionValidationError(ValueError):
    """A decision input is invalid, ambiguous, or outside the supported limits."""


def _exact_text(value: Fraction) -> str:
    if max(value.numerator.bit_length(), value.denominator.bit_length()) > MAX_RESULT_BITS:
        raise DecisionValidationError("calculation exceeds the exact-result size limit")
    return str(value)


def _number(value: Any, field: str) -> Fraction:
    if isinstance(value, bool) or not isinstance(value, (int, str, Decimal, Fraction)):
        raise DecisionValidationError(f"{field} must be an exact number; floats and bools are unsupported")
    if isinstance(value, str):
        if len(value) > MAX_NUMBER_TEXT:
            raise DecisionValidationError(f"{field} exceeds the numeric size limit")
        try:
            if _RATIONAL.fullmatch(value):
                numerator, denominator = value.split("/")
                value = Fraction(int(numerator), int(denominator))
            elif _DECIMAL.fullmatch(value):
                value = Decimal(value)
            else:
                raise DecisionValidationError(f"{field} must be a decimal or rational number")
        except (InvalidOperation, ZeroDivisionError) as exc:
            raise DecisionValidationError(f"{field} is not a finite exact number") from exc
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise DecisionValidationError(f"{field} must be finite")
        parts = value.as_tuple()
        if len(parts.digits) > 64 or abs(parts.exponent) > 64:
            raise DecisionValidationError(f"{field} exceeds the numeric size limit")
    if isinstance(value, int) and value.bit_length() > MAX_NUMBER_BITS:
        raise DecisionValidationError(f"{field} exceeds the numeric size limit")
    result = Fraction(value)
    if max(result.numerator.bit_length(), result.denominator.bit_length()) > MAX_NUMBER_BITS:
        raise DecisionValidationError(f"{field} exceeds the numeric size limit")
    return result


def _text(value: Any, field: str, *, identifier: bool = False) -> str:
    limit = 96 if identifier else MAX_TEXT
    if not isinstance(value, str) or not value.strip() or len(value) > limit or "\x00" in value:
        raise DecisionValidationError(f"{field} must be nonempty text of at most {limit} characters")
    if identifier and not _IDENTIFIER.fullmatch(value):
        raise DecisionValidationError(f"{field} must be a simple identifier")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise DecisionValidationError(f"{field} must be valid UTF-8 text") from exc
    return value


def _fields(value: Any, required: set[str], optional: set[str], field: str) -> Mapping:
    if not isinstance(value, Mapping):
        raise DecisionValidationError(f"{field} must be an object")
    if len(value) > len(required | optional):
        raise DecisionValidationError(f"{field} has unsupported fields")
    if not required <= value.keys() or value.keys() - required - optional:
        raise DecisionValidationError(f"{field} has missing or unsupported fields")
    return value


def _items(value: Any, field: str, limit: int, *, nonempty: bool = False) -> list | tuple:
    if not isinstance(value, (list, tuple)) or len(value) > limit or (nonempty and not value):
        raise DecisionValidationError(f"{field} must contain {'1' if nonempty else '0'}..{limit} items")
    return value


@dataclass(frozen=True)
class _Range:
    value: Fraction
    lower: Fraction
    upper: Fraction

    def to_dict(self) -> dict:
        return {"value": str(self.value), "lower": str(self.lower), "upper": str(self.upper)}


def _range(data: Mapping, field: str) -> _Range:
    value = _number(data["value"], f"{field}.value")
    if ("lower" in data) != ("upper" in data):
        raise DecisionValidationError(f"{field} requires both lower and upper bounds")
    lower = _number(data.get("lower", value), f"{field}.lower")
    upper = _number(data.get("upper", value), f"{field}.upper")
    if not lower <= value <= upper:
        raise DecisionValidationError(f"{field} requires lower <= value <= upper")
    return _Range(value, lower, upper)


@dataclass(frozen=True)
class _Option:
    id: str
    label: str

    def to_dict(self) -> dict:
        return {"id": self.id, "label": self.label}


@dataclass(frozen=True)
class _Criterion:
    id: str
    label: str
    lower: Fraction
    upper: Fraction
    direction: str
    unit: str

    def utility(self, value: Fraction) -> Fraction:
        normalized = (value - self.lower) / (self.upper - self.lower)
        return 1 - normalized if self.direction == "minimize" else normalized

    def to_dict(self) -> dict:
        return {"id": self.id, "label": self.label, "lower": str(self.lower),
                "upper": str(self.upper), "direction": self.direction, "unit": self.unit}


@dataclass(frozen=True)
class _Input:
    id: str
    option_id: str
    criterion_id: str
    kind: str
    estimate: _Range
    unit: str
    source_ref: str | None
    rationale: str | None

    def to_dict(self) -> dict:
        result = {"id": self.id, "option_id": self.option_id, "criterion_id": self.criterion_id,
                  "unit": self.unit, **self.estimate.to_dict()}
        if self.source_ref is not None:
            result["source_ref"] = self.source_ref
        if self.rationale is not None:
            result["rationale"] = self.rationale
        return result


@dataclass(frozen=True)
class _Priority:
    criterion_id: str
    statement: str
    source_ref: str

    def to_dict(self) -> dict:
        return {"criterion_id": self.criterion_id, "statement": self.statement,
                "source_ref": self.source_ref}


@dataclass(frozen=True)
class _Weight:
    criterion_id: str
    estimate: _Range

    def to_dict(self) -> dict:
        return {"criterion_id": self.criterion_id, **self.estimate.to_dict()}


def _unique(items: tuple, attribute: str, field: str) -> dict:
    result = {getattr(item, attribute): item for item in items}
    if len(result) != len(items):
        raise DecisionValidationError(f"{field} contains duplicate identifiers")
    return result


def _parse_options(value: Any) -> tuple[_Option, ...]:
    options = []
    for raw in _items(value, "options", MAX_OPTIONS, nonempty=True):
        row = _fields(raw, {"id", "label"}, set(), "option")
        options.append(_Option(_text(row["id"], "option.id", identifier=True),
                               _text(row["label"], "option.label")))
    return tuple(sorted(options, key=lambda item: item.id))


def _parse_criteria(value: Any) -> tuple[_Criterion, ...]:
    criteria = []
    for raw in _items(value, "criteria", MAX_CRITERIA, nonempty=True):
        row = _fields(raw, {"id", "label", "lower", "upper", "direction", "unit"}, set(), "criterion")
        lower, upper = _number(row["lower"], "criterion.lower"), _number(row["upper"], "criterion.upper")
        if lower >= upper:
            raise DecisionValidationError("criterion requires lower < upper")
        if row["direction"] not in ("minimize", "maximize"):
            raise DecisionValidationError("criterion.direction must be minimize or maximize")
        criteria.append(_Criterion(_text(row["id"], "criterion.id", identifier=True),
                                   _text(row["label"], "criterion.label"), lower, upper,
                                   row["direction"], _text(row["unit"], "criterion.unit")))
    return tuple(sorted(criteria, key=lambda item: item.id))


def _parse_inputs(value: Any, kind: str, options: dict, criteria: dict) -> tuple[_Input, ...]:
    inputs = []
    common = {"id", "option_id", "criterion_id", "value", "unit"}
    required = common | ({"source_ref"} if kind == "fact" else {"rationale"})
    for raw in _items(value, kind, MAX_INPUTS):
        row = _fields(raw, required, {"lower", "upper", "source_ref", "rationale"} - required, kind)
        identifier = _text(row["id"], f"{kind}.id", identifier=True)
        option_id = _text(row["option_id"], f"{kind}.option_id", identifier=True)
        criterion_id = _text(row["criterion_id"], f"{kind}.criterion_id", identifier=True)
        if option_id not in options or criterion_id not in criteria:
            raise DecisionValidationError(f"{kind} references an unknown option or criterion")
        criterion = criteria[criterion_id]
        estimate = _range(row, kind)
        if not criterion.lower <= estimate.lower <= estimate.upper <= criterion.upper:
            raise DecisionValidationError(f"{kind} falls outside the criterion's declared range")
        unit = _text(row["unit"], f"{kind}.unit")
        if unit != criterion.unit:
            raise DecisionValidationError(f"{kind}.unit must match criterion.unit; conversion is explicit")
        source = _text(row["source_ref"], f"{kind}.source_ref") if "source_ref" in row else None
        rationale = _text(row["rationale"], f"{kind}.rationale") if "rationale" in row else None
        inputs.append(_Input(identifier, option_id, criterion_id, kind, estimate, unit, source, rationale))
    return tuple(sorted(inputs, key=lambda item: item.id))


def _parse_priorities(value: Any, criteria: dict) -> tuple[_Priority, ...]:
    priorities = []
    for raw in _items(value, "priorities", MAX_CRITERIA, nonempty=True):
        row = _fields(raw, {"criterion_id", "statement", "source_ref"}, set(), "priority")
        priorities.append(_Priority(_text(row["criterion_id"], "priority.criterion_id", identifier=True),
                                    _text(row["statement"], "priority.statement"),
                                    _text(row["source_ref"], "priority.source_ref")))
    if set(_unique(tuple(priorities), "criterion_id", "priorities")) != set(criteria):
        raise DecisionValidationError("priorities must cover each criterion exactly once")
    return tuple(sorted(priorities, key=lambda item: item.criterion_id))


def _parse_weights(value: Any, criteria: dict) -> tuple[_Weight, ...]:
    weights = []
    for raw in _items(value, "weights", MAX_CRITERIA, nonempty=True):
        row = _fields(raw, {"criterion_id", "value"}, {"lower", "upper"}, "weight")
        estimate = _range(row, "weight")
        if estimate.lower < 0:
            raise DecisionValidationError("weights must be nonnegative")
        weights.append(_Weight(_text(row["criterion_id"], "weight.criterion_id", identifier=True), estimate))
    if set(_unique(tuple(weights), "criterion_id", "weights")) != set(criteria):
        raise DecisionValidationError("weights must cover each criterion exactly once")
    if not sum((item.estimate.value for item in weights), Fraction()):
        raise DecisionValidationError("weights must have a positive nominal total")
    return tuple(sorted(weights, key=lambda item: item.criterion_id))


@dataclass(frozen=True, init=False)
class DecisionRecord:
    """Validated immutable input snapshot. Construction never accepts a choice implicitly."""

    decision_id: str
    revision: int
    options: tuple[_Option, ...]
    criteria: tuple[_Criterion, ...]
    facts: tuple[_Input, ...]
    assumptions: tuple[_Input, ...]
    subjective_scores: tuple[_Input, ...]
    priorities: tuple[_Priority, ...]
    weights: tuple[_Weight, ...]
    accepted_choice: str | None

    def __init__(self, payload: Mapping):
        required = {"decision_id", "options", "criteria", "facts", "assumptions", "subjective_scores",
                    "priorities", "weights"}
        data = _fields(payload, required, {"accepted_choice", "revision"}, "decision")
        options = _parse_options(data["options"])
        criteria = _parse_criteria(data["criteria"])
        option_map = _unique(options, "id", "options")
        criterion_map = _unique(criteria, "id", "criteria")
        if len(options) * len(criteria) > MAX_INPUTS:
            raise DecisionValidationError("decision exceeds the option/criterion cell limit")
        groups = {field: _parse_inputs(data[field], kind, option_map, criterion_map)
                  for field, kind in (("facts", "fact"), ("assumptions", "assumption"),
                                      ("subjective_scores", "subjective"))}
        inputs = groups["facts"] + groups["assumptions"] + groups["subjective_scores"]
        _unique(inputs, "id", "inputs")
        cells = {(item.option_id, item.criterion_id) for item in inputs}
        if len(cells) != len(inputs):
            raise DecisionValidationError("ambiguous cell: multiple inputs score the same option/criterion")
        if len(cells) != len(options) * len(criteria):
            raise DecisionValidationError("missing input: every option/criterion needs exactly one score")
        accepted = data.get("accepted_choice")
        if accepted is not None:
            accepted = _text(accepted, "accepted_choice", identifier=True)
            if accepted not in option_map:
                raise DecisionValidationError("accepted_choice must reference a declared option")
        revision = data.get("revision", 1)
        if type(revision) is not int or not 1 <= revision <= 1_000_000:
            raise DecisionValidationError("revision must be an integer from 1 to 1000000")
        values = {"decision_id": _text(data["decision_id"], "decision_id", identifier=True),
                  "revision": revision, "options": options, "criteria": criteria, **groups,
                  "priorities": _parse_priorities(data["priorities"], criterion_map),
                  "weights": _parse_weights(data["weights"], criterion_map), "accepted_choice": accepted}
        text_size = sum(len(value) for collection in (options, criteria, inputs, values["priorities"])
                        for item in collection for value in item.to_dict().values() if isinstance(value, str))
        if text_size > MAX_RECORD_TEXT:
            raise DecisionValidationError("decision exceeds the total text size limit")
        for name, value in values.items():
            object.__setattr__(self, name, value)

    @classmethod
    def from_dict(cls, payload: Mapping) -> DecisionRecord:
        return cls(payload)

    def to_dict(self) -> dict:
        return {"decision_id": self.decision_id, "revision": self.revision,
                **{name: [item.to_dict() for item in getattr(self, name)]
                   for name in ("options", "criteria", "facts", "assumptions", "subjective_scores",
                                "priorities", "weights")}, "accepted_choice": self.accepted_choice}


def _record(value: DecisionRecord | Mapping) -> DecisionRecord:
    return value if isinstance(value, DecisionRecord) else DecisionRecord.from_dict(value)


def _weighted_extreme(values: list[Fraction], weights: tuple[_Weight, ...], *, minimum: bool) -> Fraction:
    """Optimize a linear-fractional score over a nonnegative weight box in O(n²).

    At an optimum, values below the resulting mean use one weight endpoint and
    values above it use the other. Trying the sorted split points covers them all
    without an exponential corner enumeration or floating-point solver.
    """
    ordered = sorted(zip(values, weights), key=lambda item: item[0])
    candidates = []
    for split in range(len(ordered) + 1):
        total = Fraction()
        numerator = Fraction()
        for index, (value, weight) in enumerate(ordered):
            use_upper = (index < split) == minimum
            chosen = weight.estimate.upper if use_upper else weight.estimate.lower
            total += chosen
            numerator += value * chosen
        if total:
            candidates.append(numerator / total)
    return min(candidates) if minimum else max(candidates)


def evaluate_decision(record: DecisionRecord | Mapping) -> dict:
    """Return deterministic exact rankings, explicit ties, provenance, and uncertainty."""
    record = _record(record)
    inputs = record.facts + record.assumptions + record.subjective_scores
    cells = {(item.option_id, item.criterion_id): item for item in inputs}
    total_weight = sum((item.estimate.value for item in record.weights), Fraction())
    weights = {item.criterion_id: item for item in record.weights}
    rows = []
    for option in record.options:
        contributions = []
        utilities = []
        lower_utilities = []
        upper_utilities = []
        for criterion in record.criteria:
            item = cells[(option.id, criterion.id)]
            utility = criterion.utility(item.estimate.value)
            lower, upper = sorted((criterion.utility(item.estimate.lower),
                                   criterion.utility(item.estimate.upper)))
            normalized_weight = weights[criterion.id].estimate.value / total_weight
            utilities.append(utility * normalized_weight)
            lower_utilities.append(lower)
            upper_utilities.append(upper)
            contributions.append({"criterion_id": criterion.id, "input_id": item.id, "kind": item.kind,
                                  "utility": _exact_text(utility), "normalized_weight": _exact_text(normalized_weight),
                                  "contribution": _exact_text(utility * normalized_weight),
                                  "source_ref": item.source_ref, "rationale": item.rationale})
        score = sum(utilities, Fraction())
        lower = _weighted_extreme(lower_utilities, record.weights, minimum=True)
        upper = _weighted_extreme(upper_utilities, record.weights, minimum=False)
        rows.append({"option_id": option.id, "label": option.label, "score": score,
                     "lower": lower, "upper": upper, "contributions": contributions})
    rows.sort(key=lambda row: (-row["score"], row["option_id"]))
    leaders = [row["option_id"] for row in rows if row["score"] == rows[0]["score"]]
    robust = next((row["option_id"] for row in rows
                   if all(row["lower"] > other["upper"] for other in rows if other is not row)), None)
    possible = sorted(row["option_id"] for row in rows
                      if row["upper"] >= max(other["lower"] for other in rows))
    previous_score = None
    rank = 0
    for position, row in enumerate(rows, 1):
        if row["score"] != previous_score:
            rank = position
        previous_score = row["score"]
        row["rank"] = rank
        for key in ("score", "lower", "upper"):
            row[key] = _exact_text(row[key])
    uncertain = any(item.estimate.lower != item.estimate.upper for item in (*inputs, *record.weights))
    return {"decision_id": record.decision_id, "revision": record.revision,
            "method": "normalized_weighted_sum", "record": record.to_dict(), "ranking": rows,
            "leaders": leaders, "recommended_option": leaders[0] if len(leaders) == 1 else None,
            "robust_winner": robust, "possible_leaders": possible,
            "uncertain": uncertain, "ranking_ambiguous": robust is None,
            "accepted_choice": record.accepted_choice,
            "accepted_choice_needs_review": record.accepted_choice is not None and record.accepted_choice != robust,
            "action_authorized": False,
            "uncertainty_method": "independent input/weight bounds; conservative interval dominance, not probabilities"}


def revise_assumption(record: DecisionRecord | Mapping, assumption_id: str, value: Any,
                      *, lower: Any = None, upper: Any = None) -> DecisionRecord:
    """Create a revised snapshot, preserving acceptance; omitted bounds mean a point scenario."""
    record = _record(record)
    assumption_id = _text(assumption_id, "assumption_id", identifier=True)
    if (lower is None) != (upper is None):
        raise DecisionValidationError("a revised assumption requires both lower and upper bounds")
    payload = record.to_dict()
    match = next((row for row in payload["assumptions"] if row["id"] == assumption_id), None)
    if match is None:
        raise DecisionValidationError("assumption_id must reference an assumption, not a fact or subjective score")
    match.update(value=value, lower=value if lower is None else lower, upper=value if upper is None else upper)
    payload["revision"] += 1
    return DecisionRecord.from_dict(payload)


def sensitivity_analysis(record: DecisionRecord | Mapping, assumption_id: str, values: list | tuple) -> dict:
    """Recompute point scenarios for one assumption; report changed leaders and exact score deltas."""
    record = _record(record)
    values = _items(values, "sensitivity values", MAX_SCENARIOS, nonempty=True)
    baseline = evaluate_decision(record)
    baseline_scores = {row["option_id"]: Fraction(row["score"]) for row in baseline["ranking"]}
    scenarios = []
    for value in values:
        revised = revise_assumption(record, assumption_id, value)
        result = evaluate_decision(revised)
        # The baseline plus the explicit replacement reconstructs the scenario;
        # avoid copying a potentially large evidence record for every scenario.
        del result["record"]
        assumption = next(item for item in revised.assumptions if item.id == assumption_id)
        scenarios.append({"value": str(_number(value, "sensitivity value")), "assumption": assumption.to_dict(),
                          "result": result,
                          "leaders_changed": result["leaders"] != baseline["leaders"],
                          "score_deltas": {row["option_id"]: _exact_text(Fraction(row["score"]) - baseline_scores[row["option_id"]])
                                           for row in result["ranking"]}})
    return {"assumption_id": assumption_id, "baseline": baseline, "scenarios": scenarios,
            "decisive": any(row["leaders_changed"] for row in scenarios),
            "accepted_choice": record.accepted_choice, "action_authorized": False}


def build_decision_package(payload: Mapping) -> dict:
    """Build a complete JSON artifact without publishing, writing, or authorizing it.

    The direct record payload may include ``sensitivity`` with ``assumption_id``
    and a bounded ``values`` list. Receipts retain the baseline, each changed
    assumption, recomputed rankings and deltas, without treating acceptance as a
    request for external action. The caller owns grants and artifact persistence.
    """
    if not isinstance(payload, Mapping):
        raise DecisionValidationError("decision package must be an object")
    if len(payload) > 12:
        raise DecisionValidationError("decision package has unsupported fields")
    data = dict(payload)
    sensitivity = data.pop("sensitivity", None)
    record = DecisionRecord.from_dict(data)
    result = evaluate_decision(record)
    receipt = None
    if sensitivity is not None:
        request = _fields(sensitivity, {"assumption_id", "values"}, set(), "sensitivity")
        receipt = sensitivity_analysis(record, request["assumption_id"], request["values"])
        # The artifact's top-level evaluation already includes this baseline.
        del receipt["baseline"]
    result["sensitivity"] = receipt
    content = (json.dumps(result, sort_keys=True, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if len(content) > MAX_PACKAGE_BYTES:
        raise DecisionValidationError("decision package exceeds the output byte limit")
    return {"outputs": [{"name": "decision.json", "mime": "application/json", "content_bytes": content}],
            "metadata": {"adapter": "decision", "schema_version": 1,
                         "decision_id": record.decision_id, "revision": record.revision,
                         "input_schema": {"facts": "measured values with evidence references",
                                          "assumptions": "scenario values with rationales",
                                          "subjective_scores": "explicitly subjective judgments with rationales",
                                          "priorities": "user statements with source references",
                                          "weights": "nonnegative importance weights with optional bounds"},
                         "transformations": ["fixed declared-range normalization", "exact rational weighted sum",
                                             "bounded weight/input interval optimization", "deterministic tie grouping"],
                         "sensitivity": {"assumption_id": receipt["assumption_id"],
                                         "scenario_count": len(receipt["scenarios"]),
                                         "decisive": receipt["decisive"]} if receipt else None,
                         "validator_manifest": {"status": "passed", "complete_matrix": True,
                                                "exact_arithmetic": "Fraction",
                                                "json_round_trip": json.loads(content) == result,
                                                "evidence_verification": "references_only_not_retrieved"},
                         "supported_formats": ["application/json"], "action_authorized": False}}


__all__ = ["DecisionRecord", "DecisionValidationError", "evaluate_decision", "revise_assumption",
           "sensitivity_analysis", "build_decision_package"]
