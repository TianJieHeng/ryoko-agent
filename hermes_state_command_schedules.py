"""Conversation schedule controls over the existing registry and command journal.

No model or tool execution lives here. The cron owner produces queue references;
only the ordinary runtime.command consumer may execute them.
"""
from __future__ import annotations

import json
import time

from cron.durable_contract import canonical, digest, require, validate_definition, next_due
from hermes_state_schedules import ScheduleRegistry

_COMMAND_CONTROLS = frozenset({"runtime.schedule.create", "runtime.schedule.import", "runtime.schedule.update",
                              "runtime.schedule.run_now", "runtime.schedule.cutover"})


def public_command_schedule(registry, conn, row):
    version = registry._version(conn, row)
    definition = json.loads(version["definition_json"])
    result = {key: row[key] for key in ("schedule_id", "project_id", "version", "revision", "state", "next_due",
        "remaining_checks", "health", "last_success", "last_error")}
    declaration = conn.execute("SELECT declaration_json FROM durable_schedule_imports WHERE schedule_key=?",
                               (row["schedule_key"],)).fetchone()
    cutover = conn.execute("SELECT source_id,retirement_receipt FROM durable_schedule_cutovers WHERE schedule_key=?",
                          (row["schedule_key"],)).fetchone()
    occurrences = []
    for item in conn.execute("SELECT o.*,c.status command_status,c.run_id,c.claimed_generation,c.result_json "
        "FROM durable_command_occurrences o LEFT JOIN runtime_commands c USING(session_id,command_id) "
        "WHERE o.schedule_key=? ORDER BY o.created_at DESC,o.occurrence_id DESC LIMIT 50", (row["schedule_key"],)):
        state = item["state"] if item["state"] == "outcome_unknown" else item["command_status"] or item["state"]
        outcome = json.loads(item["result_json"] or "{}")
        reference = outcome.get("runtime_result") or {}
        delivery_id = reference.get("delivery_id")
        delivery = conn.execute("SELECT state FROM delivery_obligations WHERE obligation_id=? AND session_key=? AND authority='runtime.v1'",
                                (delivery_id, item["session_id"])).fetchone() if delivery_id else None
        mission = outcome.get("mission") or {}
        occurrences.append({"occurrence_id": item["occurrence_id"], "version": item["version"],
            "due_at": item["due_at"], "session_id": item["session_id"], "command_id": item["command_id"],
            "run_id": item["run_id"], "state": state, "generation": item["claimed_generation"],
            "accepted_at": item["created_at"], "mission_id": mission.get("mission_id"),
            "detail": json.loads(item["detail_json"]), "delivery_id": delivery_id,
            "delivery_state": delivery[0] if delivery else "not_requested"})
    total = conn.execute("SELECT COUNT(*) FROM durable_command_occurrences WHERE schedule_key=?", (row["schedule_key"],)).fetchone()[0]
    result.update(owner_agent_id=registry.actor["agent_id"], sha256=version["sha256"], definition=definition,
        authority="runtime_command_queue", occurrences=occurrences, occurrences_total=total, history_truncated=total > len(occurrences),
        import_declaration=json.loads(declaration[0]) if declaration else None, cutover_attestation=dict(cutover) if cutover else None,
        foreign_cutover_verified=False if declaration else None, scheduler_pause_cancels_running=False,
        external_effect_authority="existing_runtime_policy", execution_requires_live_owned_session=True)
    canonical(result)
    return result


