"""Immutable BE07 artifact/source metadata on the existing SessionDB writer.

Blobs stay in the BE06 checked store. ProjectsDB owns live grants and selected
project references; artifact_heads alone owns same-artifact version CAS. Filing
across those databases is deliberately not advertised as an atomic transaction.
"""
from __future__ import annotations

from contextlib import nullcontext
from functools import wraps
import inspect
import json
import math
import time
from urllib.parse import parse_qsl, urlsplit

from hermes_state_delivery import _descriptor
from hermes_state_effects import _actor, _digest, effect_digest
from hermes_state_runtime import RuntimeStoreError, _identifier

MAX_ARTIFACT_ROWS = 65536
MAX_SOURCE_ROWS = 16384
MAX_ARTIFACT_PAGE = 100


class ArtifactStoreError(RuntimeStoreError):
    pass


def _check(condition, code, message):
    if not condition:
        raise ArtifactStoreError(code, message)


def _json(value):
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError, RecursionError) as exc:
        raise ArtifactStoreError("invalid_artifact", "Metadata must be finite JSON") from exc
    _check(len(encoded.encode()) <= 32768, "artifact_metadata_limit", "Use bounded source and artifact references")
    return encoded


def _version(value, *, optional=False):
    if value is None and optional:
        return None
    _check(type(value) is int and 0 < value < 2**31, "invalid_artifact", "Expected a positive bounded version")
    return value


def _ref(value):
    _check(isinstance(value, dict) and set(value) == {"artifact_id", "version"}, "invalid_artifact", "Expected exact artifact reference")
    return {"artifact_id": _identifier(value["artifact_id"], "artifact_id"), "version": _version(value["version"])}


def _text(value, maximum=4096):
    _check(isinstance(value, str) and len(value.encode()) <= maximum, "invalid_artifact", "Text exceeds metadata bound")
    return value


def _time(value, *, optional=False):
    if value is None and optional:
        return None
    _check(type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 253402300799,
           "invalid_artifact", "Expected a finite UTC time")
    return float(value)


def _metadata(value):
    value = dict(value or {})
    allowed = {"parent_version", "branch_of", "derived_from", "locked_sections", "source_refs", "provenance",
               "validation", "approval_status", "approval_id"}
    _check(set(value) <= allowed, "invalid_artifact", "Unknown artifact metadata fields")
    value.setdefault("parent_version", None)
    value.setdefault("branch_of", None)
    _version(value["parent_version"], optional=True)
    if value["branch_of"] is not None:
        value["branch_of"] = _ref(value["branch_of"])
    for key in ("derived_from", "locked_sections", "source_refs"):
        value.setdefault(key, [])
        _check(isinstance(value[key], list) and len(value[key]) <= 64, "invalid_artifact", "Metadata reference list exceeds bound")
    value["derived_from"] = [_ref(item) for item in value["derived_from"]]
    for item in value["locked_sections"]:
        _check(isinstance(item, dict) and set(item) == {"anchor", "sha256"}, "invalid_artifact", "Locked sections need exact anchor and digest")
        _identifier(item["anchor"], "anchor")
        _digest(item["sha256"], "sha256")
    for item in value["source_refs"]:
        _identifier(item, "source_ref")
    value.setdefault("provenance", {"kind": "generated"})
    _check(isinstance(value["provenance"], dict) and set(value["provenance"]) <= {"kind", "source_ref"}
           and value["provenance"].get("kind") in {"source", "generated", "revision", "capture", "template"},
           "invalid_artifact", "Provenance must distinguish source and generated content")
    if "source_ref" in value["provenance"]:
        _identifier(value["provenance"]["source_ref"], "source_ref")
    value.setdefault("validation", {"status": "not_checked"})
    _check(isinstance(value["validation"], dict) and set(value["validation"]) <= {"status", "receipt_ref"}
           and value["validation"].get("status") in {"not_checked", "passed", "failed"},
           "invalid_artifact", "Validation needs a typed receipt status")
    if "receipt_ref" in value["validation"]:
        _identifier(value["validation"]["receipt_ref"], "receipt_ref")
    value.setdefault("approval_status", "unreviewed")
    _check(value["approval_status"] in {"unreviewed", "approved", "rejected"}, "invalid_artifact", "Unknown approval status")
    if value.get("approval_id") is not None:
        _identifier(value["approval_id"], "approval_id")
    _json(value)
    return value


def _project_guarded(table=None, identifier=None):
    """Always acquire the project grant fence before the SessionDB transaction."""
    def decorate(method):
        signature = inspect.signature(method)
        @wraps(method)
        def guarded(self, *args, **kwargs):
            bound = signature.bind(self, *args, **kwargs)
            bound.apply_defaults()
            values = bound.arguments
            actor, access = _actor(values["actor"]), values.get("access")
            project_id = values.get("project_id")
            if project_id is None and table is not None:
                key = _identifier(values[identifier], identifier)
                with self._read_ctx() as conn:
                    row = conn.execute(f"SELECT project_id FROM {table} WHERE {identifier}=? LIMIT 1", (key,)).fetchone()
                _check(row is not None, "artifact_not_found" if identifier == "artifact_id" else "source_not_found", "Record does not exist")
                project_id = row[0]
            permission = "read" if method.__name__.startswith(("read_", "get_", "list_")) else "write"
            if project_id is not None:
                self._artifact_access(actor, project_id, access, permission)
            guard = access.guard(project_id, actor, permission) if project_id is not None else nullcontext()
            with guard:
                return method(self, *args, **kwargs)
        return guarded
    return decorate


