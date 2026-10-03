"""Durable local notification policy and grouping on the canonical SessionDB writer.

Notices are immutable evidence projections, not a second delivery queue. Physical
attempts, acknowledgments, uncertainty and explicit replay stay in the BE06 outbox.
"""
from __future__ import annotations

import json
import time

from cron.durable_contract import canonical, digest, exact, identifier, instant, require
from cron.durable_notifications import delivery_hold, validate_notification_policy
from hermes_state_schedules import ScheduleRegistry, assert_schedule_control

CONTROL_METHODS = frozenset({"runtime.monitor.policy.set", "runtime.monitor.snooze", "runtime.monitor.dismiss"})


class MonitorNotifications(ScheduleRegistry):
    def _owned(self, conn, project_id, schedule_id, sid, *, revision=None):
        row = self._row(conn, self.key(schedule_id))
        require(row["project_id"] == project_id, "Schedule project differs", "identity_mismatch")
        if revision is not None:
            require(row["revision"] == revision, "Schedule revision changed", "schedule_revision_conflict")
        definition = json.loads(self._version(conn, row)["definition_json"])
        require(definition["kind"] == "monitor", "Monitor required")
        policy = conn.execute("SELECT * FROM durable_monitor_policies WHERE schedule_key=? AND version=?",
                              (row["schedule_key"], row["version"])).fetchone()
        destinations = conn.execute("SELECT DISTINCT destination_session FROM durable_monitor_policies WHERE schedule_key=?",
                                    (row["schedule_key"],)).fetchall()
        require(all(item[0] == sid for item in destinations),
                "Notification belongs to another conversation", "identity_mismatch")
        return row, definition, policy

    def _view(self, conn, row, policy, cursor=None):
        suffix, params = "", [row["schedule_key"]]
        if cursor is not None:
            exact(cursor, "created_at intent_id")
            instant(cursor["created_at"]); identifier(cursor["intent_id"])
            suffix = "AND (created_at,intent_id)<(?,?) "
            params += [cursor["created_at"], cursor["intent_id"]]
        page = [dict(item) for item in conn.execute("SELECT * FROM durable_monitor_notices WHERE schedule_key=? "
            + suffix + "ORDER BY created_at DESC,intent_id DESC LIMIT 9", params)]
        notices, more = page[:8], len(page) > 8
        next_cursor = canonical({"created_at": notices[-1]["created_at"], "intent_id": notices[-1]["intent_id"]}) if more else None
        config = json.loads(policy["policy_json"]) if policy else None
        now = time.time()
        for item in notices:
            item["payload"] = json.loads(item.pop("payload_json"))
            item.pop("schedule_key")
            item["hold_reason"] = notice_hold(conn, row, policy, item, now)
            if item["delivery_id"]:
                from hermes_state_delivery import _receipt
                delivery = conn.execute("SELECT * FROM delivery_obligations WHERE obligation_id=?", (item["delivery_id"],)).fetchone()
                item["delivery"] = _receipt(delivery)
            else:
                item["delivery"] = None
        total = conn.execute("SELECT COUNT(*) FROM durable_monitor_notices WHERE schedule_key=?", (row["schedule_key"],)).fetchone()[0]
        pending = conn.execute("SELECT COUNT(*) FROM durable_monitor_notices n LEFT JOIN delivery_obligations d ON d.obligation_id=n.delivery_id "
            "WHERE n.schedule_key=? AND n.dismissed_at IS NULL AND (n.delivery_id IS NULL OR d.state!='delivered')",
            (row["schedule_key"],)).fetchone()[0]
        return {"schedule_id": row["schedule_id"], "project_id": row["project_id"], "version": row["version"],
            "revision": row["revision"], "schedule_state": row["state"], "health": row["health"], "policy": config,
            "policy_revision": policy["policy_revision"] if policy else None,
            "destination": {"kind": "local_runtime", "session_id": policy["destination_session"], **self.actor} if policy else None,
            "remaining_deliveries": policy["remaining"] if policy else 0,
            "snoozed_until": policy["snoozed_until"] if policy else None,
            "notices": notices, "notices_total": total, "pending_total": pending, "history_truncated": total > len(notices),
            "next_cursor_json": next_cursor,
            "external_delivery_supported": False, "checks_suppressed_by_snooze": False}

    def get(self, project_id, schedule_id, sid, *, cursor=None):
        with self.access.guard(project_id, self.actor, "read"), self.db._runtime_read() as conn:
            row, _definition, policy = self._owned(conn, project_id, schedule_id, sid)
            return self._view(conn, row, policy, cursor)

    def _control(self, run, project_id, schedule_id, revision, change):
        assert_schedule_control(run, CONTROL_METHODS)
        with self.access.guard(project_id, self.actor, "write"):
            def write(conn):
                self._fence(conn, run)
                row, definition, policy = self._owned(conn, project_id, schedule_id, run.session_id, revision=revision)
                change(conn, row, definition, policy)
                conn.execute("UPDATE durable_schedules SET revision=revision+1 WHERE schedule_key=?", (row["schedule_key"],))
                row, _definition, policy = self._owned(conn, project_id, schedule_id, run.session_id)
                return self._view(conn, row, policy)
            return self.db._execute_write(write)

    def set_policy(self, run, project_id, schedule_id, revision, policy_value):
        def change(conn, row, definition, policy):
            now = time.time()
            require(row["state"] != "revoked" and definition["specification"]["notify_policy"] == "local_runtime",
                    "An exact local notification schedule is required")
            validate_notification_policy(policy_value, now=now, schedule_expires_at=definition["expires_at"])
            number = policy["policy_revision"] + 1 if policy else 1
            # Explicit new policy grants do not silently adopt old pending evidence.
            # Old notices remain visible, including already accepted/unknown delivery.
            conn.execute("UPDATE durable_monitor_notices SET state='superseded_policy' WHERE schedule_key=? AND version=? "
                         "AND state='pending'", (row["schedule_key"], row["version"]))
            conn.execute("INSERT INTO durable_monitor_policies VALUES(?,?,?,?,?,?,?,?,NULL,?) "
                "ON CONFLICT(schedule_key,version) DO UPDATE SET policy_revision=excluded.policy_revision,policy_json=excluded.policy_json,"
                "owner_binding_json=excluded.owner_binding_json,command_id=excluded.command_id,remaining=excluded.remaining,snoozed_until=NULL",
                (row["schedule_key"], row["version"], number, canonical(policy_value), canonical(self.context.identity.to_record()),
                 run.session_id, run.command_id, policy_value["max_deliveries"], now))
        return self._control(run, project_id, schedule_id, revision, change)

    def snooze(self, run, project_id, schedule_id, revision, until_at):
        def change(conn, row, _definition, policy):
            require(policy is not None, "Notification policy required", "notification_policy_required")
            config = json.loads(policy["policy_json"])
            require(until_at is None or time.time() < instant(until_at) <= config["expires_at"], "Snooze needs explicit expiry within the grant")
            conn.execute("UPDATE durable_monitor_policies SET snoozed_until=? WHERE schedule_key=? AND version=?",
                         (until_at, row["schedule_key"], row["version"]))
        return self._control(run, project_id, schedule_id, revision, change)

    def dismiss(self, run, project_id, schedule_id, revision, intent_id):
        def change(conn, row, _definition, _policy):
            item = conn.execute("SELECT * FROM durable_monitor_notices WHERE intent_id=? AND schedule_key=?",
                                (intent_id, row["schedule_key"])).fetchone()
            require(item is not None, "Owned notification not found", "notification_not_found")
            conn.execute("UPDATE durable_monitor_notices SET state='dismissed',dismissed_at=COALESCE(dismissed_at,?) WHERE intent_id=?",
                         (time.time(), intent_id))
            conn.execute("UPDATE durable_monitor_intents SET state='dismissed' WHERE intent_id=?", (intent_id,))
            if item["delivery_id"]:
                delivery = conn.execute("SELECT * FROM delivery_obligations WHERE obligation_id=?", (item["delivery_id"],)).fetchone()
                if delivery["attempts"] == 0:
                    # No physical attempt happened. Retain the old batch and re-group
                    # its undismissed items rather than dropping digest neighbours.
                    conn.execute("UPDATE delivery_obligations SET state='dead_letter',last_error='notification_dismissed' WHERE obligation_id=?",
                                 (item["delivery_id"],))
                    conn.execute("UPDATE durable_monitor_notices SET delivery_id=NULL,state='pending' WHERE delivery_id=? AND dismissed_at IS NULL",
                                 (item["delivery_id"],))
        return self._control(run, project_id, schedule_id, revision, change)


