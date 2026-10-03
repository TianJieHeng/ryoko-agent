"""Legacy stores retain history without silently promoting it to owned conversations."""
import json
import sqlite3
from pathlib import Path

from hermes_state import SessionDB
from hermes_state_common import SCHEMA_VERSION
from hermes_state_conversation_schema import CONVERSATION_SCHEMA_SQL
from tests.hermes_state.test_workflow_migration import _seed_legacy, _rows


def test_conversation_migration_is_additive_and_creation_is_atomic(tmp_path, monkeypatch):
    import hermes_state_schema as schema
    from agent.agent_identity import resolve_agent_context
    import pytest

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    path = tmp_path / "state.db"
    with monkeypatch.context() as old:
        old.setattr(schema, "SCHEMA_SQL", schema.SCHEMA_SQL.replace(CONVERSATION_SCHEMA_SQL, ""))
        old.setattr(schema, "SCHEMA_VERSION", SCHEMA_VERSION - 1)
        old.setattr(schema, "_READ_PROBE_STATEMENTS", None)
        db = SessionDB(path)
        _seed_legacy(db, "legacy")
        db.close()
    before = _rows(path)
    db = SessionDB(path)
    try:
        assert _rows(path) == before
        with sqlite3.connect(path) as conn:
            assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == SCHEMA_VERSION
            assert conn.execute("SELECT count(*) FROM runtime_conversations").fetchone()[0] == 0
            assert conn.execute("SELECT count(*) FROM runtime_conversation_operations").fetchone()[0] == 0
            assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        config = {"agent_identity": {"schema_version": 1, "principal_id": "owner", "profile_id": "home",
            "primary_agent_id": "ryoko", "active_agent_id": "ryoko",
            "agents": {"ryoko": {"policy_version": 1, "role": "primary", "memory_backend": "personal_mcp"}}}}
        context = resolve_agent_context(config, session_id="atomic", profile_home=tmp_path)
        def fail(*args):
            raise RuntimeError("fixture receipt write failure")
        with monkeypatch.context() as failure:
            failure.setattr(db, "_conversation_record_operation", fail)
            with pytest.raises(RuntimeError, match="fixture receipt"):
                db.create_runtime_conversation(context, {"schema_version": 1, "idempotency_key": "atomic", "title": "Atomic"})
        assert db.get_session("atomic") is None
        assert db.read_runtime_conversation_operation(context, "atomic")["found"] is False
        created = db.create_runtime_conversation(context, {"schema_version": 1, "idempotency_key": "atomic", "title": "Atomic"})
        def no_write(*args, **kwargs):
            raise AssertionError("Receipt inspection may not write or enqueue")
        with monkeypatch.context() as read_only:
            read_only.setattr(db, "_execute_write", no_write)
            assert db.read_runtime_conversation_operation(context, "atomic")["conversation"] == created["conversation"]
            assert not db.read_runtime_conversation_operation(context, "absent")["found"]
    finally:
        db.close()


def test_display_title_backfill_keeps_legacy_aliases_and_allows_duplicate_canonical_names(tmp_path, monkeypatch):
    import hermes_state_schema as schema
    from agent.agent_identity import resolve_agent_context
    from hermes_state_conversations import _conversation_owner

    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    config = {'agent_identity': {'schema_version': 1, 'principal_id': 'owner', 'profile_id': 'profile',
        'primary_agent_id': 'ryoko', 'active_agent_id': 'ryoko',
        'agents': {'ryoko': {'policy_version': 1, 'role': 'primary', 'memory_backend': 'personal_mcp'}}}}
    root = resolve_agent_context(config, session_id='existing-canonical', profile_home=tmp_path)
    with monkeypatch.context() as old:
        old.setattr(schema, 'SCHEMA_SQL', schema.SCHEMA_SQL.replace('    display_title TEXT,\n', ''))
        old.setattr(schema, '_READ_PROBE_STATEMENTS', None)
        db = SessionDB(tmp_path / 'state.db')
        db.create_session('legacy', source='cli')
        db.set_session_title('legacy', 'Legacy alias')
        db.create_session(root.identity.session_id, source='web')
        db.claim_session_agent_identity(root.identity.session_id, root.identity.to_record())
        db.publish_compression_child(parent_session_id=root.identity.session_id, child_session_id='existing-tip',
            source='web', messages=[{'role': 'user', 'content': 'Prior discussion'}],
            model_config={'agent_identity': root.identity.to_record()}, require_compression_lease=False)
        db.set_session_title('existing-tip', 'A new thought')
        db._execute_write(lambda conn: conn.execute('INSERT INTO runtime_conversations '
            '(conversation_id,owner_key,binding_json,created_at,updated_at) VALUES(?,?,?,?,?)',
            (root.identity.session_id, _conversation_owner(root), json.dumps(root.identity.to_record()), 1, 1)))
        db.close()
    for repeat in range(2):
        db = SessionDB(tmp_path / 'state.db')
        try:
            old = db.read_runtime_conversation(root, root.identity.session_id)['conversation']
            assert old['title'] == 'A new thought'
            assert db.get_session_title('existing-tip') == 'A new thought'
            assert db.get_session_title('legacy') == 'Legacy alias'
            if repeat == 0:
                peer = resolve_agent_context(config, session_id='same-title-new', profile_home=tmp_path)
                made = db.create_runtime_conversation(peer, {'schema_version': 1, 'idempotency_key': 'duplicate-title', 'title': 'A new thought'})
                assert made['conversation']['title'] == old['title']
                assert db.get_session_title('same-title-new') is None
                renamed = db.mutate_runtime_conversation(root, {'schema_version': 1, 'conversation_id': root.identity.session_id,
                    'idempotency_key': 'rename', 'expected_revision': 1, 'title': 'A new thought'}, operation='rename')
                assert renamed['conversation']['revision'] == 2
            rows = db.list_runtime_conversations(root, query='new thought')['conversations']
            assert {row['conversation_id'] for row in rows} == {'existing-canonical', 'same-title-new'}
            assert all(row['title'] == 'A new thought' for row in rows)
        finally:
            db.close()
