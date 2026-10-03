"""Live grant and immutable payload checks for BE06 same-client monitor notices."""
from contextlib import contextmanager
import json

from cron.durable_contract import digest, require


@contextmanager
def notification_delivery_scope(context, db, sid, actor, delivery_id):
    with db._runtime_read() as conn:
        batch = conn.execute("SELECT * FROM durable_monitor_batches WHERE delivery_id=?", (delivery_id,)).fetchone()
        if batch is None:
            project = None
        else:
            policy = conn.execute("SELECT * FROM durable_monitor_policies WHERE schedule_key=? AND version=?",
                                  (batch["schedule_key"], batch["version"])).fetchone()
            row = conn.execute("SELECT * FROM durable_schedules WHERE schedule_key=?", (batch["schedule_key"],)).fetchone()
            require(policy is not None and row is not None and json.loads(row["owner_json"]) == actor
                    and policy["destination_session"] == sid
                    and json.loads(policy["owner_binding_json"]) == context.identity.to_record(),
                    "Notification's exact live owner binding changed", "identity_mismatch")
            project = row["project_id"]
    if project is None:
        yield
    else:
        from agent.project_context import project_access
        from agent.identity_lifecycle import agent_runtime_scope
        with agent_runtime_scope(context), project_access(context).guard(project, actor, "read"):
            yield


def notification_payload(db, claim):
    with db._runtime_read() as conn:
        row = conn.execute("SELECT * FROM durable_monitor_batches WHERE delivery_id=?", (claim["delivery_id"],)).fetchone()
        if row is None:
            return None
        require(row["sha256"] == claim["artifact"]["sha256"] == digest(json.loads(row["payload_json"])),
                "Notification digest changed", "notification_digest_mismatch")
        return {"delivery_id": claim["delivery_id"], "attempt_token": claim["attempt_token"],
                "sha256": row["sha256"], "notification_json": row["payload_json"]}
