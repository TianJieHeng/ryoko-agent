"""Finite durable schedule definitions and timezone-aware occurrence identities."""
from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from hermes_state_runtime import RuntimeStoreError


def require(value, message, code="invalid_schedule"):
    if not value:
        raise RuntimeStoreError(code, message)


def canonical(value):
    def check(item, depth=0):
        require(depth <= 12, "Schedule nesting exceeds bound")
        if isinstance(item, dict):
            require(len(item) <= 64 and all(isinstance(key, str) for key in item), "Bounded string keys required")
            for child in item.values():
                check(child, depth + 1)
        elif isinstance(item, list):
            require(len(item) <= 100, "Schedule list exceeds bound")
            for child in item:
                check(child, depth + 1)
        else:
            require(item is None or type(item) in (str, int, float, bool), "Finite JSON required")
    check(value)
    try:
        result = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (ValueError, TypeError) as exc:
        raise RuntimeStoreError("invalid_schedule", "Finite JSON required") from exc
    require(len(result.encode()) <= 131072, "Schedule JSON exceeds 128 KiB")
    return result


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def exact(value, fields):
    require(isinstance(value, dict) and set(value) == set(fields.split()), "Exact schedule fields required")


def identifier(value):
    require(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value) is not None,
            "Bounded schedule identifier required")
    return value


def instant(value):
    require(type(value) in (int, float) and math.isfinite(value) and 0 < value < 253402214400,
            "Finite UTC timestamp required")
    return round(value, 3)


def immutable_ref(value):
    exact(value, "artifact_id version sha256")
    identifier(value["artifact_id"])
    require(type(value["version"]) is int and 0 < value["version"] < 2**31, "Positive immutable version required")
    require(isinstance(value["sha256"], str) and re.fullmatch(r"[0-9a-f]{64}", value["sha256"]) is not None,
            "Immutable digest required")


def validate_review(value):
    exact(value, "purpose source_refs workflow_ref")
    require(isinstance(value["purpose"], str) and value["purpose"] in {"memory_review", "skill_review"}, "Review purpose is required")
    require(isinstance(value["source_refs"], list) and 1 <= len(value["source_refs"]) <= 16,
            "Review needs bounded immutable sources")
    for ref in value["source_refs"]:
        immutable_ref(ref)
    if value["workflow_ref"] is not None:
        exact(value["workflow_ref"], "workflow_id version sha256")
        immutable_ref({"artifact_id": value["workflow_ref"]["workflow_id"],
                       "version": value["workflow_ref"]["version"], "sha256": value["workflow_ref"]["sha256"]})
    require(value["purpose"] != "skill_review" or value["workflow_ref"] is not None,
            "Skill review must pin an immutable workflow")


def validate_monitor(value):
    exact(value, "question source_set predicate notify_policy condition_action")
    require(isinstance(value["question"], str) and 1 <= len(value["question"]) <= 2048, "Monitor question required")
    require(isinstance(value["source_set"], list) and 1 <= len(value["source_set"]) <= 8,
            "One to eight exact local artifact sources required")
    for source in value["source_set"]:
        identifier(source)
    require(len(set(value["source_set"])) == len(value["source_set"]), "Duplicate monitor source")
    require(isinstance(value["notify_policy"], str) and value["notify_policy"] in {"record_only", "local_runtime"},
            "External notification adapters are not certified")
    predicate = value["predicate"]
    require(isinstance(predicate, dict) and type(predicate.get("version")) is int and predicate["version"] == 1,
            "Supported predicate version required")
    kind = predicate.get("kind")
    fields = {"normalized_text": "version kind", "json_fields": "version kind fields", "threshold": "version kind field operator value"}
    require(isinstance(kind, str) and kind in fields, "Unsupported predicate")
    exact(predicate, fields[kind])
    if kind == "json_fields":
        require(isinstance(predicate["fields"], list) and 1 <= len(predicate["fields"]) <= 32,
                "Bounded exact JSON field names required")
        for field in predicate["fields"]:
            identifier(field)
    if kind == "threshold":
        identifier(predicate["field"])
        require(isinstance(predicate["operator"], str) and predicate["operator"] in {"gt", "gte", "lt", "lte", "eq"}, "Unsupported threshold operator")
        require(type(predicate["value"]) in (int, float) and math.isfinite(predicate["value"]), "Finite threshold required")
    if value["condition_action"] is not None:
        validate_review(value["condition_action"])


