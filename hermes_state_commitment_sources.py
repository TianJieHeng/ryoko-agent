"""Bounded imported-source interpretation; no mailbox, calendar or send adapter.

Classifications are deliberately labelled lexical proposals. Dates retain their
source wording; neither source prose nor a preview can accept an obligation.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from hermes_state_runtime import RuntimeStoreError

MAX_MESSAGES = 500
MAX_RANGE_SECONDS = 31 * 86400
CLASSIFICATIONS = frozenset({"request", "info", "decision", "waiting"})


def require(condition, code, message):
    if not condition:
        raise RuntimeStoreError(code, message)


def canonical(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError, RecursionError) as exc:
        raise RuntimeStoreError("invalid_commitment", "Expected finite JSON") from exc


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def text(value, maximum=256):
    require(isinstance(value, str) and 0 < len(value) <= maximum and value.strip() == value
            and not any(ord(c) < 32 and c not in "\n\t" for c in value),
            "invalid_commitment", "Expected bounded nonempty text")
    return value


def identifier(value, maximum=256):
    text(value, maximum)
    require(not any(ord(c) < 32 for c in value), "invalid_commitment", "Identifier cannot contain control characters")
    return value


def timestamp(value):
    text(value, 64)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        require(parsed.tzinfo is not None, "commitment_timezone_required", "An explicit timestamp offset is required")
        return parsed.timestamp()
    except (ValueError, OverflowError) as exc:
        raise RuntimeStoreError("invalid_commitment", "Invalid timestamp") from exc


def zone(value):
    text(value, 128)
    try:
        return ZoneInfo(value)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise RuntimeStoreError("commitment_timezone_required", "An available IANA timezone is required") from exc


def zoned_timestamp(value, timezone):
    stamp = timestamp(value)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(parsed.utcoffset() == datetime.fromtimestamp(stamp, zone(timezone)).utcoffset(),
            "commitment_timezone_mismatch", "Timestamp offset disagrees with the selected timezone")
    return stamp


def due_record(value):
    if value is None:
        return None
    require(isinstance(value, dict) and set(value) == {"at", "timezone", "kind"}
            and value["kind"] in {"due", "check"}, "invalid_commitment", "Exact due/check time and timezone required")
    zoned_timestamp(value["at"], value["timezone"])
    return json.loads(canonical(value))


def _classify(body):
    # Intentionally narrow English-only cues, never a claim of comprehensive NLP.
    rules = (("waiting", r"\b(?:waiting (?:for|on)|awaiting|pending (?:your|their))\b"),
             ("decision", r"\b(?:decide|decision|approve|approval|choose|sign off)\b"),
             ("request", r"\b(?:please|can you|could you|would you|need you to|action required)\b|\?"))
    return next((kind for kind, pattern in rules if re.search(pattern, body, re.I)), "info")


def _dates(body):
    pattern = r"\b(?:\d{4}-\d{2}-\d{2}|today|tomorrow|next (?:week|month|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)|by (?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday))\b"
    return [{"text": match.group(), "start": match.start(), "end": match.end(), "resolution": "unresolved"}
            for match in re.finditer(pattern, body, re.I)][:32]


def parse_inbox(value, selection):
    require(isinstance(value, dict) and set(value) == {"schema_version", "source_id", "captured_at", "messages"}
            and type(value["schema_version"]) is int and value["schema_version"] == 1,
            "invalid_inbox_snapshot", "Expected one versioned imported inbox snapshot")
    identifier(value["source_id"])
    captured = timestamp(value["captured_at"])
    messages = value["messages"]
    require(isinstance(messages, list) and 1 <= len(messages) <= MAX_MESSAGES,
            "invalid_inbox_snapshot", "Imported message count exceeds bound")
    require(isinstance(selection, dict), "inbox_selection_required", "Select exact threads or a bounded date range")
    threads = None
    if set(selection) == {"thread_ids"}:
        threads = selection["thread_ids"]
        require(isinstance(threads, list) and 1 <= len(threads) <= 100,
                "inbox_selection_required", "Select between one and 100 thread identifiers")
        for item in threads:
            identifier(item)
        require(len(set(threads)) == len(threads), "inbox_selection_required", "Thread selection contains duplicates")
    else:
        require(set(selection) == {"start_at", "end_at"}, "inbox_selection_required", "Explicit bounded selection required")
        start, end = timestamp(selection["start_at"]), timestamp(selection["end_at"])
        require(0 < end - start <= MAX_RANGE_SECONDS, "inbox_selection_required", "Inbox time range must be at most 31 days")
    groups, seen = {}, set()
    for message in messages:
        require(isinstance(message, dict) and set(message) == {
            "message_id", "thread_id", "sent_at", "participants", "attachments", "body"},
            "invalid_inbox_snapshot", "Imported messages must preserve participants, attachments, body and time")
        for field in ("message_id", "thread_id"):
            identifier(message[field])
        require(message["message_id"] not in seen, "invalid_inbox_snapshot", "Duplicate message identity")
        seen.add(message["message_id"])
        sent = timestamp(message["sent_at"])
        require(sent <= captured, "invalid_inbox_snapshot", "Snapshot predates a contained message")
        text(message["body"], 8000)
        participants = message["participants"]
        require(isinstance(participants, list) and 1 <= len(participants) <= 100,
                "invalid_inbox_snapshot", "Preserve bounded participant identities")
        for participant in participants:
            identifier(participant, 320)
        attachments = message["attachments"]
        require(isinstance(attachments, list) and len(attachments) <= 100,
                "invalid_inbox_snapshot", "Attachment count exceeds bound")
        for attachment in attachments:
            require(isinstance(attachment, dict) and set(attachment) == {"name", "source_ref"},
                    "invalid_inbox_snapshot", "Attachments require a name and retained source reference")
            text(attachment["name"])
            identifier(attachment["source_ref"], 1024)
        selected = message["thread_id"] in threads if threads is not None else start <= sent < end
        if selected:
            groups.setdefault(message["thread_id"], []).append({**message, "classification": _classify(message["body"]),
                "date_mentions": _dates(message["body"]), "classification_status": "lexical_proposal_unverified",
                "urgency": "not_inferred", "owner": None, "accepted": False})
    if threads is not None:
        require(set(threads) == set(groups), "inbox_selection_missing", "A selected thread is absent from this snapshot")
    require(bool(groups), "inbox_selection_missing", "No messages match this selection")
    result = []
    for thread_id, group in sorted(groups.items()):
        group.sort(key=lambda row: (timestamp(row["sent_at"]), row["message_id"]))
        result.append({"thread_id": thread_id, "messages": group,
            "current_participants": group[-1]["participants"],
            "attachments": [{"message_id": row["message_id"], **item} for row in group for item in row["attachments"]]})
    return result


def calendar_preview(value, *, timezone, participants, start_at, end_at, duration_minutes, now):
    require(isinstance(value, dict) and set(value) == {
        "schema_version", "timezone", "captured_at", "participants", "window", "busy"}
        and type(value["schema_version"]) is int and value["schema_version"] == 1,
        "invalid_calendar_snapshot", "Expected supplied availability-only snapshot")
    zone(timezone)
    require(value["timezone"] == timezone, "commitment_timezone_mismatch", "Availability timezone differs from request")
    captured = timestamp(value["captured_at"])
    require(captured <= now, "invalid_calendar_snapshot", "Availability snapshot is future-dated")
    require(isinstance(participants, list) and 1 <= len(participants) <= 50,
            "invalid_calendar_snapshot", "Exact supplied participant identities are required")
    for participant in participants:
        identifier(participant, 320)
    require(len(set(participants)) == len(participants), "invalid_calendar_snapshot", "Duplicate participant")
    require(isinstance(value["participants"], list) and participants == value["participants"],
            "calendar_identity_unresolved", "Availability must name the exact requested participants")
    window = value["window"]
    require(isinstance(window, dict) and set(window) == {"start_at", "end_at"},
            "invalid_calendar_snapshot", "Exact coverage window required")
    lower, upper = (zoned_timestamp(window[key], timezone) for key in ("start_at", "end_at"))
    start, end = zoned_timestamp(start_at, timezone), zoned_timestamp(end_at, timezone)
    require(lower <= start < end <= upper and end - start <= 7 * 86400,
            "invalid_calendar_snapshot", "Requested interval must be covered and at most seven days")
    require(type(duration_minutes) is int and 5 <= duration_minutes <= 480,
            "invalid_calendar_snapshot", "Duration must be 5–480 minutes")
    busy = value["busy"]
    require(isinstance(busy, list) and len(busy) <= 1000, "invalid_calendar_snapshot", "Availability interval bound exceeded")
    intervals = []
    for interval in busy:
        require(isinstance(interval, dict) and set(interval) == {"start_at", "end_at"},
                "invalid_calendar_snapshot", "Busy intervals cannot contain event or private title data")
        a, b = (zoned_timestamp(interval[key], timezone) for key in ("start_at", "end_at"))
        require(lower <= a < b <= upper, "invalid_calendar_snapshot", "Busy interval is outside supplied coverage")
        intervals.append((a, b))
    slots, candidate, duration = [], start, duration_minutes * 60
    while candidate + duration <= end and len(slots) < 10:
        overlaps = [b for a, b in intervals if candidate < b and a < candidate + duration]
        if overlaps:
            candidate = max(overlaps)
        else:
            slots.append({"start_at": datetime.fromtimestamp(candidate, zone(timezone)).isoformat(),
                          "end_at": datetime.fromtimestamp(candidate + duration, zone(timezone)).isoformat()})
            candidate += duration
    return {"timezone": timezone, "participants": participants, "identity_status": "supplied_unverified",
            "slots": slots, "availability_status": "supplied_snapshot_only", "live_availability_verified": False,
            "captured_at": value["captured_at"], "snapshot_age_seconds": now - captured,
            "stale": now - captured > 900, "calendar_changed": False, "invitation_sent": False}
