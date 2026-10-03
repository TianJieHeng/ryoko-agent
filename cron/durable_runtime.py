"""Owned finite schedule execution, called only by the existing cron tick."""
from __future__ import annotations

import json
import logging
import time
import uuid

from agent.agent_identity import resolve_owned_agent_context
from agent.identity_lifecycle import agent_runtime_scope, identity_config
from cron.durable_contract import canonical, digest, require, next_due, occurrence_id
from hermes_state_schedules import ScheduleRegistry

logger = logging.getLogger(__name__)


def _occurrence_session(version, oid):
    return version["session_id"] + "_" + oid[-8:]


def _check(registry, occurrence, definition, holder, generation):
    from tools.capability_broker import require_live_policy
    require(require_live_policy(require_run=False) == registry.context, "Scheduled policy changed", "identity_mismatch")
    require(time.time() < occurrence["deadline_at"], "Original scheduled deadline expired", "schedule_expired")
    with registry.access.guard(definition["project_id"], registry.actor, "read"), registry.db._runtime_read() as conn:
        registry.db._runtime_fence_on_conn(conn, occurrence["session_id"], holder, generation)
        row = registry._row(conn, occurrence["schedule_key"])
        require(row["state"] == "active" and row["version"] == occurrence["version"],
                "Schedule paused or revoked", "schedule_stopped")
        command = registry.db._runtime_command_on_conn(conn, occurrence["session_id"], occurrence["command_id"])
        require(command is not None and command["status"] == "claimed" and command["claimed_holder"] == holder
                and command["claimed_generation"] == generation, "Scheduled claim is stale", "stale_owner")


def _observe(registry, occurrence, definition, holder, generation, observation):
    key, version, oid = occurrence["schedule_key"], occurrence["version"], occurrence["occurrence_id"]
    now = time.time()
    with registry.access.guard(definition["project_id"], registry.actor, "read"):
        def write(conn):
            registry.db._runtime_fence_on_conn(conn, occurrence["session_id"], holder, generation)
            row = registry._row(conn, key)
            require(row["state"] == "active" and row["version"] == version and now < occurrence["deadline_at"],
                    "Monitor no longer authorized", "schedule_stopped")
            prior = conn.execute("SELECT * FROM durable_monitor_state WHERE schedule_key=? AND version=?", (key, version)).fetchone()
            baseline = prior is None
            previous = json.loads(prior["baseline_json"]) if prior else None
            changed = not baseline and previous != observation["projection"]
            predicate = definition["specification"]["predicate"]
            matched = changed and (predicate["kind"] != "threshold" or any(
                value is True and previous.get(source) is not True for source, value in observation["projection"].items()))
            result = {**observation, "baseline": baseline, "changed": changed, "matched": matched,
                      "observed_at": now, "predicate_version": predicate["version"]}
            conn.execute("INSERT INTO durable_monitor_observations VALUES(?,?,?,?,?,?)",
                         (oid, key, version, now, "healthy", canonical(result)))
            conn.execute("INSERT INTO durable_monitor_state VALUES(?,?,?,?,?) ON CONFLICT(schedule_key,version) DO UPDATE SET "
                "baseline_json=excluded.baseline_json,source_refs_json=excluded.source_refs_json,last_success=excluded.last_success",
                (key, version, canonical(observation["projection"]), canonical(observation["source_refs"]), now))
            action = definition["specification"]["condition_action"]
            action_granted = False
            if matched:
                conn.execute("INSERT INTO durable_monitor_intents VALUES(?,?,?,'recorded',?,?)",
                    ("notice_" + oid, oid, "notification", canonical({"source_refs": observation["source_refs"],
                     "question": definition["specification"]["question"], "delivery": "record_only"}), now))
                if action is not None:
                    target = digest({"project_id": definition["project_id"], "action": action, "schedule_sha256": digest(definition)})
                    grant = conn.execute("SELECT * FROM durable_condition_grants WHERE schedule_key=? AND version=? "
                        "AND state='active' AND remaining>0 AND expires_at>? AND target_digest=? ORDER BY created_at DESC LIMIT 1",
                        (key, version, now, target)).fetchone()
                    action_granted = bool(grant and now - occurrence["accepted_at"] <= grant["max_age_seconds"])
                    grant_id = grant["grant_id"] if action_granted else None
                    if action_granted:
                        conn.execute("UPDATE durable_condition_grants SET remaining=remaining-1 WHERE grant_id=?", (grant_id,))
                    conn.execute("INSERT INTO durable_monitor_intents VALUES(?,?,?,?,?,?)", ("action_" + oid, oid, "action",
                        "authorized" if action_granted else "awaiting_authorization", canonical({"target_digest": target,
                        "grant_id": grant_id, "observed_at": now, "external_actions": False}), now))
            return result, action_granted
        return registry.db._execute_write(write)


