"""Select already-owned front-door facts without memory, attachment or tool reads.

This adapter projects visible turn data only. System/developer instructions,
reasoning fields, opaque provider state and raw tool payloads are never copied.
Source classification remains private; synthetic tests replace this owner seam
with explicitly synthetic fixtures, rather than inferring publicity from text.
"""
from __future__ import annotations

import json
import re

from agent.decisions.contracts import digest, require
from agent.decisions.planner_context import build_planner_context
from agent.decisions.receipts import scope_digest

_SAFE_STATUS = re.compile(r"[A-Za-z0-9_.:-]{1,64}\Z")


def _source(turn_id, index=None):
    return "turn_" + digest([turn_id, index])[:24]


def _visible_request(request, turn_id):
    if isinstance(request, str):
        return request, []
    if not isinstance(request, list):
        return "", []
    require(len(request) <= 64, "planner_attachment_bound")
    text, attachments = [], []
    for index, part in enumerate(request):
        if not isinstance(part, dict):
            continue
        kind = part.get("type")
        if kind in {"text", "input_text"} and isinstance(part.get("text"), str):
            text.append(part["text"])
        elif kind in {"image", "image_url", "input_image", "document", "file", "input_file"}:
            # The presence of a part never proves its bytes were read or that
            # a signed URL can safely be exported to the classification service.
            label = part.get("filename")
            if not isinstance(label, str) or "://" in label:
                label = "attachment_" + str(index + 1)
            attachments.append({"id": "attachment_" + str(index + 1),
                "source_id": _source(turn_id, index), "type": "image" if "image" in kind else "document",
                "label": label, "available": True, "extracted_text_available": False,
                "contents_required": True})
    return "\n".join(text), attachments


def _recent_items(history, current, turn_id):
    rows = history if isinstance(history, (list, tuple)) else ()
    selected, outcomes, tool_names = [], [], {}
    # No full transcript serialization and no arbitrary tool-result flattening.
    start = max(0, len(rows) - 16)
    for index, row in enumerate(rows[start:], start):
        if not isinstance(row, dict):
            continue
        source_id = _source(turn_id, index)
        role, content = row.get("role"), row.get("content")
        if role == "assistant":
            for call in (row.get("tool_calls") or ())[:32]:
                if isinstance(call, dict) and isinstance(call.get("function"), dict):
                    name = call["function"].get("name")
                    if isinstance(name, str) and isinstance(call.get("id"), str):
                        tool_names[call["id"]] = name
        if role in {"user", "assistant"} and isinstance(content, str):
            if index == len(rows) - 1 and role == "user" and content == current:
                continue
            selected.append({"role": role, "content": content, "source_id": source_id})
        elif role == "tool":
            name = tool_names.get(row.get("tool_call_id"), row.get("name"))
            if not isinstance(name, str):
                continue
            from tools.agent_policy_gate import authorize_tool
            if authorize_tool(name) is not None:
                continue
            status, code = "unknown", ""
            if isinstance(content, str) and len(content) <= 4096:
                try:
                    record = json.loads(content)
                except (TypeError, ValueError):
                    record = None
                if isinstance(record, dict):
                    reported = record.get("status")
                    if reported in {"success", "completed", "ok", "failed", "error", "blocked", "pending"}:
                        status = {"completed": "success", "ok": "success", "failed": "error",
                                  "blocked": "error"}.get(reported, reported)
                    elif type(record.get("success")) is bool:
                        status = "success" if record["success"] else "failed"
                    error = record.get("error")
                    if error:
                        status = "error"
                        candidate = error.get("code") if isinstance(error, dict) else None
                        if isinstance(candidate, str) and _SAFE_STATUS.fullmatch(candidate):
                            code = candidate
            summary = "Reported status: " + status + ("; error code: " + code if code else "")
            outcomes.append({"tool_id": name, "status": status, "summary": summary,
                "source_id": source_id, "unresolved": status not in {"success", "completed", "ok"}})
    return selected, outcomes


def owner_planner_context(run, *, request, history=(), turn_id="", goal=""):
    """Trusted runtime owner adapter. No caller/config can label a user public."""
    current, attachments = _visible_request(request, turn_id or run.run_id)
    messages, outcomes = _recent_items(history, current, turn_id or run.run_id)
    if not goal:
        from agent.mission_runtime import goal_state_for_agent
        active = goal_state_for_agent(run.agent)
        if active is not None and active.status == "active":
            goal = {"text": active.goal, "source_id": "mission_" + digest([run.run_id, active.created_at])[:24]}
    return build_planner_context(current, scope_digest=scope_digest(run.context),
        source_id=_source(turn_id or run.run_id), goal=goal, messages=messages,
        tool_outcomes=outcomes, attachments=attachments, classification="private")
