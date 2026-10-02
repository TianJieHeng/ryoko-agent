"""Bounded durable scheduling metadata for the existing runtime command journal.

There is one prompt copy (runtime_commands) and one execution loop. Reservations
here govern launch slots, not provider acceptance or remote effect completion.
"""
from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from pathlib import Path

from hermes_state_runtime import RuntimeStoreError


@dataclass(frozen=True)
class AdmissionPolicy:
    max_active: int = 4
    max_queued: int = 128
    max_per_principal: int = 32
    max_payload_bytes: int = 256 * 1024
    max_queue_bytes: int = 4 * 1024 * 1024
    max_database_bytes: int = 256 * 1024 * 1024
    ttl_seconds: float = 300
    interactive_boost_seconds: float = 15
    launch_lease_seconds: float = 30

    def __post_init__(self):
        integers = (self.max_active, self.max_queued, self.max_per_principal,
                    self.max_payload_bytes, self.max_queue_bytes, self.max_database_bytes)
        durations = (self.ttl_seconds, self.interactive_boost_seconds, self.launch_lease_seconds)
        if any(type(v) is not int or v <= 0 for v in integers) or any(
                isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0
                for v in durations):
            raise ValueError("Admission limits must be finite and positive")


def _require(condition, code):
    if not condition:
        raise RuntimeStoreError(code, code.replace("_", " "))