class CommandScheduleRegistry(ScheduleRegistry):
    def control(self, agent, request, name):
        """Short, idempotent control commits without taking an inference turn lease."""
        from agent.runtime_commands import _RUN
        from tui_gateway import server
        from tools.capability_broker import require_live_policy
        transport, session = server._current_session_steer_authority(request.session_id)
        require(name in _COMMAND_CONTROLS and server._current_rpc_method.get() == name and _RUN.get() is None
                and transport is not None and session is not None and session.get("agent") is agent,
                "Owned human schedule control required", "schedule_control_required")
        require(require_live_policy(require_run=False) == self.context, "Policy changed", "identity_mismatch")
        data = request.model_dump(exclude={"session_id", "schema_version", "command_id"})
        sid = self.db.read_runtime_snapshot(agent.session_id)["session_id"]
        if name == "runtime.schedule.run_now":
            return self.run_now(sid, request, data)
        definition = validate_definition(json.loads(request.definition_json)) if name in {
            "runtime.schedule.create", "runtime.schedule.import"} else None
        project = definition["project_id"] if definition else request.project_id
        command = {"schema_version": 1, "command_id": request.command_id, "idempotency_key": request.command_id,
            "expected_revision": None, "operation": "artifact", "payload": {"mode": name, "request_sha256": digest(data)}}
        if name == "runtime.schedule.update":
            command["payload"]["schedule_state"] = request.state
        def admission(conn, root, _encoded, receipt):
            handlers = {"runtime.schedule.create": lambda: self._create(conn, sid, agent, request, definition, control_run_id=receipt["run_id"]),
                "runtime.schedule.import": lambda: self._create(conn, sid, agent, request, definition, control_run_id=receipt["run_id"]),
                "runtime.schedule.update": lambda: self._update(conn, request),
                "runtime.schedule.cutover": lambda: self._cutover(conn, request)}
            result = handlers[name]()
            conn.execute("UPDATE runtime_commands SET status='completed',result_json=? WHERE session_id=? AND command_id=?",
                         (canonical(result), root, request.command_id))
            self.db._append_runtime_event_on_conn(conn, root, "command.completed",
                {"command_id": request.command_id, "result": {"schedule_id": result["schedule_id"], "revision": result["revision"]}},
                conn.execute("SELECT turn_owner_generation FROM sessions WHERE id=?", (root,)).fetchone()[0],
                run_id=receipt["run_id"])
        with self.access.guard(project, self.actor, "write"):
            self.db.submit_runtime_command(sid, self.actor, command, admission=admission)
            return self.db.read_runtime_command(sid, request.command_id)["result"]

    def _create(self, conn, sid, agent, request, definition, *, control_run_id):
        require(definition["kind"] == "command", "Conversation command schedule required")
        now, key = time.time(), self.key(definition["schedule_id"])
        imported = json.loads(request.import_json) if hasattr(request, "import_json") else None
        require(imported is not None or definition["expires_at"] > now, "Standing authority expired", "schedule_expired")
        target = self.db._runtime_session_on_conn(conn, definition["specification"]["session_id"])
        require(target == sid, "Schedule target must be its owned canonical conversation", "identity_mismatch")
        # Canonical root is a stable target across transcript compression.
        require(definition["specification"]["session_id"] == sid, "Use canonical conversation session_id", "identity_mismatch")
        if imported is not None:
            from cron.command_schedule_contract import validate_import
            validate_import(imported)
            require(definition["schedule_id"] == "import_" + digest(imported["source_id"])[:32], "Stable import identity required")
        old = conn.execute("SELECT * FROM durable_schedules WHERE schedule_key=?", (key,)).fetchone()
        if old:
            retained = self._version(conn, old)
            require(json.loads(retained["definition_json"])["kind"] == "command", "Schedule kind cannot change", "schedule_immutable")
            if old["version"] == definition["version"]:
                declaration = conn.execute("SELECT declaration_json FROM durable_schedule_imports WHERE schedule_key=?", (key,)).fetchone()
                require(retained["sha256"] == digest(definition) and
                        (imported is None or (json.loads(declaration[0]) if declaration else None) == imported),
                        "Existing schedule intent is immutable", "schedule_immutable")
                return self._public(conn, old)
            require(old["project_id"] == definition["project_id"] and old["state"] == "paused"
                    and old["revision"] == request.expected_revision and definition["version"] == old["version"] + 1,
                    "Pause exact revision before editing", "schedule_revision_conflict")
            require(imported is None, "Import declaration is immutable; edit with schedule.create", "schedule_immutable")
            self.no_unresolved(conn, key)
        else:
            require(definition["version"] == 1 and request.expected_revision is None, "New schedule starts at version one")
        require(conn.execute("SELECT COUNT(*) FROM durable_schedule_versions").fetchone()[0] < 16384, "Schedule capacity reached", "schedule_capacity")
        due = next_due(definition, now - 0.001)
        # Imported one-shots may be historical, and must remain inspectable.
        require(imported is not None or due is not None and due < definition["expires_at"], "No occurrence before expiry")
        remaining = min(old["remaining_checks"], definition["budget"]["max_checks"]) if old else definition["budget"]["max_checks"]
        conn.execute("INSERT INTO durable_schedule_versions VALUES(?,?,?,?,?,?)", (key, definition["version"], canonical(definition),
            digest(definition), sid, now))
        policy = getattr(agent, "_runtime_budget_policy", None)
        conn.execute("INSERT INTO durable_command_schedule_bindings VALUES(?,?,?,?)", (key, definition["version"], sid,
            policy.snapshot if policy is not None else None))
        if old:
            conn.execute("UPDATE durable_schedules SET version=?,revision=revision+1,next_due=?,remaining_checks=?,health='unknown',last_error=NULL,owner_binding_json=? WHERE schedule_key=?",
                         (definition["version"], due, remaining, canonical(self.context.identity.to_record()), key))
        else:
            conn.execute("INSERT INTO durable_schedules(schedule_key,schedule_id,project_id,owner_json,owner_binding_json,version,state,next_due,remaining_checks,created_at) "
                "VALUES(?,?,?,?,?,?,'paused',?,?,?)", (key, definition["schedule_id"], definition["project_id"], canonical(self.actor),
                canonical(self.context.identity.to_record()), definition["version"], due, remaining, now))
        if imported is not None:
            conn.execute("INSERT INTO durable_schedule_imports VALUES(?,?,?,?,?)", (key, canonical(imported), request.command_id, control_run_id, now))
            for item in imported["occurrences"]:
                slot = "legacy:" + item["source_occurrence_id"]
                oid = "occ_" + digest({"schedule": key, "source_occurrence_id": item["source_occurrence_id"]})[:48]
                conn.execute("INSERT INTO durable_command_occurrences VALUES(?,?,?,?,?,?,NULL,?,?,?)", (oid, key, definition["version"], slot,
                    item["due_at"], sid, item["state"], now, canonical({"legacy_occurrence_id": item["source_occurrence_id"], "imported": True})))
        return self._public(conn, self._row(conn, key))

    def no_unresolved(self, conn, key):
        require(conn.execute("SELECT 1 FROM durable_command_occurrences o LEFT JOIN runtime_commands c USING(session_id,command_id) "
            "WHERE o.schedule_key=? AND (c.status IN ('accepted','claimed') OR o.state='outcome_unknown') LIMIT 1", (key,)).fetchone() is None,
            "Unresolved scheduled commands remain", "schedule_unresolved")

    def _revision_row(self, conn, request):
        row = self._row(conn, self.key(request.schedule_id))
        require(row["project_id"] == request.project_id and row["revision"] == request.expected_revision,
                "Schedule revision changed", "schedule_revision_conflict")
        require(json.loads(self._version(conn, row)["definition_json"])["kind"] == "command", "Command schedule required")
        return row

    def _activation(self, conn, row, definition, now):
        from hermes_state_runtime_controls import assert_owner_running_on_conn
        assert_owner_running_on_conn(conn, self.actor)
        from tools.capability_broker import require_live_policy
        require(require_live_policy(require_run=False) == self.context, "Schedule policy changed", "identity_mismatch")
        require(row["state"] != "revoked", "Schedule revoked", "schedule_revoked")
        require(definition["expires_at"] > now and row["remaining_checks"] > 0, "Standing authority expired or exhausted", "schedule_expired")
        owner = json.loads(row["owner_binding_json"])
        require(all(owner[field] == self.context.identity.to_record()[field] for field in
            ("principal_id", "profile_id", "agent_id", "policy_digest", "profile_home_digest", "lifecycle")),
            "Original schedule owner policy required", "identity_mismatch")
        imported = conn.execute("SELECT 1 FROM durable_schedule_imports WHERE schedule_key=?", (row["schedule_key"],)).fetchone()
        require(not imported or conn.execute("SELECT 1 FROM durable_schedule_cutovers WHERE schedule_key=?", (row["schedule_key"],)).fetchone(),
                "Legacy retirement attestation required", "schedule_cutover_blocked")
        require(conn.execute("SELECT 1 FROM durable_command_occurrences WHERE schedule_key=? AND state='outcome_unknown'", (row["schedule_key"],)).fetchone() is None,
                "Unknown scheduled outcome requires reconciliation", "schedule_unresolved")
        lost = conn.execute("SELECT 1 FROM durable_command_occurrences o JOIN runtime_commands c USING(session_id,command_id) "
            "LEFT JOIN session_turn_leases l ON l.conversation_id=o.session_id WHERE o.schedule_key=? AND c.status='claimed' AND "
            "(l.expires_at IS NULL OR l.expires_at<=? OR c.claimed_holder!=l.holder OR c.claimed_generation!=l.generation) LIMIT 1",
            (row["schedule_key"], now)).fetchone()
        require(lost is None, "Lost claimed occurrence requires reconciliation", "schedule_unresolved")

    def _update(self, conn, request):
        row = self._revision_row(conn, request)
        require(row["state"] != "revoked", "Revocation is terminal", "schedule_revoked")
        definition = json.loads(self._version(conn, row)["definition_json"])
        if request.state == "active":
            self._activation(conn, row, definition, time.time())
        conn.execute("UPDATE durable_schedules SET state=?,revision=revision+1 WHERE schedule_key=?", (request.state, row["schedule_key"]))
        # This changes future producer admissions only. Existing commands own
        # their outcomes; cancellation uses the ordinary mission/command API.
        return self._public(conn, self._row(conn, row["schedule_key"]))

    def _cutover(self, conn, request):
        row = self._revision_row(conn, request)
        require(row["state"] == "paused" and request.unresolved_occurrences == [],
                "Pause and reconcile legacy claims before cutover", "schedule_cutover_blocked")
        imported = conn.execute("SELECT declaration_json FROM durable_schedule_imports WHERE schedule_key=?", (row["schedule_key"],)).fetchone()
        require(imported is not None and json.loads(imported[0])["source_id"] == request.source_id,
                "Exact legacy source required", "schedule_cutover_blocked")
        old = conn.execute("SELECT retirement_receipt FROM durable_schedule_cutovers WHERE schedule_key=?", (row["schedule_key"],)).fetchone()
        require(old is None or old[0] == request.retirement_receipt, "Cutover receipt is immutable", "idempotency_conflict")
        conn.execute("INSERT OR IGNORE INTO durable_schedule_cutovers VALUES(?,?,?,?,?)", (row["schedule_key"], request.source_id,
                     request.retirement_receipt, request.command_id, time.time()))
        conn.execute("UPDATE durable_schedules SET revision=revision+1 WHERE schedule_key=?", (row["schedule_key"],))
        return self._public(conn, self._row(conn, row["schedule_key"]))

    def run_now(self, sid, request, intent):
        from hermes_state_runtime import RuntimeStoreError
        key = self.key(request.schedule_id)
        slot = "manual:" + request.command_id
        def replay(conn):
            previous = conn.execute("SELECT * FROM durable_schedule_manual_requests WHERE session_id=? AND request_id=?",
                                    (sid, request.command_id)).fetchone()
            if previous is None:
                return None
            require(previous["intent_sha256"] == digest(intent), "Run-now intent changed", "idempotency_conflict")
            old = conn.execute("SELECT * FROM durable_command_occurrences WHERE occurrence_id=?", (previous["occurrence_id"],)).fetchone()
            return self.occurrence_receipt(conn, old)
        with self.access.guard(request.project_id, self.actor, "write"):
            with self.db._runtime_read() as conn:
                previous = replay(conn)
                if previous is not None:
                    return previous
                row = dict(self._revision_row(conn, request))
                binding = conn.execute("SELECT session_id FROM durable_command_schedule_bindings WHERE schedule_key=? AND version=?", (key, row["version"])).fetchone()
                require(binding[0] == sid, "Owned target conversation required", "identity_mismatch")
            try:
                return self.admit_command(row, now=time.time(), manual_slot=slot, intent_sha256=digest(intent))
            except RuntimeStoreError:
                # Another exact request may have committed between the read and
                # writer fence. Return only its immutable original identity.
                with self.db._runtime_read() as conn:
                    previous = replay(conn)
                    if previous is not None:
                        return previous
                raise

    def occurrence_receipt(self, conn, occurrence):
        command = self.db._runtime_command_on_conn(conn, occurrence["session_id"], occurrence["command_id"]) if occurrence["command_id"] else None
        return {"schedule_id": self._row(conn, occurrence["schedule_key"])["schedule_id"],
            "occurrence_id": occurrence["occurrence_id"], "session_id": occurrence["session_id"],
            "command_id": occurrence["command_id"], "command_receipt": json.loads(command["receipt_json"]) if command else None,
            "state": command["status"] if command else occurrence["state"], "dispatch_performed": False}

    def admit_command(self, row, *, now, manual_slot=None, intent_sha256=None):
        from cron.command_schedule_runtime import admit_command
        return admit_command(self, row, now=now, manual_slot=manual_slot, intent_sha256=intent_sha256)
