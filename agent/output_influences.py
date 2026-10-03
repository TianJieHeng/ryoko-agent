"""Versioned input references and acknowledged controls, never causal explanations.

Fresh context is the only enumerated coverage. No prompt prefix or old message
is rewritten; local ignore/correction directives enter a future user sidecar.
Personal-harness packs without a verified record contract cannot be mutated.
"""
from __future__ import annotations

import hashlib
import json
import time
from contextlib import nullcontext

from agent.result_artifacts import artifact_actor
from hermes_state_runtime import RuntimeStoreError

MAX_CONTROLS = 64


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def require(condition, code, message):
    if not condition:
        raise RuntimeStoreError(code, message)


def _authority(agent):
    from agent.memory_router import assert_memory_owner
    context = agent.runtime_context
    assert_memory_owner(context)
    manager = getattr(agent, "_memory_manager", None)
    require(manager is not None, "memory_not_initialized", "Owned memory is not initialized")
    manager.assert_owner()
    db, actor = agent._session_db, artifact_actor(context)
    require(db.get_session_model_config_value(agent.session_id, "agent_identity") == context.identity.to_record(),
            "identity_mismatch", "Output context belongs to another session identity")
    return db, actor, manager


def _project(agent, project_id, operation="read"):
    from agent.project_context import project_access
    return (project_access(agent.runtime_context).guard(project_id, artifact_actor(agent.runtime_context), operation)
            if project_id else nullcontext())


def _load_context(conn, agent, actor, run_id):
    row = conn.execute("SELECT record_json FROM runtime_output_contexts WHERE session_id=? AND run_id=? AND actor_json=?",
                       (agent.session_id, run_id, canonical(actor))).fetchone()
    require(row is not None, "output_context_unavailable", "This output has no recorded fresh-context references")
    return json.loads(row[0])


def inspect_output(agent, run_id=None):
    db, actor, manager = _authority(agent)
    with db._runtime_read() as conn:
        rows = conn.execute("SELECT run_id,record_json FROM runtime_output_contexts WHERE session_id=? AND actor_json=? ORDER BY created_at DESC LIMIT 20",
                            (agent.session_id, canonical(actor))).fetchall()
        latest = rows[0]["run_id"] if rows else None
        if run_id is None:
            return {"outputs": [{"run_id": row["run_id"], "context_sha256": digest(json.loads(row["record_json"]))} for row in rows],
                    "backend": manager.backend, "complete": False,
                    "unavailable_reason": None if rows else "no_verified_output_context"}
        record = _load_context(conn, agent, actor, run_id)
    with _project(agent, record["project_id"]):
        namespace = manager.store.namespace_id if manager.store is not None else None
        require(record["namespace_id"] == namespace, "identity_mismatch", "Output memory namespace changed")
        return {**record, "context_sha256": digest(record), "latest": latest == run_id,
                "causal_explanation": False, "historical_context_enumerated": False}


def _controls(db, agent, actor):
    with db._runtime_read() as conn:
        rows = conn.execute("SELECT record_json FROM runtime_context_controls WHERE actor_json=? ORDER BY created_at LIMIT 1025",
                            (canonical(actor),)).fetchall()
    require(len(rows) <= 1024, "context_control_capacity", "Owner control history bound exceeded")
    return [record for row in rows if (record := json.loads(row[0]))["scope"] != "response"
            or record["session_id"] == agent.session_id]


def inspect_control(agent, control_id):
    db, actor, _manager = _authority(agent)
    records = _controls(db, agent, actor)
    row = next((record for record in records if record["control_id"] == control_id), None)
    require(row is not None, "context_control_unavailable", "Owned context control is unavailable")
    with _project(agent, row["project_id"]):
        return _receipt(row)


def _receipt(row):
    return {key: row[key] for key in ("control_id", "run_id", "action", "scope", "project_id", "status",
        "acknowledged_version", "applied_run_id", "current_output_changed", "current_run_application", "deletion_semantics")}


