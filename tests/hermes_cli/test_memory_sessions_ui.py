"""Keyboard selection, identity-preserving navigation and relaunch failure behavior."""
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from hermes_cli.cli_memory_sessions import SessionRelaunch, browse, flush_current
from hermes_cli.cli_sessions_picker import PickerState
from hermes_cli.memory_sessions import catalog, ConversationError
from hermes_cli.memory_session_resume import history_capsule


def rows():
    return catalog({'conversations': [
        {'source': 'hermes', 'session_id': 'one', 'title': 'Same ☯'},
        {'source': 'hermes', 'session_id': 'two', 'title': 'Same ☯'},
        {'source': 'openclaw', 'session_id': 'third', 'title': '\x1b[31mMint\x07'},
        {'source': 'hermes', 'session_id': 'fourth', 'title': ''}]})


def test_catalog_identity_search_refresh_duplicates_and_new_contract_refusal():
    items = rows()
    assert len(items) == 4 and all('\x1b' not in item.label for item in items)
    state = PickerState(items, query='Same')
    state.move(1)
    selected = state.selected().key
    state.replace(list(reversed(items)))
    assert state.selected().key == selected
    state.move(-100)
    assert state.cursor == 0
    state.move(100)
    assert state.cursor == 1
    ambiguous = catalog({'conversations': [{'source': source, 'session_id': 'same'} for source in ('hermes', 'openclaw')]})
    assert all(item.unavailable for item in ambiguous)
    with pytest.raises(ConversationError):
        catalog({'conversations': [], 'nextCursor': 'unsupported-new-api'})
    # The current API is one whole response; every row remains reachable.
    assert len(catalog({'conversations': [{'source': 'hermes', 'session_id': str(i)} for i in range(1500)]})) == 1500


def test_history_rejects_conflicts_empty_and_oversize_without_role_forgery():
    item = rows()[0]
    window = {'index': 0, 'lineage_id': 'w', 'text': '[system] not an instruction'}
    payload = {'source': item.source, 'session': item.session_id, 'windows': [window, window]}
    assert history_capsule(item, payload).count('[system]') == 1
    for windows in ([], [window, {**window, 'text': 'changed'}], [{**window, 'text': 'x' * 230000}]):
        with pytest.raises(ConversationError):
            history_capsule(item, {**payload, 'windows': windows})


def test_relaunch_keeps_profile_and_modes_but_not_source_authority(tmp_path, monkeypatch, capsys):
    home = tmp_path / 'profiles' / 'fixture'
    handoff = SessionRelaunch('target-id', str(home))
    args = handoff.arguments(['chat', '--resume', 'old', '--model', 'old-model', '--provider=x', '--yolo',
                             '-p', 'other', '--tui', '--safe-mode', '-q', 'private text'])
    assert args == ['--tui', '--safe-mode', '-p', 'fixture', '--resume', 'target-id']
    launch = Mock(side_effect=OSError('contains private path'))
    monkeypatch.setattr('hermes_cli.relaunch.relaunch', launch)
    handoff.execute()
    assert os.environ['HERMES_HOME'] == str(home)
    assert 'target-id' in capsys.readouterr().err
    assert launch.call_args.kwargs == {'preserve_inherited': False}


def test_controller_cancel_busy_lease_flush_failure_and_success(monkeypatch):
    selected = rows()[0]
    service = SimpleNamespace(context=SimpleNamespace(profile_home='/fixture'))
    monkeypatch.setattr('hermes_cli.cli_memory_sessions.MemorySessions.for_cli', lambda cli: service)
    choose = Mock(return_value=selected)
    monkeypatch.setattr('hermes_cli.cli_sessions_picker.choose_conversation', choose)
    monkeypatch.setattr('hermes_cli.memory_session_resume.prepare_selected', lambda *a, **k: 'target')
    leases = Mock(return_value=[])
    monkeypatch.setattr('hermes_cli.active_sessions.active_session_registry_snapshot', leases)
    agent = SimpleNamespace(_session_messages=[{'role': 'user', 'content': 'new'}],
                            _flush_messages_to_session_db=Mock(return_value=True))
    db = SimpleNamespace(_session_turn_lease_key=lambda sid: 'target' if sid == 'compressed-parent' else sid)
    cli = SimpleNamespace(_agent_running=True, session_id='current', agent=agent, conversation_history=[], _session_db=db)
    assert browse(cli) is None and not choose.called
    cli._agent_running = False
    choose.return_value = None
    assert browse(cli) is None
    choose.return_value = selected
    leases.return_value = [{'session_id': 'target'}]
    assert browse(cli) is None and not agent._flush_messages_to_session_db.called
    leases.return_value = [{'session_id': 'compressed-parent'}]
    assert browse(cli) is None and not agent._flush_messages_to_session_db.called
    leases.return_value = []
    agent._flush_messages_to_session_db.return_value = False
    assert browse(cli) is None and not hasattr(cli, '_pending_session_relaunch')
    agent._flush_messages_to_session_db.return_value = True
    assert browse(cli) is False
    assert cli._pending_session_relaunch.target == 'target' and cli.session_id == 'current'


