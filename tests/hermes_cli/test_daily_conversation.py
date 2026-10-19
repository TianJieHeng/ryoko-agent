"""Daily startup preserves profile/date identity and captures only actual new messages."""
from datetime import datetime, timezone
import json
from types import SimpleNamespace

import pytest

from hermes_cli.daily_conversation import daily_name, prepare_daily, apply_daily_default
from hermes_cli.memory_sessions import ConversationError
from tests.hermes_cli.test_memory_sessions_protocol import owned, protocol, mcp_fixture  # noqa: F401


def test_date_locality_startup_precedence_and_real_config_loader(owned, monkeypatch):
    from agent.agent_configuration import base_configuration
    from hermes_cli.config import atomic_config_write
    from hermes_cli import daily_conversation as daily
    from hermes_cli.main import _resolve_chat_session_args
    config = owned.raw
    config['memory']['daily_conversation'] = {
        'enabled': True, 'prefix': 'mobile-daily-', 'timezone': 'America/New_York'}
    atomic_config_write(owned.home / 'config.yaml', config)
    raw = base_configuration()
    instant = datetime(2026, 10, 19, 3, 59, tzinfo=timezone.utc)
    assert daily_name(raw, now=instant) == 'mobile-daily-2026-10-18'
    assert daily_name(raw, now=instant.replace(hour=4)) == 'mobile-daily-2026-10-19'
    other = {'memory': {'daily_conversation': {'prefix': 'other-', 'timezone': 'Asia/Tokyo'}}}
    assert daily_name(other, now=instant) == 'other-2026-10-19'
    assert daily_name(raw, now=instant) == 'mobile-daily-2026-10-18'
    monkeypatch.setattr(daily.sys.stdin, 'isatty', lambda: True)
    calls = []
    monkeypatch.setattr(daily, 'prepare_daily', lambda *a: calls.append(a[2]) or 'chosen-local')
    monkeypatch.setattr(daily.MemorySessions, 'for_cli', lambda cli: owned.service)
    from hermes_cli import active_sessions
    monkeypatch.setattr(active_sessions, 'active_session_registry_snapshot', lambda **kw: [])
    for fields in ({'resume': 'explicit'}, {'continue_last': True}, {'query': 'hello'},
                   {'query_file': 'prompt.txt'}, {'oneshot_exit': True}, {'safe_mode': True}):
        args = SimpleNamespace(**fields)
        apply_daily_default(args)
        assert not calls
    apply_daily_default(SimpleNamespace(), use_tui=True)
    assert not calls
    # Exercise the actual startup resolver; name resolution leaves this explicit target intact.
    args = SimpleNamespace(resume=None, continue_last=None, no_restore_cwd=True)
    _resolve_chat_session_args(args, use_tui=False)
    assert args.resume == 'chosen-local' and len(calls) == 1


def test_daily_create_reopen_history_capture_and_failure_are_one_identity(owned, protocol):
    from agent.identity_lifecycle import agent_runtime_scope
    from agent.conversation_outbox import settings, enqueue, database
    from agent.agent_configuration import prepare_construction
    from hermes_state_memory_sessions import bound_remote
    name = 'mobile-daily-2026-10-19'
    protocol.catalog = {'conversations': []}
    notices = []
    target = prepare_daily(owned.db, owned.service, name, announce=notices.append)
    assert owned.db.get_messages(target) == []
    assert owned.db.get_session(target)['display_name'] == name
    assert prepare_daily(owned.db, owned.service, name) == target
    stored = owned.db.get_session_model_config_value(target, 'agent_identity')
    prepare_construction(owned.raw, owned.db, target, stored=stored)
    with agent_runtime_scope(owned.primary):
        remote = bound_remote(owned.db, target, owned.primary)
        enqueue(owned.home, settings(owned.raw), target, 'real-command', 100,
                [('user', 'Actual question')], remote=remote)
    with database(owned.home) as db:
        rows = db.execute('SELECT payload FROM turns').fetchall()
        assert len(rows) == 1
        payload = json.loads(rows[0][0])
        assert payload['session_id'] == name and payload['title'] == name
    protocol.catalog = {'conversations': [{'source': 'hermes', 'session_id': name,
                                          'hub_lineage': 'created-hub', 'turns': 1}]}
    assert prepare_daily(owned.db, owned.service, name) == target
    next_name = 'mobile-daily-2026-10-20'
    protocol.catalog = {'conversations': [{'source': 'hermes', 'session_id': next_name, 'turns': 2}]}
    protocol.transcript = {'source': 'hermes', 'session': next_name, 'windows': []}
    next_target = prepare_daily(owned.db, owned.service, next_name, announce=notices.append)
    assert next_target != target and owned.db.get_messages(next_target) == []
    assert any('no sealed history' in note for note in notices)
    protocol.status = 503
    with pytest.raises(ConversationError):
        prepare_daily(owned.db, owned.service, 'mobile-daily-2026-10-21')
    with owned.db._runtime_read() as db:
        assert db.execute('SELECT count(*) FROM memory_session_bindings').fetchone()[0] == 2