def record_notice(conn, row, occurrence, definition, observation, *, previous_refs):
    """Called inside the fenced occurrence observation/baseline transaction."""
    key, version, oid = row["schedule_key"], row["version"], occurrence["occurrence_id"]
    policy = conn.execute("SELECT * FROM durable_monitor_policies WHERE schedule_key=? AND version=?", (key, version)).fetchone()
    payload = {"intent_id": "notice_" + oid, "occurrence_id": oid, "schedule_id": row["schedule_id"],
        "schedule_version": version, "question": definition["specification"]["question"],
        "source_refs": observation["source_refs"], "previous_source_refs": previous_refs,
        "observed_at": observation["observed_at"], "predicate_version": observation["predicate_version"],
        "source_scope": "retained_local_artifacts", "live_connection_verified": False}
    fingerprint = digest({"source_refs": observation["source_refs"], "projection": observation["projection"],
                          "predicate": definition["specification"]["predicate"]})
    existing = conn.execute("SELECT intent_id FROM durable_monitor_notices WHERE schedule_key=? AND version=? AND fingerprint=?",
                            (key, version, fingerprint)).fetchone()
    if existing:
        conn.execute("UPDATE durable_monitor_intents SET state='deduplicated' WHERE intent_id=?", (payload["intent_id"],))
        conn.execute("UPDATE durable_occurrences SET delivery_state='deduplicated' WHERE occurrence_id=?", (oid,))
        return "deduplicated"
    conn.execute("INSERT INTO durable_monitor_notices VALUES(?,?,?,?,?,?,?,NULL,NULL,?)", (payload["intent_id"], key, version,
        policy["policy_revision"] if policy else None, fingerprint, canonical(payload), "pending" if policy else "awaiting_policy",
        observation["observed_at"]))
    conn.execute("UPDATE durable_monitor_intents SET state=? WHERE intent_id=?",
                 ("pending" if policy else "awaiting_policy", payload["intent_id"]))
    conn.execute("UPDATE durable_occurrences SET delivery_state=? WHERE occurrence_id=?",
                 ("pending" if policy else "awaiting_policy", oid))
    return "pending" if policy else "awaiting_policy"


