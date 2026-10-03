"""BE11 adds workflow storage without rewriting pre-existing durable authority."""
import sqlite3
import time
from pathlib import Path

from hermes_state import SessionDB
from hermes_state_common import SCHEMA_VERSION
from hermes_state_effects import effect_digest
from hermes_state_workflow_schema import WORKFLOW_SCHEMA_SQL


_LEGACY_TABLES = (
    "sessions", "messages", "runtime_state", "runtime_commands", "runtime_events",
    "runtime_missions", "runtime_effects", "runtime_effect_approvals",
)
_WORKFLOW_TABLES = {
    "workflow_versions", "workflow_heads", "workflow_templates", "workflow_evaluations",
    "workflow_evidence", "workflow_runs", "workflow_decisions",
}


def _rows(path):
    with sqlite3.connect(path) as conn:
        return {table: conn.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall()
                for table in _LEGACY_TABLES}


def _seed_legacy(db, label):
    actor = {"principal_id": "owner", "profile_id": label, "agent_id": "ryoko"}
    db.create_session("session", source="test")
    db.append_message("session", "user", content="Preserve private UTF-8 evidence café " + label)
    receipt = db.submit_runtime_command("session", actor, {
        "schema_version": 1, "command_id": "existing", "idempotency_key": "existing",
        "operation": "submit", "identity_binding": actor, "payload": {"text": "existing work " + label},
    })
    assert db.try_acquire_session_turn_lease("session", "legacy-owner")
    fence = {"holder": "legacy-owner", "generation": db.get_session_turn_lease("session")["generation"]}
    assert db.claim_runtime_command("session", "existing", **fence)
    db.create_mission("session", actor, **fence, contract={
        "outcome": "Preserve existing mission " + label, "risk": "low", "uncertainty": "low",
        "acceptance": [{"criterion_id": "review", "kind": "user_acceptance"}],
    })
    binding = dict(session_id="session", run_id=receipt["run_id"], **fence,
                   action_digest=effect_digest("publish"), input_digest=effect_digest(label),
                   target_ref="artifact:existing:1", policy_version="1", policy_digest=effect_digest("policy"),
                   input_revision="original", artifact_revision="1")
    approval = db.request_effect_approval(actor=actor, **binding, expires_at=time.time() + 300)
    db.resolve_effect_approval(approval["approval_id"], actor, **fence,
                               approval_digest=approval["approval_digest"], choice="once")
    effect = db.prepare_effect(actor=actor, **binding, operation_id="publish-existing",
                               intent_key="existing-intent", operation_type="artifact_publish",
                               approval_id=approval["approval_id"])
    assert db.dispatch_effect(effect["effect_id"], actor, **fence)["dispatched_now"]
    # An unresolved external effect and its consumed approval must survive the
    # additive migration exactly, never becoming replayable or newly approved.
    return actor, effect["effect_id"], approval["approval_id"]


def test_real_schema38_upgrade_preserves_sessions_missions_effects_and_profile_isolation(tmp_path, monkeypatch):
    import hermes_state_schema as schema

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    homes = {label: tmp_path / label for label in ("a", "b")}
    expected, references = {}, {}
    # The v39 change is additive. Build the complete prior SessionDB schema by
    # withholding just its separately declared workflow DDL, not by testing a
    # hand-written minimal database or by calling the migration SQL directly.
    with monkeypatch.context() as legacy:
        legacy.setattr(schema, "SCHEMA_SQL", schema.SCHEMA_SQL.replace(WORKFLOW_SCHEMA_SQL, ""))
        legacy.setattr(schema, "SCHEMA_VERSION", 38)
        legacy.setattr(schema, "_READ_PROBE_STATEMENTS", None)
        for label, home in homes.items():
            home.mkdir()
            monkeypatch.setenv("HERMES_HOME", str(home))
            db = SessionDB(home / "state.db")
            try:
                references[label] = _seed_legacy(db, label)
            finally:
                db.close()
            with sqlite3.connect(home / "state.db") as conn:
                assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 38
                names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                assert names.isdisjoint(_WORKFLOW_TABLES)
            expected[label] = _rows(home / "state.db")

    for label in ("a", "b", "a"):
        home = homes[label]
        monkeypatch.setenv("HERMES_HOME", str(home))
        db = SessionDB(home / "state.db")
        try:
            actor, effect_id, approval_id = references[label]
            assert db.get_effect(effect_id, actor)["state"] == "dispatched"
            assert db.get_effect_approval(approval_id, actor)["status"] == "consumed"
            assert db.get_mission("session", actor)["outcome"].endswith(label)
            assert db.get_messages("session")[0]["content"].endswith(label)
            with db._runtime_read() as conn:
                assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == SCHEMA_VERSION
                names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                assert _WORKFLOW_TABLES <= names
                assert all(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] == 0
                           for table in _WORKFLOW_TABLES)
                assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
                assert not conn.execute("PRAGMA foreign_key_check").fetchall()
        finally:
            db.close()
        assert _rows(home / "state.db") == expected[label]