def validate_definition(record):
    exact(record, "schema_version schedule_id version project_id timezone trigger policy budget expires_at kind specification")
    require(type(record["schema_version"]) is int and record["schema_version"] == 1, "Unsupported schedule version")
    identifier(record["schedule_id"]); identifier(record["project_id"])
    require(type(record["version"]) is int and 0 < record["version"] < 2**31, "Positive version required")
    try:
        ZoneInfo(record["timezone"])
    except (ValueError, TypeError, KeyError) as exc:
        raise RuntimeStoreError("invalid_schedule", "Exact IANA timezone required") from exc
    trigger = record["trigger"]
    require(isinstance(trigger, dict), "Trigger required")
    schemas = {"at": "kind at", "interval": "kind anchor seconds", "calendar": "kind hour minute weekdays fold gap"}
    require(isinstance(trigger.get("kind"), str) and trigger["kind"] in schemas, "Unsupported schedule trigger")
    exact(trigger, schemas[trigger["kind"]])
    if trigger["kind"] == "at":
        instant(trigger["at"])
    elif trigger["kind"] == "interval":
        instant(trigger["anchor"])
        require(type(trigger["seconds"]) is int and 60 <= trigger["seconds"] <= 366 * 86400, "Cadence is at least one minute")
    else:
        require(type(trigger["hour"]) is int and 0 <= trigger["hour"] < 24
                and type(trigger["minute"]) is int and 0 <= trigger["minute"] < 60, "Valid wall-clock time required")
        require(isinstance(trigger["weekdays"], list) and 1 <= len(trigger["weekdays"]) <= 7
                and all(type(day) is int and 0 <= day <= 6 for day in trigger["weekdays"]), "Weekdays required")
        require(isinstance(trigger["fold"], str) and trigger["fold"] in {"first", "second"} and trigger["gap"] == "skip", "Explicit DST fold/gap policy required")
    policy = record["policy"]
    exact(policy, "missed_run grace_seconds overlap")
    require(isinstance(policy["missed_run"], str) and policy["missed_run"] in {"skip", "latest"} and policy["overlap"] == "block", "Supported missed/overlap policy required")
    require(type(policy["grace_seconds"]) is int and 0 <= policy["grace_seconds"] <= 86400, "Bounded grace required")
    budget = record["budget"]
    exact(budget, "max_checks max_bytes deadline_seconds")
    for key, maximum in {"max_checks": 10000, "max_bytes": 2097152, "deadline_seconds": 60}.items():
        require(type(budget[key]) is int and 1 <= budget[key] <= maximum, "Finite local job budget required")
    instant(record["expires_at"])
    from cron.durable_workflow_contract import validate_workflow_draft
    validators = {"monitor": validate_monitor, "review": validate_review, "workflow_draft": validate_workflow_draft,
                  "weekly_review": lambda value: exact(value, "")}
    require(isinstance(record["kind"], str) and record["kind"] in validators, "Only installed finite local adapters are supported; scripts and agent loops are unavailable")
    validators[record["kind"]](record["specification"])
    canonical(record)
    return record


def next_due(record, after):
    """Strictly later UTC instant; each ambiguous wall time fires only its selected fold."""
    trigger = record["trigger"]
    kind = trigger["kind"]
    if kind == "at":
        result = instant(trigger["at"])
        return result if result > after else None
    if kind == "interval":
        anchor, period = instant(trigger["anchor"]), trigger["seconds"]
        return round(anchor + max(0, math.floor((after - anchor) / period) + 1) * period, 3)
    zone = ZoneInfo(record["timezone"])
    start = datetime.fromtimestamp(after, timezone.utc).astimezone(zone).date()
    for offset in range(9):
        day = start + timedelta(days=offset)
        if day.weekday() not in trigger["weekdays"]:
            continue
        local = datetime(day.year, day.month, day.day, trigger["hour"], trigger["minute"],
                         tzinfo=zone, fold=0 if trigger["fold"] == "first" else 1)
        stamp = local.timestamp()
        back = datetime.fromtimestamp(stamp, zone)
        # Nonexistent local time round-trips to a different wall time.
        if back.replace(tzinfo=None) != local.replace(tzinfo=None):
            continue
        if stamp > after:
            return stamp
    raise RuntimeStoreError("invalid_schedule", "No calendar occurrence in bounded window")


def occurrence_id(schedule_key, version, due_at):
    return "occ_" + digest({"schedule": schedule_key, "version": version, "due_at": instant(due_at)})[:48]