def notice_hold(conn, row, policy, item, now):
    if item["dismissed_at"] is not None:
        return "dismissed"
    if policy is None or item["policy_revision"] is None:
        return "notification_policy_required"
    if item["version"] != row["version"] or item["policy_revision"] != policy["policy_revision"]:
        return "notification_policy_superseded"
    if row["state"] != "active":
        return "schedule_" + row["state"]
    config = json.loads(policy["policy_json"])
    hold = delivery_hold(config, now, snoozed_until=policy["snoozed_until"], first_pending_at=item["created_at"])
    if hold:
        return hold
    if item["delivery_id"] is None and policy["remaining"] <= 0:
        return "notification_budget_exhausted"
    return None


def admit_notifications(registry, key):
    """One bounded grouping transaction from the existing cron tick; no I/O."""
    now = time.time()
    with registry.db._runtime_read() as conn:
        project_id = registry._row(conn, key)["project_id"]
    with registry.access.guard(project_id, registry.actor, "read"):
        def write(conn):
            from tools.capability_broker import require_live_policy
            require(require_live_policy(require_run=False) == registry.context, "Notification policy changed", "identity_mismatch")
            row = registry._row(conn, key)
            definition = json.loads(registry._version(conn, row)["definition_json"])
            policy = conn.execute("SELECT * FROM durable_monitor_policies WHERE schedule_key=? AND version=?", (key, row["version"])).fetchone()
            if policy is None or definition["expires_at"] <= now or row["state"] != "active":
                return None
            require(json.loads(policy["owner_binding_json"]) == registry.context.identity.to_record(),
                    "Local notification owner changed", "identity_mismatch")
            registry.db._delivery_actor_on_conn(conn, policy["destination_session"], registry.actor)
            items = conn.execute("SELECT * FROM durable_monitor_notices WHERE schedule_key=? AND version=? AND policy_revision=? "
                "AND state='pending' AND dismissed_at IS NULL ORDER BY created_at,intent_id LIMIT 8",
                (key, row["version"], policy["policy_revision"])).fetchall()
            if not items or notice_hold(conn, row, policy, items[0], now):
                return None
            # Immediate notices contain one change. Digests preserve every pending
            # item in the bounded oldest-first batch, never only the newest value.
            config = json.loads(policy["policy_json"])
            if config["digest_seconds"] == 0:
                items = items[:1]
            payload = {"schema_version": 1, "schedule_id": row["schedule_id"], "schedule_version": row["version"],
                "policy_revision": policy["policy_revision"], "kind": "digest" if config["digest_seconds"] else "change",
                "items": [json.loads(item["payload_json"]) for item in items]}
            encoded, checksum = canonical(payload), digest(payload)
            did = "monitor_" + digest({"schedule": key, "payload": checksum})[:48]
            destination = {"kind": "local_runtime", "session_id": policy["destination_session"], **registry.actor}
            descriptor = {"artifact_id": did, "version": 1, "sha256": checksum, "size": len(encoded.encode()),
                          "mime": "application/vnd.hermes.monitor-notice+json"}
            metadata = {"actor": registry.actor, "destination": destination, "artifact": descriptor,
                        "command_id": items[0]["intent_id"], "monitor_notification": {"delivery_id": did}}
            conn.execute("INSERT INTO durable_monitor_batches VALUES(?,?,?,?,?,?,?,?)", (did, key, row["version"],
                policy["policy_revision"], policy["destination_session"], encoded, checksum, now))
            conn.execute("INSERT INTO delivery_obligations(obligation_id,session_key,platform,chat_id,content,state,attempts,created_at,"
                "updated_at,authority,metadata_json,deadline_at,retention_until,next_attempt_at,max_attempts,acknowledgement_json) "
                "VALUES(?,?,?,?,?,'pending',0,?,?,'runtime.v1',?,?,?,?,3,?)", (did, policy["destination_session"], "local_runtime",
                registry.actor["principal_id"], "", now, now, canonical(metadata), min(now + 86400, config["expires_at"], definition["expires_at"]),
                now + 30 * 86400, now, canonical({"level": "none", "components": {"text": "not_sent", "artifact": "not_sent"}, "platform_ids": []})))
            for item in items:
                conn.execute("UPDATE durable_monitor_notices SET state='queued',delivery_id=? WHERE intent_id=?", (did, item["intent_id"]))
                conn.execute("UPDATE durable_monitor_intents SET state='queued' WHERE intent_id=?", (item["intent_id"],))
            conn.execute("UPDATE durable_monitor_policies SET remaining=remaining-1 WHERE schedule_key=? AND version=?", (key, row["version"]))
            return did
        return registry.db._execute_write(write)


