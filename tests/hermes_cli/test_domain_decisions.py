"""Known-answer BE10 decision fixtures, changed-input sensitivity and safe boundaries."""

from copy import deepcopy
from dataclasses import FrozenInstanceError
from decimal import Decimal, localcontext
from fractions import Fraction
from itertools import product
import json

import pytest

from hermes_cli.domain_decisions import (
    DecisionRecord,
    DecisionValidationError,
    MAX_CRITERIA,
    MAX_OPTIONS,
    MAX_SCENARIOS,
    build_decision_package,
    evaluate_decision,
    revise_assumption,
    sensitivity_analysis,
)


@pytest.fixture
def decision():
    return {
        "decision_id": "vendor-selection",
        "options": [{"id": "b", "label": "B"}, {"id": "a", "label": "A"}],
        "criteria": [
            {"id": "cost", "label": "Cost", "lower": "0", "upper": "100",
             "direction": "minimize", "unit": "USD"},
            {"id": "benefit", "label": "Benefit", "lower": "0", "upper": "10",
             "direction": "maximize", "unit": "points"},
        ],
        "facts": [
            {"id": "a-cost", "option_id": "a", "criterion_id": "cost", "value": "60",
             "unit": "USD", "source_ref": "quote:a:v1"},
            {"id": "b-cost", "option_id": "b", "criterion_id": "cost", "value": "40",
             "unit": "USD", "source_ref": "quote:b:v1"},
        ],
        "assumptions": [
            {"id": "a-benefit", "option_id": "a", "criterion_id": "benefit", "value": "8",
             "unit": "points", "rationale": "Expected benefit before the pilot"},
        ],
        "subjective_scores": [
            {"id": "b-benefit", "option_id": "b", "criterion_id": "benefit", "value": "6",
             "unit": "points", "rationale": "User's qualitative comparison"},
        ],
        "priorities": [
            {"criterion_id": "cost", "statement": "Keep cost low", "source_ref": "user:request"},
            {"criterion_id": "benefit", "statement": "Prefer practical benefit", "source_ref": "user:request"},
        ],
        "weights": [{"criterion_id": "cost", "value": "1"},
                    {"criterion_id": "benefit", "value": "3"}],
        "accepted_choice": "a",
    }


def _by_option(result):
    return {row["option_id"]: row for row in result["ranking"]}


def test_known_answer_preserves_fact_assumption_judgment_and_priority_distinctions(decision):
    result = evaluate_decision(decision)
    rows = _by_option(result)
    assert rows["a"]["score"] == "7/10"  # (1 * 0.4 + 3 * 0.8) / 4
    assert rows["b"]["score"] == "3/5"
    assert rows["a"]["lower"] == rows["a"]["score"] == rows["a"]["upper"]
    assert result["leaders"] == ["a"]
    assert result["robust_winner"] == "a"
    assert result["possible_leaders"] == ["a"]
    assert not result["uncertain"] and not result["ranking_ambiguous"]
    kinds = {cell["input_id"]: cell["kind"]
             for row in rows.values() for cell in row["contributions"]}
    assert kinds == {"a-cost": "fact", "b-cost": "fact", "a-benefit": "assumption", "b-benefit": "subjective"}
    assert result["record"]["priorities"] != result["record"]["weights"]
    assert result["accepted_choice"] == "a" and result["action_authorized"] is False
    assert not result["accepted_choice_needs_review"]
    for row in rows.values():
        assert sum(Fraction(cell["contribution"]) for cell in row["contributions"]) == Fraction(row["score"])


def test_exact_decimal_fraction_arithmetic_is_independent_of_decimal_context(decision):
    decision["assumptions"][0]["value"] = Fraction(20, 3)
    decision["facts"][0]["value"] = Decimal("60.0000")
    decision["weights"][0]["value"] = Decimal("0.1")
    decision["weights"][1]["value"] = Fraction(3, 10)
    with localcontext() as context:
        context.prec = 2
        result = evaluate_decision(decision)
    assert [row["score"] for row in result["ranking"]] == ["3/5", "3/5"]
    assert result["leaders"] == ["a", "b"]
    assert [row["rank"] for row in result["ranking"]] == [1, 1]
    assert result["recommended_option"] is None
    assert result["robust_winner"] is None
    assert result["ranking_ambiguous"]


