"""Additive notice schema preserves sessions and never enables old schedules."""
from hermes_state import SessionDB


def test_notification_schema_restores_missing_projection_without_creating_a_policy(tmp_path):
    path = tmp_path / "state.db"
    db = SessionDB(path)
    db.create_session("retained", source="cli")
    db.append_message("retained", "user", "keep this")
    tables = ("durable_monitor_policies", "durable_monitor_notices", "durable_monitor_batches")
    def old_shape(conn):
        for table in tables:
            conn.execute(f'DROP TABLE IF EXISTS "{table}"')
        conn.execute("UPDATE schema_version SET version=41")
    db._execute_write(old_shape)
    db.close()
    reopened = SessionDB(path)
    try:
        assert reopened.get_messages("retained")[0]["content"] == "keep this"
        with reopened._runtime_read() as conn:
            for table in tables:
                assert conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM delivery_obligations WHERE authority='runtime.v1'").fetchone()[0] == 0
    finally:
        reopened.close()
