"""Compare matched direct/reviewed mission observations without running an agent.

The input is an explicit measurement artifact, never a source of execution or
completion authority. Synthetic fixtures remain labelled; missing user-review
time is unknown rather than zero. Nothing automatically promotes a policy.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

_HEX = re.compile(r"^[0-9a-f]{64}$")
_FIELDS = {"pair_id", "task_id", "policy", "task_digest", "acceptance_digest", "provenance",
           "deterministic_passed", "agent_elapsed_ms", "provider_cost_micros", "cost_basis",
           "user_review_time_ms", "review_observation"}


class ObservationError(ValueError):
    pass


def _measure(value, name, *, optional=False):
    if optional and value is None:
        return None
    if type(value) is not int or not 0 <= value <= 2**63 - 1:
        raise ObservationError(f"{name} must be a bounded nonnegative integer")
    return value


def _observation(value):
    if not isinstance(value, dict) or set(value) != _FIELDS:
        raise ObservationError("An observation must contain the exact declared fields")
    for name in ("pair_id", "task_id"):
        if not isinstance(value[name], str) or not 0 < len(value[name]) <= 128:
            raise ObservationError("Observation identifiers must be bounded")
    for name in ("task_digest", "acceptance_digest"):
        if not isinstance(value[name], str) or not _HEX.fullmatch(value[name]):
            raise ObservationError("Task and acceptance fingerprints must be SHA-256")
    if value["policy"] not in ("direct", "reviewed") or value["provenance"] not in ("observed", "synthetic"):
        raise ObservationError("Unknown policy or observation provenance")
    if type(value["deterministic_passed"]) is not bool:
        raise ObservationError("Deterministic outcome must be explicit")
    _measure(value["agent_elapsed_ms"], "agent_elapsed_ms")
    cost = _measure(value["provider_cost_micros"], "provider_cost_micros", optional=True)
    review = _measure(value["user_review_time_ms"], "user_review_time_ms", optional=True)
    if value["cost_basis"] not in ("metered_estimate", "provider_receipt", "unavailable"):
        raise ObservationError("Unknown cost basis")
    if (cost is None) != (value["cost_basis"] == "unavailable"):
        raise ObservationError("Missing cost must be labelled unavailable")
    if value["review_observation"] not in ("measured_active", "reported", "unavailable"):
        raise ObservationError("Unknown review-time source")
    if (review is None) != (value["review_observation"] == "unavailable"):
        raise ObservationError("Missing review time must be labelled unavailable")
    return dict(value)


def compare_mission_policies(document):
    if (not isinstance(document, dict) or set(document) != {"schema_version", "observations"}
            or type(document["schema_version"]) is not int or document["schema_version"] != 1
            or not isinstance(document["observations"], list) or len(document["observations"]) > 2000):
        raise ObservationError("Expected a bounded version-one observation document")
    pairs = {}
    for item in document["observations"]:
        item = _observation(item)
        pair = pairs.setdefault(item["pair_id"], {})
        if item["policy"] in pair:
            raise ObservationError("A pair cannot contain duplicate policy observations")
        pair[item["policy"]] = item
    matched, unmatched = [], []
    for pair_id, pair in sorted(pairs.items()):
        if len(pair) != 2:
            unmatched.append(pair_id)
            continue
        direct, reviewed = pair["direct"], pair["reviewed"]
        if any(direct[key] != reviewed[key] for key in ("task_id", "task_digest", "acceptance_digest", "provenance")):
            raise ObservationError("Compared policies must share exact task, acceptance and provenance")
        comparable_cost = direct["cost_basis"] == reviewed["cost_basis"] != "unavailable"
        comparable_review = direct["review_observation"] == reviewed["review_observation"] != "unavailable"
        passed = direct["deterministic_passed"] and reviewed["deterministic_passed"]
        matched.append({"pair_id": pair_id, "task_id": direct["task_id"], "provenance": direct["provenance"],
            "both_deterministic_passed": passed,
            "direct_deterministic_passed": direct["deterministic_passed"],
            "reviewed_deterministic_passed": reviewed["deterministic_passed"],
            "agent_elapsed_delta_ms": reviewed["agent_elapsed_ms"] - direct["agent_elapsed_ms"],
            "provider_cost_delta_micros": reviewed["provider_cost_micros"] - direct["provider_cost_micros"] if comparable_cost else None,
            "cost_basis": direct["cost_basis"] if comparable_cost else "incomparable",
            "user_review_delta_ms": reviewed["user_review_time_ms"] - direct["user_review_time_ms"] if comparable_review else None,
            "review_observation": direct["review_observation"] if comparable_review else "incomparable",
            "observed_review_comparable": bool(passed and direct["provenance"] == "observed"
                and comparable_review and direct["review_observation"] == "measured_active")})
    observed = [pair for pair in matched if pair["observed_review_comparable"]]
    return {"schema_version": 1, "delta_convention": "reviewed_minus_direct", "pairs": matched,
        "unmatched_pair_ids": unmatched, "matched_pairs": len(matched), "observed_review_pairs": len(observed),
        "user_review_delta_total_ms": sum(pair["user_review_delta_ms"] for pair in observed) if observed else None,
        "comparison_status": "observed_review_available" if observed else "observed_review_evidence_missing",
        "statistical_generalization": "not_established", "promotion_recommendation": "none_automatic",
        "execution_performed": False, "completion_authority": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("observations", type=Path, help="Local measurement JSON, maximum 8 MiB")
    args = parser.parse_args(argv)
    with args.observations.open("rb") as stream:
        data = stream.read(8 * 1024 * 1024 + 1)
    if len(data) > 8 * 1024 * 1024:
        parser.error("Observation file exceeds its byte bound")
    try:
        result = compare_mission_policies(json.loads(data))
    except (ObservationError, UnicodeError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, sort_keys=True, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