def test_uncertainty_can_overturn_nominal_recommendation_without_fabricating_probabilities(decision):
    decision["assumptions"][0].update(lower="6", upper="9")
    result = evaluate_decision(decision)
    a = _by_option(result)["a"]
    assert a["score"] == "7/10"
    assert (a["lower"], a["upper"]) == ("11/20", "31/40")
    assert result["recommended_option"] == "a"
    assert result["robust_winner"] is None
    assert result["possible_leaders"] == ["a", "b"]
    assert result["uncertain"] and result["ranking_ambiguous"]
    assert result["accepted_choice"] == "a" and result["accepted_choice_needs_review"]
    assert "not probabilities" in result["uncertainty_method"]
    revised = revise_assumption(decision, "a-benefit", "8", lower="7", upper="9")
    assert evaluate_decision(revised)["robust_winner"] == "a"


def test_cost_uncertainty_inverts_normalized_endpoints(decision):
    decision["facts"][0].update(lower="20", upper="80")
    result = evaluate_decision(decision)
    a = _by_option(result)["a"]
    assert (a["lower"], a["upper"]) == ("13/20", "4/5")


def test_weight_interval_optimization_handles_zero_lower_bounds(decision):
    for weight in decision["weights"]:
        weight.update(lower="0", upper=weight["value"])
    result = evaluate_decision(decision)
    rows = _by_option(result)
    assert (rows["a"]["lower"], rows["a"]["upper"]) == ("2/5", "4/5")
    assert rows["b"]["lower"] == rows["b"]["upper"] == "3/5"
    assert result["robust_winner"] is None


def test_weight_interval_result_matches_independent_brute_force_corners(decision):
    decision["criteria"].append({"id": "speed", "label": "Speed", "lower": "0", "upper": "1",
                                 "direction": "maximize", "unit": "ratio"})
    decision["priorities"].append({"criterion_id": "speed", "statement": "Prefer fast", "source_ref": "user:request"})
    decision["weights"].append({"criterion_id": "speed", "value": "2", "lower": "1/2", "upper": "5"})
    decision["weights"][0].update(lower="0", upper="2")
    decision["weights"][1].update(lower="1", upper="4")
    for option_id, value in (("a", "1/3"), ("b", "4/5")):
        decision["facts"].append({"id": option_id + "-speed", "option_id": option_id,
                                  "criterion_id": "speed", "value": value,
                                  "unit": "ratio", "source_ref": "benchmark:v1"})
    result = evaluate_decision(decision)
    corners = product((Fraction(0), Fraction(2)), (Fraction(1), Fraction(4)), (Fraction(1, 2), Fraction(5)))
    utilities = {"a": (Fraction(2, 5), Fraction(4, 5), Fraction(1, 3)),
                 "b": (Fraction(3, 5), Fraction(3, 5), Fraction(4, 5))}
    expected = {"a": [], "b": []}
    for weights in corners:
        for option_id, values in utilities.items():
            expected[option_id].append(sum(w * u for w, u in zip(weights, values)) / sum(weights))
    for option_id, row in _by_option(result).items():
        assert Fraction(row["lower"]) == min(expected[option_id])
        assert Fraction(row["upper"]) == max(expected[option_id])


def test_decisive_assumption_recomputes_scores_preserving_accepted_choice_and_original(decision):
    record = DecisionRecord.from_dict(decision)
    before = record.to_dict()
    revised = revise_assumption(record, "a-benefit", "4")
    changed = evaluate_decision(revised)
    assert changed["leaders"] == ["b"]
    assert _by_option(changed)["a"]["score"] == "2/5"
    assert changed["accepted_choice"] == "a" and changed["accepted_choice_needs_review"]
    assert changed["action_authorized"] is False
    assert revised.revision == record.revision + 1
    assert record.to_dict() == before
    receipt = sensitivity_analysis(record, "a-benefit", ["8", "20/3", "4"])
    assert receipt["decisive"]
    assert [scenario["result"]["leaders"] for scenario in receipt["scenarios"]] == [["a"], ["a", "b"], ["b"]]
    assert receipt["scenarios"][-1]["score_deltas"] == {"b": "0", "a": "-3/10"}
    assert all(scenario["result"]["accepted_choice"] == "a" for scenario in receipt["scenarios"])
    assert all(scenario["result"]["action_authorized"] is False for scenario in receipt["scenarios"])
    assert record.to_dict() == before


