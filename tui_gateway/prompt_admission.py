"""Consume durable queue references through the gateway's existing turn pipeline."""
from __future__ import annotations

import logging
from contextlib import contextmanager
import threading
import uuid

from agent.admission import AdmissionQueue
from agent.runtime_context import AgentContext

log = logging.getLogger(__name__)
_LOCK = threading.RLock()
_HANDLE = None
_WORKER = uuid.uuid4().hex
_STARTED = False
_STOPPING = False


def _sessions_by_store(server):
    with server._sessions_lock:
        sessions = list(server._sessions.items())
    stores = {}
    for sid, session in sessions:
        agent = session.get("agent")
        db = getattr(agent, "_session_db", None)
        if not isinstance(getattr(agent, "runtime_context", None), AgentContext) or db is None:
            continue
        key = str(db.db_path)
        stores.setdefault(key, (db, []))[1].append((sid, session))
    return stores.values()


def _launch(server, queue, job, live):
    sid, session = live
    agent = session["agent"]
    command_id = job["command_id"]
    with server._session_profile_runtime_scope(session):
        with server._session_turn_admission(session) as admitted:
            if not admitted or session.get("_closing"):
                queue.reject_launch(agent.session_id, command_id, "admission_draining")
                return
            if session.get("running"):
                # The real turn gate won a race with the scheduler; the durable
                # launch lease recovers this still-unclaimed reference later.
                return
            if server._ensure_active_session_slot(sid, session) is not None:
                queue.reject_launch(agent.session_id, command_id, "session_not_owned")
                return
            record = queue.db.read_runtime_command(agent.session_id, command_id)
            if record is None or record["status"] != "accepted":
                return
            session["running"] = True
            session["_turn_cancel_requested"] = False
            session["last_active"] = server.time.time()
            server._start_inflight_turn(session, record["command"]["payload"]["text"])
        try:
            started = server._run_prompt_submit(None, sid, session, record["command"]["payload"]["text"],
                image_paths=[], runtime_command_receipt=record["receipt"])
        except BaseException:
            with session["history_lock"]:
                session["running"] = False
            queue.reject_launch(agent.session_id, command_id, "launch_failed")
            raise
        if not started:
            queue.reject_launch(agent.session_id, command_id, "launch_refused")


def pump(server):
    """One bounded pass, shared by submission, turn completion and maintenance."""
    pending = False
    with _LOCK:
        if _STOPPING:
            return False
        for db, sessions in _sessions_by_store(server):
            queue = AdmissionQueue(db)
            eligible = {}
            queue.reconcile()
            for sid, session in sessions:
                agent = session["agent"]
                root = db.read_runtime_snapshot(agent.session_id)["session_id"]
                if session.get("_closing") or session.get("_runtime_admission_stopped"):
                    queue.cancel_session(agent.session_id, "session_closed" if session.get("_closing") else "cancelled_before_launch")
                elif not session.get("running") and session.get("transport") is not None:
                    # A disconnect keeps the accepted record pending. Reattach
                    # supplies the live transport; it does not create a new run.
                    if session.get("transport") is not getattr(server, "_detached_ws_transport", None):
                        eligible[root] = (sid, session)
            for _ in range(queue.policy.max_active):
                job = queue.reserve_next(_WORKER, eligible)
                if job is None:
                    break
                live = eligible.pop(job["session_id"])
                _launch(server, queue, job, live)
            queue.reconcile()
            with db._runtime_read() as conn:
                pending |= conn.execute("SELECT 1 FROM runtime_admission_queue WHERE state IN ('queued','running') LIMIT 1").fetchone() is not None
    return pending


def wake(server):
    global _HANDLE
    pending = pump(server)
    with _LOCK:
        if not _STOPPING and pending and (_HANDLE is None or _HANDLE.cancelled):
            from agent.periodic_scheduler import schedule
            _HANDLE = schedule(lambda: pump(server), 0.25)


def _maintenance(server):
    # Stdio has no gateway cron thread. Reuse this already-running maintenance
    # handle and the ordinary cron authority, only under explicit profile opt-in.
    # Shutdown takes the same lock before retiring accepted queue references.
    with _LOCK:
        if _STOPPING:
            return
        try:
            from tui_gateway.runtime_schedule_tick import tick_if_owned
            tick_if_owned(server)
        except Exception:
            log.exception("Opted-in stdio cron tick failed; durable work retained")
    from hermes_state_registry import borrow_live_shared_session_dbs
    # Includes the launch store before any conversation is resumed, and every
    # currently served named profile. Unattached commands expire durably; only
    # authenticated live sessions are eligible to execute.
    launch_db = server._get_db()
    if launch_db is not None:
        AdmissionQueue(launch_db).reconcile()
    with borrow_live_shared_session_dbs() as databases:
        for db in databases:
            AdmissionQueue(db).reconcile()
    pump(server)


def start(server):
    global _HANDLE, _STARTED
    with _LOCK:
        if _STARTED:
            return
        _STARTED = True
        if _HANDLE is not None:
            _HANDLE.cancel()
        from agent.periodic_scheduler import schedule
        _HANDLE = schedule(lambda: _maintenance(server), 0.5)


def cancel_session(session, reason="session_closed"):
    agent = session.get("agent")
    if isinstance(getattr(agent, "runtime_context", None), AgentContext):
        session["_runtime_admission_stopped"] = True
        try:
            AdmissionQueue(agent._session_db).cancel_session(agent.session_id, reason)
        except Exception:
            # An unavailable disk must not stop local/provider interruption.
            # Keep the in-memory admission fence; maintenance retries the write.
            log.exception("Queue cancellation deferred; local launch remains stopped")


@contextmanager
def admission_open():
    with _LOCK:
        yield not _STOPPING


def shutdown(server):
    """Stop this consumer before session teardown; preserve other workers' queues."""
    global _STOPPING
    with _LOCK:
        _STOPPING = True
        if _HANDLE is not None:
            _HANDLE.cancel()
        for db, sessions in _sessions_by_store(server):
            queue = AdmissionQueue(db)
            for _sid, session in sessions:
                queue.cancel_session(session["agent"].session_id, "shutdown_before_launch")
