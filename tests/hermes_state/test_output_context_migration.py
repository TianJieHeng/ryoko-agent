"""Schema43 retains prior conversations and durable effects without replay."""
import sqlite3
from pathlib import Path
from hermes_state import SessionDB
from hermes_state_common import SCHEMA_VERSION
from hermes_state_output_context_schema import OUTPUT_CONTEXT_SCHEMA_SQL
from tests.hermes_state.test_workflow_migration import _seed_legacy, _rows


def test_schema42_to43_preserves_durable_history_and_creates_empty_receipts(tmp_path, monkeypatch):
    import hermes_state_schema as schema
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    path = tmp_path / "state.db"
    with monkeypatch.context() as old:
        old.setattr(schema, "SCHEMA_SQL", schema.SCHEMA_SQL.replace(OUTPUT_CONTEXT_SCHEMA_SQL, ""))
        old.setattr(schema, "SCHEMA_VERSION", 42)
        old.setattr(schema, "_READ_PROBE_STATEMENTS", None)
        db = SessionDB(path)
        _seed_legacy(db, "legacy")
        db.close()
    before = _rows(path)
    db = SessionDB(path)
    db.close()
    assert _rows(path) == before
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == SCHEMA_VERSION
        assert conn.execute("SELECT count(*) FROM runtime_output_contexts").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM runtime_context_controls").fetchone()[0] == 0
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
