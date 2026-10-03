"""Clock-to-command adapter. It never constructs an agent or calls inference."""
from __future__ import annotations

import json
import math
import time

from agent.admission import AdmissionQueue
from cron.durable_contract import canonical, digest, require, next_due, instant


def _slot_id(key, version, slot):
    return "occ_" + digest({"schedule": key, "version": version, "slot": slot})[:48]


def _busy(conn, key):
    return conn.execute("SELECT 1 FROM durable_command_occurrences o JOIN runtime_commands c USING(session_id,command_id) "
        "WHERE o.schedule_key=? AND c.status IN ('accepted','claimed') LIMIT 1", (key,)).fetchone() is not None


def _latest(definition, due, now):
    trigger = definition["trigger"]
    if trigger["kind"] == "interval":
        anchor = instant(trigger["anchor"])
        return round(anchor + math.floor((now - anchor) / trigger["seconds"]) * trigger["seconds"], 3)
    if trigger["kind"] == "calendar":
        latest, candidate = due, next_due(definition, now - 8 * 86400)
        for _ in range(9):
            if candidate > now:
                break
            latest, candidate = candidate, next_due(definition, candidate)
        return latest
    return due


def _manual_request(conn, sid, slot, intent, oid):
    if not slot.startswith("manual:"):
        return
    request_id = slot[len("manual:"):]
    previous = conn.execute("SELECT intent_sha256,occurrence_id FROM durable_schedule_manual_requests WHERE session_id=? AND request_id=?",
                            (sid, request_id)).fetchone()
    require(previous is None or previous["intent_sha256"] == intent and previous["occurrence_id"] == oid,
            "Run-now key reused for different intent", "idempotency_conflict")
    conn.execute("INSERT OR IGNORE INTO durable_schedule_manual_requests VALUES(?,?,?,?)", (sid, request_id, intent, oid))


def _skip(conn, registry, row, binding, slot, due, now, detail, *, advance):
    oid = _slot_id(row["schedule_key"], row["version"], slot)
    conn.execute("INSERT INTO durable_command_occurrences VALUES(?,?,?,?,?,?,NULL,'skipped',?,?)",
        (oid, row["schedule_key"], row["version"], slot, due, binding["session_id"], now, canonical(detail)))
    _manual_request(conn, binding["session_id"], slot, detail.get("intent_sha256"), oid)
    conn.execute("UPDATE durable_schedules SET next_due=?,revision=revision+1 WHERE schedule_key=?", (advance, row["schedule_key"]))
    return oid


