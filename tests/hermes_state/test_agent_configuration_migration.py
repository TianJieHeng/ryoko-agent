"""Adding identity settings must not enroll or relabel existing conversations."""
from pathlib import Path
import sqlite3

from hermes_state import SessionDB
from hermes_state_agent_configuration_schema import AGENT_CONFIGURATION_SCHEMA_SQL
from tests.agent.test_identity_lifecycle import config, write_config
from agent.agent_identity import resolve_agent_context


def test_additive_configuration_schema_keeps_old_bindings_private_and_unenrolled(tmp_path, monkeypatch):
    import hermes_state_schema as schema
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    homes, bindings = {}, {}
    with monkeypatch.context() as legacy:
        legacy.setattr(schema, 'SCHEMA_SQL', schema.SCHEMA_SQL.replace(AGENT_CONFIGURATION_SCHEMA_SQL, ''))
        legacy.setattr(schema, '_READ_PROBE_STATEMENTS', None)
        for label in ('a', 'b'):
            home = tmp_path / label
            raw = config('assistant')
            raw['agent_identity']['profile_id'] = 'profile_' + label
            write_config(home, raw)
            monkeypatch.setenv('HERMES_HOME', str(home))
            db = SessionDB(home / 'state.db')
            context = resolve_agent_context(raw, session_id='existing', profile_home=home)
            bindings[label] = context.identity.to_record()
            db.create_session('existing', source='cli')
            db.claim_session_agent_identity('existing', bindings[label])
            db.append_message('existing', 'user', 'Private transcript ' + label)
            db.close()
            homes[label] = home
            with sqlite3.connect(home / 'state.db') as conn:
                assert conn.execute("SELECT 1 FROM sqlite_master WHERE name='agent_configuration_sessions'").fetchone() is None
    for label in ('a', 'b', 'a'):
        monkeypatch.setenv('HERMES_HOME', str(homes[label]))
        db = SessionDB(homes[label] / 'state.db')
        try:
            assert db.get_session_model_config_value('existing', 'agent_identity') == bindings[label]
            assert db.get_messages('existing')[0]['content'] == 'Private transcript ' + label
            with db._runtime_read() as conn:
                assert conn.execute('SELECT COUNT(*) FROM agent_configuration_sessions').fetchone()[0] == 0
                assert conn.execute('SELECT COUNT(*) FROM agent_configuration_snapshots').fetchone()[0] == 0
                assert conn.execute('SELECT COUNT(*) FROM agent_configuration_revocations').fetchone()[0] == 0
                assert not conn.execute('PRAGMA foreign_key_check').fetchall()
        finally:
            db.close()
