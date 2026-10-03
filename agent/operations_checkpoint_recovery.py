"""Fenced reconstruction of derived runtime state from canonical local facts.

Only the cached projection is replaced. Checkpoint/journal/commands, consumed
approvals, effect evidence, generation counters and context bytes are never
rewound. There is no adapter dispatch, model invocation or profile cutover here.
"""
from __future__ import annotations

import json
import time

from agent.operations_control import authority, digest, require
from hermes_state_runtime import _EVENT_TYPES, _checkpoint_metadata, _json, _project_runtime_event

MAX_REPLAY_EVENTS = 2000
MAX_REPLAY_BYTES = 8 * 1024 * 1024


def reconstruct_on_conn(db, conn, sid, context):
    row = conn.execute("SELECT * FROM runtime_checkpoints WHERE session_id=?", (sid,)).fetchone()
    require(row is not None, "checkpoint_missing")
    state = db._runtime_state_on_conn(conn, sid)
    saved = json.loads(row["checkpoint_json"])
    require(isinstance(saved, dict), "checkpoint_corrupt")
    metadata = _checkpoint_metadata({key: value for key, value in saved.items()
                                     if key not in {"snapshot", "context_projection_ref"}})
    require(row["schema_version"] == 1 and metadata["config_version"] == context.config_digest
            and metadata["policy_version"] == context.policy.digest
            and metadata["runtime_version"] == "be08.v1"
            and metadata["prompt_projection_version"] == "1", "checkpoint_version_mismatch")
    require(0 <= state["cursor_floor"] <= row["included_seq"] + 1 == row["published_seq"] <= state["revision"],
            "checkpoint_replay_unavailable")
    ref = saved.get("context_projection_ref")
    if ref is not None:
        require(isinstance(ref, dict) and set(ref) == {"projection_id", "session_id", "schema_version", "included_seq"}
                and ref["schema_version"] == 1, "checkpoint_context_mismatch")
        projection = conn.execute("SELECT * FROM runtime_context_projections WHERE session_id=?", (ref["session_id"],)).fetchone()
        require(projection is not None and projection["schema_version"] == 1
                and db._runtime_session_on_conn(conn, ref["session_id"]) == sid
                and projection["projection_id"] == ref["projection_id"]
                and projection["included_seq"] == ref["included_seq"] <= row["included_seq"],
                "checkpoint_context_mismatch")
    checkpoint_digest = digest(saved)
    projection = saved.get("snapshot")
    require(isinstance(projection, dict)
            and set(projection) <= {"state", "outstanding_requests", "artifacts", "unresolved_effects", "unresolved_invocations"}
            and isinstance(projection.get("state"), dict)
            and set(projection["state"]) == {"status", "run_id", "last_command_id", "last_operation"}
            and projection["state"]["status"] in {"idle", "accepted", "claimed", "completed", "failed", "blocked", "cancelled"},
            "checkpoint_projection_invalid")
    for key in ("outstanding_requests", "artifacts", "unresolved_effects", "unresolved_invocations"):
        require(projection.get(key, []) == metadata[key], "checkpoint_projection_invalid")
        projection[key] = metadata[key]
    rows = conn.execute("SELECT * FROM runtime_events WHERE session_id=? AND seq>? ORDER BY seq LIMIT ?",
                        (sid, row["included_seq"], MAX_REPLAY_EVENTS + 1)).fetchall()
    require(len(rows) <= MAX_REPLAY_EVENTS and sum(len(item["payload_json"].encode()) for item in rows) <= MAX_REPLAY_BYTES,
            "checkpoint_replay_limit")
    require(len(rows) == state["revision"] - row["included_seq"]
            and all(item["schema_version"] == 1 and item["type"] in _EVENT_TYPES and item["seq"] == row["included_seq"] + index + 1
                    for index, item in enumerate(rows)), "checkpoint_replay_gap")
    require(rows[0]["type"] == "checkpoint.published"
            and json.loads(rows[0]["payload_json"]) == {"checkpoint_id": row["checkpoint_id"], "included_seq": row["included_seq"]},
            "checkpoint_journal_mismatch")
    for item in rows:
        event = dict(item)
        event["payload"] = json.loads(event.pop("payload_json"))
        operation = None
        if event["type"].startswith("command."):
            command = db._runtime_command_on_conn(conn, sid, event["payload"]["command_id"])
            require(command is not None, "checkpoint_command_missing")
            operation = json.loads(command["command_json"])["operation"]
        _project_runtime_event(projection, event, operation)
    # Never revive an old approval or use a stale effect outcome from a checkpoint.
    refs = db._runtime_references_on_conn(conn, sid, state, projection)
    require(not refs["references_truncated"], "checkpoint_reference_limit")
    for key in ("outstanding_requests", "artifacts", "unresolved_effects", "unresolved_invocations"):
        projection[key] = refs[key]
    _json(projection)
    target = {"checkpoint_id": row["checkpoint_id"], "checkpoint_digest": checkpoint_digest,
              "included_seq": row["included_seq"], "through_revision": state["revision"],
              "projection_digest": digest(projection), "replayed_events": len(rows)}
    return projection, target


def qualify(db, context):
    from hermes_state_runtime import RuntimeStoreError
    sid, _ = authority(db, context)
    try:
        with db._runtime_read() as conn:
            _, target = reconstruct_on_conn(db, conn, sid, context)
            lease = conn.execute("SELECT expires_at FROM session_turn_leases WHERE conversation_id=?", (sid,)).fetchone()
    except (RuntimeStoreError, ValueError, KeyError, TypeError) as error:
        code = getattr(error, "code", "checkpoint_corrupt")
        return {"status": "unavailable" if code == "checkpoint_missing" else "incompatible",
                "restore_allowed": False, "checks": {}, "blocking_gates": [code]}
    busy = lease is not None and lease["expires_at"] > time.time()
    return {"status": "qualified_for_isolated_drill", **target, "checks": {"versions": True,
            "contiguous_replay": True, "authoritative_references": True},
            "restore_allowed": not busy, "blocking_gates": ["operations_session_busy"] if busy else [],
            "restore_scope": "derived_projection_only", "history_rewind": False,
            "external_effect_replay": False, "full_profile_cutover_certified": False}


def restore_projection(db, context, plan, *, holder, generation):
    """Called only by the existing preview/authorization/maintenance-lease broker."""
    sid, _ = authority(db, context)

    def write(conn):
        db._runtime_fence_on_conn(conn, sid, holder, generation)
        projection, target = reconstruct_on_conn(db, conn, sid, context)
        require(target == plan["target"] and target["through_revision"] == plan["before_revision"],
                "operations_preview_changed")
        # One writer transaction includes both journal receipts and replacement.
        # Even an invalid current cache can be repaired without feeding it to append.
        conn.execute("UPDATE runtime_state SET snapshot_json=? WHERE session_id=?", (_json(projection), sid))
        payload = {"action": "restore-checkpoint", "plan_digest": plan["plan_digest"],
                   "affected_ids": [sid], "before_revision": plan["before_revision"]}
        db._append_runtime_event_on_conn(conn, sid, "operations.repair_started", payload, generation,
                                        operation_id=plan["plan_digest"])
        event = db._append_runtime_event_on_conn(conn, sid, "operations.repair_finished",
            {**payload, "outcome": "projection_reconstructed"}, generation, operation_id=plan["plan_digest"])
        return {"plan_digest": plan["plan_digest"], "outcome": "projection_reconstructed",
                "revision": event["seq"], "generation": generation, "history_rewound": False,
                "external_effects_replayed": 0}
    return db._execute_write(write)
