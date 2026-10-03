"""Quiet-hour folds/gaps are wall-clock suppression, digests elapsed UTC time."""
from datetime import datetime
import pytest
from cron.durable_notifications import in_quiet_hours, delivery_hold, validate_notification_policy


def at(text):
    return datetime.fromisoformat(text).timestamp()


def policy():
    return {"kind": "local_runtime", "timezone": "America/New_York", "quiet_hours": {
        "start_minute": 60, "end_minute": 120, "fold": "both", "gap": "next_valid"},
        "digest_seconds": 60, "max_deliveries": 3, "expires_at": at("2027-01-01T00:00:00+00:00")}


def test_quiet_fold_both_instances_gap_next_valid_cross_midnight_and_exact_end():
    value = policy()
    validate_notification_policy(value, now=at("2026-01-01T00:00:00+00:00"), schedule_expires_at=value["expires_at"])
    assert in_quiet_hours(value, at("2026-11-01T01:30:00-04:00"))
    assert in_quiet_hours(value, at("2026-11-01T01:30:00-05:00"))
    assert not in_quiet_hours(value, at("2026-11-01T02:00:00-05:00"))
    value["quiet_hours"].update(start_minute=60, end_minute=150)
    assert in_quiet_hours(value, at("2026-03-08T01:59:59-05:00"))
    assert not in_quiet_hours(value, at("2026-03-08T03:00:00-04:00"))
    value["quiet_hours"].update(start_minute=1320, end_minute=420)
    assert in_quiet_hours(value, at("2026-11-01T23:00:00-05:00"))
    assert in_quiet_hours(value, at("2026-11-02T06:59:59-05:00"))
    assert not in_quiet_hours(value, at("2026-11-02T07:00:00-05:00"))


def test_snooze_exact_expiry_digest_elapsed_time_and_bounded_grant():
    value = policy(); value["quiet_hours"] = None
    now = at("2026-11-01T01:59:30-04:00")
    assert delivery_hold(value, now, snoozed_until=now + 60) == "snoozed"
    assert delivery_hold(value, now + 60, snoozed_until=now + 60) is None
    assert delivery_hold(value, now + 59, first_pending_at=now) == "digest_window"
    assert delivery_hold(value, now + 60, first_pending_at=now) is None
    assert delivery_hold(value, value["expires_at"]) == "notification_expired"
    for key, invalid in (("max_deliveries", 0), ("digest_seconds", 1), ("kind", "slack")):
        candidate = {**value, key: invalid}
        with pytest.raises(ValueError):
            validate_notification_policy(candidate, now=now, schedule_expires_at=value["expires_at"])