def control_output(agent, *, control_id, run_id, context_sha256, record_id, expected_version,
                   namespace_id, action, scope, project_id=None, content=None):
    db, actor, manager = _authority(agent)
    require(manager.backend == "builtin" and manager.store is not None and not manager.disabled,
            "memory_operation_unsupported", "This backend has no verified versioned control contract")
    require(action in {"ignore", "correct", "remove"} and scope in {"response", "project", "general"},
            "context_control_invalid", "Unknown output context action or scope")
    require((scope == "project") == (project_id is not None), "memory_scope_mismatch", "Project scope needs its exact project")
    require((action == "correct") == (isinstance(content, str) and 0 < len(content.encode()) <= 4096),
            "context_control_invalid", "Only a correction accepts bounded replacement text")
    require(action != "remove" or scope != "response", "memory_scope_mismatch", "Use ignore for a response-only change")
    request = dict(control_id=control_id, run_id=run_id, context_sha256=context_sha256, record_id=record_id,
                   expected_version=expected_version, namespace_id=namespace_id, action=action, scope=scope,
                   project_id=project_id, content=content)
    identity = digest(request)
    with _project(agent, project_id, "write"):
        existing = next((row for row in _controls(db, agent, actor) if row["control_id"] == control_id), None)
        if existing:
            require(existing["request_sha256"] == identity, "idempotency_conflict", "Control identity names different input")
            return _receipt(existing)
        output = inspect_output(agent, run_id)
        require(output["latest"] and output["context_sha256"] == context_sha256,
                "output_context_stale", "Inspect the latest exact output before changing its influences")
        ref = next((ref for ref in output["references"] if ref["record_id"] == record_id and ref["version"] == expected_version), None)
        require(ref is not None and ref["namespace_id"] == namespace_id == manager.store.namespace_id,
                "identity_mismatch", "Exact supplied record and namespace required")
        if ref["scope"].startswith("project:"):
            require(project_id == ref["scope"][8:] or scope == "response", "memory_scope_mismatch", "Record is scoped to another project")
        current = manager.store.read_record(record_id)
        require(current["version"] == expected_version and current["deletion_state"] == "present",
                "version_conflict", "The supplied memory version is no longer current")
        if action in {"correct", "remove"} and scope != "response":
            wanted = "individual" if scope == "general" else "project:" + project_id
            require(current["scope"] == wanted, "memory_scope_mismatch", "Durable mutation must match the record's existing scope; use ignore here or create a scoped preference")
            require(action != "correct" or current["kind"] != "procedure_reference", "memory_operation_unsupported",
                    "Procedure references must be changed through canonical workflow controls")
        active = getattr(agent, "_active_runtime_run", None)
        # Once a provider request exists, a current-run rewrite is necessarily late.
        row = {**request, "session_id": agent.session_id, "request_sha256": identity, "status": "queued_next_turn", "acknowledged_version": None,
               "applied_run_id": None, "current_output_changed": False,
               "current_run_application": "late_not_applied" if active is not None else "not_applied",
               "deletion_semantics": "none"}
        def insert(conn):
            db._mission_owner_on_conn(conn, agent.session_id, actor)
            latest = conn.execute("SELECT run_id,record_json FROM runtime_output_contexts WHERE session_id=? AND actor_json=? ORDER BY created_at DESC LIMIT 1",
                                  (agent.session_id, canonical(actor))).fetchone()
            require(latest is not None and latest["run_id"] == run_id and digest(json.loads(latest["record_json"])) == context_sha256,
                    "output_context_stale", "A newer output arrived before this control was accepted")
            require(conn.execute("SELECT COUNT(*) FROM runtime_context_controls WHERE actor_json=?", (canonical(actor),)).fetchone()[0] < 1024,
                    "context_control_capacity", "Owner control history bound reached")
            require(conn.execute("SELECT 1 FROM runtime_context_controls WHERE control_id=?", (control_id,)).fetchone() is None,
                    "idempotency_conflict", "Control identity is already owned by another request")
            conn.execute("INSERT INTO runtime_context_controls VALUES(?,?,?,?,?)",
                         (control_id, agent.session_id, canonical(actor), canonical(row), time.time()))
        db._execute_write(insert)
        if action in {"correct", "remove"} and scope != "response":
            # The individual store remains the only preference writer. A journal
            # error after this point is uncertain, never a claimed remote delete.
            row["status"] = "mutation_pending"
            _save_control(db, agent, actor, row)
            if action == "remove":
                outcome = manager.store.delete_record(record_id, expected_version=expected_version)
                row["deletion_semantics"] = "tombstone_not_physical_erasure"
            else:
                outcome = manager.store.write_record(content, record_id=record_id, expected_version=expected_version,
                    target=current["target"], kind=current["kind"], scope=current["scope"], source_ref="output-control:" + control_id,
                    author=actor["principal_id"], valid_from=current["valid_from"], valid_to=current["valid_to"],
                    confidence=current["confidence"], validity=current["validity"])
            row["status"] = "memory_acknowledged" if outcome["success"] else "version_conflict"
            row["acknowledged_version"] = outcome.get("acknowledged_version")
            _save_control(db, agent, actor, row)
        return _receipt(row)


def _save_control(db, agent, actor, row):
    def write(conn):
        db._mission_owner_on_conn(conn, agent.session_id, actor)
        conn.execute("UPDATE runtime_context_controls SET record_json=? WHERE control_id=? AND session_id=? AND actor_json=?",
                     (canonical(row), row["control_id"], row["session_id"], canonical(actor)))
    db._execute_write(write)