def _finish(registry, occurrence, definition, holder, generation, result, *, error=None):
    now, status = time.time(), "failed" if error else "completed"
    # Even a stop/revocation or read denial gets its honest receipt while the
    # claim is live. No new source read or action is authorized by this write.
    def write(conn):
        registry.db._runtime_fence_on_conn(conn, occurrence["session_id"], holder, generation)
        command = registry.db._runtime_command_on_conn(conn, occurrence["session_id"], occurrence["command_id"])
        require(command is not None and command["status"] == "claimed" and command["claimed_holder"] == holder
                and command["claimed_generation"] == generation, "Scheduled claim is stale", "stale_owner")
        encoded = canonical(result)
        conn.execute("UPDATE runtime_commands SET status=?,result_json=? WHERE session_id=? AND command_id=?",
                     (status, encoded, occurrence["session_id"], occurrence["command_id"]))
        registry.db._append_runtime_event_on_conn(conn, occurrence["session_id"], "command." + status,
            {"command_id": occurrence["command_id"], "result": result}, generation, run_id=occurrence["run_id"])
        conn.execute("UPDATE durable_occurrences SET state=?,result_json=? WHERE occurrence_id=?",
                     (status, encoded, occurrence["occurrence_id"]))
        conn.execute("UPDATE durable_schedules SET health=?,last_success=CASE WHEN ? IS NULL THEN ? ELSE last_success END,"
            "last_error=? WHERE schedule_key=?", ("unhealthy" if error else "healthy", error, now, error, occurrence["schedule_key"]))
        if error and definition["kind"] == "monitor":
            conn.execute("INSERT OR IGNORE INTO durable_monitor_observations VALUES(?,?,?,?,?,?)", (occurrence["occurrence_id"],
                occurrence["schedule_key"], occurrence["version"], now, "unhealthy", canonical({"error": error})))
        conn.execute("UPDATE durable_monitor_intents SET state=? WHERE occurrence_id=? AND kind='action' AND state='authorized'",
                     ("failed" if error else "completed", occurrence["occurrence_id"]))
    registry.db._execute_write(write)


def execute_occurrence(registry, occurrence, definition):
    db, sid = registry.db, occurrence["session_id"]
    record = db.read_runtime_command(sid, occurrence["command_id"])
    # Accepted work may start once; a claimed or terminal command cannot.
    if record is None or record["status"] != "accepted":
        return False
    holder = "schedule:" + uuid.uuid4().hex
    if not db.try_acquire_session_turn_lease(sid, holder, ttl_seconds=65):
        return False
    generation = db.get_session_turn_lease(sid)["generation"]
    try:
        if not db.claim_runtime_command(sid, occurrence["command_id"], holder=holder, generation=generation):
            return False
        def stamp(conn):
            db._runtime_fence_on_conn(conn, sid, holder, generation)
            conn.execute("UPDATE durable_occurrences SET state='claimed',holder=?,generation=? WHERE occurrence_id=?",
                         (holder, generation, occurrence["occurrence_id"]))
        db._execute_write(stamp)
        check = lambda: _check(registry, occurrence, definition, holder, generation)
        try:
            check()
            from cron.durable_sources import observe_sources, review_sources
            if definition["kind"] == "monitor":
                observation = observe_sources(registry.context, db, definition, check)
                result, granted = _observe(registry, occurrence, definition, holder, generation, observation)
                if granted:
                    check()
                    result["action_review"] = review_sources(registry.context, db, definition, check,
                        specification=definition["specification"]["condition_action"])
            elif definition["kind"] == "review":
                result = review_sources(registry.context, db, definition, check)
            else:
                from hermes_state_commitments import CommitmentRegistry
                result = CommitmentRegistry(registry.context, db).weekly_review(definition["project_id"])
                check()
            result = {"execution_scope": "local_finite", "delivery_state": "not_requested", **result}
        except Exception as exc:
            code = getattr(exc, "code", type(exc).__name__)
            # Do not persist arbitrary exception text containing source contents.
            _finish(registry, occurrence, definition, holder, generation,
                    {"error": code, "execution_scope": "local_finite", "delivery_state": "not_requested"}, error=code)
        else:
            _finish(registry, occurrence, definition, holder, generation, result)
        return True
    finally:
        db.release_session_turn_lease(sid, holder, generation=generation)


def _recover(db):
    """Dead claims are uncertainty, including the crash between claim and stamp."""
    now = time.time()
    def write(conn):
        rows = conn.execute("SELECT o.*,c.status command_status,c.claimed_holder,c.claimed_generation,l.holder lease_holder,"
            "l.generation lease_generation,l.expires_at FROM durable_occurrences o JOIN runtime_commands c "
            "ON c.session_id=o.session_id AND c.command_id=o.command_id LEFT JOIN session_turn_leases l ON l.conversation_id=o.session_id "
            "WHERE o.state IN ('accepted','claimed')").fetchall()
        for row in rows:
            live = row["expires_at"] is not None and row["expires_at"] > now and row["claimed_holder"] == row["lease_holder"] and row["claimed_generation"] == row["lease_generation"]
            if row["command_status"] == "claimed" and not live:
                conn.execute("UPDATE durable_occurrences SET state='outcome_unknown',result_json=? WHERE occurrence_id=?",
                    (canonical({"reason": "owner_lost_after_claim", "replayed": False}), row["occurrence_id"]))
                conn.execute("UPDATE durable_schedules SET health='unhealthy',last_error='occurrence_outcome_unknown' WHERE schedule_key=?",
                             (row["schedule_key"],))
    db._execute_write(write)


