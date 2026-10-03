"""Bounded capture projections and atomic, reversible metadata review on SessionDB.

Original artifact bytes and acquisition records never move or disappear. Filing
is a label with independently checked project grants, not a cross-store move.
"""
from __future__ import annotations

import hashlib
import json
import time

from hermes_state_artifacts import _check, _json, _project_guarded, _ref
from hermes_state_effects import _actor, _digest
from hermes_state_runtime import _identifier

MAX_CAPTURE_TEXT = 65536


def _hash(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _sequence(capture):
    return capture["extractions"][-1]["sequence"] if capture["extractions"] else 0


def _consolidated(conn, capture_id):
    row = conn.execute("SELECT consolidated_into FROM artifact_capture_consolidations "
        "WHERE capture_id=? ORDER BY revision DESC LIMIT 1", (capture_id,)).fetchone()
    return row[0] if row else None


def _processing(conn, capture):
    row = conn.execute("SELECT record_json,text_content FROM artifact_capture_indexes WHERE capture_id=?",
                       (capture["capture_id"],)).fetchone()
    if row is None:
        return {"status": "not_indexed", "method": "none", "source_ref": None,
            "extraction_sequence": _sequence(capture), "indexed_characters": 0,
            "truncated": False, "failure_code": None, "indexed_at": None}, ""
    record = json.loads(row[0])
    if record["extraction_sequence"] != _sequence(capture):
        return {**record, "status": "stale"}, ""
    return record, row[1]


class SessionCapturesMixin:
    def _capture_inspect_on_conn(self, conn, capture_id, actor, access):
        row = self._source_row_on_conn(conn, "artifact_captures", "capture_id", capture_id, actor, access)
        capture = self._capture_result_on_conn(conn, row)
        processing, _text = _processing(conn, capture)
        filings = [{"revision": item[0], "filed_project_id": item[1], "created_at": item[2]} for item in
            conn.execute("SELECT revision,project_id,created_at FROM artifact_capture_filings WHERE capture_id=? ORDER BY revision", (capture_id,))]
        consolidations = [{"revision": item[0], "consolidated_into": item[1], "created_at": item[2]} for item in
            conn.execute("SELECT revision,consolidated_into,created_at FROM artifact_capture_consolidations WHERE capture_id=? ORDER BY revision", (capture_id,))]
        return {"capture": capture, "processing": processing, "consolidated_into": _consolidated(conn, capture_id),
                "filing_history": filings, "consolidation_history": consolidations}

    @_project_guarded("artifact_captures", "capture_id")
    def get_capture_processing(self, capture_id, actor, *, access):
        with self._runtime_read() as conn:
            return self._capture_inspect_on_conn(conn, capture_id, _actor(actor), access)

    @_project_guarded("artifact_captures", "capture_id")
    def record_capture_processing(self, capture_id, actor, *, expected_extraction_sequence, source_ref,
                                  text_content, method, failure_code, truncated, access):
        """Extraction attempt and search projection share one write and extraction CAS."""
        actor = _actor(actor)
        _check(type(expected_extraction_sequence) is int and 0 <= expected_extraction_sequence < 32,
               "invalid_artifact", "Expected a bounded extraction sequence with an attempt remaining")
        _check(type(text_content) is str and len(text_content.encode()) <= MAX_CAPTURE_TEXT,
               "invalid_artifact", "Capture index text exceeds its UTF-8 byte bound")
        _check(method in {"utf8_identity", "supplied_text", "none"} and type(truncated) is bool,
               "invalid_artifact", "Unknown local processing method")
        if source_ref is not None:
            source_ref = _ref(source_ref)
        if failure_code is not None:
            _identifier(failure_code, "failure_code")
        _check((source_ref is not None) == (failure_code is None) and
               (source_ref is not None or (not text_content and method == "none")),
               "invalid_artifact", "Only successful extraction contributes indexed text")
        def write(conn):
            row = self._source_row_on_conn(conn, "artifact_captures", "capture_id", capture_id, actor, access, "write")
            capture = self._capture_result_on_conn(conn, row)
            _check(_sequence(capture) == expected_extraction_sequence, "revision_conflict", "Capture extraction changed")
            if source_ref:
                source = self._artifact_row_on_conn(conn, source_ref["artifact_id"], source_ref["version"], actor, access)
                _check(source["project_id"] == capture["project_id"], "identity_mismatch", "Indexed source must belong to capture project")
            sequence, now = expected_extraction_sequence + 1, time.time()
            extraction = {"status": "succeeded" if source_ref else "unavailable", "extracted_ref": source_ref, "failure_code": failure_code}
            conn.execute("INSERT INTO artifact_capture_extractions(capture_id,sequence,record_json,created_at) VALUES(?,?,?,?)",
                         (capture_id, sequence, _json(extraction), now))
            record = {"status": "indexed" if source_ref else "metadata_only", "method": method,
                "source_ref": source_ref, "extraction_sequence": sequence, "indexed_characters": len(text_content),
                "truncated": truncated, "failure_code": failure_code, "indexed_at": now}
            conn.execute("INSERT INTO artifact_capture_indexes(capture_id,record_json,text_content,created_at) VALUES(?,?,?,?) "
                         "ON CONFLICT(capture_id) DO UPDATE SET record_json=excluded.record_json,text_content=excluded.text_content,created_at=excluded.created_at",
                         (capture_id, _json(record), text_content, now))
            return self._capture_inspect_on_conn(conn, capture_id, actor, access)
        return self._execute_write(write)

    @_project_guarded()
    def list_capture_search_documents(self, project_id, actor, *, scan_limit, access):
        actor = self._artifact_access(actor, project_id, access, "read")
        _check(type(scan_limit) is int and 1 <= scan_limit <= 500, "invalid_artifact", "Capture search scan must be bounded")
        with self._runtime_read() as conn:
            rows = conn.execute("SELECT * FROM artifact_captures WHERE project_id=? ORDER BY created_at DESC,capture_id LIMIT ?",
                                (project_id, scan_limit + 1)).fetchall()
            documents = []
            for row in rows[:scan_limit]:
                # Source rows and referenced artifacts are re-authorized; an index never grants access.
                self._artifact_access(actor, row["project_id"], access, "read")
                capture = self._capture_result_on_conn(conn, row)
                original = capture["original_ref"]
                source = self._artifact_row_on_conn(conn, original["artifact_id"], original["version"], actor, access)
                _check(source["project_id"] == project_id, "identity_mismatch", "Capture original project changed")
                processing, text = _processing(conn, capture)
                if processing["source_ref"] and processing["status"] != "stale":
                    reference = processing["source_ref"]
                    extracted = self._artifact_row_on_conn(conn, reference["artifact_id"], reference["version"], actor, access)
                    _check(extracted["project_id"] == project_id, "identity_mismatch", "Indexed extraction project changed")
                documents.append({"capture": capture, "processing": processing, "text": text,
                                  "consolidated_into": _consolidated(conn, capture["capture_id"])})
            return documents, len(rows) > scan_limit

    def _capture_batch_input(self, project_id, actor, batch_id, items, access):
        self._artifact_access(actor, project_id, access, "write")
        _identifier(batch_id, "batch_id")
        _check(type(items) is list and 1 <= len(items) <= 25, "invalid_artifact", "Select one to twenty-five captures explicitly")
        seen = set()
        for item in items:
            _check(type(item) is dict and set(item) == {"capture_id", "expected_revision", "filed_project_id", "consolidated_into"},
                   "invalid_artifact", "Batch items require exact review fields")
            key = _identifier(item["capture_id"], "capture_id")
            _check(key not in seen, "invalid_artifact", "Capture selection contains duplicates")
            seen.add(key)
            _check(type(item["expected_revision"]) is int and 0 <= item["expected_revision"] < 128,
                   "invalid_artifact", "Capture revision is outside retained history bounds")
            if item["filed_project_id"] is not None:
                self._artifact_access(actor, item["filed_project_id"], access, "write")
            if item["consolidated_into"] is not None:
                _identifier(item["consolidated_into"], "consolidated_into")
        return _hash({"actor": actor, "project_id": project_id, "batch_id": batch_id, "items": items})

    def _capture_batch_preview_on_conn(self, conn, project_id, actor, batch_id, items, access):
        reviewed = []
        desired = {item["capture_id"]: item["consolidated_into"] for item in items}
        for item in items:
            capture = self._capture_result_on_conn(conn, self._source_row_on_conn(conn, "artifact_captures", "capture_id", item["capture_id"], actor, access, "write"))
            _check(capture["project_id"] == project_id, "identity_mismatch", "Batch selection belongs to another project")
            _check(capture["revision"] == item["expected_revision"], "revision_conflict", "Capture filing changed; preview again")
            original = capture["original_ref"]
            source = self._artifact_row_on_conn(conn, original["artifact_id"], original["version"], actor, access)
            digest = json.loads(source["descriptor_json"])["sha256"]
            target_id = item["consolidated_into"]
            if target_id is not None:
                _check(target_id != capture["capture_id"], "invalid_artifact", "Capture cannot consolidate into itself")
                target = self._capture_result_on_conn(conn, self._source_row_on_conn(conn, "artifact_captures", "capture_id", target_id, actor, access, "write"))
                _check(target["project_id"] == project_id, "identity_mismatch", "Consolidation must stay in the same owning project")
                _check(desired.get(target_id, _consolidated(conn, target_id)) is None,
                       "invalid_artifact", "Consolidation targets must be independent originals, without chains or cycles")
                # A root with linked members cannot itself be consolidated, even if a future batch would undo them.
                linked = conn.execute("SELECT 1 FROM artifact_capture_consolidations c WHERE c.consolidated_into=? "
                    "AND c.revision=(SELECT MAX(n.revision) FROM artifact_capture_consolidations n WHERE n.capture_id=c.capture_id) LIMIT 1",
                    (capture["capture_id"],)).fetchone()
                _check(linked is None, "invalid_artifact", "Unlink existing members before consolidating their root")
                ref = target["original_ref"]
                artifact = self._artifact_row_on_conn(conn, ref["artifact_id"], ref["version"], actor, access)
                _check(json.loads(artifact["descriptor_json"])["sha256"] == digest,
                       "duplicate_mismatch", "Only exact original-byte duplicates may be consolidated")
            reviewed.append({**item, "previous_filed_project_id": capture["filed_project_id"],
                             "previous_consolidated_into": _consolidated(conn, capture["capture_id"]), "original_sha256": digest})
        digest = _hash({"actor": actor, "project_id": project_id, "batch_id": batch_id, "items": reviewed})
        return {"batch_id": batch_id, "project_id": project_id, "preview_digest": digest, "items": reviewed,
                "originals_preserved": True, "scope": "capture_metadata_only"}

    @_project_guarded()
    def preview_capture_batch(self, project_id, actor, *, batch_id, items, access):
        actor = _actor(actor)
        self._capture_batch_input(project_id, actor, batch_id, items, access)
        with self._runtime_read() as conn:
            return self._capture_batch_preview_on_conn(conn, project_id, actor, batch_id, items, access)

    @_project_guarded()
    def commit_capture_batch(self, project_id, actor, *, batch_id, items, preview_digest, access):
        actor = _actor(actor)
        request_digest = self._capture_batch_input(project_id, actor, batch_id, items, access)
        _digest(preview_digest, "preview_digest")
        def write(conn):
            existing = conn.execute("SELECT * FROM artifact_capture_batches WHERE batch_id=?", (batch_id,)).fetchone()
            if existing:
                _check(existing["project_id"] == project_id and existing["actor_json"] == _json(actor)
                       and existing["request_digest"] == request_digest and existing["preview_digest"] == preview_digest,
                       "idempotency_conflict", "Batch identity is already bound to different exact changes")
                # Replayed receipts are not a grant bypass, including source and duplicate targets.
                for item in items:
                    for key in (item["capture_id"], item["consolidated_into"]):
                        if key is not None:
                            row = self._source_row_on_conn(conn, "artifact_captures", "capture_id", key, actor, access, "write")
                            _check(row["project_id"] == project_id, "identity_mismatch", "Capture project changed")
                return {**json.loads(existing["result_json"]), "replayed": True}
            preview = self._capture_batch_preview_on_conn(conn, project_id, actor, batch_id, items, access)
            _check(preview["preview_digest"] == preview_digest, "revision_conflict", "Capture batch changed; preview again")
            self._artifact_quota(conn, "artifact_capture_batches")
            now, results = time.time(), []
            for item in items:
                revision = item["expected_revision"] + 1
                conn.execute("INSERT INTO artifact_capture_filings(capture_id,revision,project_id,created_at) VALUES(?,?,?,?)",
                             (item["capture_id"], revision, item["filed_project_id"], now))
                conn.execute("UPDATE artifact_captures SET filed_project_id=?,filing_revision=? WHERE capture_id=?",
                             (item["filed_project_id"], revision, item["capture_id"]))
                conn.execute("INSERT INTO artifact_capture_consolidations(capture_id,revision,consolidated_into,created_at) VALUES(?,?,?,?)",
                             (item["capture_id"], revision, item["consolidated_into"], now))
                results.append({"capture_id": item["capture_id"], "revision": revision,
                                "filed_project_id": item["filed_project_id"], "consolidated_into": item["consolidated_into"]})
            result = {"batch_id": batch_id, "project_id": project_id, "preview_digest": preview_digest,
                      "items": results, "originals_preserved": True, "replayed": False, "scope": "capture_metadata_only"}
            conn.execute("INSERT INTO artifact_capture_batches(batch_id,project_id,actor_json,request_digest,preview_digest,result_json,created_at) VALUES(?,?,?,?,?,?,?)",
                         (batch_id, project_id, _json(actor), request_digest, preview_digest, _json(result), now))
            return result
        return self._execute_write(write)
