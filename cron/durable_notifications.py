"""Bounded local notice policy and clock interpretation; never a clock loop.

Quiet hours are wall-clock intervals [start,end), including both folds. A gap
contains no instants: delivery resumes at the first real local minute outside
quiet hours. Digest windows use elapsed UTC seconds and survive clock folds.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from cron.durable_contract import exact, instant, require
from hermes_state_runtime import RuntimeStoreError


def validate_notification_policy(value, *, now, schedule_expires_at):
    exact(value, "kind timezone quiet_hours digest_seconds max_deliveries expires_at")
    require(value["kind"] == "local_runtime", "Only same-client local notification is certified")
    try:
        ZoneInfo(value["timezone"])
    except (ValueError, TypeError, KeyError) as exc:
        raise RuntimeStoreError("invalid_schedule", "Exact IANA notification timezone required") from exc
    quiet = value["quiet_hours"]
    if quiet is not None:
        exact(quiet, "start_minute end_minute fold gap")
        require(all(type(quiet[key]) is int and 0 <= quiet[key] < 1440 for key in ("start_minute", "end_minute"))
                and quiet["start_minute"] != quiet["end_minute"], "Nonempty bounded quiet-hours interval required")
        require(quiet["fold"] == "both" and quiet["gap"] == "next_valid", "Explicit quiet-hours DST policy required")
    seconds = value["digest_seconds"]
    require(type(seconds) is int and (seconds == 0 or 60 <= seconds <= 86400), "Digest window must be zero or 60–86400 seconds")
    require(type(value["max_deliveries"]) is int and 1 <= value["max_deliveries"] <= 1000, "Bounded local delivery budget required")
    require(now < instant(value["expires_at"]) <= instant(schedule_expires_at), "Notification grant must expire within the schedule")
    return value


def in_quiet_hours(policy, now):
    quiet = policy["quiet_hours"]
    if quiet is None:
        return False
    local = datetime.fromtimestamp(now, timezone.utc).astimezone(ZoneInfo(policy["timezone"]))
    minute, start, end = local.hour * 60 + local.minute, quiet["start_minute"], quiet["end_minute"]
    return start <= minute < end if start < end else minute >= start or minute < end


def delivery_hold(policy, now, *, snoozed_until=None, first_pending_at=None):
    if now >= policy["expires_at"]:
        return "notification_expired"
    if snoozed_until is not None and now < snoozed_until:
        return "snoozed"
    if in_quiet_hours(policy, now):
        return "quiet_hours"
    if first_pending_at is not None and now < first_pending_at + policy["digest_seconds"]:
        return "digest_window"
    return None
