"""Canonical schedule/version authority on the existing SessionDB writer.

The cron ticker is the only clock consumer. Runtime command acceptance and the
occurrence/deadline/budget debit commit together. Claimed work is never replayed.
"""
from __future__ import annotations

import json
import time

from agent.result_artifacts import artifact_actor
from agent.project_context import project_access
from cron.durable_contract import canonical, digest, require, validate_definition, next_due, occurrence_id


CONTROL_METHODS = frozenset({"runtime.schedule.create", "runtime.schedule.update", "runtime.schedule.grant",
                            "runtime.schedule.import", "runtime.schedule.reconcile"})


def assert_schedule_control(run, methods=CONTROL_METHODS):
    from agent.artifact_commands import ArtifactControlRun, assert_artifact_dispatch
    from tui_gateway import server
    require(isinstance(run, ArtifactControlRun) and server._current_rpc_method.get() in methods,
            "Owned human schedule control required", "schedule_control_required")
    assert_artifact_dispatch(run)


class ScheduleRegistry:
    def __init__(self, context, db):
        self.context, self.db = context, db
        self.actor, self.access = artifact_actor(context), project_access(context)

    def key(self, schedule_id):
        return digest({"owner": self.actor, "schedule_id": schedule_id})

    def _fence(self, conn, run):
        require(run.context == self.context and run.db is self.db, "Schedule owner changed", "identity_mismatch")
        self.db._mission_owner_on_conn(conn, run.session_id, self.actor, holder=run.holder, generation=run.generation)
        self.db._effect_run_on_conn(conn, run.session_id, self.actor, holder=run.holder, generation=run.generation,
                                    run_id=run.run_id, dispatch=True)

    def _row(self, conn, key):
        row = conn.execute("SELECT * FROM durable_schedules WHERE schedule_key=?", (key,)).fetchone()
        require(row is not None and json.loads(row["owner_json"]) == self.actor,
                "Owned schedule not found", "schedule_not_found")
        return row

    @staticmethod
    def _version(conn, row):
        version = conn.execute("SELECT * FROM durable_schedule_versions WHERE schedule_key=? AND version=?",
                               (row["schedule_key"], row["version"])).fetchone()
        require(version is not None and digest(json.loads(version["definition_json"])) == version["sha256"],
                "Immutable schedule changed", "schedule_digest_mismatch")
        return version

    def _public(self, conn, row):
        version = self._version(conn, row)
        result = {key: row[key] for key in ("schedule_id", "project_id", "version", "revision", "state", "next_due",
            "remaining_checks", "health", "last_success", "last_error")}
        result.update(owner_agent_id=self.actor["agent_id"], sha256=version["sha256"],
                      definition=json.loads(version["definition_json"]), authority="hermes_cron")
        imported = conn.execute("SELECT declaration_json FROM durable_schedule_imports WHERE schedule_key=?", (row["schedule_key"],)).fetchone()
        result["import_declaration"] = json.loads(imported[0]) if imported else None
        result["foreign_cutover_verified"] = False if imported else None
        result["occurrences"] = [dict(item) for item in conn.execute(
            "SELECT occurrence_id,version,due_at,run_id,state,generation,delivery_state,result_json "
            "FROM durable_occurrences WHERE schedule_key=? ORDER BY accepted_at DESC,occurrence_id DESC LIMIT 20", (row["schedule_key"],))]
        for occurrence in result["occurrences"]:
            detail = json.loads(occurrence.pop("result_json") or "null")
            occurrence["result"] = {key: value for key, value in (detail or {}).items() if key in {
                "error", "execution_scope", "source_scope", "baseline", "changed", "matched", "live_connection_verified",
                "delivery_state", "purpose", "owner_agent_id", "source_refs", "workflow_ref", "semantic_review", "replayed"}}
            occurrence["result_digest"] = digest(detail)
        result["intents"] = [json.loads(item[0]) | {"state": item[1], "kind": item[2], "intent_id": item[3]} for item in conn.execute(
            "SELECT i.record_json,i.state,i.kind,i.intent_id FROM durable_monitor_intents i JOIN durable_occurrences o USING(occurrence_id) "
            "WHERE o.schedule_key=? ORDER BY i.created_at DESC LIMIT 20", (row["schedule_key"],))]
        result["occurrences_total"] = conn.execute("SELECT COUNT(*) FROM durable_occurrences WHERE schedule_key=?", (row["schedule_key"],)).fetchone()[0]
        result["intents_total"] = conn.execute("SELECT COUNT(*) FROM durable_monitor_intents i JOIN durable_occurrences o USING(occurrence_id) WHERE o.schedule_key=?", (row["schedule_key"],)).fetchone()[0]
        result["history_truncated"] = result["occurrences_total"] > len(result["occurrences"]) or result["intents_total"] > len(result["intents"])
        canonical(result)  # Validate before a surrounding writer can commit an unreturnable result.
        return result

    def get(self, project_id, schedule_id):
        with self.access.guard(project_id, self.actor, "read"), self.db._runtime_read() as conn:
            row = self._row(conn, self.key(schedule_id))
            require(row["project_id"] == project_id, "Schedule project differs", "identity_mismatch")
            return self._public(conn, row)

    def list(self, project_id):
        with self.access.guard(project_id, self.actor, "read"), self.db._runtime_read() as conn:
            return [self._public(conn, row) for row in conn.execute(
                "SELECT * FROM durable_schedules WHERE project_id=? AND owner_json=? ORDER BY created_at DESC LIMIT 100",
                (project_id, canonical(self.actor)))]

    def create(self, run, definition, *, expected_revision=None, imported=None):
        assert_schedule_control(run, {"runtime.schedule.create", "runtime.schedule.import"})
        record = validate_definition(definition)
        now, key, project = time.time(), self.key(record["schedule_id"]), record["project_id"]
        require(record["expires_at"] > now, "Cannot create an expired schedule")
        if imported is not None:
            from cron.durable_contract import exact, identifier
            exact(imported, "authority source_id source_state unresolved_occurrences")
            identifier(imported["source_id"])
            require(imported["authority"] == "dots_runner" and imported["source_state"] in {"paused", "retired"}
                    and imported["unresolved_occurrences"] == [],
                    "Pause foreign execution and reconcile active/unknown occurrences before import", "schedule_cutover_blocked")
            require(record["schedule_id"] == "import_" + digest(imported["source_id"])[:32],
                    "Imported identity must be deterministic")
        with self.access.guard(project, self.actor, "write"):
            def write(conn):
                self._fence(conn, run)
                require(conn.execute("SELECT COUNT(*) FROM durable_schedule_versions").fetchone()[0] < 16384,
                        "Retained schedule capacity reached", "schedule_capacity")
                old = conn.execute("SELECT * FROM durable_schedules WHERE schedule_key=?", (key,)).fetchone()
                if old and old["version"] == record["version"]:
                    retained = self._version(conn, old)
                    require(old["project_id"] == project and retained["sha256"] == digest(record),
                            "An existing version is immutable", "schedule_immutable")
                    return self._public(conn, old)
                if old:
                    require(old["project_id"] == project and old["state"] == "paused" and old["revision"] == expected_revision,
                            "Pause the owned exact revision before versioning", "schedule_revision_conflict")
                    require(record["version"] == old["version"] + 1, "Next immutable schedule version required")
                    self._no_unresolved(conn, key)
                    remaining = min(old["remaining_checks"], record["budget"]["max_checks"])
                else:
                    require(record["version"] == 1 and expected_revision is None, "New schedule starts at version one")
                    remaining = record["budget"]["max_checks"]
                sid = "sched_" + key[:32] + "_" + str(record["version"])
                conn.execute("INSERT INTO durable_schedule_versions VALUES(?,?,?,?,?,?)", (key, record["version"],
                    canonical(record), digest(record), sid, now))
                due = next_due(record, now - 0.001)
                require(due is not None and due < record["expires_at"], "No future occurrence before expiry")
                if old:
                    conn.execute("UPDATE durable_schedules SET version=?,revision=revision+1,state='paused',next_due=?,"
                        "remaining_checks=?,health='unknown',last_error=NULL,owner_binding_json=? WHERE schedule_key=?",
                        (record["version"], due, remaining, canonical(self.context.identity.to_record()), key))
                else:
                    conn.execute("INSERT INTO durable_schedules(schedule_key,schedule_id,project_id,owner_json,owner_binding_json,"
                        "version,state,next_due,remaining_checks,created_at) VALUES(?,?,?,?,?,?,'paused',?,?,?)",
                        (key, record["schedule_id"], project, canonical(self.actor), canonical(self.context.identity.to_record()),
                         record["version"], due, remaining, now))
                if imported is not None:
                    conn.execute("INSERT INTO durable_schedule_imports VALUES(?,?,?,?,?)", (key, canonical(imported), run.command_id, run.run_id, now))
                return self._public(conn, self._row(conn, key))
            return self.db._execute_write(write)

    @staticmethod
    def _no_unresolved(conn, key):
        require(conn.execute("SELECT 1 FROM durable_occurrences WHERE schedule_key=? AND state IN ('accepted','claimed','outcome_unknown')",
                             (key,)).fetchone() is None, "Resolve accepted or unknown occurrences before ownership changes", "schedule_unresolved")

    def update(self, run, project_id, schedule_id, expected_revision, state):
        assert_schedule_control(run, {"runtime.schedule.update"})
        require(state in {"active", "paused", "revoked"}, "Unsupported schedule state")
        with self.access.guard(project_id, self.actor, "write"):
            def write(conn):
                self._fence(conn, run)
                row = self._row(conn, self.key(schedule_id))
                require(row["project_id"] == project_id and row["revision"] == expected_revision,
                        "Schedule revision changed", "schedule_revision_conflict")
                require(row["state"] != "revoked", "Revocation is terminal", "schedule_revoked")
                if state == "active":
                    self._no_unresolved(conn, row["schedule_key"])
                    record = json.loads(self._version(conn, row)["definition_json"])
                    require(record["expires_at"] > time.time() and row["remaining_checks"] > 0,
                            "Schedule expired or exhausted", "schedule_expired")
                    owner = json.loads(row["owner_binding_json"])
                    require(all(owner[field] == self.context.identity.to_record()[field] for field in
                        ("principal_id", "profile_id", "agent_id", "policy_digest", "profile_home_digest", "lifecycle")),
                        "Resume requires original owner policy; version paused schedule to reauthorize", "identity_mismatch")
                conn.execute("UPDATE durable_schedules SET state=?,revision=revision+1 WHERE schedule_key=?", (state, row["schedule_key"]))
                if state in {"paused", "revoked"}:
                    # Stop unclaimed admissions atomically; a claim that won the
                    # race retains its outcome and checks the stop before I/O.
                    pending = conn.execute("SELECT o.* FROM durable_occurrences o JOIN runtime_commands c "
                        "ON c.session_id=o.session_id AND c.command_id=o.command_id WHERE o.schedule_key=? "
                        "AND o.state='accepted' AND c.status='accepted'", (row["schedule_key"],)).fetchall()
                    for item in pending:
                        stopped = {"reason": "schedule_" + state, "dispatched": False}
                        conn.execute("UPDATE durable_occurrences SET state='cancelled',result_json=? WHERE occurrence_id=?",
                                     (canonical(stopped), item["occurrence_id"]))
                        conn.execute("UPDATE runtime_commands SET status='blocked',result_json=? WHERE session_id=? AND command_id=?",
                                     (canonical(stopped), item["session_id"], item["command_id"]))
                        self.db._append_runtime_event_on_conn(conn, item["session_id"], "command.blocked",
                            {"command_id": item["command_id"], "result": stopped}, 0, run_id=item["run_id"])
                if state == "revoked":
                    conn.execute("UPDATE durable_condition_grants SET state='revoked' WHERE schedule_key=?", (row["schedule_key"],))
                return self._public(conn, self._row(conn, row["schedule_key"]))
            return self.db._execute_write(write)

    def grant(self, run, project_id, schedule_id, *, expected_revision, expires_at, max_age_seconds, max_fires):
        assert_schedule_control(run, {"runtime.schedule.grant"})
        now = time.time()
        require(type(max_fires) is int and 1 <= max_fires <= 100 and type(max_age_seconds) is int
                and 1 <= max_age_seconds <= 3600 and type(expires_at) in (int, float) and now < expires_at <= now + 30 * 86400,
                "Finite fresh action grant required")
        with self.access.guard(project_id, self.actor, "write"):
            def write(conn):
                self._fence(conn, run)
                row = self._row(conn, self.key(schedule_id))
                require(row["project_id"] == project_id and row["revision"] == expected_revision and row["state"] != "revoked",
                        "Schedule revision changed", "schedule_revision_conflict")
                definition = json.loads(self._version(conn, row)["definition_json"])
                action = definition["specification"].get("condition_action")
                require(definition["kind"] == "monitor" and action is not None, "No exact local action is declared")
                target = digest({"project_id": project_id, "action": action, "schedule_sha256": digest(definition)})
                grant_id = "grant_" + digest({"command": run.command_id, "target": target})[:40]
                conn.execute("INSERT INTO durable_condition_grants VALUES(?,?,?,?,?,?,?,'active',?)",
                    (grant_id, row["schedule_key"], row["version"], target, expires_at, max_age_seconds, max_fires, now))
                return {"grant_id": grant_id, "target_digest": target, "expires_at": expires_at, "remaining": max_fires,
                        "external_actions": False}
            return self.db._execute_write(write)

    def reconcile(self, run, project_id, schedule_id, *, occurrence, evidence_ref):
        assert_schedule_control(run, {"runtime.schedule.reconcile"})
        from hermes_cli.domain_media import _read
        _read(self.context, self.db, project_id, evidence_ref)
        with self.access.guard(project_id, self.actor, "write"):
            def write(conn):
                self._fence(conn, run)
                row = self._row(conn, self.key(schedule_id))
                require(row["project_id"] == project_id and row["state"] == "paused", "Pause before reconciliation")
                item = conn.execute("SELECT * FROM durable_occurrences WHERE occurrence_id=? AND schedule_key=?",
                                    (occurrence, row["schedule_key"])).fetchone()
                require(item is not None and item["state"] == "outcome_unknown", "Only unknown occurrences can reconcile")
                result = {"reconciled_by": self.actor, "evidence_ref": evidence_ref, "replayed": False,
                          "external_effects_undone": False, "prior_receipt": json.loads(item["result_json"] or "null")}
                conn.execute("UPDATE durable_occurrences SET state='reconciled',result_json=? WHERE occurrence_id=?",
                             (canonical(result), occurrence))
                return self._public(conn, row)
            return self.db._execute_write(write)

    def admit(self, row, *, now):
        """Accepted command, occurrence, next due and bounded check debit commit atomically."""
        key = row["schedule_key"]
        with self.access.guard(row["project_id"], self.actor, "read"), self.db._runtime_read() as conn:
            current = self._row(conn, key)
            version = self._version(conn, current)
            definition = json.loads(version["definition_json"])
        due = current["next_due"]
        require(due is not None and due <= now, "Occurrence is not due", "schedule_not_due")
        oid = occurrence_id(key, current["version"], due)
        sid = version["session_id"] + "_" + oid[-8:]
        self.db.create_session(sid, source="cron")
        self.db.claim_session_agent_identity(sid, self.context.identity.to_record())
        command = {"schema_version": 1, "command_id": oid, "idempotency_key": oid,
            "expected_revision": None, "operation": "artifact", "payload": {"schedule_key": key,
            "version": current["version"], "due_at": due, "definition_sha256": version["sha256"]}}
        def admission(conn, session_id, encoded, receipt):
            live = self._row(conn, key)
            require(live["state"] == "active" and live["version"] == current["version"]
                    and live["next_due"] == due and live["revision"] == current["revision"],
                    "Schedule changed before admission", "schedule_revision_conflict")
            self._no_unresolved(conn, key)
            require(definition["expires_at"] > now and live["remaining_checks"] > 0,
                    "Schedule expired or exhausted", "schedule_expired")
            require(conn.execute("SELECT COUNT(*) FROM durable_occurrences").fetchone()[0] < 65536,
                    "Retained occurrence capacity reached", "schedule_capacity")
            conn.execute("INSERT INTO durable_occurrences(occurrence_id,schedule_key,version,due_at,session_id,command_id,run_id,state,"
                "accepted_at,deadline_at) VALUES(?,?,?,?,?,?,?,'accepted',?,?)", (oid, key, live["version"], due, session_id,
                oid, receipt["run_id"], now, min(now + definition["budget"]["deadline_seconds"], definition["expires_at"])))
            next_at = next_due(definition, max(due, now))
            conn.execute("UPDATE durable_schedules SET next_due=?,remaining_checks=remaining_checks-1,revision=revision+1 WHERE schedule_key=?",
                         (next_at, key))
        with self.access.guard(row["project_id"], self.actor, "read"):
            self.db.submit_runtime_command(sid, self.actor, command, admission=admission)
        with self.db._runtime_read() as conn:
            return dict(conn.execute("SELECT * FROM durable_occurrences WHERE occurrence_id=?", (oid,)).fetchone()), definition