class AdmissionQueue:
    def __init__(self, db, policy: AdmissionPolicy | None = None):
        self.db, self.policy = db, policy or AdmissionPolicy()

    def submit(self, agent, envelope, *, workload="interactive", deadline=None):
        from agent.runtime_commands import _envelope
        db, sid, actor, command = _envelope(agent, envelope)
        _require(db is self.db and command["operation"] == "submit", "invalid_command")
        _require(workload in {"interactive", "background"}, "invalid_workload")
        existing = db.read_runtime_command(sid, command["command_id"])
        accepted_at = (db.read_runtime_run_accepted_at(sid, existing["receipt"]["run_id"])
                       if existing is not None else None)
        now = accepted_at if accepted_at is not None else time.time()
        expires = min(now + self.policy.ttl_seconds, deadline) if deadline is not None else now + self.policy.ttl_seconds
        _require(math.isfinite(expires) and (existing is not None or expires > now), "queue_expired")

        def admit(conn, root_sid, command_json, receipt):
            self._reconcile(conn, time.time())
            _require(expires > time.time(), "queue_expired")
            control = conn.execute("SELECT draining FROM runtime_admission_control WHERE id=1").fetchone()
            _require(control is None or not control[0], "admission_draining")
            accepted = db._runtime_command_on_conn(conn, root_sid, receipt["command_id"])
            budget_snapshot = accepted["budget_policy_json"]
            size = len(command_json.encode("utf-8")) + len((budget_snapshot or "").encode("utf-8"))
            _require(size <= self.policy.max_payload_bytes, "queue_payload_limit")
            rows = conn.execute("SELECT principal_id,payload_bytes FROM runtime_admission_queue "
                                "WHERE state='queued'").fetchall()
            _require(len(rows) < self.policy.max_queued, "queue_full")
            _require(sum(row["principal_id"] == actor["principal_id"] for row in rows)
                     < self.policy.max_per_principal, "principal_queue_full")
            _require(sum(row["payload_bytes"] for row in rows) + size <= self.policy.max_queue_bytes,
                     "queue_byte_limit")
            # Bound this admission's disk footprint, including WAL and headroom for
            # its terminal receipt. Other SessionDB producers retain their own policy.
            page_bytes = conn.execute("PRAGMA page_count").fetchone()[0] * conn.execute("PRAGMA page_size").fetchone()[0]
            wal = Path(str(self.db.db_path) + "-wal")
            wal_bytes = wal.stat().st_size if wal.exists() else 0
            _require(page_bytes + wal_bytes + size * 2 + 65536 <= self.policy.max_database_bytes,
                     "admission_disk_limit")
            conn.execute("INSERT INTO runtime_admission_queue(session_id,command_id,principal_id,workload,"
                         "enqueued_at,expires_at,payload_bytes,budget_policy_json) VALUES(?,?,?,?,?,?,?,?)",
                         (root_sid, receipt["command_id"], actor["principal_id"], workload, now, expires, size,
                          budget_snapshot))

        policy = getattr(agent, "_runtime_budget_policy", None)
        receipt = db.submit_runtime_command(sid, actor=actor, command=command, admission=admit,
                                           budget_policy_json=policy.snapshot if policy is not None else None)
        if existing is not None:
            # An explicitly retried pre-queue BE02 acceptance may acquire its
            # first queue reference. Never reenqueue a claimed/terminal command
            # or replace an existing reference, even after its queue lease ends.
            def adopt(conn):
                root = db._runtime_session_on_conn(conn, sid)
                row = db._runtime_command_on_conn(conn, root, receipt["command_id"])
                queued = conn.execute("SELECT 1 FROM runtime_admission_queue WHERE session_id=? AND command_id=?",
                                      (root, receipt["command_id"])).fetchone()
                if row["status"] == "accepted" and queued is None:
                    try:
                        admit(conn, root, row["command_json"], receipt)
                    except RuntimeStoreError as exc:
                        self._terminal(conn, {"session_id": root, "command_id": receipt["command_id"]},
                                       "rejected", exc.code, time.time())
            db._execute_write(adopt)
        return receipt

    def _terminal(self, conn, row, state, reason, now):
        command = self.db._runtime_command_on_conn(conn, row["session_id"], row["command_id"])
        if command is not None and command["status"] == "accepted":
            status = "cancelled" if state == "cancelled" else "blocked"
            result = {"outcome": reason, "admission_state": state, "completed": False,
                      "dispatched": False, "remote_effects_undone": False}
            conn.execute("UPDATE runtime_commands SET status=?,result_json=? WHERE session_id=? AND command_id=?",
                         (status, json.dumps(result), row["session_id"], row["command_id"]))
            generation = conn.execute("SELECT turn_owner_generation FROM sessions WHERE id=?", (row["session_id"],)).fetchone()[0]
            self.db._append_runtime_event_on_conn(conn, row["session_id"], f"command.{status}",
                {"command_id": row["command_id"], "result": result}, generation, run_id=command["run_id"])
        conn.execute("UPDATE runtime_admission_queue SET state=?,reason=?,finished_at=? "
                     "WHERE session_id=? AND command_id=?", (state, reason, now, row["session_id"], row["command_id"]))

    def _reconcile(self, conn, now):
        rows = conn.execute("SELECT q.*,c.status AS command_status FROM runtime_admission_queue q "
                            "JOIN runtime_commands c USING(session_id,command_id) WHERE q.state IN ('queued','running')").fetchall()
        for row in rows:
            if row["command_status"] not in {"accepted", "claimed"}:
                self._terminal(conn, row, "finished", row["command_status"], now)
            elif row["command_status"] == "accepted":
                if row["expires_at"] <= now:
                    self._terminal(conn, row, "expired", "queue_expired", now)
                elif row["state"] == "running" and row["lease_until"] <= now:
                    # Only an unclaimed launch may be recovered. Claimed effects
                    # remain uncertain; they are never replayed by this scheduler.
                    conn.execute("UPDATE runtime_admission_queue SET state='queued',worker_id=NULL,lease_until=NULL "
                                 "WHERE session_id=? AND command_id=?", (row["session_id"], row["command_id"]))
        # Terminal facts live in the command journal; prune only redundant queue
        # metadata, retaining bounded recent service history for fairness.
        conn.execute("DELETE FROM runtime_admission_queue WHERE rowid IN (SELECT rowid FROM runtime_admission_queue "
                     "WHERE state NOT IN ('queued','running') ORDER BY finished_at DESC LIMIT -1 OFFSET 1024)")

    def reserve_next(self, worker_id: str, eligible_sessions) -> dict | None:
        eligible = frozenset(eligible_sessions)
        if not eligible:
            self.reconcile()
            return None
        now = time.time()
        def write(conn):
            self._reconcile(conn, now)
            draining = conn.execute("SELECT draining FROM runtime_admission_control WHERE id=1").fetchone()
            if draining is not None and draining[0]:
                return None
            active = conn.execute("SELECT session_id FROM runtime_admission_queue WHERE state='running'").fetchall()
            if len(active) >= self.policy.max_active:
                return None
            busy = {row["session_id"] for row in active}
            rows = conn.execute("SELECT * FROM runtime_admission_queue WHERE state='queued' ORDER BY enqueued_at,command_id").fetchall()
            rows = [row for row in rows if row["session_id"] in eligible and row["session_id"] not in busy]
            if not rows:
                return None
            served = dict(conn.execute("SELECT principal_id,MAX(started_at) FROM runtime_admission_queue "
                                       "WHERE started_at IS NOT NULL GROUP BY principal_id").fetchall())
            # Round-robin principals by last service. Within one principal the
            # finite priority boost lets old background jobs outrank new input.
            row = min(rows, key=lambda r: (served.get(r["principal_id"], 0),
                r["enqueued_at"] - (self.policy.interactive_boost_seconds if r["workload"] == "interactive" else 0),
                r["command_id"]))
            conn.execute("UPDATE runtime_admission_queue SET state='running',worker_id=?,lease_until=?,started_at=? "
                         "WHERE session_id=? AND command_id=?", (worker_id, now + self.policy.launch_lease_seconds,
                         now, row["session_id"], row["command_id"]))
            return {**dict(row), "state": "running", "worker_id": worker_id}
        return self.db._execute_write(write)

    def reject_launch(self, session_id, command_id, reason="launch_refused"):
        def write(conn):
            sid = self.db._runtime_session_on_conn(conn, session_id)
            row = conn.execute("SELECT * FROM runtime_admission_queue WHERE session_id=? AND command_id=?",
                               (sid, command_id)).fetchone()
            if row is not None:
                command = self.db._runtime_command_on_conn(conn, sid, command_id)
                if command["status"] == "accepted":
                    self._terminal(conn, row, "rejected", reason, time.time())
        self.db._execute_write(write)

    def cancel_session(self, session_id, reason="cancelled") -> int:
        def write(conn):
            sid = self.db._runtime_session_on_conn(conn, session_id)
            rows = conn.execute("SELECT q.* FROM runtime_admission_queue q JOIN runtime_commands c "
                "USING(session_id,command_id) WHERE q.session_id=? AND c.status='accepted' "
                "AND q.state IN ('queued','running')", (sid,)).fetchall()
            for row in rows:
                self._terminal(conn, row, "cancelled", reason, time.time())
            return len(rows)
        return self.db._execute_write(write)

    def cancel_command(self, agent, envelope):
        """Accept an idempotent cancel when only queued work owns this session.

        Returns None when no queue target exists, preserving no_active_run for
        callers. This path has no external effect and needs no execution lease.
        """
        from agent.runtime_commands import _envelope
        db, sid, actor, command = _envelope(agent, envelope)
        _require(command["operation"] == "cancel", "invalid_command")
        with db._runtime_read() as conn:
            root = db._runtime_session_on_conn(conn, sid)
            existing = db._runtime_command_on_conn(conn, root, command["command_id"])
            pending = conn.execute("SELECT 1 FROM runtime_admission_queue q JOIN runtime_commands c "
                "USING(session_id,command_id) WHERE q.session_id=? AND c.status='accepted' "
                "AND q.state IN ('queued','running') LIMIT 1", (root,)).fetchone()
        if existing is None and pending is None:
            return None
        def cancel(conn, root_sid, _encoded, receipt):
            rows = conn.execute("SELECT q.* FROM runtime_admission_queue q JOIN runtime_commands c "
                "USING(session_id,command_id) WHERE q.session_id=? AND c.status='accepted' "
                "AND q.state IN ('queued','running')", (root_sid,)).fetchall()
            for row in rows:
                self._terminal(conn, row, "cancelled", "cancelled_before_launch", time.time())
            result = {"outcome": "cancel_requested", "cancelled_queued": len(rows), "provider_cancelled": False,
                      "cancellation": {"request_id": receipt["command_id"], "requested_at": time.time(),
                        "local_state": "stopped", "upstream_ack": None, "pending_effect_ids": [],
                        "pending_handles": [], "partial_result_available": False, "remote_effects_undone": False}}
            conn.execute("UPDATE runtime_commands SET status='completed',result_json=? WHERE session_id=? AND command_id=?",
                         (json.dumps(result), root_sid, receipt["command_id"]))
            generation = conn.execute("SELECT turn_owner_generation FROM sessions WHERE id=?", (root_sid,)).fetchone()[0]
            db._append_runtime_event_on_conn(conn, root_sid, "command.completed",
                {"command_id": receipt["command_id"], "result": result}, generation, run_id=receipt["run_id"])
        return db.submit_runtime_command(sid, actor=actor, command=command, admission=cancel)

    def set_draining(self, draining=True, *, reject_queued=False):
        def write(conn):
            conn.execute("INSERT INTO runtime_admission_control(id,draining) VALUES(1,?) "
                         "ON CONFLICT(id) DO UPDATE SET draining=excluded.draining", (int(draining),))
            if reject_queued:
                rows = conn.execute("SELECT q.* FROM runtime_admission_queue q JOIN runtime_commands c "
                    "USING(session_id,command_id) WHERE c.status='accepted' AND q.state IN ('queued','running')").fetchall()
                for row in rows:
                    self._terminal(conn, row, "rejected", "admission_draining", time.time())
        self.db._execute_write(write)

    def reconcile(self):
        self.db._execute_write(lambda conn: self._reconcile(conn, time.time()))

    def _snapshot_on_conn(self, conn, sid):
        rows = conn.execute("SELECT command_id,state,enqueued_at,expires_at,reason FROM runtime_admission_queue "
                            "WHERE session_id=? ORDER BY enqueued_at DESC LIMIT 128", (sid,)).fetchall()
        control = conn.execute("SELECT draining FROM runtime_admission_control WHERE id=1").fetchone()
        return {"draining": bool(control and control[0]), "jobs": [dict(row) for row in rows]}

    def snapshot(self, session_id):
        with self.db._runtime_read() as conn:
            sid = self.db._runtime_session_on_conn(conn, session_id)
            return self._snapshot_on_conn(conn, sid)

    def runtime_snapshot(self, session_id):
        # Queue state and journal cursor must describe the same SQLite snapshot.
        # Reads never launch work or terminalize an expired job.
        with self.db._runtime_read() as conn:
            sid = self.db._runtime_session_on_conn(conn, session_id)
            return {**self.db._runtime_snapshot_on_conn(conn, sid),
                    "admission": self._snapshot_on_conn(conn, sid)}

    def budget_policy(self, session_id, command_id):
        with self.db._runtime_read() as conn:
            sid = self.db._runtime_session_on_conn(conn, session_id)
            row = conn.execute("SELECT budget_policy_json FROM runtime_admission_queue WHERE session_id=? AND command_id=?",
                               (sid, command_id)).fetchone()
            return (row is not None, row[0] if row is not None else None)

    def accepted_at(self, session_id, command_id):
        with self.db._runtime_read() as conn:
            sid = self.db._runtime_session_on_conn(conn, session_id)
            row = conn.execute("SELECT enqueued_at FROM runtime_admission_queue WHERE session_id=? AND command_id=?",
                               (sid, command_id)).fetchone()
            return row[0] if row else None

    def deadline(self, session_id, command_id):
        with self.db._runtime_read() as conn:
            sid = self.db._runtime_session_on_conn(conn, session_id)
            row = conn.execute("SELECT expires_at FROM runtime_admission_queue WHERE session_id=? AND command_id=?",
                               (sid, command_id)).fetchone()
            return row[0] if row else None


def assert_launch_on_conn(conn, session_id, command_id):
    row = conn.execute("SELECT state,expires_at,lease_until FROM runtime_admission_queue "
                       "WHERE session_id=? AND command_id=?", (session_id, command_id)).fetchone()
    if row is not None:
        now = time.time()
        _require(row["state"] == "running" and row["lease_until"] > now and row["expires_at"] > now,
                 "admission_not_reserved")