def test_changed_facts_and_weights_recompute_without_stale_ranking(decision):
    baseline = evaluate_decision(decision)
    changed_fact = deepcopy(decision)
    changed_fact["facts"][0]["value"] = "100"
    assert evaluate_decision(changed_fact)["leaders"] == ["a", "b"]
    changed_weight = deepcopy(decision)
    changed_weight["weights"][1]["value"] = "0"
    changed = evaluate_decision(changed_weight)
    assert changed["leaders"] == ["b"]
    assert _by_option(changed)["a"]["score"] != _by_option(baseline)["a"]["score"]
    assert changed["accepted_choice"] == baseline["accepted_choice"]


def test_snapshot_roundtrip_and_input_order_are_deterministic(decision):
    record = DecisionRecord.from_dict(decision)
    snapshot = record.to_dict()
    assert DecisionRecord.from_dict(json.loads(json.dumps(snapshot))) == record
    scrambled = deepcopy(decision)
    for field in ("options", "criteria", "facts", "priorities", "weights"):
        scrambled[field].reverse()
    assert evaluate_decision(scrambled) == evaluate_decision(record)
    decision["facts"][0]["value"] = "99"
    snapshot["facts"][0]["value"] = "90"
    assert record.to_dict()["facts"][0]["value"] == "60"
    with pytest.raises(FrozenInstanceError):
        record.accepted_choice = "b"
    with pytest.raises(FrozenInstanceError):
        record.assumptions[0].rationale = "Changed"


@pytest.mark.parametrize("value", [0.5, True, False, float("nan"), float("inf"), Decimal("NaN"),
                                  Decimal("sNaN"), Decimal("Infinity"), "NaN", "Infinity", "1/0",
                                  "1e999999", Decimal("1e-999999"), 1 << 257, Fraction(1, 1 << 257),
                                  "9" * 129, "__import__('os').system('false')", None])
def test_nonexact_nonfinite_oversized_or_executable_numbers_are_rejected(decision, value):
    decision["assumptions"][0]["value"] = value
    with pytest.raises(DecisionValidationError):
        evaluate_decision(decision)


@pytest.mark.parametrize("field", ["lower", "upper", "value"])
def test_weight_and_range_numbers_use_same_exact_validation(decision, field):
    decision["weights"][0].update(lower="0", upper="2")
    decision["weights"][0][field] = 1.0
    with pytest.raises(DecisionValidationError):
        evaluate_decision(decision)


@pytest.mark.parametrize("mutation, message", [
    (lambda d: d["facts"].pop(), "missing input"),
    (lambda d: d["facts"].append(dict(d["facts"][0], id="duplicate-cell")), "ambiguous cell"),
    (lambda d: d["assumptions"][0].update(id="a-cost"), "duplicate identifiers"),
    (lambda d: d["facts"][0].update(option_id="unknown"), "unknown option"),
    (lambda d: d["facts"][0].update(unit="EUR"), "unit must match"),
    (lambda d: d["facts"][0].update(value="101"), "declared range"),
    (lambda d: d["assumptions"][0].update(lower="9", upper="10"), "lower <= value <= upper"),
    (lambda d: d["assumptions"][0].update(lower="7"), "both lower and upper"),
    (lambda d: d["criteria"][0].update(upper="0"), "lower < upper"),
    (lambda d: d["criteria"][0].update(direction="automatic"), "minimize or maximize"),
    (lambda d: d["priorities"].pop(), "priorities must cover"),
    (lambda d: d["weights"].append(dict(d["weights"][0])), "duplicate identifiers"),
    (lambda d: d["weights"][0].update(value="-1"), "nonnegative"),
    (lambda d: d["weights"].pop(), "weights must cover"),
    (lambda d: [weight.update(value="0") for weight in d["weights"]], "positive nominal total"),
    (lambda d: d["facts"][0].pop("source_ref"), "missing or unsupported"),
    (lambda d: d["subjective_scores"][0].pop("rationale"), "missing or unsupported"),
    (lambda d: d.update(accepted_choice="unknown"), "declared option"),
    (lambda d: d.update(action_authorized=True), "unsupported fields"),
    (lambda d: d.update(revision=True), "revision must be an integer"),
])
def test_invalid_or_ambiguous_input_fails_closed(decision, mutation, message):
    mutation(decision)
    with pytest.raises(DecisionValidationError, match=message):
        evaluate_decision(decision)


