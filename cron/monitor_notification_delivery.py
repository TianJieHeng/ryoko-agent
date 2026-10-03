"""Use the existing cron tick and BE06 local outbox for finite monitor delivery."""
from __future__ import annotations

import json
import logging
import time

logger = logging.getLogger(__name__)


def tick_monitor_notifications(db, home):
    """Bounded continuation of the sole per-profile cron tick, including offline holds."""
    from agent.conversation_identity import recorded_owner_scope
    from agent.identity_lifecycle import agent_runtime_scope
    from hermes_state_monitor_notifications import MonitorNotifications, admit_notifications, sync_notification_delivery
    def recover(conn):
        stale = conn.execute("SELECT o.obligation_id FROM delivery_obligations o JOIN durable_monitor_batches b "
            "ON b.delivery_id=o.obligation_id WHERE o.authority='runtime.v1' AND o.state='attempting' AND o.updated_at<=?",
            (time.time() - 60,)).fetchall()
        for item in stale:
            conn.execute("UPDATE delivery_obligations SET state='outcome_unknown',last_error='local_attempt_interrupted',next_attempt_at=NULL "
                         "WHERE obligation_id=?", (item[0],))
            sync_notification_delivery(conn, item[0])
    db._execute_write(recover)
    with db._runtime_read() as conn:
        rows = [dict(row) for row in conn.execute("SELECT s.*,p.destination_session,p.owner_binding_json notification_owner_binding_json FROM durable_schedules s "
            "JOIN durable_monitor_policies p ON p.schedule_key=s.schedule_key AND p.version=s.version "
            "WHERE s.state='active' ORDER BY s.created_at LIMIT 100")]
    for row in rows:
        try:
            context = recorded_owner_scope(db, owner_binding=json.loads(row["notification_owner_binding_json"]),
                session_id=row["destination_session"], profile_home=home)
            with agent_runtime_scope(context):
                admit_notifications(MonitorNotifications(context, db), row["schedule_key"])
        except Exception as exc:
            logger.warning("Monitor notice admission held: %s", getattr(exc, "code", type(exc).__name__))
    # No gateway is started here. Only an already registered exact owned local
    # transport can consume a pending obligation; offline notices remain durable.
    import sys
    server = sys.modules.get("tui_gateway.server")
    if server is None:
        return
    with server._sessions_lock:
        sessions = [(sid, session.get("agent"), session.get("transport")) for sid, session in server._sessions.items()]
    from gateway.durable_outbox import deliver_result
    for ui_sid, agent, transport in sessions:
        context = getattr(agent, "runtime_context", None)
        if context is None or context.profile_home != str(home.resolve()) or transport is None:
            continue
        with db._runtime_read() as conn:
            pending = [row[0] for row in conn.execute("SELECT o.obligation_id FROM delivery_obligations o "
                "JOIN durable_monitor_batches b ON b.delivery_id=o.obligation_id "
                "WHERE o.authority='runtime.v1' AND o.session_key=? AND o.state IN ('pending','failed') "
                "AND (o.next_attempt_at IS NULL OR o.next_attempt_at<=?) ORDER BY o.created_at LIMIT 20", (agent.session_id, time.time()))]
        for did in pending:
            try:
                deliver_result(agent, did, ui_sid, transport)
            except Exception as exc:
                logger.warning("Monitor notice delivery held: %s", getattr(exc, "code", type(exc).__name__))