def admit_command(registry, row, *, now, manual_slot=None, intent_sha256=None):
    key = row["schedule_key"]
    db = registry.db
    with registry.access.guard(row["project_id"], registry.actor, "read"):
        def prepare(conn):
            live = registry._row(conn, key)
            require(live["revision"] == row["revision"] and live["version"] == row["version"] and live["state"] == "active",
                    "Schedule changed", "schedule_revision_conflict")
            definition = json.loads(registry._version(conn, live)["definition_json"])
            registry._activation(conn, live, definition, time.time())
            binding = conn.execute("SELECT * FROM durable_command_schedule_bindings WHERE schedule_key=? AND version=?", (key, live["version"])).fetchone()
            require(binding is not None, "Command binding missing", "identity_mismatch")
            due = now if manual_slot else live["next_due"]
            require(due is not None and due <= now, "Occurrence not due", "schedule_not_due")
            slot = manual_slot or "due:" + str(round(due, 3))
            old = conn.execute("SELECT * FROM durable_command_occurrences WHERE schedule_key=? AND version=? AND slot=?", (key, live["version"], slot)).fetchone()
            if old:
                require(not manual_slot or json.loads(old["detail_json"]).get("intent_sha256") == intent_sha256,
                        "Run-now intent changed", "idempotency_conflict")
                return None, registry.occurrence_receipt(conn, old)
            require(conn.execute("SELECT COUNT(*) FROM durable_command_occurrences").fetchone()[0] < 65536,
                    "Occurrence capacity reached", "schedule_capacity")
            if not manual_slot and now - due > definition["policy"]["grace_seconds"]:
                chosen = _latest(definition, due, now) if definition["policy"]["missed_run"] == "run_once" else next_due(definition, now)
                if chosen != due:
                    oid = _skip(conn, registry, live, binding, slot, due, now,
                        {"reason": "missed_window", "skipped_until": chosen, "policy": definition["policy"]["missed_run"]}, advance=chosen)
                    live = registry._row(conn, key)
                    if chosen is None or chosen > now:
                        return None, registry.occurrence_receipt(conn, conn.execute("SELECT * FROM durable_command_occurrences WHERE occurrence_id=?", (oid,)).fetchone())
                    due, slot = chosen, "due:" + str(round(chosen, 3))
            if definition["policy"]["overlap"] == "skip" and _busy(conn, key):
                require(manual_slot is None, "Schedule already has unfinished work", "schedule_overlap")
                oid = _skip(conn, registry, live, binding, slot, due, now,
                    {"reason": "overlap", "intent_sha256": intent_sha256},
                    advance=live["next_due"] if manual_slot else next_due(definition, now))
                return None, registry.occurrence_receipt(conn, conn.execute("SELECT * FROM durable_command_occurrences WHERE occurrence_id=?", (oid,)).fetchone())
            return (dict(live), definition, dict(binding), due, slot), None
        prepared, skipped = db._execute_write(prepare)
        if prepared is None:
            return skipped
        live, definition, binding, due, slot = prepared
        oid = _slot_id(key, live["version"], slot)
        command_id = manual_slot[len("manual:"):] if manual_slot else oid
        idempotency_key = "schedule_" + intent_sha256 if manual_slot else oid
        envelope = {"schema_version": 1, "command_id": command_id, "idempotency_key": idempotency_key, "expected_revision": None,
                    "operation": "submit", "identity_binding": registry.actor,
                    "payload": {"text": definition["specification"]["prompt"]}}
        def admission(conn, root, _encoded, receipt):
            current = registry._row(conn, key)
            require(current["revision"] == live["revision"] and current["state"] == "active" and current["version"] == live["version"],
                    "Schedule changed before admission", "schedule_revision_conflict")
            registry._activation(conn, current, definition, time.time())
            require(root == binding["session_id"], "Conversation binding changed", "identity_mismatch")
            require(definition["policy"]["overlap"] != "skip" or not _busy(conn, key),
                    "Another occurrence won admission", "schedule_overlap")
            conn.execute("INSERT INTO durable_command_occurrences VALUES(?,?,?,?,?,?,?,'accepted',?,?)", (oid, key, live["version"],
                slot, due, root, command_id, now, canonical({"intent_sha256": intent_sha256, "manual": manual_slot is not None})))
            _manual_request(conn, root, slot, intent_sha256, oid)
            conn.execute("UPDATE durable_schedules SET next_due=?,remaining_checks=remaining_checks-1,revision=revision+1,health='healthy',last_error=NULL WHERE schedule_key=?",
                (current["next_due"] if manual_slot else next_due(definition, max(due, now)), key))
        AdmissionQueue(db).submit_bound(binding["session_id"], registry.actor, envelope, workload="background",
            deadline=min(now + definition["budget"]["deadline_seconds"], definition["expires_at"]),
            budget_policy_json=binding["budget_policy_json"], admission=admission)
        with db._runtime_read() as conn:
            return registry.occurrence_receipt(conn, conn.execute("SELECT * FROM durable_command_occurrences WHERE occurrence_id=?", (oid,)).fetchone())


def recover_command_occurrences(db):
    """A lost claimed turn is unknown, never a fresh launch or a clean failure."""
    # Expiry belongs to the same queue even before the service reattaches a
    # conversation; stale accepted references must not cause permanent overlap.
    AdmissionQueue(db).reconcile()
    def write(conn):
        for row in conn.execute("SELECT o.*,c.status command_status,c.claimed_holder,c.claimed_generation,l.holder lease_holder,"
            "l.generation lease_generation,l.expires_at FROM durable_command_occurrences o JOIN runtime_commands c USING(session_id,command_id) "
            "LEFT JOIN session_turn_leases l ON l.conversation_id=o.session_id WHERE o.state IN ('accepted','claimed')").fetchall():
            live = row["expires_at"] is not None and row["expires_at"] > time.time() and row["claimed_holder"] == row["lease_holder"] and row["claimed_generation"] == row["lease_generation"]
            state = row["command_status"]
            if state == "claimed" and not live:
                state = "outcome_unknown"
                conn.execute("UPDATE durable_schedules SET state='paused',revision=revision+1,health='unhealthy',last_error='occurrence_outcome_unknown' WHERE schedule_key=?",
                             (row["schedule_key"],))
            conn.execute("UPDATE durable_command_occurrences SET state=? WHERE occurrence_id=?", (state, row["occurrence_id"]))
    db._execute_write(write)
