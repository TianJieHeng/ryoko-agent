"""BE08 context recovery references share the transcript's existing writer commit."""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
import hashlib
import json
import time
import uuid

from hermes_state_effects import _actor, _digest
from hermes_state_runtime import _identifier, _json, _require, _revision

MAX_CONTEXT_ANCHORS = 100


def _source_on_conn(conn, session_id, watermark):
    _require(type(watermark) is int and watermark >= 0, "invalid_context", "A source message watermark is required")
    digest, count, first = hashlib.sha256(), 0, None
    for row in conn.execute("SELECT * FROM messages WHERE session_id=? AND active=1 AND id<=? ORDER BY id", (session_id, watermark)):
        # Includes every provider sidecar and the original API-visible user bytes.
        encoded = json.dumps(dict(row), sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)
        digest.update(encoded.encode())
        digest.update(b"\n")
        first = row["id"] if first is None else first
        count += 1
    return {"message_watermark": watermark, "first_message_id": first, "source_count": count, "source_digest": digest.hexdigest()}


class SessionContextMixin:
    def context_sidecar_is_persisted(self, session_id, row_id, content, api_content):
        with self._runtime_read() as conn:
            row = conn.execute("SELECT content,api_content FROM messages WHERE session_id=? AND id=? AND active=1 AND role='user'",
                               (session_id, row_id)).fetchone()
            if row is None:
                return False
            return row["api_content"] == api_content if api_content is not None else row["content"] == self._encode_content(content)

    def capture_context_source(self, session_id, *, watermark):
        with self._runtime_read() as conn:
            self._runtime_session_on_conn(conn, session_id)
            return _source_on_conn(conn, session_id, watermark)

    def prepare_context_commit(self, session_id, actor, *, holder, generation, run_id, immutable_prefix_digest,
                               config_version, policy_version, fresh_context_versions=None, source=None,
                               fallback_state="none", system_prompt=None, project_access=None):
        actor = _actor(actor)
        _digest(immutable_prefix_digest, "immutable_prefix_digest")
        _identifier(config_version, "config_version")
        _identifier(policy_version, "policy_version")
        _require(fallback_state in {"none", "summary", "deterministic", "degraded"}, "invalid_context", "Unknown context fallback state")
        fresh = fresh_context_versions or {"cursor": "", "records": [], "invalidation_refs": []}
        _require(isinstance(fresh, dict) and set(fresh) <= {"cursor", "scope_key", "records", "invalidation_refs", "degraded"},
                 "invalid_context", "Fresh context must contain only version references")
        _require(isinstance(fresh.get("cursor", ""), str) and len(fresh.get("cursor", "")) <= 512,
                 "invalid_context", "Fresh context cursor exceeds bound")
        for name in ("records", "invalidation_refs"):
            refs = fresh.get(name, [])
            _require(isinstance(refs, list) and len(refs) <= 64, "context_reference_limit", "Fresh context reference bound exceeded")
            for ref in refs:
                _require(isinstance(ref, dict) and set(ref) <= {"record_id", "version", "source_ref", "namespace_id", "deletion_state", "scope"},
                         "invalid_context", "Context versions cannot contain memory payloads")
                _identifier(ref.get("record_id"), "record_id")
                _require(type(ref.get("version")) is int and ref["version"] > 0, "invalid_context", "Context record version is required")
        _require(len(_json(fresh).encode()) <= 16384, "context_reference_limit", "Fresh reference metadata exceeds bound")
        with self._runtime_read() as conn:
            sid = self._effect_run_on_conn(conn, session_id, actor, run_id, holder, generation, dispatch=True)
            snapshot = self._runtime_snapshot_on_conn(conn, sid)
            old = conn.execute("SELECT projection_json FROM runtime_context_projections WHERE session_id=?", (session_id,)).fetchone()
            prior = json.loads(old[0]) if old else None
            required_refs = {item["anchor_id"] for item in (prior or {}).get("anchors", []) if item["kind"] == "evidence"}
            refs = set(required_refs)
            refs.update(ref["source_ref"] for ref in fresh.get("records", []) if isinstance(ref.get("source_ref"), str))
            for row in conn.execute("SELECT metadata_json FROM runtime_artifact_versions WHERE session_id=? AND run_id=? AND principal_id=? "
                "AND profile_id=? AND agent_id=? AND artifact_kind='project_artifact' AND publication_state='committed'",
                (sid, run_id, actor["principal_id"], actor["profile_id"], actor["agent_id"])):
                required_refs.update(json.loads(row[0]).get("source_refs", []))
            refs.update(required_refs)
            anchors, projects = [], set()
            for anchor_id in sorted(refs):
                row = conn.execute("SELECT project_id,record_json FROM artifact_evidence_anchors WHERE anchor_id=?", (anchor_id,)).fetchone()
                _require(row is not None or anchor_id not in required_refs, "context_source_changed", "A protected evidence anchor is missing")
                if row is not None:
                    record = json.loads(row["record_json"])
                    anchors.append({"kind": "evidence", "anchor_id": anchor_id, "project_id": row["project_id"], "source_version": record["source_version"]})
                    projects.add(row["project_id"])
            approval_ids = {item["anchor_id"] for item in (prior or {}).get("anchors", []) if item["kind"] == "approval"}
            approvals = conn.execute("SELECT approval_id,approval_digest FROM runtime_effect_approvals WHERE session_id=? AND run_id=? "
                "AND principal_id=? AND profile_id=? AND agent_id=?", (sid, run_id, actor["principal_id"], actor["profile_id"], actor["agent_id"])).fetchall()
            approval_ids.update(row[0] for row in approvals)
            for approval_id in sorted(approval_ids):
                row = self._effect_approval_on_conn(conn, approval_id, actor)
                anchors.append({"kind": "approval", "anchor_id": approval_id, "approval_digest": row["approval_digest"]})
            _require(len(anchors) <= MAX_CONTEXT_ANCHORS, "context_reference_limit", "Protected anchors exceed bound; retain original context")
            source = source or _source_on_conn(conn, session_id, conn.execute("SELECT COALESCE(MAX(id),0) FROM messages WHERE session_id=? AND active=1", (session_id,)).fetchone()[0])
            result = {"schema_version": 1, "session_id": session_id, "runtime_session_id": sid, "actor": actor,
                      "holder": holder, "generation": generation, "run_id": run_id, "expected_revision": snapshot["revision"],
                      "immutable_prefix_digest": immutable_prefix_digest, "config_version": config_version,
                      "policy_version": policy_version, "fresh_context_versions": fresh, "source": source,
                      "source_seq_range": {"start": (prior["source_seq_range"]["end"] + 1) if prior else 0, "end": snapshot["revision"]},
                      "anchors": anchors, "sidecar_version": 1, "fallback_state": fallback_state,
                      "system_prompt": system_prompt, "project_access": project_access}
        # Never acquire a project writer lock while a SessionDB snapshot is held.
        for project_id in projects:
            self._artifact_access(actor, project_id, project_access, "read")
        return result

    @contextmanager
    def context_commit_guard(self, metadata):
        with ExitStack() as stack:
            if metadata:
                for project in sorted({item["project_id"] for item in metadata["anchors"] if item["kind"] == "evidence"}):
                    stack.enter_context(metadata["project_access"].guard(project, metadata["actor"], "read"))
            yield

    def _validate_context_commit_on_conn(self, conn, session_id, metadata):
        _require(isinstance(metadata, dict) and metadata.get("schema_version") == 1 and metadata.get("session_id") == session_id,
                 "invalid_context", "Context commit belongs to another session or version")
        sid = self._effect_run_on_conn(conn, session_id, metadata["actor"], metadata["run_id"], metadata["holder"], metadata["generation"], dispatch=True)
        _require(sid == metadata["runtime_session_id"], "identity_mismatch", "Context root changed")
        state = self._runtime_state_on_conn(conn, sid)
        _revision(state["revision"], metadata["expected_revision"])
        from agent.provider_capabilities import decode_protocol_sidecar
        for row in conn.execute("SELECT provider_sidecar FROM messages WHERE session_id=? AND active=1 AND provider_sidecar IS NOT NULL", (session_id,)):
            decode_protocol_sidecar(row[0])
        source = metadata["source"]
        _require(_source_on_conn(conn, session_id, source["message_watermark"]) == source,
                 "context_source_changed", "Held source bytes changed; retain the original context")
        for anchor in metadata["anchors"]:
            if anchor["kind"] == "approval":
                row = self._effect_approval_on_conn(conn, anchor["anchor_id"], metadata["actor"])
                _require(row["approval_digest"] == anchor["approval_digest"], "context_source_changed", "Exact approval binding changed")
            else:
                row = conn.execute("SELECT project_id,record_json FROM artifact_evidence_anchors WHERE anchor_id=?", (anchor["anchor_id"],)).fetchone()
                _require(row is not None and row[0] == anchor["project_id"] and json.loads(row[1])["source_version"] == anchor["source_version"],
                         "context_source_changed", "Exact evidence anchor changed")
                self._artifact_access(metadata["actor"], anchor["project_id"], metadata["project_access"], "read")
        return sid

    def _commit_context_projection_on_conn(self, conn, session_id, metadata):
        sid = metadata["runtime_session_id"]
        snapshot = self._runtime_snapshot_on_conn(conn, sid)
        checkpoint = {"schema_version": 1, "config_version": metadata["config_version"], "policy_version": metadata["policy_version"],
                      "runtime_version": "be08.v1", "prompt_projection_version": "1",
                      **{key: snapshot[key] for key in ("artifacts", "outstanding_requests", "unresolved_effects", "unresolved_invocations")}}
        receipt = self._publish_runtime_checkpoint_on_conn(conn, sid, checkpoint, holder=metadata["holder"], generation=metadata["generation"],
                                                          expected_revision=metadata["expected_revision"], included_seq=metadata["expected_revision"])
        projection = {key: metadata[key] for key in ("schema_version", "source_seq_range", "immutable_prefix_digest", "fresh_context_versions", "anchors", "sidecar_version", "fallback_state", "source")}
        projection.update(projection_id=uuid.uuid4().hex, checkpoint_id=receipt["checkpoint_id"], included_seq=receipt["included_seq"],
                          published_seq=receipt["published_seq"], generation=metadata["generation"])
        conn.execute("INSERT INTO runtime_context_projections(session_id,projection_id,schema_version,generation,checkpoint_id,included_seq,projection_json,created_at) "
            "VALUES(?,?,1,?,?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET projection_id=excluded.projection_id,schema_version=1,"
            "generation=excluded.generation,checkpoint_id=excluded.checkpoint_id,included_seq=excluded.included_seq,projection_json=excluded.projection_json,created_at=excluded.created_at",
            (session_id, projection["projection_id"], metadata["generation"], receipt["checkpoint_id"], receipt["included_seq"], _json(projection), time.time()))
        checkpoint_row = conn.execute("SELECT checkpoint_json FROM runtime_checkpoints WHERE session_id=?", (sid,)).fetchone()
        saved_checkpoint = json.loads(checkpoint_row[0])
        saved_checkpoint["context_projection_ref"] = {"schema_version": 1, "projection_id": projection["projection_id"],
            "session_id": session_id, "included_seq": receipt["included_seq"]}
        conn.execute("UPDATE runtime_checkpoints SET checkpoint_json=? WHERE session_id=? AND checkpoint_id=?",
                     (_json(saved_checkpoint), sid, receipt["checkpoint_id"]))
        if metadata["system_prompt"] is not None:
            _require(hashlib.sha256(metadata["system_prompt"].encode()).hexdigest() == metadata["immutable_prefix_digest"],
                     "context_source_changed", "System prefix differs from its projection digest")
            conn.execute("UPDATE sessions SET system_prompt_hash=?,system_prompt=NULL WHERE id=?",
                         (self._store_system_prompt(conn, metadata["system_prompt"]), session_id))
            self._delete_unreferenced_system_prompts(conn)
        return projection

    def read_context_projection(self, session_id, actor):
        actor = _actor(actor)
        with self._runtime_read() as conn:
            sid = self._runtime_session_on_conn(conn, session_id)
            state = self._runtime_state_on_conn(conn, sid)
            _require(state is not None and all(state[key] == value for key, value in actor.items()), "identity_mismatch", "Context belongs to another actor")
            row = conn.execute("SELECT * FROM runtime_context_projections WHERE session_id=?", (session_id,)).fetchone()
            _require(row is None or row["schema_version"] == 1, "unsupported_schema", "Unsupported context projection")
            return json.loads(row["projection_json"]) if row else None