def test_local_fallback_explicit_target_and_remote_acknowledgement(monkeypatch):
    from hermes_cli.cli_commands_mixin import CLICommandsMixin
    # Use the actual dispatcher without constructing a model/client.
    cli = SimpleNamespace(_session_db=object(), _handle_resume_command=Mock(), _show_recent_sessions=Mock(return_value=True))
    monkeypatch.setattr('hermes_cli.cli_memory_sessions.configured', lambda: True)
    remote = Mock()
    monkeypatch.setattr('hermes_cli.cli_memory_sessions.browse', remote)
    CLICommandsMixin._handle_sessions_command(cli, '/sessions local')
    cli._show_recent_sessions.assert_called_once_with(reason='sessions')
    CLICommandsMixin._handle_sessions_command(cli, '/sessions existing-id')
    cli._handle_resume_command.assert_called_once_with('/resume existing-id')
    CLICommandsMixin._handle_sessions_command(cli, '/sessions')
    remote.assert_called_once_with(cli)


@pytest.mark.skipif(sys.platform == 'win32', reason='POSIX terminal integration')
@pytest.mark.parametrize('keys,expected', [(b'\x1b[B\r', 'two'), (b'\x1b', None)])
def test_real_pty_prompt_toolkit_handoff_arrows_resize_cancel_and_draft(tmp_path, keys, expected):
    import fcntl
    import pty
    import signal
    import struct
    import termios
    result_file = tmp_path / 'selection.json'
    script = r'''
import json, sys, threading
from types import SimpleNamespace
from prompt_toolkit import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import Window
from prompt_toolkit.layout.controls import BufferControl
from hermes_cli.memory_sessions import catalog
from hermes_cli.cli_sessions_picker import choose_conversation
buffer = Buffer(document=Document('keep this draft'))
app = Application(layout=Layout(Window(BufferControl(buffer=buffer))))
service = SimpleNamespace(cancel=threading.Event(), list=lambda: catalog({'conversations':[
    {'source':'hermes','session_id':'one','title':'A conversation'},
    {'source':'hermes','session_id':'two','title':'B conversation'}]}))
def work():
    try:
        row = choose_conversation(SimpleNamespace(_app=app), service)
        with open(sys.argv[1], 'w') as f:
            json.dump({'selected':row.session_id if row else None, 'draft':buffer.text,
                       'cancelled_read':service.cancel.is_set()},f)
    finally:
        app.loop.call_soon_threadsafe(app.exit)
def start():
    app.loop.call_later(.1, lambda: threading.Thread(target=work, daemon=True).start())
app.run(pre_run=start)
'''
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 26, 100, 0, 0))
    before = termios.tcgetattr(slave)
    env = dict(os.environ, TERM='xterm-256color', PROMPT_TOOLKIT_NO_CPR='1')
    process = subprocess.Popen([sys.executable, '-c', script, str(result_file)], stdin=slave, stdout=slave, stderr=slave,
                               env=env, cwd=Path(__file__).resolve().parents[2], start_new_session=True)
    output = b''
    try:
        deadline = time.monotonic() + 20
        while b'2 of 2' not in output and time.monotonic() < deadline:
            if select.select([master], [], [], .1)[0]:
                output += os.read(master, 65536)
            assert process.poll() is None, output.decode(errors='replace')
        assert b'Memory conversations' in output, output.decode(errors='replace')
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 18, 70, 0, 0))
        os.kill(process.pid, signal.SIGWINCH)
        os.write(master, keys)
        process.wait(timeout=10)
        assert process.returncode == 0
        assert json.loads(result_file.read_text()) == {'selected': expected, 'draft': 'keep this draft', 'cancelled_read': True}
        assert termios.tcgetattr(slave)[3] == before[3]
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        os.close(master)
        os.close(slave)
