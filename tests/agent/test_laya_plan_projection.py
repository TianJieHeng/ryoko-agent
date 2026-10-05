"""DP16v2 carries every causal receipt while retaining strict historical v1 bounds."""
from copy import deepcopy

import pytest
from pydantic import ValidationError

from tui_gateway.contracts.decision_plans import DecisionToolPlan


def record(version=2):
    return {"need": "needs_tools", "effort_bucket": "four_plus", "families": ["files"],
        "verified_tool_ids": ["read"], "live_catalog_version": "a" * 64, "bundle_id": "b" * 64,
        "reopen_policy": "authorized_search_describe_call", "scope_digest": "c" * 64,
        "mode": "shadow", "fallback": None,
        "decision_receipt_ids": [f"{index:064x}" for index in range(62)], "elapsed_ms": 12.5,
        "protocol_version": version, "metrics": {"batch_count": 3, "question_count": 62}}


def test_all_v2_receipts_replay_and_v1_bounds_remain_closed():
    raw = record()
    raw["verified_tool_ids"] = ["工具 📚"]
    replayed = DecisionToolPlan.model_validate(raw).model_dump(mode="json")
    assert replayed["decision_receipt_ids"] == raw["decision_receipt_ids"]
    assert replayed["verified_tool_ids"] == raw["verified_tool_ids"]
    for mutate in (
        lambda value: value.update(protocol_version=1),
        lambda value: value.update(protocol_version=3),
        lambda value: value["decision_receipt_ids"].append("e" * 64),
        lambda value: value["metrics"].update(batch_count=4),
        lambda value: value["metrics"].update(remote_unknown=1),
        lambda value: value.update(verified_tool_ids=["unsafe\x00name"]),
        lambda value: value.update(verified_tool_ids=[f"tool_{index}" for index in range(13)]),
        lambda value: value.update(raw_state="private-canary"),
    ):
        invalid = deepcopy(raw)
        mutate(invalid)
        with pytest.raises(ValidationError):
            DecisionToolPlan.model_validate(invalid)
    legacy = record(1)
    legacy["decision_receipt_ids"] = legacy["decision_receipt_ids"][:50]
    legacy.pop("protocol_version")
    legacy.pop("metrics")
    assert DecisionToolPlan.model_validate(legacy).protocol_version == 1


def test_duplicate_receipts_and_nonfinite_or_boolean_metrics_reject():
    for mutate in (
        lambda value: value["decision_receipt_ids"].__setitem__(1, value["decision_receipt_ids"][0]),
        lambda value: value["metrics"].update(input_tokens=True),
        lambda value: value["metrics"].update(inference_ms=float("nan")),
    ):
        value = record()
        mutate(value)
        with pytest.raises(ValidationError):
            DecisionToolPlan.model_validate(value)
