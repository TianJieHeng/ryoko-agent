"""Pure, bounded DP16 context assembled only from explicitly supplied turn data.

Redaction is deliberately not a privacy qualification. It neither retrieves
memory/credentials nor changes the owner's source classification. Insufficient
or oversized useful context is an incumbent fallback, never a clipped task.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
import unicodedata
from urllib.parse import parse_qsl, unquote, urlsplit

from agent.decisions.contracts import StatePacket, canonical, require, sha256

MAX_REQUEST_CHARS = 4096
MAX_GOAL_CHARS = 1024
MAX_HISTORY_CHARS = 3072
MAX_METADATA_CHARS = 1024
MAX_STATE_BYTES = 12 * 1024

_URL = re.compile(r"https?://[^\s<>\"']+", re.I)
_SECRET_QUERY = re.compile(r"(?:token|secret|password|signature|credential|authorization|api.?key|^key$|^sig$|^se$|^sv$)", re.I)
_INLINE_ATTACHMENT = re.compile(r"data:[a-z0-9.+-]+/[a-z0-9.+-]+(?:;[^,\s]*)?,[^\s<>\"']+", re.I)
_ATTACHMENT_REFERENCE = re.compile(r"\b(?:attached|uploaded)\b|\b(?:this|the)\s+(?:image|photo|picture|screenshot|attachment)\b|\bthis\s+pdf\b|添付|附件", re.I)
_SECRET_PATTERNS = (
    _INLINE_ATTACHMENT,
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", re.I),
    re.compile(r"\b(?:sk-(?:proj-)?|gh[pousr]_|github_pat_|xox[baprs]-)[A-Za-z0-9_\-\r\n\t]{12,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
)
_SECRET_ASSIGNMENT = re.compile(
    r'''(\b(?:[a-z][a-z0-9_]*_)?(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|passwd|client[_-]?secret|secret|token|authorization)\b["']?\s*(?:[:=]|\bis\b)\s*)("[^"\r\n]*"|'[^'\r\n]*'|[^\s,;&}\]]+)''', re.I)
_FOLLOWUP = re.compile(
    r"\b(?:that|it|those|them|again|continue|same as before|previous|earlier)\b|(?:それ|もう一度|続けて|那个|那個|继续|繼續|再来|再來)", re.I)
_CORRECTION = re.compile(r"\b(?:instead|actually|correction|rather than|not .{1,80} but)\b|(?:訂正|更正|改为|改為)", re.I)


@dataclass(frozen=True)
class PlannerContext:
    state_json: str = field(repr=False)
    scope_digest: str
    classification: str
    fallback: str | None = None

    def __post_init__(self):
        sha256(self.scope_digest)
        require(self.classification in {"private", "public", "synthetic"}, "invalid_data_class")
        require(len(self.state_json.encode("utf-8")) <= MAX_STATE_BYTES, "planner_context_bytes_exceeded")
        require(canonical(json.loads(self.state_json)) == self.state_json, "invalid_planner_context")

    @property
    def values(self):
        return json.loads(self.state_json)

    @property
    def packet(self):
        require(self.fallback is None, self.fallback or "invalid_planner_context")
        return StatePacket(self.state_json, self.scope_digest, self.classification)


def redact_context_text(value):
    """Pure text-only redaction; no vault, environment, or profile reads."""
    require(type(value) is str, "invalid_context_text")
    # Strip invisible separators *before* matching, so split credentials are
    # withheld as a whole instead of exporting reconstructable fragments.
    cleaned = "".join(char for char in value if char in "\n\t" or
                      unicodedata.category(char) not in {"Cc", "Cf", "Cs"})
    count = int(cleaned != value)
    value = cleaned

    def redact_url(match):
        nonlocal count
        try:
            parsed = urlsplit(match.group())
            sensitive = (parsed.username is not None or parsed.password is not None or
                         any(_SECRET_QUERY.search(unquote(key)) for key, _ in parse_qsl(parsed.query)))
        except ValueError:
            sensitive = True
        if sensitive:
            count += 1
            return "[redacted-url]"
        return match.group()

    value = _URL.sub(redact_url, value)
    for pattern in _SECRET_PATTERNS:
        value, changed = pattern.subn("[redacted]", value)
        count += changed
    value, changed = _SECRET_ASSIGNMENT.subn(lambda match: match.group(1) + "[redacted]", value)
    count += changed
    return value, count


def _sequence(value):
    require(type(value) in (tuple, list) and len(value) <= 64, "invalid_context_items")
    require(all(type(item) is dict for item in value), "invalid_context_item")
    return value


def _source(value):
    require(type(value) is str and 0 < len(value) <= 160, "invalid_context_source")
    clean, count = redact_context_text(value)
    require(count == 0 and not any(char.isspace() for char in clean), "invalid_context_source")
    return clean


def _characters(value):
    if type(value) is str:
        return len(value)
    if type(value) is dict:
        return sum(_characters(item) for item in value.values())
    if type(value) in (list, tuple):
        return sum(_characters(item) for item in value)
    return 0


class _Assembly:
    def __init__(self):
        self.redactions = 0
        self.truncated = []
        self.fallback = None

    def fail(self, reason):
        self.fallback = self.fallback or reason

    def text(self, value, field, limit):
        require(type(value) is str, "invalid_context_text")
        # Omit whole oversized fields: a sliced credential or identifier is unsafe.
        if len(value) > limit:
            self.truncated.append(field)
            self.fail("planner_context_chars_exceeded")
            return ""
        text, redactions = redact_context_text(value)
        self.redactions += redactions
        if len(text) > limit:
            self.truncated.append(field)
            self.fail("planner_context_chars_exceeded")
            return ""
        return text

    def fact(self, value, source_id, field):
        if type(value) is str:
            value = {"text": value, "source_id": source_id}
        require(type(value) is dict, "invalid_context_fact")
        return {"text": self.text(value.get("text", ""), field, MAX_GOAL_CHARS),
                "source_id": _source(value.get("source_id", source_id))}


def _select_messages(messages, references, corrections, assembly):
    eligible = [item for item in messages if item.get("role") in {"user", "assistant"}
                and item.get("relevant", True) is True]
    required = set(corrections)
    for reference in references:
        sources = reference.get("source_ids", ())
        require(type(sources) in (list, tuple), "invalid_reference_evidence")
        required.update(_source(item) for item in sources)
    # Owner-selected antecedents and the immediate request/answer pair take
    # precedence; order is restored afterward, without touching the transcript.
    indices = {index for index, item in enumerate(eligible) if item.get("source_id") in required}
    indices.update(range(max(0, len(eligible) - 2), len(eligible)))
    if len(indices) > 4:
        assembly.fail("planner_context_history_exceeded")
        indices = set(sorted(indices)[-4:])
    for index in reversed(range(len(eligible))):
        if len(indices) >= 4:
            break
        indices.add(index)
    return [eligible[index] for index in sorted(indices)], len(messages) - len(indices)


def _build_history(messages, outcomes, source_id, corrections, assembly):
    history = []
    for index, item in enumerate(messages):
        content = item.get("content", "")
        if type(content) is not str:
            assembly.fail("planner_context_text_unavailable")
            content = ""
        row = {"role": item["role"], "content": assembly.text(content, "messages", MAX_HISTORY_CHARS),
               "source_id": _source(item.get("source_id", f"message-{index}"))}
        if row["source_id"] in corrections:
            row["superseded_by"] = source_id
        if item.get("quoted") is True:
            row["quoted"] = True
        history.append(row)
    relevant = [item for item in outcomes if item.get("relevant", True) is True]
    required = [item for item in relevant if item.get("unresolved", item.get("status") == "error") is True]
    if len(required) > 2:
        assembly.fail("planner_context_outcomes_exceeded")
    selected = list(relevant[-2:])
    for item in reversed(required):
        if not any(item is existing for existing in selected):
            selected.insert(0, item)
            selected = selected[:2]
    summaries = []
    for index, item in enumerate(selected):
        status = item.get("status", "unknown")
        require(status in {"success", "error", "partial", "pending", "unknown", "cancelled"}, "invalid_outcome_status")
        summaries.append({"source_id": _source(item.get("source_id", f"outcome-{index}")),
            "tool_id": _source(item["tool_id"]), "status": status,
            "summary": assembly.text(item.get("summary", ""), "tool_outcomes", MAX_HISTORY_CHARS),
            "unresolved": item.get("unresolved", status == "error") is True})
    if _characters([history, summaries]) > MAX_HISTORY_CHARS:
        assembly.fail("planner_context_chars_exceeded")
        assembly.truncated.append("history")
        history, summaries = [], []
    return history, summaries, len(outcomes) - len(selected)


def _build_metadata(references, attachments, source_id, assembly):
    refs, files = [], []
    for item in references:
        status = item.get("status", "unresolved")
        require(status in {"resolved", "unresolved", "ambiguous"}, "invalid_reference_status")
        sources = item.get("source_ids", ())
        require(type(sources) in (tuple, list) and len(sources) <= 8, "invalid_reference_evidence")
        row = {"label": assembly.text(item.get("label", ""), "references", MAX_METADATA_CHARS), "status": status,
               "target": assembly.text(item.get("target", ""), "references", MAX_METADATA_CHARS),
               "source_ids": [_source(value) for value in sources]}
        if status != "resolved" or not row["source_ids"] or not row["target"]:
            assembly.fail("planner_reference_unresolved")
        refs.append(row)
    for index, item in enumerate(attachments):
        row = {"id": _source(item.get("id", f"attachment-{index}")),
               "source_id": _source(item.get("source_id", source_id)),
               "type": assembly.text(item.get("type", "unknown"), "attachments", 96),
               "label": assembly.text(item.get("label", ""), "attachments", MAX_METADATA_CHARS)}
        for name, default in (("available", False), ("extracted_text_available", False), ("contents_required", True)):
            require(type(item.get(name, default)) is bool, "invalid_attachment_metadata")
            row[name] = item.get(name, default)
        if row["contents_required"] and not row["extracted_text_available"]:
            assembly.fail("planner_attachment_content_unavailable")
        files.append(row)
    if _characters([refs, files]) > MAX_METADATA_CHARS:
        assembly.fail("planner_context_chars_exceeded")
        assembly.truncated.append("metadata")
        refs, files = [], []
    return refs, files


def build_planner_context(request, *, scope_digest, source_id="current", goal="", constraints=(),
                          messages=(), tool_outcomes=(), references=(), attachments=(),
                          correction_source_ids=(), classification="private"):
    """Build from owner-selected data; missing antecedents require explicit evidence.

    Facts are strings or ``{text, source_id}`` dictionaries. Messages are selected
    user/assistant text only. Tool outcomes must already be status summaries.
    References use ``{label, status, target, source_ids}``; attachment rows carry
    metadata only. Callers must separately authorize any source classification
    other than private and extracted text's availability for this destination.
    """
    sha256(scope_digest)
    require(classification in {"private", "public", "synthetic"}, "invalid_data_class")
    source_id = _source(source_id)
    require(type(correction_source_ids) in (tuple, list) and len(correction_source_ids) <= 8, "invalid_context_corrections")
    corrections = tuple(_source(item) for item in correction_source_ids)
    messages, tool_outcomes = _sequence(messages), _sequence(tool_outcomes)
    references, attachments = _sequence(references), _sequence(attachments)
    require(type(constraints) in (tuple, list) and len(constraints) <= 16, "invalid_context_constraints")
    assembly = _Assembly()
    current = assembly.text(request, "request", MAX_REQUEST_CHARS)
    active_goal = assembly.fact(goal, source_id, "goal") if goal else None
    facts = [assembly.fact(item, source_id, "constraints") for item in constraints]
    for fact in [active_goal, *facts]:
        if fact and fact["source_id"] in corrections:
            fact["superseded_by"] = source_id
    if _characters([active_goal, facts]) > MAX_GOAL_CHARS:
        assembly.fail("planner_context_chars_exceeded")
        assembly.truncated.append("goal_constraints")
        active_goal, facts = None, []
    selected, omitted_messages = _select_messages(messages, references, corrections, assembly)
    history, outcomes, omitted_outcomes = _build_history(selected, tool_outcomes, source_id, corrections, assembly)
    refs, files = _build_metadata(references, attachments, source_id, assembly)
    evidence = {source_id} | {row["source_id"] for row in [*history, *outcomes, *files, *facts]}
    if active_goal:
        evidence.add(active_goal["source_id"])
    if any(not set(row["source_ids"]) <= evidence for row in refs):
        assembly.fail("planner_reference_evidence_missing")
    if _FOLLOWUP.search(current) and not any(row["status"] == "resolved" for row in refs):
        assembly.fail("planner_reference_unresolved")
    if _INLINE_ATTACHMENT.search(request) or (_ATTACHMENT_REFERENCE.search(current) and not files):
        assembly.fail("planner_attachment_content_unavailable")
    if not current.strip():
        assembly.fail("planner_request_missing")
    values = {"request": current, "request_source_id": source_id, "goal": active_goal,
              "constraints": facts, "messages": history, "tool_outcomes": outcomes,
              "references": refs, "attachments": files,
              "context_flags": {"omitted_messages": omitted_messages, "omitted_outcomes": omitted_outcomes,
                  "redactions": assembly.redactions, "truncated_fields": sorted(set(assembly.truncated)),
                  "current_request_is_correction": bool(corrections or _CORRECTION.search(current)),
                  "correction_source_ids": list(corrections), "fallback": assembly.fallback}}
    encoded = canonical(values)
    if len(encoded.encode("utf-8")) > MAX_STATE_BYTES:
        assembly.fail("planner_context_bytes_exceeded")
        # Never retain overflow text in a diagnostic-only fallback packet.
        values = {"context_flags": {**values["context_flags"], "truncated_fields": ["context"],
                                    "fallback": assembly.fallback}}
        encoded = canonical(values)
    return PlannerContext(encoded, scope_digest, classification, assembly.fallback)