def _missed(registry, row, definition, now):
    due, policy = row["next_due"], definition["policy"]
    if due is None or now - due <= policy["grace_seconds"]:
        return
    if policy["missed_run"] == "latest":
        trigger = definition["trigger"]
        if trigger["kind"] == "interval":
            import math
            latest = trigger["anchor"] + math.floor((now - trigger["anchor"]) / trigger["seconds"]) * trigger["seconds"]
        elif trigger["kind"] == "calendar":
            latest, candidate = due, next_due(definition, now - 8 * 86400)
            for _ in range(9):
                if candidate > now:
                    break
                latest, candidate = candidate, next_due(definition, candidate)
        else:
            latest = due
    else:
        latest = next_due(definition, now)
    if latest == due:
        return
    key, version = row["schedule_key"], row["version"]
    def write(conn):
        live = registry._row(conn, key)
        require(live["revision"] == row["revision"] and live["state"] == "active", "Schedule changed", "schedule_revision_conflict")
        registry._no_unresolved(conn, key)
        oid = occurrence_id(key, version, due)
        conn.execute("INSERT OR IGNORE INTO durable_occurrences(occurrence_id,schedule_key,version,due_at,session_id,command_id,state,"
            "accepted_at,deadline_at,result_json) VALUES(?,?,?,?,?,?,'skipped',?,?,?)",
            (oid, key, version, due, "not_admitted", oid, now, now, canonical({"reason": "missed_window", "policy": policy["missed_run"],
             "skipped_until": latest, "clock_basis": "UTC"})))
        conn.execute("UPDATE durable_schedules SET next_due=?,revision=revision+1 WHERE schedule_key=?", (latest, key))
    registry.db._execute_write(write)


def tick_durable_schedules():
    """One bounded scan under the caller's existing per-profile cron tick lock."""
    from hermes_constants import get_hermes_home
    from hermes_state import SessionDB
    home = get_hermes_home()
    # Avoid opening/creating a state database on legacy-only idle installations.
    if not (home / "state.db").exists():
        return 0
    db = SessionDB(home / "state.db")
    completed = 0
    try:
        with db._runtime_read() as conn:
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='durable_schedules'").fetchone() is None:
                return 0
        _recover(db)
        with db._runtime_read() as conn:
            rows = [dict(row) for row in conn.execute("SELECT * FROM durable_schedules WHERE state='active' ORDER BY next_due LIMIT 100")]
        for row in rows:
            try:
                with db._runtime_read() as conn:
                    version = dict(ScheduleRegistry._version(conn, row))
                    pending = conn.execute("SELECT * FROM durable_occurrences WHERE schedule_key=? AND state='accepted' ORDER BY due_at LIMIT 1",
                                           (row["schedule_key"],)).fetchone()
                definition = json.loads(version["definition_json"])
                now = time.time()
                if pending is None and (row["next_due"] is None or row["next_due"] > now):
                    continue
                oid = pending["occurrence_id"] if pending else occurrence_id(row["schedule_key"], row["version"], row["next_due"])
                sid = pending["session_id"] if pending else _occurrence_session(version, oid)
                context = resolve_owned_agent_context(identity_config(), owner_binding=json.loads(row["owner_binding_json"]),
                    session_id=sid, profile_home=home)
                with agent_runtime_scope(context):
                    registry = ScheduleRegistry(context, db)
                    if pending:
                        occurrence = dict(pending)
                    else:
                        with registry.access.guard(row["project_id"], registry.actor, "read"):
                            _missed(registry, row, definition, now)
                        with db._runtime_read() as conn:
                            row = dict(registry._row(conn, row["schedule_key"]))
                        if row["next_due"] is None or row["next_due"] > now:
                            continue
                        # Catch-up may choose a different canonical due instant.
                        oid = occurrence_id(row["schedule_key"], row["version"], row["next_due"])
                        context = resolve_owned_agent_context(identity_config(), owner_binding=json.loads(row["owner_binding_json"]),
                            session_id=_occurrence_session(version, oid), profile_home=home)
                        with agent_runtime_scope(context):
                            registry = ScheduleRegistry(context, db)
                            occurrence, definition = registry.admit(row, now=now)
                            completed += execute_occurrence(registry, occurrence, definition)
                        continue
                    completed += execute_occurrence(registry, occurrence, definition)
            except Exception as exc:
                code = getattr(exc, "code", type(exc).__name__)
                state = {"schedule_expired": "expired", "IdentityPolicyError": "paused"}.get(code)
                db._execute_write(lambda conn: conn.execute("UPDATE durable_schedules SET health='unhealthy',last_error=?,"
                    "state=COALESCE(?,state) WHERE schedule_key=?", (code, state, row["schedule_key"])))
                logger.warning("Durable schedule %s blocked: %s", row["schedule_id"], code)
        return completed
    finally:
        db.close()