class SessionArtifactsMixin:
    def _artifact_access(self, actor, project_id, access, permission):
        from agent.project_context import ProjectAccess
        actor = _actor(actor)
        _identifier(project_id, "project_id")
        _check(type(access) is ProjectAccess, "project_access_required", "Live project authorization is required")
        _check(access.context.profile_home == str(self.db_path.parent.resolve()),
               "identity_mismatch", "Project authorization belongs to another profile store")
        access.assert_access(project_id, actor, permission)
        return actor

    @staticmethod
    def _artifact_quota(conn, table, maximum=MAX_SOURCE_ROWS):
        _check(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] < maximum,
               "artifact_storage_limit", "Metadata capacity reached; retained source history cannot be discarded")

    def _artifact_row_on_conn(self, conn, artifact_id, version, actor, access, *, reserved=False):
        _identifier(artifact_id, "artifact_id")
        _version(version)
        row = conn.execute("SELECT * FROM runtime_artifact_versions WHERE artifact_id=? AND version=?", (artifact_id, version)).fetchone()
        _check(row is not None, "artifact_not_found", "Artifact version does not exist")
        if row["project_id"] is None:
            _check(all(row[key] == actor[key] for key in ("principal_id", "profile_id", "agent_id")),
                   "identity_mismatch", "Private runtime result belongs to another actor")
        else:
            self._artifact_access(actor, row["project_id"], access, "read")
        _check(reserved or row["publication_state"] == "committed", "artifact_not_ready", "Artifact bytes are not committed")
        return row

    @staticmethod
    def _artifact_result_on_conn(conn, row):
        head = conn.execute("SELECT version,revision FROM artifact_heads WHERE artifact_id=?", (row["artifact_id"],)).fetchone()
        invalid = conn.execute("SELECT 1 FROM artifact_derivations WHERE artifact_id=? AND version=? AND invalidated_at IS NOT NULL LIMIT 1",
                               (row["artifact_id"], row["version"])).fetchone()
        return {key: row[key] for key in ("artifact_id", "version", "project_id", "artifact_kind", "publication_state", "disposition", "created_at")} | {
            "descriptor": json.loads(row["descriptor_json"]), "metadata": json.loads(row["metadata_json"]),
            "owner_actor": {key: row[key] for key in ("principal_id", "profile_id", "agent_id")},
            "head_version": head[0] if head else None, "head_revision": head[1] if head else 0,
            "derived_validity": "stale" if invalid else "current"}

    @_project_guarded()
    def reserve_artifact_version(self, session_id, actor, *, holder, generation, run_id, command_id, project_id,
                                 artifact_id, request_id, parent_version, content_sha256, size, mime, access,
                                 metadata=None, expected_head_version=None):
        actor = self._artifact_access(actor, project_id, access, "write")
        for key, value in (("artifact_id", artifact_id), ("request_id", request_id), ("command_id", command_id), ("mime", mime)):
            _identifier(value, key)
        _version(parent_version, optional=True)
        expected_head_version = parent_version if expected_head_version is None else _version(expected_head_version)
        _digest(content_sha256, "content_sha256")
        _check(type(size) is int and 0 <= size <= 8 * 1024 * 1024, "invalid_artifact", "Artifact byte size exceeds supported bounds")
        metadata = _metadata(metadata)
        _check(metadata["parent_version"] in (None, parent_version), "invalid_artifact", "Parent metadata changed")
        metadata["parent_version"] = parent_version
        proposal = dict(project_id=project_id, artifact_id=artifact_id, parent_version=parent_version,
                        expected_head_version=expected_head_version, content_sha256=content_sha256, size=size, mime=mime, metadata=metadata)
        encoded = _json(proposal)
        def write(conn):
            self._artifact_access(actor, project_id, access, "write")
            sid = self._effect_run_on_conn(conn, session_id, actor, run_id, holder, generation, dispatch=True)
            command = self._runtime_command_on_conn(conn, sid, command_id)
            _check(command is not None and command["run_id"] == run_id, "claim_conflict", "Artifact command differs from accepted run")
            existing = conn.execute("SELECT * FROM runtime_artifact_versions WHERE session_id=? AND run_id=? AND reservation_key=?",
                                    (sid, run_id, request_id)).fetchone()
            if existing:
                _check(existing["reservation_json"] == encoded, "idempotency_conflict", "Artifact request changed after version allocation")
                return self._artifact_result_on_conn(conn, existing)
            root = conn.execute("SELECT * FROM runtime_artifact_versions WHERE artifact_id=? LIMIT 1", (artifact_id,)).fetchone()
            _check(root is None or (root["project_id"] == project_id and root["artifact_kind"] == "project_artifact"),
                   "identity_mismatch", "Artifact identity belongs to another project or private result")
            if parent_version is not None:
                self._artifact_row_on_conn(conn, artifact_id, parent_version, actor, access)
            self._artifact_quota(conn, "runtime_artifact_versions", MAX_ARTIFACT_ROWS)
            version = conn.execute("SELECT COALESCE(MAX(version),0)+1 FROM runtime_artifact_versions WHERE artifact_id=?", (artifact_id,)).fetchone()[0]
            conn.execute("INSERT INTO runtime_artifact_versions(artifact_id,version,session_id,command_id,run_id,principal_id,profile_id,agent_id,"
                "descriptor_json,created_at,artifact_kind,publication_state,project_id,metadata_json,reservation_key,reservation_json,disposition) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,'project_artifact','reserved',?,?,?,?,'reserved')",
                (artifact_id, version, sid, command_id, run_id, actor["principal_id"], actor["profile_id"], actor["agent_id"],
                 "{}", time.time(), project_id, _json(metadata), request_id, encoded))
            return self._artifact_result_on_conn(conn, self._artifact_row_on_conn(conn, artifact_id, version, actor, access, reserved=True))
        return self._execute_write(write)

    def _invalidate_artifact_derivatives_on_conn(self, conn, artifact_id, version):
        # Dependency edges only point to committed older inputs. Recursive UNION is
        # cycle-safe and updates validity, never content bytes or provenance.
        conn.execute("WITH RECURSIVE stale(artifact_id,version) AS ("
            "SELECT artifact_id,version FROM artifact_derivations WHERE source_artifact_id=? AND source_version<>? "
            "UNION SELECT d.artifact_id,d.version FROM artifact_derivations d JOIN stale s "
            "ON d.source_artifact_id=s.artifact_id AND d.source_version=s.version) "
            "UPDATE artifact_derivations SET invalidated_at=COALESCE(invalidated_at,?) WHERE (artifact_id,version) IN (SELECT * FROM stale)",
            (artifact_id, version, time.time()))

    @_project_guarded()
    def register_artifact_version(self, session_id, actor, *, holder, generation, run_id, command_id, descriptor,
                                  project_id, metadata, expected_head_version=None, publish_head=True, access=None):
        actor = self._artifact_access(actor, project_id, access, "write")
        descriptor, metadata = _descriptor(descriptor), _metadata(metadata)
        _version(expected_head_version, optional=True)
        _check(type(publish_head) is bool, "invalid_artifact", "Head publication must be explicit")
        def write(conn):
            self._artifact_access(actor, project_id, access, "write")
            sid = self._effect_run_on_conn(conn, session_id, actor, run_id, holder, generation, dispatch=True)
            row = self._artifact_row_on_conn(conn, descriptor["artifact_id"], descriptor["version"], actor, access, reserved=True)
            _check(row["artifact_kind"] == "project_artifact" and row["project_id"] == project_id and row["session_id"] == sid
                   and row["run_id"] == run_id and row["command_id"] == command_id
                   and all(row[key] == actor[key] for key in ("principal_id", "profile_id", "agent_id")),
                   "identity_mismatch", "Artifact publication differs from its immutable reservation")
            proposal = json.loads(row["reservation_json"])
            _check(metadata["parent_version"] in (None, proposal["parent_version"]),
                   "idempotency_conflict", "Artifact parent changed after preparation")
            metadata["parent_version"] = proposal["parent_version"]
            _check(metadata == proposal["metadata"] and expected_head_version == proposal["expected_head_version"]
                   and descriptor["producing_run"] == run_id and all(descriptor[key] == proposal[source]
                   for key, source in (("sha256", "content_sha256"), ("size", "size"), ("mime", "mime"))),
                   "idempotency_conflict", "Artifact bytes, metadata or base changed after preparation")
            digest = effect_digest({"descriptor": descriptor, "metadata": metadata, "publish_head": publish_head})
            if row["publication_state"] == "committed":
                _check(row["commit_digest"] == digest, "idempotency_conflict", "Committed artifact version is immutable")
                return self._artifact_result_on_conn(conn, row)
            effects = conn.execute("SELECT * FROM runtime_effects WHERE session_id=? AND run_id=? AND principal_id=? AND profile_id=? "
                "AND agent_id=? AND operation_type='project_artifact_publish' AND state='confirmed' AND target_ref=? AND input_digest=?",
                (sid, run_id, actor["principal_id"], actor["profile_id"], actor["agent_id"],
                 f"artifact:{descriptor['artifact_id']}:{descriptor['version']}", effect_digest(descriptor))).fetchall()
            effect = next((item for item in effects if json.loads(item["input_ref_json"]) == descriptor), None)
            _check(effect is not None, "artifact_publication_unconfirmed", "Exact checked bytes need a confirmed publication effect")
            _check(metadata.get("approval_id") in (None, effect["approval_id"]),
                   "approval_mismatch", "Metadata cannot attach a different action's approval")
            stored_metadata = dict(metadata)
            stored_metadata["publication_approval_id"] = effect["approval_id"]
            if metadata["approval_status"] == "approved":
                _check(effect["approval_id"] is not None and metadata.get("approval_id") in (None, effect["approval_id"]),
                       "approval_mismatch", "Approved content needs its exact publication approval")
                approved = self._effect_approval_on_conn(conn, effect["approval_id"], actor)
                _check(approved["status"] == "consumed", "approval_mismatch", "Publication approval has not been consumed")
            for ref in metadata["derived_from"]:
                source = self._artifact_row_on_conn(conn, ref["artifact_id"], ref["version"], actor, access)
                _check(source["project_id"] == project_id, "identity_mismatch", "Derivatives must remain in their authorized project")
                source_head = conn.execute("SELECT version FROM artifact_heads WHERE artifact_id=?", (ref["artifact_id"],)).fetchone()
                source_stale = conn.execute("SELECT 1 FROM artifact_derivations WHERE artifact_id=? AND version=? AND invalidated_at IS NOT NULL LIMIT 1",
                                            (ref["artifact_id"], ref["version"])).fetchone()
                stale = time.time() if source_stale or (source_head and source_head[0] != ref["version"]) else None
                self._artifact_quota(conn, "artifact_derivations", MAX_ARTIFACT_ROWS * 4)
                conn.execute("INSERT INTO artifact_derivations(artifact_id,version,source_artifact_id,source_version,invalidated_at) VALUES(?,?,?,?,?)",
                    (descriptor["artifact_id"], descriptor["version"], ref["artifact_id"], ref["version"], stale))
            for anchor_id in metadata["source_refs"]:
                anchor = conn.execute("SELECT project_id FROM artifact_evidence_anchors WHERE anchor_id=?", (anchor_id,)).fetchone()
                _check(anchor is not None and anchor[0] == project_id, "evidence_not_found", "Artifact source anchor is outside its project")
            current = conn.execute("SELECT * FROM artifact_heads WHERE artifact_id=?", (descriptor["artifact_id"],)).fetchone()
            head_version = current["version"] if current else None
            disposition = "head" if publish_head and head_version == expected_head_version else "branch"
            if disposition == "head":
                conn.execute("INSERT INTO artifact_heads(artifact_id,version,revision,project_id,updated_at) VALUES(?,?,1,?,?) "
                    "ON CONFLICT(artifact_id) DO UPDATE SET version=excluded.version,revision=artifact_heads.revision+1,updated_at=excluded.updated_at",
                    (descriptor["artifact_id"], descriptor["version"], project_id, time.time()))
                self._invalidate_artifact_derivatives_on_conn(conn, descriptor["artifact_id"], descriptor["version"])
            conn.execute("UPDATE runtime_artifact_versions SET descriptor_json=?,metadata_json=?,publication_state='committed',commit_digest=?,disposition=? "
                         "WHERE artifact_id=? AND version=?", (_json(descriptor), _json(stored_metadata), digest, disposition, descriptor["artifact_id"], descriptor["version"]))
            return self._artifact_result_on_conn(conn, self._artifact_row_on_conn(conn, descriptor["artifact_id"], descriptor["version"], actor, access))
        return self._execute_write(write)

    @_project_guarded("runtime_artifact_versions", "artifact_id")
    def read_artifact_reservation(self, artifact_id, version, actor, *, access):
        actor = _actor(actor)
        with self._runtime_read() as conn:
            row = self._artifact_row_on_conn(conn, artifact_id, version, actor, access, reserved=True)
            _check(all(row[key] == actor[key] for key in ("principal_id", "profile_id", "agent_id")),
                   "identity_mismatch", "Only the reserving actor can recover a prepared publication")
            return {**self._artifact_result_on_conn(conn, row),
                    **{key: row[key] for key in ("session_id", "run_id", "command_id")}}

    @_project_guarded("runtime_artifact_versions", "artifact_id")
    def read_artifact_version(self, artifact_id, version, actor, *, access=None):
        actor = _actor(actor)
        with self._runtime_read() as conn:
            return self._artifact_result_on_conn(conn, self._artifact_row_on_conn(conn, artifact_id, version, actor, access))

    @_project_guarded("runtime_artifact_versions", "artifact_id")
    def get_artifact_head(self, artifact_id, actor, *, access):
        actor = _actor(actor)
        with self._runtime_read() as conn:
            head = conn.execute("SELECT * FROM artifact_heads WHERE artifact_id=?", (_identifier(artifact_id, "artifact_id"),)).fetchone()
            _check(head is not None, "artifact_not_found", "Artifact has no committed head")
            return self._artifact_result_on_conn(conn, self._artifact_row_on_conn(conn, artifact_id, head["version"], actor, access))

    @_project_guarded("runtime_artifact_versions", "artifact_id")
    def restore_artifact_head(self, artifact_id, version, actor, *, expected_revision, access):
        actor = _actor(actor)
        _check(type(expected_revision) is int and expected_revision > 0, "invalid_artifact", "Expected an exact head revision")
        def write(conn):
            row = self._artifact_row_on_conn(conn, artifact_id, version, actor, access)
            self._artifact_access(actor, row["project_id"], access, "write")
            head = conn.execute("SELECT * FROM artifact_heads WHERE artifact_id=?", (artifact_id,)).fetchone()
            _check(head is not None and head["revision"] == expected_revision, "revision_conflict", "Artifact head changed")
            conn.execute("UPDATE artifact_heads SET version=?,revision=revision+1,updated_at=? WHERE artifact_id=?", (version, time.time(), artifact_id))
            self._invalidate_artifact_derivatives_on_conn(conn, artifact_id, version)
            return self._artifact_result_on_conn(conn, row)
        return self._execute_write(write)

    @_project_guarded("runtime_artifact_versions", "artifact_id")
    def list_artifact_versions(self, artifact_id, actor, *, access, limit=100):
        _identifier(artifact_id, "artifact_id")
        return self._list_artifact_rows("artifact_id", artifact_id, actor, access, limit)

    @_project_guarded()
    def list_project_artifacts(self, project_id, actor, *, access, limit=100):
        self._artifact_access(actor, project_id, access, "read")
        return self._list_artifact_rows("project_id", project_id, actor, access, limit)

    def _list_artifact_rows(self, column, value, actor, access, limit):
        actor = _actor(actor)
        _check(type(limit) is int and 1 <= limit <= MAX_ARTIFACT_PAGE, "invalid_artifact", "Artifact page must be bounded")
        with self._runtime_read() as conn:
            rows = conn.execute(f"SELECT * FROM runtime_artifact_versions WHERE {column}=? AND publication_state='committed' "
                                "ORDER BY created_at DESC,artifact_id,version DESC LIMIT ?", (value, limit)).fetchall()
            return [self._artifact_result_on_conn(conn, self._artifact_row_on_conn(conn, row["artifact_id"], row["version"], actor, access)) for row in rows]

    def _source_row_on_conn(self, conn, table, key, value, actor, access, permission="read"):
        row = conn.execute(f"SELECT * FROM {table} WHERE {key}=?", (_identifier(value, key),)).fetchone()
        _check(row is not None, "source_not_found", "Source metadata does not exist")
        self._artifact_access(actor, row["project_id"], access, permission)
        return row

    @staticmethod
    def _source_result(row):
        return {**json.loads(row["record_json"]), "owner_actor": {key: row[key] for key in ("principal_id", "profile_id", "agent_id")}}

    @_project_guarded()
    def create_capture(self, actor, *, capture_id, project_id, original_ref, source_url=None, acquired_at,
                       annotation="", suggested_project_id=None, access):
        actor = self._artifact_access(actor, project_id, access, "write")
        original_ref = _ref(original_ref)
        _identifier(capture_id, "capture_id")
        if source_url is not None:
            _text(source_url, 4096)
            url = urlsplit(source_url)
            _check(not any(ord(character) < 32 for character in source_url)
                   and url.scheme in {"https", "http"} and url.hostname and not url.username and not url.password
                   and not ({key.lower() for key, _ in parse_qsl(url.query)} & {"token", "access_token", "key", "api_key", "password", "signature"}),
                   "invalid_artifact", "Capture URLs cannot contain credentials")
        if suggested_project_id is not None:
            self._artifact_access(actor, suggested_project_id, access, "read")
        record = dict(capture_id=capture_id, project_id=project_id, original_ref=original_ref, source_url=source_url,
                      acquired_at=_time(acquired_at), annotation=_text(annotation), suggested_project_id=suggested_project_id)
        def write(conn):
            self._artifact_access(actor, project_id, access, "write")
            original = self._artifact_row_on_conn(conn, original_ref["artifact_id"], original_ref["version"], actor, access)
            _check(original["project_id"] == project_id, "identity_mismatch", "Capture original must belong to its owning project")
            existing = conn.execute("SELECT * FROM artifact_captures WHERE capture_id=?", (capture_id,)).fetchone()
            if existing:
                _check(existing["record_json"] == _json(record), "idempotency_conflict", "Capture source or annotation changed")
                return self._capture_result_on_conn(conn, existing)
            self._artifact_quota(conn, "artifact_captures")
            conn.execute("INSERT INTO artifact_captures(capture_id,project_id,principal_id,profile_id,agent_id,record_json,created_at) VALUES(?,?,?,?,?,?,?)",
                (capture_id, project_id, actor["principal_id"], actor["profile_id"], actor["agent_id"], _json(record), time.time()))
            return self._capture_result_on_conn(conn, self._source_row_on_conn(conn, "artifact_captures", "capture_id", capture_id, actor, access))
        return self._execute_write(write)

    def _capture_result_on_conn(self, conn, row):
        extraction = [{"sequence": item[0], **json.loads(item[1]), "created_at": item[2]} for item in conn.execute(
            "SELECT sequence,record_json,created_at FROM artifact_capture_extractions WHERE capture_id=? ORDER BY sequence", (row["capture_id"],))]
        return {**self._source_result(row), "filed_project_id": row["filed_project_id"], "revision": row["filing_revision"], "extractions": extraction}

    @_project_guarded("artifact_captures", "capture_id")
    def get_capture(self, capture_id, actor, *, access):
        actor = _actor(actor)
        with self._runtime_read() as conn:
            return self._capture_result_on_conn(conn, self._source_row_on_conn(conn, "artifact_captures", "capture_id", capture_id, actor, access))

    @_project_guarded()
    def list_captures(self, project_id, actor, *, access, limit=100):
        return self._list_sources("artifact_captures", project_id, actor, access, limit)

    @_project_guarded("artifact_captures", "capture_id")
    def record_capture_extraction(self, capture_id, actor, *, status, extracted_ref=None, failure_code=None, access):
        actor = _actor(actor)
        _check(status in {"succeeded", "failed", "unavailable"}, "invalid_artifact", "Unknown extraction status")
        _check((status == "succeeded") == (extracted_ref is not None), "invalid_artifact", "Successful extraction needs an artifact reference")
        if extracted_ref is not None:
            extracted_ref = _ref(extracted_ref)
        if failure_code is not None:
            _identifier(failure_code, "failure_code")
        record = dict(status=status, extracted_ref=extracted_ref, failure_code=failure_code)
        def write(conn):
            row = self._source_row_on_conn(conn, "artifact_captures", "capture_id", capture_id, actor, access, "write")
            if extracted_ref:
                extracted = self._artifact_row_on_conn(conn, extracted_ref["artifact_id"], extracted_ref["version"], actor, access)
                _check(extracted["project_id"] == row["project_id"], "identity_mismatch", "Extraction must belong to the original capture project")
            seq = conn.execute("SELECT COALESCE(MAX(sequence),0)+1 FROM artifact_capture_extractions WHERE capture_id=?", (capture_id,)).fetchone()[0]
            _check(seq <= 32, "artifact_storage_limit", "Extraction attempt bound reached; original remains available")
            conn.execute("INSERT INTO artifact_capture_extractions(capture_id,sequence,record_json,created_at) VALUES(?,?,?,?)", (capture_id, seq, _json(record), time.time()))
            return self._capture_result_on_conn(conn, row)
        return self._execute_write(write)

    @_project_guarded("artifact_captures", "capture_id")
    def file_capture(self, capture_id, actor, *, filed_project_id, expected_revision, access):
        actor = _actor(actor)
        _check(type(expected_revision) is int and expected_revision >= 0, "invalid_artifact", "Expected capture filing revision")
        def write(conn):
            row = self._source_row_on_conn(conn, "artifact_captures", "capture_id", capture_id, actor, access, "write")
            if filed_project_id is not None:
                self._artifact_access(actor, filed_project_id, access, "write")
            _check(row["filing_revision"] == expected_revision, "revision_conflict", "Capture filing changed")
            _check(expected_revision < 128, "artifact_storage_limit", "Capture filing history bound reached")
            revision = expected_revision + 1
            conn.execute("INSERT INTO artifact_capture_filings(capture_id,revision,project_id,created_at) VALUES(?,?,?,?)", (capture_id, revision, filed_project_id, time.time()))
            conn.execute("UPDATE artifact_captures SET filed_project_id=?,filing_revision=? WHERE capture_id=?", (filed_project_id, revision, capture_id))
            return self._capture_result_on_conn(conn, self._source_row_on_conn(conn, "artifact_captures", "capture_id", capture_id, actor, access))
        return self._execute_write(write)

    @_project_guarded()
    def create_template(self, actor, *, template_id, version, project_id, baseline_ref, structure, style, assets,
                        slots, exclusions, parent_version=None, access):
        actor = self._artifact_access(actor, project_id, access, "write")
        _identifier(template_id, "template_id")
        _version(version)
        _version(parent_version, optional=True)
        baseline_ref = _ref(baseline_ref)
        for value in (structure, assets, slots, exclusions):
            _check(isinstance(value, list) and len(value) <= 64, "invalid_artifact", "Template components must be explicit bounded lists")
        _check(isinstance(style, dict) and all(isinstance(key, str) and isinstance(value, str) for key, value in style.items()),
               "invalid_artifact", "Template style must contain explicit string rules")
        _check(all(isinstance(item, str) for item in [*structure, *exclusions]), "invalid_artifact", "Structure and exclusions are explicit strings")
        for slot in slots:
            _check(isinstance(slot, dict) and set(slot) == {"name", "purpose", "required"}
                   and isinstance(slot["name"], str) and isinstance(slot["purpose"], str) and type(slot["required"]) is bool,
                   "invalid_artifact", "Template slots need name, purpose and required flag")
        assets = [_ref(item) for item in assets]
        record = dict(template_id=template_id, version=version, project_id=project_id, baseline_ref=baseline_ref,
                      structure=structure, style=style, assets=assets, slots=slots, exclusions=exclusions, parent_version=parent_version)
        encoded = _json(record)
        def write(conn):
            self._artifact_access(actor, project_id, access, "write")
            for ref in [baseline_ref, *assets]:
                self._artifact_row_on_conn(conn, ref["artifact_id"], ref["version"], actor, access)
            root = conn.execute("SELECT project_id FROM artifact_templates WHERE template_id=? LIMIT 1", (template_id,)).fetchone()
            _check(root is None or root[0] == project_id, "identity_mismatch", "Template identity belongs to another project")
            if parent_version is not None:
                parent = conn.execute("SELECT project_id FROM artifact_templates WHERE template_id=? AND version=?", (template_id, parent_version)).fetchone()
                _check(parent is not None and parent[0] == project_id and parent_version < version, "invalid_artifact", "Template parent must be an earlier immutable baseline")
            existing = conn.execute("SELECT * FROM artifact_templates WHERE template_id=? AND version=?", (template_id, version)).fetchone()
            if existing:
                _check(existing["record_json"] == encoded, "idempotency_conflict", "Template version is immutable")
                return self._source_result(existing)
            self._artifact_quota(conn, "artifact_templates")
            conn.execute("INSERT INTO artifact_templates(template_id,version,project_id,principal_id,profile_id,agent_id,record_json,created_at) VALUES(?,?,?,?,?,?,?,?)",
                         (template_id, version, project_id, actor["principal_id"], actor["profile_id"], actor["agent_id"], encoded, time.time()))
            return {**record, "owner_actor": actor}
        return self._execute_write(write)

    @_project_guarded("artifact_templates", "template_id")
    def get_template(self, template_id, actor, *, version=None, access):
        actor = _actor(actor)
        _version(version, optional=True)
        with self._runtime_read() as conn:
            row = conn.execute("SELECT * FROM artifact_templates WHERE template_id=? "
                               "AND (? IS NULL OR version=?) ORDER BY version DESC LIMIT 1",
                               (_identifier(template_id, "template_id"), version, version)).fetchone()
            _check(row is not None, "source_not_found", "Template version does not exist")
            self._artifact_access(actor, row["project_id"], access, "read")
            return self._template_result_on_conn(conn, row, actor, access)

    def _template_result_on_conn(self, conn, row, actor, access):
        result = self._source_result(row)
        for ref in [result["baseline_ref"], *result["assets"]]:
            self._artifact_row_on_conn(conn, ref["artifact_id"], ref["version"], actor, access)
        return result

    @_project_guarded()
    def list_templates(self, project_id, actor, *, access, limit=100):
        return self._list_sources("artifact_templates", project_id, actor, access, limit)

    @_project_guarded()
    def create_evidence_anchor(self, actor, *, anchor_id, project_id, kind, source_ref, source_version, range_ref,
                               captured_at, authority, validity="unverified", fresh_until=None, annotation="", access):
        actor = self._artifact_access(actor, project_id, access, "write")
        _identifier(anchor_id, "anchor_id")
        _identifier(source_version, "source_version")
        _check(kind in {"source_span", "source_id", "decision", "constraint", "approval", "artifact_version"}, "invalid_artifact", "Unknown evidence kind")
        _check(authority in {"observed", "source_claim", "user_approved", "inferred"}, "invalid_artifact", "Unknown evidence authority")
        _check(validity in {"current", "stale", "revoked", "unverified"}, "invalid_artifact", "Unknown evidence validity")
        _check(isinstance(source_ref, dict) and set(source_ref) in ({"artifact_id", "version"}, {"capture_id"}, {"approval_id"}),
               "invalid_artifact", "Evidence source must name a retained immutable record")
        if "artifact_id" in source_ref:
            source_ref = _ref(source_ref)
            _check(source_version == str(source_ref["version"]), "invalid_artifact", "Source version does not match artifact version")
        else:
            _identifier(next(iter(source_ref.values())), "source_ref")
        if range_ref is not None:
            _check(isinstance(range_ref, dict) and set(range_ref) == {"unit", "start", "end"}
                   and range_ref["unit"] in {"line", "byte", "section"}, "invalid_artifact", "Evidence range needs explicit units")
            if range_ref["unit"] in {"line", "byte"}:
                _check(type(range_ref["start"]) is int and type(range_ref["end"]) is int and 0 <= range_ref["start"] <= range_ref["end"],
                       "invalid_artifact", "Evidence range is reversed or invalid")
            else:
                _identifier(range_ref["start"], "start")
                _identifier(range_ref["end"], "end")
        _check(kind != "source_span" or range_ref is not None, "invalid_artifact", "Source spans require a range")
        _check(kind != "approval" or "approval_id" in source_ref, "invalid_artifact", "Approval anchors require an exact approval record")
        record = dict(anchor_id=anchor_id, project_id=project_id, kind=kind, source_ref=source_ref, source_version=source_version,
                      range_ref=range_ref, captured_at=_time(captured_at), authority=authority, validity=validity,
                      fresh_until=_time(fresh_until, optional=True), annotation=_text(annotation))
        encoded = _json(record)
        def write(conn):
            self._artifact_access(actor, project_id, access, "write")
            source_descriptor = None
            if "artifact_id" in source_ref:
                source = self._artifact_row_on_conn(conn, source_ref["artifact_id"], source_ref["version"], actor, access)
                _check(source["project_id"] == project_id, "identity_mismatch", "Evidence artifact is outside this project")
                source_descriptor = json.loads(source["descriptor_json"])
            elif "capture_id" in source_ref:
                source = self._source_row_on_conn(conn, "artifact_captures", "capture_id", source_ref["capture_id"], actor, access)
                _check(source["project_id"] == project_id, "identity_mismatch", "Evidence capture is outside this project")
                original_ref = json.loads(source["record_json"])["original_ref"]
                _check(source_version == str(original_ref["version"]), "invalid_artifact", "Capture evidence version differs from retained original")
                original = self._artifact_row_on_conn(conn, original_ref["artifact_id"], original_ref["version"], actor, access)
                source_descriptor = json.loads(original["descriptor_json"])
            else:
                approval = self._effect_approval_on_conn(conn, source_ref["approval_id"], actor)
                associated = conn.execute("SELECT 1 FROM runtime_artifact_versions WHERE project_id=? AND publication_state='committed' "
                    "AND json_extract(metadata_json,'$.publication_approval_id')=? LIMIT 1",
                    (project_id, source_ref["approval_id"])).fetchone()
                _check(associated is not None and approval["status"] == "consumed", "approval_mismatch", "Approval anchor needs consumed publication authority in this project")
            if range_ref is not None and range_ref["unit"] == "byte":
                _check(source_descriptor is not None and range_ref["end"] <= source_descriptor["size"],
                       "invalid_artifact", "Evidence byte span exceeds retained source bytes")
            existing = conn.execute("SELECT * FROM artifact_evidence_anchors WHERE anchor_id=?", (anchor_id,)).fetchone()
            if existing:
                _check(existing["record_json"] == encoded, "idempotency_conflict", "Evidence anchors are immutable")
                return self._evidence_result_on_conn(conn, existing)
            self._artifact_quota(conn, "artifact_evidence_anchors")
            conn.execute("INSERT INTO artifact_evidence_anchors(anchor_id,project_id,principal_id,profile_id,agent_id,record_json,created_at) VALUES(?,?,?,?,?,?,?)",
                (anchor_id, project_id, actor["principal_id"], actor["profile_id"], actor["agent_id"], encoded, time.time()))
            return self._evidence_result_on_conn(conn, self._source_row_on_conn(conn, "artifact_evidence_anchors", "anchor_id", anchor_id, actor, access))
        return self._execute_write(write)

    def _evidence_result_on_conn(self, conn, row):
        result = self._source_result(row)
        effective = result["validity"]
        if effective == "current" and result["fresh_until"] is not None and result["fresh_until"] <= time.time():
            effective = "stale"
        ref = result["source_ref"]
        if effective == "current" and "artifact_id" in ref:
            head = conn.execute("SELECT version FROM artifact_heads WHERE artifact_id=?", (ref["artifact_id"],)).fetchone()
            stale = conn.execute("SELECT 1 FROM artifact_derivations WHERE artifact_id=? AND version=? AND invalidated_at IS NOT NULL LIMIT 1",
                                 (ref["artifact_id"], ref["version"])).fetchone()
            if (head and head[0] != ref["version"]) or stale:
                effective = "stale"
        if effective == "current" and result["range_ref"] is not None and result["range_ref"]["unit"] in {"line", "section"}:
            effective = "unverified"  # Metadata alone cannot prove extracted line/section membership.
        return {**result, "effective_validity": effective, "grants_execution": False}

    @_project_guarded("artifact_evidence_anchors", "anchor_id")
    def get_evidence_anchor(self, anchor_id, actor, *, access):
        actor = _actor(actor)
        with self._runtime_read() as conn:
            return self._evidence_result_on_conn(conn, self._source_row_on_conn(conn, "artifact_evidence_anchors", "anchor_id", anchor_id, actor, access))

    @_project_guarded()
    def list_evidence_anchors(self, project_id, actor, *, access, limit=100):
        return self._list_sources("artifact_evidence_anchors", project_id, actor, access, limit)

    def _list_sources(self, table, project_id, actor, access, limit):
        actor = self._artifact_access(actor, project_id, access, "read")
        _check(type(limit) is int and 1 <= limit <= MAX_ARTIFACT_PAGE, "invalid_artifact", "Source page must be bounded")
        with self._runtime_read() as conn:
            rows = conn.execute(f"SELECT * FROM {table} WHERE project_id=? ORDER BY created_at DESC LIMIT ?", (project_id, limit)).fetchall()
            serializers = {"artifact_captures": self._capture_result_on_conn,
                           "artifact_evidence_anchors": self._evidence_result_on_conn,
                           "artifact_templates": lambda _conn, row: self._template_result_on_conn(_conn, row, actor, access)}
            return [serializers[table](conn, row) for row in rows]
