"""Bounded supplied-transcript and creative packages; no recorder or generator.

Consent references record an operator declaration, not permission inferred from
transcript text. Outcomes remain proposals and cannot create obligations.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import time

from agent.result_artifacts import artifact_actor, read_project_artifact
from agent.project_context import project_access

MAX_BYTES = 2 * 1024 * 1024
MAX_SEGMENTS = 1000
MAX_DURATION_MS = 24 * 60 * 60 * 1000


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _text(value, maximum=8000):
    if not isinstance(value, str) or not 0 < len(value) <= maximum or "\0" in value:
        raise ValueError("A bounded nonempty text field is required")
    return value


def _read(context, db, project_id, ref):
    if not isinstance(ref, dict) or set(ref) != {"artifact_id", "version", "sha256"}:
        raise ValueError("An exact immutable source reference is required")
    data = read_project_artifact(context, db, project_id, ref["artifact_id"], ref["version"])
    if len(data) > MAX_BYTES or hashlib.sha256(data).hexdigest() != ref["sha256"]:
        raise ValueError("Source digest or size differs")
    row = db.read_artifact_version(ref["artifact_id"], ref["version"], artifact_actor(context),
                                   access=project_access(context))
    if row["project_id"] != project_id:
        raise ValueError("Source project differs")
    return data, row["descriptor"]["mime"]


def _timestamp(text):
    match = re.fullmatch(r"(?:(\d{1,2}):)?(\d{2}):(\d{2})[.,](\d{3})", text)
    if not match or int(match[2]) > 59 or int(match[3]) > 59:
        raise ValueError("Unsupported subtitle timestamp")
    return ((int(match[1] or 0) * 60 + int(match[2])) * 60 + int(match[3])) * 1000 + int(match[4])


def parse_transcript(data, mime):
    """Preserve supplied uncertainty; timing validity is not semantic accuracy."""
    if not isinstance(data, bytes) or len(data) > MAX_BYTES:
        raise ValueError("Transcript exceeds byte limit")
    text = data.decode("utf-8-sig")
    if mime == "application/json":
        value = json.loads(text)
        if not isinstance(value, dict) or set(value) != {"schema_version", "segments"} or value["schema_version"] != 1:
            raise ValueError("Unsupported transcript schema")
        segments = value["segments"]
    elif mime in {"text/vtt", "application/x-subrip"}:
        text = text.replace("\r\n", "\n")
        if mime == "text/vtt":
            if not text.startswith("WEBVTT\n"):
                raise ValueError("Missing WebVTT header")
            text = text[7:].lstrip("\n")
        segments = []
        for block in re.split(r"\n\s*\n", text.strip()):
            lines = block.splitlines()
            if lines and "-->" not in lines[0]:
                lines = lines[1:]
            if len(lines) < 2 or " --> " not in lines[0]:
                raise ValueError("Unsupported subtitle cue")
            start, end = lines[0].split(" --> ", 1)
            segments.append({"start_ms": _timestamp(start), "end_ms": _timestamp(end),
                             "text": "\n".join(lines[1:]), "speaker": None, "confidence": "unknown"})
    else:
        raise ValueError("Unsupported supplied transcript format")
    if not isinstance(segments, list) or not 1 <= len(segments) <= MAX_SEGMENTS:
        raise ValueError("Transcript segment count exceeds limit")
    result, previous_start, previous_end, gaps = [], -1, 0, []
    for index, segment in enumerate(segments):
        if not isinstance(segment, dict) or set(segment) != {"start_ms", "end_ms", "text", "speaker", "confidence"}:
            raise ValueError("Unsupported transcript segment")
        start, end = segment["start_ms"], segment["end_ms"]
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= MAX_DURATION_MS or start < previous_start:
            raise ValueError("Invalid or unordered transcript timing")
        if segment["speaker"] is not None:
            _text(segment["speaker"], 120)
        if segment["confidence"] not in {"unknown", "uncertain", "supplied"}:
            raise ValueError("Unsupported confidence; transcript cannot assert verification")
        _text(segment["text"])
        if start > previous_end:
            gaps.append({"start_ms": previous_end, "end_ms": start, "status": "unobserved"})
        result.append({**segment, "segment_id": index, "overlaps_previous": start < previous_end})
        previous_start, previous_end = start, max(previous_end, end)
    return {"segments": result, "gaps": gaps, "duration_ms": previous_end,
            "original_sha256": hashlib.sha256(data).hexdigest(), "semantic_accuracy": "unverified"}


def build_meeting_package(context, db, *, project_id, transcript_ref, consent_ref,
                          retention_until, outcomes=(), speaker_corrections=None, now=None):
    now = time.time() if now is None else now
    if isinstance(retention_until, bool) or not isinstance(retention_until, (int, float)) or not math.isfinite(retention_until) or not now < retention_until <= now + 366 * 86400:
        raise ValueError("A future bounded retention deadline is required")
    consent, consent_mime = _read(context, db, project_id, consent_ref)
    if consent_mime != "application/json":
        raise ValueError("Consent must be an explicit JSON declaration")
    declaration = json.loads(consent)
    if (not isinstance(declaration, dict) or declaration.get("operation") != "supplied_transcript_processing"
            or declaration.get("consent") is not True or declaration.get("source_sha256") != transcript_ref.get("sha256")):
        raise ValueError("Consent declaration does not bind this source and operation")
    raw, mime = _read(context, db, project_id, transcript_ref)
    parsed = parse_transcript(raw, mime)
    corrections = speaker_corrections or {}
    if not isinstance(corrections, dict) or len(corrections) > MAX_SEGMENTS:
        raise ValueError("Speaker corrections exceed limit")
    for key, speaker in corrections.items():
        if not isinstance(key, str) or not key.isdigit() or not 0 <= int(key) < len(parsed["segments"]):
            raise ValueError("Correction must name a real segment")
        row = parsed["segments"][int(key)]
        row["original_speaker"], row["speaker"] = row["speaker"], _text(speaker, 120)
        row["speaker_status"] = "operator_corrected_unverified"
    if not isinstance(outcomes, (list, tuple)) or len(outcomes) > 100:
        raise ValueError("Outcome count exceeds limit")
    proposals = []
    for item in outcomes:
        if not isinstance(item, dict) or set(item) != {"text", "kind", "segment_ids"} or item["kind"] not in {"summary", "decision_proposal", "commitment_proposal"}:
            raise ValueError("Outcomes must be explicit proposals with source segments")
        refs = item["segment_ids"]
        if not isinstance(refs, list) or not 1 <= len(refs) <= 20 or len(set(refs)) != len(refs) or any(type(i) is not int or not 0 <= i < len(parsed["segments"]) for i in refs):
            raise ValueError("Outcome source segments are invalid")
        proposals.append({**item, "text": _text(item["text"]), "status": "proposed", "accepted_obligation": False,
                          "claim_support": "operator_interpretation_unverified"})
    lines = ["# Meeting package", "", "Supplied transcript; interpretation and speaker identity remain unverified.",
             "", "## Proposed outcomes"]
    lines.extend(f"- {p['kind']}: {p['text']} [segments {','.join(map(str, p['segment_ids']))}]" for p in proposals)
    lines += ["", "## Timestamped source"]
    lines.extend(f"- [{s['start_ms']}–{s['end_ms']} ms, segment {s['segment_id']}] {s['speaker'] or 'unknown speaker'} ({s['confidence']}): {s['text']}" for s in parsed["segments"])
    lines += ["", "## Retention and consent", f"Retention deadline: {retention_until}",
              "This package does not delete the original or any backup at that deadline.",
              "No recording, transcription service, message, schedule or accepted obligation was created."]
    metadata = {"kind": "meeting", "inputs": [transcript_ref, consent_ref], "retention_until": retention_until,
                "consent_status": "operator_declaration", "transcript": parsed, "outcomes": proposals,
                "transformations": ["bounded_transcript_parse", "explicit_speaker_corrections", "proposed_outcome_citations"],
                "validator_manifest": {"timing": "passed", "semantic_support": "unverified", "acceptance": "not_requested"}}
    return {"content_bytes": ("\n".join(lines) + "\n").encode(), "mime": "text/markdown", "metadata": metadata}


def build_creative_package(context, db, *, project_id, brief, prompts, assets=(), continuity=()):
    from hermes_cli.artifact_formats import validate_artifact
    _text(brief, 16000)
    if not isinstance(prompts, list) or not 1 <= len(prompts) <= 32 or not isinstance(assets, (list, tuple)) or len(assets) > 32:
        raise ValueError("Creative package limits exceeded")
    if not isinstance(continuity, (list, tuple)) or len(continuity) > 32:
        raise ValueError("Continuity limit exceeded")
    for prompt in prompts:
        _text(prompt, 16000)
    for constraint in continuity:
        _text(constraint, 2000)
    names, resolved = set(), []
    for asset in assets:
        if not isinstance(asset, dict) or set(asset) != {"name", "ref", "rights", "stage"}:
            raise ValueError("Exact asset name/ref/rights/stage required")
        name = _text(asset["name"], 120)
        if name in names or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name):
            raise ValueError("Asset names must be unique safe basenames")
        names.add(name)
        if asset["stage"] != "supplied":
            raise ValueError("This adapter cannot certify generated assets")
        if asset["rights"] not in {"owned", "licensed", "permission_recorded", "unknown"}:
            raise ValueError("Explicit rights declaration required")
        raw, mime = _read(context, db, project_id, asset["ref"])
        validation = validate_artifact(raw, mime)
        resolved.append({**asset, "mime": mime, "validation": validation,
                         "reuse_status": "review_required" if asset["rights"] == "unknown" else "declared_not_legally_verified"})
    lines = ["# Creative production package", "", brief, "", "## Prompts"]
    lines.extend(f"### Prompt {index + 1}\n{prompt}" for index, prompt in enumerate(prompts))
    lines += ["", "## Continuity", *[f"- {item}" for item in continuity], "", "## Reference assets"]
    lines.extend(f"- {a['name']}: supplied, {a['rights']}, {a['ref']['sha256']}" for a in resolved)
    lines += ["", "## Production status", "Prompt-only package complete. External production and human rights review remain pending."]
    return {"content_bytes": ("\n".join(lines) + "\n").encode(), "mime": "text/markdown",
            "metadata": {"kind": "creative", "stage": "prompt_only", "production_status": "awaiting_production",
                         "inputs": [a["ref"] for a in resolved], "assets": resolved, "continuity": list(continuity),
                         "transformations": ["prompt_package", "supplied_asset_validation"],
                         "validator_manifest": {"asset_bytes": "validated", "generated_assets": 0, "rights": "declarations_only"}}}
