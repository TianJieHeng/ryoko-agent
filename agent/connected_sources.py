"""Pure bounded source projections. Provider content never supplies authority.

Only documented Gmail Thread/Message and Calendar FreeBusy payloads are parsed.
Unknown vendor normalizations fail closed; no guesses from prose/snippets.
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone
from email.utils import getaddresses
import hashlib
import json
import re

from hermes_state_commitment_sources import parse_inbox, timestamp, zone, zoned_timestamp

MAX_BYTES = 2 * 1024 * 1024
MAX_MESSAGES = 100
FRESH_SECONDS = 900
TOOLS = {
    "gmail_thread": ("gmail", "GMAIL_FETCH_MESSAGE_BY_THREAD_ID"),
    "calendar_availability": ("googlecalendar", "GOOGLECALENDAR_FREE_BUSY_QUERY"),
}


class ConnectedSourceError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def require(value, code):
    if not value:
        raise ConnectedSourceError(code)


def canonical(value):
    try:
        data = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()
    except (TypeError, ValueError, RecursionError) as exc:
        raise ConnectedSourceError("source_schema_mismatch") from exc
    require(len(data) <= MAX_BYTES, "source_byte_limit")
    return data + b"\n"


def sha(value):
    return hashlib.sha256(value).hexdigest()


def exact(value, maximum=256):
    require(isinstance(value, str) and 0 < len(value) <= maximum and value == value.strip()
            and "*" not in value and not any(ord(c) < 32 for c in value), "invalid_source_selection")
    return value


def selection(value):
    require(isinstance(value, dict) and isinstance(value.get("kind"), str) and value["kind"] in TOOLS, "unsupported_source_adapter")
    exact(value.get("account_id"))
    # Account ID travels in a wire selector, never in a URL or credential field.
    if value["kind"] == "gmail_thread":
        require(set(value) == {"kind", "account_id", "mailbox", "thread_id"}, "invalid_source_selection")
        mailbox = exact(value["mailbox"], 320)
        require(re.fullmatch(r"[^\s<>@]+@[^\s<>@]+", mailbox) is not None, "invalid_source_selection")
        exact(value["thread_id"])
    else:
        require(set(value) == {"kind", "account_id", "calendar_ids", "timezone", "start_at", "end_at"},
                "invalid_source_selection")
        ids = value["calendar_ids"]
        require(isinstance(ids, list) and 1 <= len(ids) <= 20 and all(isinstance(item, str) for item in ids)
                and len(set(ids)) == len(ids), "invalid_source_selection")
        for calendar in ids:
            exact(calendar)
            require(calendar != "primary", "calendar_exact_identity_required")
        zone(value["timezone"])
        a, b = (zoned_timestamp(value[key], value["timezone"]) for key in ("start_at", "end_at"))
        require(0 < b - a <= 7 * 86400, "source_time_limit")
    return json.loads(canonical(value))


def arguments(selected):
    if selected["kind"] == "gmail_thread":
        return {"thread_id": selected["thread_id"], "user_id": selected["mailbox"]}
    return {"items": [{"id": item} for item in selected["calendar_ids"]],
            "timeMin": selected["start_at"], "timeMax": selected["end_at"], "timeZone": selected["timezone"],
            "calendarExpansionMax": len(selected["calendar_ids"]), "groupExpansionMax": 0}


def check_schema(record, selected):
    connector, tool = TOOLS[selected["kind"]]
    schema = record.get("schemas", {}).get(tool)
    require(isinstance(schema, dict) and schema.get("connector") == connector and schema.get("tool") == tool,
            "source_schema_mismatch")
    spec = schema.get("input_schema")
    require(isinstance(spec, dict) and spec.get("type") == "object", "source_schema_mismatch")
    # Provider schemas are data too. Never let JSON Schema fetch a remote $ref,
    # run attacker-provided regexes, or build recursive/combinatorial validators.
    require(len(canonical(spec)) <= 32768, "source_schema_mismatch")
    allowed_keywords = {"type", "properties", "required", "items", "description", "title", "default",
                        "additionalProperties", "enum", "minItems", "maxItems", "minLength", "maxLength",
                        "minimum", "maximum"}
    def finite_schema(node, depth=0):
        require(isinstance(node, dict) and depth <= 4 and set(node) <= allowed_keywords, "source_schema_mismatch")
        if "additionalProperties" in node:
            require(type(node["additionalProperties"]) is bool, "source_schema_mismatch")
        if "properties" in node:
            props = node["properties"]
            require(isinstance(props, dict) and len(props) <= 16, "source_schema_mismatch")
            for child in props.values():
                finite_schema(child, depth + 1)
        if "items" in node:
            finite_schema(node["items"], depth + 1)
    finite_schema(spec)
    properties, required = spec.get("properties"), spec.get("required", [])
    allowed = ({"thread_id": "string", "user_id": "string", "page_token": "string"}
               if selected["kind"] == "gmail_thread" else
               {"items": "array", "timeMin": "string", "timeMax": "string", "timeZone": "string",
                "calendarExpansionMax": "integer", "groupExpansionMax": "integer"})
    args = arguments(selected)
    require(isinstance(properties, dict) and set(args) <= set(properties) <= set(allowed)
            and isinstance(required, list) and all(isinstance(item, str) for item in required)
            and set(required) <= set(args), "source_schema_mismatch")
    for key, prop in properties.items():
        require(isinstance(prop, dict) and prop.get("type") == allowed[key], "source_schema_mismatch")
    if selected["kind"] == "calendar_availability":
        item = properties["items"].get("items", {})
        require(item.get("type") == "object" and set(item.get("properties", {})) == {"id"}
                and item["properties"]["id"].get("type") == "string", "source_schema_mismatch")
    # Validate constraints as well as our finite semantic allowlist.
    from jsonschema import Draft202012Validator
    try:
        Draft202012Validator.check_schema(spec)
        Draft202012Validator(spec).validate(args)
    except Exception as exc:
        raise ConnectedSourceError("source_schema_mismatch") from exc
    return sha(canonical(spec))


def unwrap(result):
    require(isinstance(result, dict) and result.get("error") is None, "source_provider_error")
    value = result.get("data")
    # Composio's documented action envelope is not the gateway envelope.
    require(isinstance(value, dict) and value.get("successful") is True and not value.get("error")
            and isinstance(value.get("data"), dict), "source_schema_mismatch")
    canonical(value)
    return value["data"]


def _parts(part, *, depth=0):
    require(isinstance(part, dict) and depth <= 12, "source_schema_mismatch")
    children = part.get("parts", [])
    require(isinstance(children, list) and len(children) <= 100, "source_schema_mismatch")
    yield part
    for child in children:
        yield from _parts(child, depth=depth + 1)


def _message(message, selected):
    require(isinstance(message, dict) and message.get("threadId") == selected["thread_id"], "source_scope_mismatch")
    mid = exact(message.get("id"))
    exact(message.get("historyId"))
    raw_time = message.get("internalDate")
    require(isinstance(raw_time, str) and raw_time.isdigit() and len(raw_time) <= 16, "source_schema_mismatch")
    sent = datetime.fromtimestamp(int(raw_time) / 1000, timezone.utc).isoformat()
    payload = message.get("payload")
    require(isinstance(payload, dict), "source_schema_mismatch")
    headers = payload.get("headers")
    require(isinstance(headers, list) and len(headers) <= 500, "source_schema_mismatch")
    participants, bodies, attachments = [], [], []
    for header in headers:
        require(isinstance(header, dict) and isinstance(header.get("name"), str)
                and isinstance(header.get("value"), str), "source_schema_mismatch")
        if header["name"].lower() in {"from", "to", "cc", "bcc", "reply-to"}:
            for _name, address in getaddresses([header["value"]]):
                require(re.fullmatch(r"[^\s<>@]+@[^\s<>@]+", address) is not None, "source_schema_mismatch")
                if address not in participants:
                    participants.append(address)
    parts = list(_parts(payload))
    require(len(parts) <= 300, "source_schema_mismatch")
    for part in parts:
        body = part.get("body", {})
        require(isinstance(body, dict), "source_schema_mismatch")
        filename = part.get("filename", "")
        if filename:
            exact(filename)
            attachment_id = body.get("attachmentId")
            # Inline attachment bytes stay in the immutable original only.
            ref = attachment_id or "part:" + exact(part.get("partId"))
            attachments.append({"name": filename, "source_ref": "gmail:" + mid + ":" + exact(ref)})
        elif part.get("mimeType") == "text/plain" and "data" in body:
            encoded = body["data"]
            require(isinstance(encoded, str) and len(encoded) <= 128000, "source_byte_limit")
            try:
                text = base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True).decode("utf-8")
            except (ValueError, UnicodeError) as exc:
                raise ConnectedSourceError("source_schema_mismatch") from exc
            bodies.append(text)
    body = "\n".join(bodies).strip()
    require(bool(body) and len(body) <= 8000 and bool(participants), "source_projection_incomplete")
    return {"message_id": mid, "thread_id": selected["thread_id"], "sent_at": sent,
            "participants": participants, "attachments": attachments, "body": body}


def project_gmail(value, selected, observed):
    require(value.get("id") == selected["thread_id"], "source_scope_mismatch")
    version = exact(value.get("historyId"))
    messages = value.get("messages")
    require(isinstance(messages, list) and 1 <= len(messages) <= MAX_MESSAGES, "source_message_limit")
    require(not value.get("nextPageToken") and not value.get("next_page_token"), "source_pagination_incomplete")
    snapshot = {"schema_version": 1, "source_id": "gmail:" + sha(canonical(selected)),
        "captured_at": datetime.fromtimestamp(observed, timezone.utc).isoformat(),
        "messages": [_message(item, selected) for item in messages]}
    parse_inbox(snapshot, {"thread_ids": [selected["thread_id"]]})
    return snapshot, version


def project_calendar(value, selected, observed):
    require(value.get("kind") == "calendar#freeBusy" and not value.get("groups"), "source_scope_mismatch")
    lower, upper = (timestamp(selected[key]) for key in ("start_at", "end_at"))
    require(timestamp(value.get("timeMin")) == lower and timestamp(value.get("timeMax")) == upper,
            "source_scope_mismatch")
    calendars = value.get("calendars")
    require(isinstance(calendars, dict) and set(calendars) == set(selected["calendar_ids"]), "source_scope_mismatch")
    intervals = []
    for calendar in selected["calendar_ids"]:
        row = calendars[calendar]
        require(isinstance(row, dict) and not row.get("errors"), "source_calendar_partial")
        busy = row.get("busy")
        require(isinstance(busy, list) and len(busy) <= 1000, "source_schema_mismatch")
        for item in busy:
            require(isinstance(item, dict) and set(item) == {"start", "end"}, "source_schema_mismatch")
            a, b = timestamp(item["start"]), timestamp(item["end"])
            require(lower <= a < b <= upper, "source_scope_mismatch")
            intervals.append({"start_at": datetime.fromtimestamp(a, zone(selected["timezone"])).isoformat(),
                              "end_at": datetime.fromtimestamp(b, zone(selected["timezone"])).isoformat()})
    require(len(intervals) <= 1000, "source_interval_limit")
    return {"schema_version": 1, "timezone": selected["timezone"],
        "captured_at": datetime.fromtimestamp(observed, timezone.utc).isoformat(),
        "participants": selected["calendar_ids"],
        "window": {"start_at": selected["start_at"], "end_at": selected["end_at"]}, "busy": intervals}, None


def materialize(result, selected, observed, schema_digest, scope):
    value = unwrap(result)
    snapshot, version, errors = None, None, []
    try:
        snapshot, version = (project_gmail if selected["kind"] == "gmail_thread" else project_calendar)(value, selected, observed)
    except (ValueError, TypeError, OverflowError) as exc:
        if isinstance(exc, ConnectedSourceError) and exc.code == "source_scope_mismatch":
            raise
        errors = [exc.code if isinstance(exc, ConnectedSourceError) else "source_schema_mismatch"]
    original = {"schema_version": 1, "kind": "connected_source_original", "source_kind": selected["kind"],
        "selection": selected, "scope": scope, "retrieved_at": observed, "fresh_until": observed + FRESH_SECONDS,
        "provider_version": version, "provider_version_basis": "history_id" if version else "unavailable",
        "tool_schema_sha256": schema_digest, "payload_sha256": sha(canonical(value)), "payload": value,
        "representation": "canonical_gateway_json_not_rfc822_or_provider_http_bytes",
        "coverage": "complete" if snapshot is not None else "partial", "errors": errors,
        "execution_authority": False, "claim_verification": "not_performed",
        "attachments": "metadata_only_no_attachment_fetch", "account_binding": "explicit_execute_account_selector"}
    return original, snapshot