def prepare_context_controls(agent, packet):
    """Select at a turn boundary only. A directive never erases cached history."""
    from agent.runtime_commands import _RUN
    run = _RUN.get()
    if run is None or run.agent is not agent:
        return packet
    db, actor, manager = _authority(agent)
    project_id = manager._selected_project_id
    selected = []
    for row in _controls(db, agent, actor):
        if row["status"] not in {"queued_next_turn", "context_supplied", "memory_acknowledged"}:
            continue
        if row["scope"] == "response" and row["applied_run_id"] is not None:
            selected.append({"control_id": row["control_id"], "record_id": row["record_id"], "version": row["expected_version"],
                             "action": "expire", "scope": "response", "replacement": None})
            continue
        if row["scope"] == "project" and row["project_id"] != project_id:
            continue
        if row["action"] in {"correct", "remove"} and row["scope"] != "response":
            continue  # Actual acknowledged store versions enter normal fresh updates.
        current = manager.store.read_record(row["record_id"])
        if current["version"] != row["expected_version"] or current["namespace_id"] != row["namespace_id"]:
            row["status"] = "stale_not_applied"
            _save_control(db, agent, actor, row)
            continue
        if current["scope"].startswith("project:") and current["scope"] != "project:" + str(project_id):
            continue
        selected.append({"control_id": row["control_id"], "record_id": row["record_id"], "version": row["expected_version"],
                         "action": row["action"], "scope": row["scope"], "replacement": row["content"]})
    require(len(selected) <= MAX_CONTROLS, "context_control_capacity", "Fresh control bound exceeded")
    if selected:
        ignored = {(row["record_id"], row["version"]) for row in selected if row["action"] != "expire"}
        packet["records"] = [row for row in packet.get("records", []) if (row["record_id"], row["version"]) not in ignored]
        directive = ("User-requested context controls for this response. Ignore the specified earlier memory versions; "
                     "a replacement applies only in the named scope. An expire action ends a prior response-only override; restore the original preference. "
                     "Earlier cached text remains historical, not current guidance.\n"
                     + canonical(selected))
        packet["context_text"] = "\n".join(value for value in (packet.get("context_text", ""), directive) if value)
    agent._pending_output_controls = selected
    return packet


def record_supplied_context(agent, request):
    """Called after a provider call returns, with its supplied request, not UI claims."""
    from agent.runtime_commands import _RUN, assert_runtime_dispatch
    run = _RUN.get()
    if run is None or run.agent is not agent:
        return
    assert_runtime_dispatch()
    if getattr(agent, "_memory_manager", None) is None:
        return
    db, actor, manager = _authority(agent)
    # Only record refs when the exact escaped packet actually occurs in request
    # text. Middleware dropping/replacing it yields no fabricated receipt.
    encoded = getattr(agent, "_fresh_context_packet_json", None)
    def contains(value):
        if isinstance(value, str):
            return encoded in value
        if isinstance(value, list):
            return any(contains(item) for item in value)
        if isinstance(value, dict):
            return any(contains(item) for item in value.values())
        return False
    present = bool(encoded and contains(request))
    projection = getattr(agent, "_fresh_context_projection", {})
    refs = projection.get("records", []) if present else []
    controls = getattr(agent, "_pending_output_controls", []) if present else []
    record = {"run_id": run.run_id, "backend": manager.backend,
              "namespace_id": manager.store.namespace_id if manager.store else None,
              "project_id": manager._selected_project_id, "references": refs,
              "context_packet_sha256": hashlib.sha256((encoded if present else "").encode()).hexdigest(),
              "immutable_prefix_sha256": hashlib.sha256((getattr(agent, "_cached_system_prompt", "") or "").encode()).hexdigest(),
              "coverage": "fresh_memory_context_only", "controls": controls,
              "degraded": bool(projection.get("degraded")) or bool(encoded and not present), "state": "supplied_to_provider_call"}
    selected = {row["control_id"] for row in controls if row["action"] != "expire"}
    expired = {row["control_id"] for row in controls if row["action"] == "expire"}
    versions = {(row["record_id"], row["version"]) for row in refs}
    def write(conn):
        db._mission_owner_on_conn(conn, run.session_id, actor, holder=run.holder, generation=run.generation)
        existing = conn.execute("SELECT record_json FROM runtime_output_contexts WHERE session_id=? AND run_id=?", (agent.session_id, run.run_id)).fetchone()
        if existing is not None:
            return  # The first successfully supplied request is the immutable receipt.
        conn.execute("INSERT INTO runtime_output_contexts VALUES(?,?,?,?,?)",
                     (agent.session_id, run.run_id, canonical(actor), canonical(record), time.time()))
        # Commit one-response consumption with its supplied-input receipt. A
        # crash cannot retain the receipt while leaving that override reusable.
        rows = conn.execute("SELECT record_json FROM runtime_context_controls WHERE actor_json=?", (canonical(actor),)).fetchall()
        for saved in rows:
            row = json.loads(saved[0])
            if row["scope"] == "response" and row["session_id"] != agent.session_id:
                continue
            acknowledged = row["status"] == "memory_acknowledged" and (row["record_id"], row["acknowledged_version"]) in versions
            if row["control_id"] in expired:
                row["status"] = "expired"
            elif row["control_id"] in selected or acknowledged:
                row.update(status="context_supplied", applied_run_id=run.run_id)
            else:
                continue
            conn.execute("UPDATE runtime_context_controls SET record_json=? WHERE control_id=? AND actor_json=?",
                         (canonical(row), row["control_id"], canonical(actor)))
    db._execute_write(write)