def guard_notification_delivery(conn, row, *, now):
    """Run inside BE06 claim/repair transaction, preserving old accepted receipts."""
    metadata = json.loads(row["metadata_json"])
    if "monitor_notification" not in metadata:
        return
    batch = conn.execute("SELECT * FROM durable_monitor_batches WHERE delivery_id=?", (row["obligation_id"],)).fetchone()
    require(batch is not None and batch["sha256"] == digest(json.loads(batch["payload_json"]))
            and batch["sha256"] == metadata["artifact"]["sha256"] and batch["destination_session"] == row["session_key"],
            "Notification receipt differs from retained evidence", "notification_digest_mismatch")
    schedule = conn.execute("SELECT * FROM durable_schedules WHERE schedule_key=?", (batch["schedule_key"],)).fetchone()
    policy = conn.execute("SELECT * FROM durable_monitor_policies WHERE schedule_key=? AND version=?",
                          (batch["schedule_key"], batch["version"])).fetchone()
    items = conn.execute("SELECT * FROM durable_monitor_notices WHERE delivery_id=?", (row["obligation_id"],)).fetchall()
    require(items, "Notification batch is no longer pending", "notification_dismissed")
    for item in items:
        hold = notice_hold(conn, schedule, policy, item, now)
        require(hold is None, "Notification delivery is held", hold or "notification_held")


def sync_notification_delivery(conn, delivery_id):
    """Project outbox truth onto existing occurrence/intent receipts, in its writer."""
    delivery = conn.execute("SELECT state FROM delivery_obligations WHERE obligation_id=?", (delivery_id,)).fetchone()
    items = conn.execute("SELECT intent_id,payload_json,dismissed_at FROM durable_monitor_notices WHERE delivery_id=?", (delivery_id,)).fetchall()
    for item in items:
        oid = json.loads(item["payload_json"])["occurrence_id"]
        conn.execute("UPDATE durable_occurrences SET delivery_state=? WHERE occurrence_id=?", (delivery["state"], oid))
        if item["dismissed_at"] is None:
            conn.execute("UPDATE durable_monitor_intents SET state=? WHERE intent_id=?", (delivery["state"], item["intent_id"]))
