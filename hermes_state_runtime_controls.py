"""Persisted owner/profile pause. Accepted work keeps its original identity and claim.

This is an admission/next-dispatch fence, not a cancellation, rollback or provider
acknowledgment. Already dispatched operations may still finish and record receipts.
"""
from __future__ import annotations

import json
import re
import time

from hermes_state_effects import _actor, effect_digest
from hermes_state_runtime import _identifier, _require


def owner_paused_on_conn(conn, actor):
    actor = _actor(actor)
    row = conn.execute("SELECT paused FROM runtime_owner_controls WHERE principal_id=? AND profile_id=?",
                       (actor["principal_id"], actor["profile_id"])).fetchone()
    return bool(row and row[0])


def is_stop_only_schedule_control(command, admission):
    """Only the owned typed scheduler's atomic, completed metadata callback.

    The public runtime command wire cannot submit artifact envelopes. There is no
    generic pause bypass: new/active schedules and commands still hit the fence.
    """
    payload = command.get("payload")
    return (command.get("operation") == "artifact" and callable(admission)
        and isinstance(payload, dict) and set(payload) == {"mode", "request_sha256", "schedule_state"}
        and payload["mode"] == "runtime.schedule.update" and payload["schedule_state"] in {"paused", "revoked"}
        and isinstance(payload["request_sha256"], str) and re.fullmatch(r"[0-9a-f]{64}", payload["request_sha256"]) is not None)


def assert_owner_running_on_conn(conn, actor):
    _require(not owner_paused_on_conn(conn, actor), "owner_paused", "Owner runtime is paused")


def assert_owner_running(db, actor):
    with db._runtime_read() as conn:
        assert_owner_running_on_conn(conn, actor)


def _state(conn, actor):
    keys = (actor["principal_id"], actor["profile_id"])
    row = conn.execute("SELECT revision,paused,updated_at FROM runtime_owner_controls "
                       "WHERE principal_id=? AND profile_id=?", keys).fetchone()
    paused = bool(row and row["paused"])
    counts = dict(conn.execute("SELECT c.status,COUNT(*) FROM runtime_commands c JOIN runtime_state s USING(session_id) "
        "WHERE s.principal_id=? AND s.profile_id=? AND c.status IN ('accepted','claimed') "
        "AND json_extract(c.command_json,'$.operation') IN ('submit','artifact') GROUP BY c.status", keys))
    return {"revision": row["revision"] if row else 0, "paused": paused,
        "updated_at": row["updated_at"] if row else None, "scope": "owner_profile",
        "admission_blocked": paused, "scheduled_dispatch_blocked": paused,
        "in_flight_dispatch": "blocked_at_next_boundary" if paused else "allowed_at_checked_boundary",
        "accepted_commands": counts.get("accepted", 0), "claimed_commands": counts.get("claimed", 0),
        "accepted_work_retained": True, "already_dispatched_may_complete": True,
        "provider_cancelled": False, "remote_effects_undone": False}


class RuntimeOwnerControls:
    def __init__(self, db, actor):
        self.db, self.actor = db, _actor(actor)

    def get(self, operation_id=None):
        with self.db._runtime_read() as conn:
            operation = None
            if operation_id is not None:
                _identifier(operation_id, "operation_id")
                row = conn.execute("SELECT receipt_json FROM runtime_owner_control_operations "
                    "WHERE principal_id=? AND profile_id=? AND operation_id=?",
                    (self.actor["principal_id"], self.actor["profile_id"], operation_id)).fetchone()
                operation = json.loads(row[0]) if row else None
            return {"control": _state(conn, self.actor), "operation": operation, "dispatch_performed": False}

    def set_paused(self, paused, *, operation_id, expected_revision):
        _identifier(operation_id, "operation_id")
        _require(type(paused) is bool and type(expected_revision) is int and expected_revision >= 0,
                 "invalid_command", "An exact control state and revision are required")
        digest = effect_digest({"operation_id": operation_id, "paused": paused,
                                "expected_revision": expected_revision})
        keys = (self.actor["principal_id"], self.actor["profile_id"])
        def write(conn):
            prior = conn.execute("SELECT digest,receipt_json FROM runtime_owner_control_operations "
                "WHERE principal_id=? AND profile_id=? AND operation_id=?", (*keys, operation_id)).fetchone()
            if prior:
                _require(prior["digest"] == digest, "idempotency_conflict", "Control key names another request")
                return {"control": _state(conn, self.actor), "operation": json.loads(prior["receipt_json"]),
                        "dispatch_performed": False}
            state = _state(conn, self.actor)
            _require(state["revision"] == expected_revision, "revision_conflict", "Control state changed")
            _require(conn.execute("SELECT COUNT(*) FROM runtime_owner_control_operations").fetchone()[0] < 65536,
                     "control_capacity", "Retained control receipt capacity reached")
            now, revision = time.time(), expected_revision + 1
            conn.execute("INSERT INTO runtime_owner_controls VALUES(?,?,?,?,?) ON CONFLICT(principal_id,profile_id) "
                "DO UPDATE SET revision=excluded.revision,paused=excluded.paused,updated_at=excluded.updated_at",
                (*keys, revision, int(paused), now))
            operation = {"operation_id": operation_id, "digest": digest, "revision": revision,
                         "paused": paused, "committed_at": now, "status": "committed"}
            conn.execute("INSERT INTO runtime_owner_control_operations VALUES(?,?,?,?,?)",
                         (*keys, operation_id, digest, json.dumps(operation, sort_keys=True)))
            return {"control": _state(conn, self.actor), "operation": operation, "dispatch_performed": False}
        return self.db._execute_write(write)
