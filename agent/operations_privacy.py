"""Bounded logical erasure manifests with measured, deliberately partial closure.

Logical row deletion is not forensic media erasure. Mandatory recovery records,
backups, remote copies and frozen in-process prompts are never silently claimed
deleted. No retention scheduler or provider call is installed by this module.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time

from agent.operations_control import authority, digest, maintenance_lease, require

_LIMITATIONS = ["sqlite_free_pages_and_wal_not_forensically_erased", "backups_require_separate_expiry",
    "provider_copies_no_deletion_acknowledgment", "exports_and_external_caches_not_enumerated",
    "active_process_copies_require_session_end", "mandatory_recovery_journal_retained",
    "artifacts_and_mission_evidence_retained", "context_projections_and_session_activity_metadata_retained",
    "session_json_jsonl_and_request_dumps_retained",
    "durable_schedules_monitors_and_commitments_retained",
    "delegation_handoffs_roots_workspaces_retained",
    "bounded_service_source_receipt_blobs_and_channel_bindings_retained",
    "remote_harness_deletion_not_supported"]


def _memory_source(store, context, record_id):
    from agent.individual_memory_scope import checked_file
    from tools.workspace_manifest import _open_root
    require(store is not None and store._scope.assert_current() == context,
            "deletion_memory_scope_mismatch")
    directory = _open_root(store._scope.directory.parent.parent)
    try:
        for part in ("individual-memory", store.namespace_id):
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        state = checked_file(directory, "records.sqlite3")
        try:
            uri = f"file:/proc/self/fd/{directory}/records.sqlite3?mode=ro"
            with sqlite3.connect(uri, uri=True) as conn:
                conn.execute("PRAGMA query_only=ON")
                require(conn.execute("SELECT namespace_json FROM memory_meta WHERE singleton=1").fetchone()[0]
                        == store._scope.canonical_json, "deletion_memory_scope_mismatch")
                row = conn.execute("SELECT r.record_json FROM memory_records r JOIN memory_heads h "
                    "ON r.record_id=h.record_id AND r.version=h.version WHERE r.record_id=?", (record_id,)).fetchone()
                require(row is not None, "deletion_memory_record_missing")
                record = json.loads(row[0])
        finally:
            os.close(state)
    finally:
        os.close(directory)
    require(record["scope"] == "individual", "deletion_project_memory_unsupported")
    require(record["deletion_state"] == "present", "deletion_record_already_deleted")
    return {"record_id": record_id, "version": record["version"], "namespace_id": store.namespace_id}


def retention_inventory(db, context):
    sid, _ = authority(db, context)
    with db._runtime_read() as conn:
        counts = {name: conn.execute(f"SELECT COUNT(*) FROM {name} WHERE session_id=?", (sid,)).fetchone()[0]
                  for name in ("messages", "runtime_commands", "runtime_events", "runtime_context_projections",
                               "runtime_effects", "runtime_artifact_versions")}
        available = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        scoped_queries = {
            "bounded_service_pipelines": ("SELECT COUNT(*) FROM bounded_service_pipelines WHERE session_id=?", (sid,)),
            "bounded_service_stages": ("SELECT COUNT(*) FROM bounded_service_stages s JOIN bounded_service_pipelines p "
                                       "ON p.pipeline_id=s.pipeline_id WHERE p.session_id=?", (sid,)),
            "runtime_channel_bindings": ("SELECT COUNT(*) FROM runtime_channel_bindings WHERE session_id=?", (sid,)),
            "delegation_handoffs": ("SELECT COUNT(*) FROM delegation_handoffs WHERE parent_session_id=? OR child_session_id=?", (sid, sid)),
            "delegation_roots": ("SELECT COUNT(DISTINCT root_run_id) FROM delegation_handoffs "
                                 "WHERE parent_session_id=? OR child_session_id=?", (sid, sid)),
            "durable_schedule_versions": ("SELECT COUNT(*) FROM durable_schedule_versions WHERE session_id=?", (sid,)),
            "durable_occurrences": ("SELECT COUNT(*) FROM durable_occurrences WHERE session_id=?", (sid,)),
        }
        retained = {name: conn.execute(query, params).fetchone()[0]
                    for name, (query, params) in scoped_queries.items() if name in available}
    return {"schema_version": 1, "session_id": sid, "stores": counts,
        "retention": {"messages": "explicit_previewed_logical_erasure",
            "recovery_journal": "checkpoint_bounded_pruning_only",
            "effects": "retain_unresolved_and_dedup_evidence",
            "individual_memory": "explicit_record_purge_and_projection_rebuild",
            "artifacts": "retained_no_automatic_expiry", "logs": "existing_log_rotation_not_certified",
            "backups": "operator_managed_no_expiry_proof", "provider_copies": "unknown"},
        "retained_derived_store_counts": retained,
        "limitations": list(_LIMITATIONS), "complete_deletion_supported": False}


def _quiescent(db, sid, conn):
    require(not conn.execute("SELECT 1 FROM runtime_commands WHERE session_id=? "
        "AND status IN ('accepted','claimed') LIMIT 1", (sid,)).fetchone(), "deletion_unresolved_run")
    require(not conn.execute("SELECT 1 FROM runtime_effects WHERE session_id=? "
        "AND state NOT IN ('confirmed','failed') LIMIT 1", (sid,)).fetchone(), "deletion_unresolved_effect")
    snapshot = db._runtime_snapshot_on_conn(conn, sid)
    require(not snapshot["unresolved_invocations"], "deletion_unresolved_invocation")
    mission = conn.execute("SELECT record_json FROM runtime_missions WHERE session_id=?", (sid,)).fetchone()
    require(mission is None or json.loads(mission[0])["state"] in {"completed", "failed", "cancelled"},
            "deletion_unresolved_mission")
    available = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "delegation_handoffs" in available:
        require(not conn.execute("SELECT 1 FROM delegation_handoffs WHERE (parent_session_id=? OR child_session_id=?) "
            "AND state NOT IN ('completed','failed','cancelled','orphaned') LIMIT 1", (sid, sid)).fetchone(),
            "deletion_unresolved_delegation")
    if "bounded_service_pipelines" in available:
        require(not conn.execute("SELECT 1 FROM bounded_service_pipelines p WHERE p.session_id=? "
            "AND (COALESCE(json_type(p.manifest_json,'$.stages'),'missing') != 'array' "
            "OR json_array_length(p.manifest_json,'$.stages')=0 "
            "OR (SELECT COUNT(*) FROM bounded_service_stages s WHERE s.pipeline_id=p.pipeline_id) "
            "< json_array_length(p.manifest_json,'$.stages')) LIMIT 1", (sid,)).fetchone(),
            "deletion_unresolved_service_pipeline")


def _transcript_source(db, sid, conn):
    _quiescent(db, sid, conn)
    columns = [row[1] for row in conn.execute("PRAGMA table_info(messages)")]
    size_expression = "+".join(f'COALESCE(length(CAST("{name}" AS BLOB)),0)' for name in columns)
    size = conn.execute(f"SELECT COALESCE(SUM({size_expression}),0) FROM messages WHERE session_id=?", (sid,)).fetchone()[0]
    require(size <= 8 * 1024 * 1024, "deletion_scope_too_large")
    rows = conn.execute("SELECT * FROM messages WHERE session_id=? ORDER BY id LIMIT 1001", (sid,)).fetchall()
    require(len(rows) <= 1000, "deletion_scope_too_large")
    session = conn.execute("SELECT title,system_prompt,system_prompt_hash FROM sessions WHERE id=?", (sid,)).fetchone()
    messages = [{key: {"sqlite_blob": value.hex()} if isinstance(value, bytes) else value
                 for key, value in dict(row).items()} for row in rows]
    return {"message_count": len(rows), "source_digest": digest({"messages": messages,
                                                               "session_display": dict(session)})}


def preview_deletion(db, context, *, record_id=None, memory_store=None, expires_at=None):
    sid, actor = authority(db, context)
    expires_at = time.time() + 300 if expires_at is None else expires_at
    require(type(expires_at) in (int, float) and time.time() < expires_at <= time.time() + 301,
            "deletion_preview_expired")
    with db._runtime_read() as conn:
        _quiescent(db, sid, conn)
        revision = db._runtime_state_on_conn(conn, sid)["revision"]
        if record_id is None:
            source = _transcript_source(db, sid, conn)
            stores = ["messages_including_inactive", "message_fts_projections", "session_title_and_system_prompt",
                      "unreferenced_system_prompts"]
        else:
            source = _memory_source(memory_store, context, record_id)
            stores = ["individual_memory_all_versions", "individual_memory_conflicts", "individual_memory_markdown"]
    body = {"schema_version": 1, "kind": "individual_memory" if record_id else "transcript_payload",
        "actor": actor, "session_id": sid, "record_refs": [record_id or sid], "stores": stores,
        "requested": time.time(), "expires_at": expires_at, "before_revision": revision,
        "source": source, "acknowledgments": [], "limitations": list(_LIMITATIONS),
        "complete_deletion": False}
    # requested is informative and is kept stable when recomputing a preview.
    body["requested"] = expires_at - 300
    return {**body, "manifest_digest": digest(body)}


def _event(db, conn, sid, manifest, phase, generation, **extra):
    payload = {"manifest_digest": manifest["manifest_digest"], "stores": manifest["stores"],
               "record_refs": manifest["record_refs"], "complete_deletion": False, **extra}
    return db._append_runtime_event_on_conn(conn, sid, "operations.deletion_" + phase, payload, generation,
                                            operation_id=manifest["manifest_digest"])


def apply_deletion(db, context, manifest, *, authorization_digest, memory_store=None):
    require(isinstance(manifest, dict) and manifest.get("manifest_digest") == authorization_digest,
            "deletion_exact_authorization_required")
    record_id = manifest.get("source", {}).get("record_id")
    current = preview_deletion(db, context, record_id=record_id, memory_store=memory_store,
                               expires_at=manifest.get("expires_at"))
    require(current == manifest, "deletion_preview_changed")
    sid, _ = authority(db, context)
    with maintenance_lease(db, context) as (holder, generation):
        def begin(conn):
            db._runtime_fence_on_conn(conn, sid, holder, generation)
            require(db._runtime_state_on_conn(conn, sid)["revision"] == manifest["before_revision"],
                    "deletion_preview_changed")
            _quiescent(db, sid, conn)
            if record_id is None:
                require(_transcript_source(db, sid, conn) == manifest["source"], "deletion_preview_changed")
            _event(db, conn, sid, manifest, "requested", generation)
            if record_id is None:
                # FTS DELETE triggers use the old source content in this same
                # transaction. Do not VACUUM/reset leases or erase recovery rows.
                conn.execute("DELETE FROM messages WHERE session_id=?", (sid,))
                conn.execute("UPDATE sessions SET title=NULL,system_prompt=NULL,system_prompt_hash=NULL,"
                             "message_count=0 WHERE id=?", (sid,))
                db._delete_unreferenced_system_prompts(conn)
                _event(db, conn, sid, manifest, "finished", generation, outcome="logical_rows_deleted")
        db._execute_write(begin)
        if record_id is not None:
            _purge_memory(memory_store, record_id, manifest["source"]["version"])
            def finish(conn):
                db._runtime_fence_on_conn(conn, sid, holder, generation)
                _event(db, conn, sid, manifest, "finished", generation, outcome="logical_rows_deleted")
            db._execute_write(finish)
    return {**manifest, "acknowledgments": [{"store": name, "status": "logical_deletion_acknowledged"}
                                          for name in manifest["stores"]], "completed_at": time.time()}


def _purge_memory(store, record_id, version):
    # The owning store's checked dirfd, namespace, flock and SQLite transaction
    # remain authoritative; its projection sync runs after this commit. A crash
    # leaves a requested manifest and is inspectable, never a false full ack.
    with store._transaction(write=True) as conn:
        record = next((row for row in store._heads(conn) if row["record_id"] == record_id), None)
        require(record is not None and record["version"] == version and record["scope"] == "individual"
                and record["deletion_state"] == "present", "deletion_preview_changed")
        record.update(content=None, source_ref="deleted", author="deleted", deletion_state="deleted",
                      deleted_at=time.time(), updated_at=time.time())
        result = store._put(conn, record, record_id, version, store._meta(conn)["revision"] + 1)
        require(result["success"], "deletion_preview_changed")
        rows = conn.execute("SELECT version,record_json FROM memory_records WHERE record_id=?", (record_id,)).fetchall()
        for row in rows:
            old = json.loads(row["record_json"])
            old.update(content=None, source_ref="deleted", author="deleted", deletion_state="deleted",
                       deleted_at=record["deleted_at"])
            conn.execute("UPDATE memory_records SET record_json=? WHERE record_id=? AND version=?",
                         (json.dumps(old, sort_keys=True), record_id, row["version"]))
        conn.execute("DELETE FROM memory_conflicts WHERE record_id=?", (record_id,))


def privacy_qualification():
    return {"schema_version": 1, "sensitive_ingestion_certified": False,
        "chosen_encryption_scope": "authenticated_backup_envelope_only",
        "live_store_encryption": "not_configured_or_verified",
        "key_custody": "caller_supplied_external_key_no_provisioning",
        "threat_model": ["backup_ciphertext_loss_with_key_held_separately",
                         "tampered_backup_detected_before_restore",
                         "live_process_with_key_is_not_protected"],
        "blocking_gates": ["live_database_and_filesystem_encryption", "approved_key_custody_and_rotation",
            "full_store_backup_restore_drill", "remote_deletion_proof", "backup_expiry_proof",
            "portable_daemon_client_deployment_certification", "upgrade_downgrade_compatibility_drill"]}
