"""Local capture text projections, approximate lexical lookup and explicit review.

No URL fetches, arbitrary paths, OCR, embeddings, model calls or preference
promotion. Existing immutable artifact references remain the original receipts.
"""
from __future__ import annotations

from difflib import get_close_matches
import re

from agent.result_artifacts import ArtifactConflict, read_project_artifact
from hermes_cli.project_sources import source_authority
from hermes_state_artifacts import _check
from hermes_state_captures import MAX_CAPTURE_TEXT, _sequence

_TEXT_MIMES = frozenset({"text/plain", "text/markdown", "text/csv", "application/json",
                         "application/x-subrip", "text/vtt"})
_STOP = frozenset({"a", "an", "and", "are", "at", "for", "from", "i", "in", "is", "it", "of", "on",
                   "or", "that", "the", "this", "to", "was", "with", "about", "find", "my", "remember"})


def _public(value):
    # Public source contracts omit internal owner and storage fields.
    capture = value["capture"]
    fields = ("capture_id", "project_id", "original_ref", "source_url", "acquired_at", "annotation",
              "suggested_project_id", "filed_project_id", "revision", "extractions")
    return {**value, "capture": {key: capture[key] for key in fields}}


def inspect_capture(context, db, capture_id):
    actor, access = source_authority(context, db)
    return _public(db.get_capture_processing(capture_id, actor, access=access))


def process_capture(context, db, capture_id, *, expected_extraction_sequence, source="original"):
    """Identity-extract verified UTF-8 bytes, recording honest failure without losing originals."""
    actor, access = source_authority(context, db)
    capture = db.get_capture(capture_id, actor, access=access)
    with access.guard(capture["project_id"], actor, "write"):
        _check(_sequence(capture) == expected_extraction_sequence, "revision_conflict", "Capture extraction changed")
        _check(source in {"original", "latest_extraction"}, "invalid_artifact", "Unknown capture extraction source")
        source_ref = capture["original_ref"]
        method = "utf8_identity"
        failure, text, truncated = None, "", False
        if source == "latest_extraction":
            latest = capture["extractions"][-1] if capture["extractions"] else None
            _check(latest is not None and latest["status"] == "succeeded", "invalid_artifact", "No successful supplied extraction is available")
            source_ref, method = latest["extracted_ref"], "supplied_text"
        row = db.read_artifact_version(source_ref["artifact_id"], source_ref["version"], actor, access=access)
        _check(row["project_id"] == capture["project_id"], "identity_mismatch", "Capture source must remain in its owning project")
        if row["descriptor"]["mime"] not in _TEXT_MIMES:
            failure = "unsupported_local_extraction"
        else:
            try:
                data = read_project_artifact(context, db, capture["project_id"], source_ref["artifact_id"], source_ref["version"])
                # Decode all bytes strictly before taking a scalar-safe UTF-8 prefix.
                text = data.decode("utf-8")
                _check("\0" not in text, "invalid_artifact", "Capture extraction cannot index NUL text")
                truncated = len(data) > MAX_CAPTURE_TEXT
                text = data[:MAX_CAPTURE_TEXT].decode("utf-8", errors="ignore")
            except (ArtifactConflict, OSError, UnicodeError):
                failure = "source_bytes_unavailable"
        if failure:
            source_ref, method, text, truncated = None, "none", "", False
        return _public(db.record_capture_processing(capture_id, actor,
            expected_extraction_sequence=expected_extraction_sequence, source_ref=source_ref,
            text_content=text, method=method, failure_code=failure, truncated=truncated, access=access))


def _terms(text):
    return list(dict.fromkeys(word for word in re.findall(r"[^\W_]+", text.casefold())
                              if word not in _STOP and 1 < len(word) <= 64))


def _rank(query, document):
    vocabulary = set(_terms(document)[:1024])
    matched, weight = [], 0.0
    for word in query:
        if word in vocabulary:
            matched.append(word)
            weight += 1
        elif len(word) >= 4:
            candidates = sorted(term for term in vocabulary if abs(len(term) - len(word)) <= 2 and term[:1] == word[:1])
            near = get_close_matches(word, candidates, n=1, cutoff=0.8)
            if near:
                matched.extend(near)
                weight += 0.7
    return weight / len(query), list(dict.fromkeys(matched))


def _excerpt(text, terms):
    folded = text.casefold()
    positions = [folded.find(term) for term in terms if folded.find(term) >= 0]
    start = max(0, min(positions, default=0) - 60)
    return text[start:start + 320]


def search_captures(context, db, project_id, *, query, limit=20, scan_limit=100):
    actor, access = source_authority(context, db)
    _check(type(query) is str and 0 < len(query) <= 512 and type(limit) is int and 1 <= limit <= 50,
           "invalid_artifact", "Search requires bounded text and result count")
    terms = _terms(query)
    _check(0 < len(terms) <= 32, "invalid_artifact", "Use one to thirty-two meaningful lexical terms")
    with access.guard(project_id, actor, "read"):
        documents, truncated = db.list_capture_search_documents(project_id, actor, scan_limit=scan_limit, access=access)
        matches = []
        for document in documents:
            capture = document["capture"]
            text = "\n".join([capture["annotation"], capture["source_url"] or "", document["text"]])
            score, matched = _rank(terms, text)
            if score:
                matches.append(_public({"capture": capture, "processing": document["processing"],
                    "consolidated_into": document["consolidated_into"], "score": round(score, 6),
                    "matched_terms": matched, "excerpt": _excerpt(text, matched)}))
        matches.sort(key=lambda row: (-row["score"], -row["capture"]["acquired_at"], row["capture"]["capture_id"]))
        return {"matches": matches[:limit], "search_mode": "lexical_fuzzy", "scanned": len(documents),
            "truncated": truncated or len(matches) > limit, "complete": False,
            "limitations": ["Local lexical overlap and limited spelling similarity; no semantic embeddings or synonym understanding.",
                "Only the newest selected scan window, 64 KiB indexed text and first 1024 distinct terms per capture are searched.",
                "Unprocessed, unavailable or stale extraction contributes metadata only; originals remain separate receipts."]}


def preview_capture_batch(context, db, project_id, *, batch_id, items):
    actor, access = source_authority(context, db)
    return db.preview_capture_batch(project_id, actor, batch_id=batch_id, items=items, access=access)


def commit_capture_batch(context, db, project_id, *, batch_id, items, preview_digest):
    actor, access = source_authority(context, db)
    return db.commit_capture_batch(project_id, actor, batch_id=batch_id, items=items,
                                   preview_digest=preview_digest, access=access)