def test_public_entrypoints_bound_collections_and_sensitivity(decision):
    oversized = deepcopy(decision)
    oversized["options"] *= MAX_OPTIONS + 1
    with pytest.raises(DecisionValidationError, match="options must contain"):
        evaluate_decision(oversized)
    oversized = deepcopy(decision)
    oversized["criteria"] *= MAX_CRITERIA + 1
    with pytest.raises(DecisionValidationError, match="criteria must contain"):
        evaluate_decision(oversized)
    for values in ([], ["8"] * (MAX_SCENARIOS + 1), iter(["8"])):
        with pytest.raises(DecisionValidationError, match="sensitivity values must contain"):
            sensitivity_analysis(decision, "a-benefit", values)
    for assumption_id in ("a-cost", "b-benefit", "missing"):
        with pytest.raises(DecisionValidationError, match="reference an assumption"):
            revise_assumption(decision, assumption_id, "4")
    with pytest.raises(DecisionValidationError, match="both lower and upper"):
        revise_assumption(decision, "a-benefit", "4", lower="3")


def test_total_evidence_text_is_bounded_before_package_generation(decision):
    decision["options"] = [{"id": f"o{index}", "label": f"Option {index}"} for index in range(20)]
    decision["criteria"] = [{"id": f"c{index}", "label": f"Criterion {index}", "lower": "0", "upper": "1",
                             "direction": "maximize", "unit": "ratio"} for index in range(20)]
    decision["facts"] = [{"id": f"{option['id']}-{criterion['id']}", "option_id": option["id"],
                          "criterion_id": criterion["id"], "value": "1", "unit": "ratio",
                          "source_ref": "a" * 4000} for option in decision["options"] for criterion in decision["criteria"]]
    decision["assumptions"] = []
    decision["subjective_scores"] = []
    decision["priorities"] = [{"criterion_id": criterion["id"], "statement": "Prefer more", "source_ref": "user:request"}
                              for criterion in decision["criteria"]]
    decision["weights"] = [{"criterion_id": criterion["id"], "value": "1"} for criterion in decision["criteria"]]
    decision["accepted_choice"] = None
    with pytest.raises(DecisionValidationError, match="total text size limit"):
        build_decision_package(decision)


def test_invalid_unicode_and_unbounded_text_fail_as_domain_validation_errors(decision):
    decision["options"][0]["label"] = "\ud800"
    with pytest.raises(DecisionValidationError, match="valid UTF-8"):
        build_decision_package(decision)
    decision["options"][0]["label"] = "a" * 4097
    with pytest.raises(DecisionValidationError, match="at most 4096"):
        build_decision_package(decision)


def test_no_choice_is_accepted_by_ranking_or_sensitivity(decision):
    del decision["accepted_choice"]
    result = evaluate_decision(decision)
    assert result["recommended_option"] == "a"
    assert result["accepted_choice"] is None
    assert result["action_authorized"] is False
    receipt = sensitivity_analysis(decision, "a-benefit", ["4"])
    assert receipt["accepted_choice"] is None
    assert receipt["scenarios"][0]["result"]["accepted_choice"] is None


def test_package_is_complete_reopenable_and_deterministic_with_sensitivity_receipts(decision):
    decision["sensitivity"] = {"assumption_id": "a-benefit", "values": ["8", "4"]}
    before = deepcopy(decision)
    package = build_decision_package(decision)
    assert package == build_decision_package(decision)
    output = package["outputs"][0]
    assert output["name"] == "decision.json" and output["mime"] == "application/json"
    reopened = json.loads(output["content_bytes"])
    assert reopened["leaders"] == ["a"]
    assert reopened["sensitivity"]["decisive"]
    scenario = reopened["sensitivity"]["scenarios"][-1]
    assert scenario["result"]["leaders"] == ["b"]
    assert scenario["assumption"]["value"] == "4"
    reconstructed = revise_assumption(reopened["record"], "a-benefit", scenario["value"])
    assert evaluate_decision(reconstructed)["ranking"] == scenario["result"]["ranking"]
    assert reopened["accepted_choice"] == scenario["result"]["accepted_choice"] == "a"
    assert package["metadata"]["validator_manifest"]["json_round_trip"]
    assert package["metadata"]["validator_manifest"]["evidence_verification"] == "references_only_not_retrieved"
    assert package["metadata"]["sensitivity"]["scenario_count"] == len(decision["sensitivity"]["values"])
    assert package["metadata"]["action_authorized"] is False
    assert decision == before


def test_package_without_scenarios_does_not_claim_sensitivity_was_run(decision):
    package = build_decision_package(decision)
    assert package["metadata"]["sensitivity"] is None
    assert json.loads(package["outputs"][0]["content_bytes"])["sensitivity"] is None
    decision["sensitivity"] = {"assumption_id": "a-benefit", "values": ["4"], "send": True}
    with pytest.raises(DecisionValidationError, match="unsupported fields"):
        build_decision_package(decision)
