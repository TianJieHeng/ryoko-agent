"""BE12 additive migration preserves preexisting runtime/session facts."""
from hermes_state import SessionDB


def test_schedule_schema_reopens_prior_version_without_importing_or_enabling_jobs(tmp_path):
    path = tmp_path / "state.db"
    db = SessionDB(path)
    db.create_session("retained", source="cli")
    db.append_message("retained", "user", "retain this history")
    def old_shape(conn):
        tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
                  if row[0].startswith("durable_") or row[0] in {"inbox_previews", "commitment_candidates", "accepted_commitments",
                                                               "commitment_history", "commitment_correspondence"}]
        for table in tables:
            conn.execute('DROP TABLE "' + table + '"')
        conn.execute("UPDATE schema_version SET version=39")
    db._execute_write(old_shape)
    db.close()
    reopened = SessionDB(path)
    try:
        assert reopened.get_messages("retained")[0]["content"] == "retain this history"
        with reopened._runtime_read() as conn:
            assert conn.execute("SELECT COUNT(*) FROM durable_schedules").fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM durable_occurrences").fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM accepted_commitments").fetchone()[0] == 0
        assert reopened.read_runtime_snapshot("retained")["compatibility_status"] == "legacy"
    finally:
        reopened.close()
