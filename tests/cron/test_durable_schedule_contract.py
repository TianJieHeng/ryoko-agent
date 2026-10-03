"""Behavior contracts for one UTC occurrence per explicit local DST policy."""
from datetime import datetime

import pytest

from cron.durable_contract import next_due, occurrence_id, validate_definition


def stamp(value):
    return datetime.fromisoformat(value).timestamp()


def definition():
    return {"schema_version": 1, "schedule_id": "watch", "version": 1, "project_id": "project",
        "timezone": "America/New_York", "trigger": {"kind": "calendar", "hour": 1, "minute": 30,
        "weekdays": list(range(7)), "fold": "first", "gap": "skip"},
        "policy": {"missed_run": "latest", "grace_seconds": 120, "overlap": "block"},
        "budget": {"max_checks": 10, "max_bytes": 10000, "deadline_seconds": 30}, "expires_at": stamp("2027-01-01T00:00:00+00:00"),
        "kind": "weekly_review", "specification": {}}


def test_explicit_fold_fires_once_and_gap_skips_without_duplicate_occurrence():
    record = validate_definition(definition())
    first = next_due(record, stamp("2026-11-01T00:00:00-04:00"))
    assert first == stamp("2026-11-01T01:30:00-04:00")
    assert next_due(record, first) == stamp("2026-11-02T01:30:00-05:00")
    record["trigger"]["fold"] = "second"
    assert next_due(record, stamp("2026-11-01T00:00:00-04:00")) == stamp("2026-11-01T01:30:00-05:00")
    record["trigger"].update(hour=2, minute=30)
    assert next_due(record, stamp("2026-03-08T00:00:00-05:00")) == stamp("2026-03-09T02:30:00-04:00")
    assert occurrence_id("key", 1, first) == occurrence_id("key", 1, float(first))
    assert occurrence_id("key", 1, first) != occurrence_id("key", 2, first)


def test_interval_clock_jump_has_stable_anchor_and_unsupported_execution_fails_closed():
    record = definition()
    record["trigger"] = {"kind": "interval", "anchor": 1000, "seconds": 60}
    assert next_due(record, 1000) == 1060
    assert next_due(record, 900) == 1000
    assert next_due(record, 1000 + 86400) == 1000 + 86460
    for kind in ("script_only", "agent", "http_monitor"):
        record["kind"] = kind
        with pytest.raises(ValueError):
            validate_definition(record)


def test_command_schedule_uses_same_explicit_dst_rules_and_enforces_prompt_authority_bounds():
    record = definition()
    record.update(kind="command", specification={"prompt": "Private summary", "session_id": "conversation", "authority_description": "One private summary"})
    record["policy"] = {"missed_run": "run_once", "grace_seconds": 60, "overlap": "queue"}
    validate_definition(record)
    first = next_due(record, stamp("2026-11-01T00:00:00-04:00"))
    assert first == stamp("2026-11-01T01:30:00-04:00")
    assert next_due(record, first) == stamp("2026-11-02T01:30:00-05:00")
    record["trigger"].update(hour=2, minute=30)
    assert next_due(record, stamp("2026-03-08T00:00:00-05:00")) == stamp("2026-03-09T02:30:00-04:00")
    record["budget"]["max_checks"] = 1001
    with pytest.raises(ValueError):
        validate_definition(record)
    record["budget"]["max_checks"] = 1
    record["budget"]["max_bytes"] = 1
    with pytest.raises(ValueError):
        validate_definition(record)
