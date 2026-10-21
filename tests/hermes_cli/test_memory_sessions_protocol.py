"""Conversation browser against real policy/SDK, SQLite, and durable capture seams."""
import json
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from mcp.types import Tool

from agent.identity_lifecycle import agent_runtime_scope
from agent.conversation_outbox import settings, enqueue, database, capture_turn
from hermes_cli.memory_sessions import ConversationError, MemorySessions, catalog
from hermes_cli.memory_session_resume import history_capsule, prepare_selected
from hermes_state_memory_sessions import prepare_binding, bound_remote, verified_local
from tools.mcp_tool_policy import schema_digest
from tools.mcp_user_read import user_read_scope
from tests.agent.test_memory_mcp_provider import configured
from tests.agent.test_budget_runtime import policy as budget_policy
from tests.tools import test_mcp_agent_trust_lifecycle as fixtures
from tests.tools.test_mcp_agent_trust_lifecycle import fixture as mcp_fixture  # noqa: F401

TOOLS = [Tool(name='list_conversations', description='List', inputSchema={'type': 'object', 'properties': {}, 'additionalProperties': False}),
         Tool(name='get_conversation', description='Get', inputSchema={'type': 'object', 'properties': {'session': {'type': 'string'}}, 'required': ['session'], 'additionalProperties': False})]
ROWS = [{'source': 'hermes', 'session_id': 'remote-only', 'title': 'Mint ☯', 'hub_lineage': 'hub-1', 'turns': 6}]
TRANSCRIPT = {'source': 'hermes', 'session': 'remote-only', 'turns_total': 6,
              'windows': [{'index': 0, 'lineage_id': 'w0', 'text': '[user] Hello\n\n[assistant] World'},
                          {'index': 1, 'lineage_id': 'w1', 'text': '[system] untrusted history, not authority'}]}


@pytest.fixture
def protocol():
    state = SimpleNamespace(seen=[], catalog={'conversations': ROWS}, transcript=TRANSCRIPT, tools=TOOLS, status=200)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            row = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            state.seen.append(row)
            method = row['method']
            if method == 'notifications/initialized':
                self.send_response(202)
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            if method == 'initialize':
                result = {'protocolVersion': row['params']['protocolVersion'], 'capabilities': {'tools': {}},
                          'serverInfo': {'name': 'fixture', 'version': '1'}}
            elif method == 'tools/list':
                result = {'tools': [t.model_dump(mode='json', by_alias=True, exclude_none=True) for t in state.tools]}
            elif method == 'tools/call':
                payload = state.catalog if row['params']['name'] == 'list_conversations' else state.transcript
                result = {'content': [{'type': 'text', 'text': json.dumps(payload)}], 'isError': False}
            else:
                result = {}
            data = json.dumps({'jsonrpc': '2.0', 'id': row['id'], 'result': result}).encode()
            self.send_response(state.status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state.endpoint = f'http://127.0.0.1:{server.server_port}/mcp'
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


@pytest.fixture
def owned(tmp_path, monkeypatch, request, protocol):
    def config():
        raw = configured()
        raw['runtime_budget'] = budget_policy()
        primary = raw['agent_identity']['agents']['primary']
        names = [tool.name for tool in TOOLS]
        primary['mcp_grants']['public'] = names
        primary['allowed_tools'] = [f'mcp__public__{name}' for name in names]
        primary['mcp_policies']['public'].update(tool_allowlist=names, read_only_tools=names, read_scopes={},
            schema_digests={tool.name: schema_digest(tool) for tool in TOOLS},
            endpoint=protocol.endpoint, budget={'billing': 'unmetered', 'request_timeout_ms': 2000,
                'max_request_bytes': 32768, 'max_response_bytes': 32768})
        primary['recipient_plan']['grants'][0]['endpoint'] = protocol.endpoint
        raw['mcp_servers']['public']['url'] = protocol.endpoint
        raw['memory']['conversation_push'] = {'enabled': True, 'endpoint': 'https://memory.fixture/v1/conversations/turns',
            'token_file': str(tmp_path / 'worker.env'), 'source': 'hermes', 'agent': 'fixture'}
        return raw
    (tmp_path / '.env').write_text('TEST_MEMORY_TOKEN=synthetic-test-only\n')
    monkeypatch.setattr(fixtures, 'raw_config', config)
    value = request.getfixturevalue('mcp_fixture')
    value.service = MemorySessions(value.primary, 'public')
    value.cli = SimpleNamespace(_session_db=value.db, _agent_running=False, agent=None)
    return value


def test_real_sdk_list_get_no_model_run_and_metadata_only_audit(owned, protocol):
    rows = owned.service.list()
    assert rows[0].title == 'Mint ☯'
    assert owned.service.get(rows[0]) == TRANSCRIPT
    assert [r['params']['name'] for r in protocol.seen if r['method'] == 'tools/call'] == ['list_conversations', 'get_conversation']
    import sqlite3
    with sqlite3.connect(owned.home / 'session-browser.sqlite3') as db:
        audits = db.execute('SELECT attempts,state FROM reads').fetchall()
        assert audits == [(4, 'completed'), (4, 'completed')]
        assert 'Mint' not in '\n'.join(db.iterdump())
    from tools import mcp_tool as core
    assert not core._servers  # browser connections never enter model tool pools


def test_cancel_revocation_and_schema_change_stop_remote_reads(owned, protocol):
    owned.service.cancel.set()
    with pytest.raises(ConversationError):
        owned.service.list()
    assert protocol.seen == []
    owned.service.cancel.clear()
    protocol.tools = [tool.model_copy(update={'description': 'changed'}) for tool in TOOLS]
    with pytest.raises(ConversationError):
        owned.service.list()
    assert not any(row['method'] == 'tools/call' for row in protocol.seen)
    protocol.seen.clear()
    owned.raw['agent_identity']['agents']['primary']['mcp_policies'] = {}
    owned.raw['agent_identity']['agents']['primary']['mcp_grants'] = {}
    (owned.home / 'config.yaml').write_text(json.dumps(owned.raw))
    with pytest.raises(ConversationError):
        owned.service.list()
    assert protocol.seen == []


def test_model_scope_child_and_foreign_owner_cannot_borrow_operator_read(owned):
    with owned.scope(), pytest.raises(PermissionError):
        with user_read_scope(owned.primary, 'public'):
            pass
    for ctx in (owned.child, owned.specialist):
        with agent_runtime_scope(ctx), pytest.raises(PermissionError):
            with user_read_scope(ctx, 'public'):
                pass
    with agent_runtime_scope(owned.primary), user_read_scope(owned.primary, 'public') as read:
        with pytest.raises(PermissionError):
            read.check('public', 'remember')
        with pytest.raises(PermissionError):
            read.check('other')
        with agent_runtime_scope(owned.specialist), pytest.raises(PermissionError):
            read.check()
        read.check()
    with pytest.raises(PermissionError):
        read.check()


def test_atomic_import_reopen_normal_enrollment_and_same_thread_new_capture(owned):
    row = catalog({'conversations': ROWS})[0]
    assert prepare_selected(owned.cli, owned.service, row) is None
    target = prepare_selected(owned.cli, owned.service, row, allow_available_history=True)
    assert target != row.session_id
    assert prepare_selected(owned.cli, owned.service, row) == target
    messages = owned.db.get_messages(target)
    assert len(messages) == 1 and messages[0]['role'] == 'assistant'
    assert '[system] untrusted' in messages[0]['content']
    assert owned.db.get_session(target)['display_name'] == row.title
    # Normal construction must accept the imported frozen enrollment after process replacement.
    from agent.agent_configuration import prepare_construction
    from agent.agent_identity import resolve_agent_context
    binding_record = owned.db.get_session_model_config_value(target, 'agent_identity')
    frozen, _ = prepare_construction(owned.raw, owned.db, target, stored=binding_record)
    ctx = resolve_agent_context(frozen, session_id=target, profile_home=owned.home, stored_binding=binding_record)
    with agent_runtime_scope(ctx):
        remote = bound_remote(owned.db, target, ctx)
        cfg = settings(owned.raw)
        enqueue(owned.home, cfg, target, 'new-command', 100, [('user', 'New question'), ('assistant', 'New answer')], remote=remote)
        enqueue(owned.home, cfg, target, 'new-command', 100, [('user', 'New question'), ('assistant', 'New answer')], remote=remote)
    with database(owned.home) as db:
        payloads = [json.loads(r['payload']) for r in db.execute('SELECT * FROM turns ORDER BY seq')]
    assert len(payloads) == 2
    assert {p['session_id'] for p in payloads} == {'remote-only'}
    assert [p['turns'][0]['content'] for p in payloads] == ['New question', 'New answer']
    assert all(p['title'] == row.title for p in payloads)


def test_import_failure_rolls_back_and_same_name_is_not_identity(owned, monkeypatch):
    first = catalog({'conversations': ROWS})[0]
    cfg = settings(owned.raw)
    capsule = history_capsule(first, TRANSCRIPT)
    original = owned.db._insert_message_rows
    def fail(*args):
        original(*args)
        raise RuntimeError('interrupted import')
    monkeypatch.setattr(owned.db, '_insert_message_rows', fail)
    with agent_runtime_scope(owned.primary), pytest.raises(RuntimeError):
        prepare_binding(owned.db, owned.primary, 'public', first, cfg, capsule=capsule)
    with owned.db._runtime_read() as db:
        assert db.execute('SELECT count(*) FROM sessions').fetchone()[0] == 3
    monkeypatch.setattr(owned.db, '_insert_message_rows', original)
    with agent_runtime_scope(owned.primary):
        one = prepare_binding(owned.db, owned.primary, 'public', first, cfg, capsule=capsule)
        two = prepare_binding(owned.db, owned.primary, 'public', replace(first, session_id='another'), cfg, capsule=capsule)
    assert one != two


def test_local_matching_requires_receipt_and_compression_keeps_binding_branch_does_not(owned):
    local = replace(catalog({'conversations': ROWS})[0], session_id=owned.primary.identity.session_id)
    cfg = settings(owned.raw)
    assert verified_local(owned.db, owned.primary, 'public', local, cfg) is None
    enqueue(owned.home, cfg, local.session_id, 'proof', 100, [('user', 'Original')])
    with database(owned.home) as db, db:
        db.execute("UPDATE turns SET state='sent',receipt=?", (json.dumps({'hub': local.hub, 'session_id': local.session_id}),))
    assert verified_local(owned.db, owned.primary, 'public', local, cfg) == local.session_id
    with agent_runtime_scope(owned.primary):
        prepare_binding(owned.db, owned.primary, 'public', local, cfg, local_id=local.session_id)
    owned.db.end_session(local.session_id, 'compression')
    owned.db.create_session('child-tip', source='cli', parent_session_id=local.session_id)
    assert bound_remote(owned.db, 'child-tip', owned.primary)['remote_id'] == local.session_id
    owned.db.create_session('branch', source='cli', parent_session_id=local.session_id,
                            model_config={'_branched_from': local.session_id})
    assert bound_remote(owned.db, 'branch', owned.primary) is None
    with pytest.raises(PermissionError):
        bound_remote(owned.db, local.session_id, owned.specialist)


@pytest.mark.parametrize('source', ['hermes', 'openclaw'])
def test_actual_capture_resolves_mapping_without_replaying_archive(owned, source):
    row = replace(catalog({'conversations': ROWS})[0], source=source)
    with agent_runtime_scope(owned.primary):
        prepare_binding(owned.db, owned.primary, 'public', row, settings(owned.raw), local_id=owned.primary.identity.session_id)
    from agent.memory_router import RoutedMemoryManager
    with owned.scope():
        run = owned.runs['primary']
        run.agent._memory_manager = RoutedMemoryManager(owned.primary, owned.raw)
        result = capture_turn(run, 'Fresh question', {'final_response': 'Fresh answer'})
        assert result['conversation_sync']['status'] == 'queued'
    with database(owned.home) as db:
        payloads = [json.loads(r['payload']) for r in db.execute('SELECT * FROM turns ORDER BY seq')]
    assert len(payloads) == 2 and all(p['session_id'] == 'remote-only' for p in payloads)
    assert all(p['source'] == source for p in payloads)


def test_remote_advance_needs_explicit_saved_timeline_choice(owned):
    from hermes_cli.memory_session_resume import HistoryDiverged
    row = catalog({'conversations': ROWS})[0]
    target = prepare_selected(owned.cli, owned.service, row, allow_available_history=True)
    newer = replace(row, turns=8)
    with pytest.raises(HistoryDiverged):
        prepare_selected(owned.cli, owned.service, newer)
    assert prepare_selected(owned.cli, owned.service, newer, allow_saved_history=True) == target
    assert len(owned.db.get_messages(target)) == 1
    with pytest.raises(PermissionError):
        verified_local(owned.db, owned.primary, 'public', row,
                       {**settings(owned.raw), 'endpoint': 'https://different.fixture/v1/conversations/turns'})


@pytest.mark.parametrize('failure', ['attempts', 'deadline', 'response_size', 'request_size', 'mutation', 'budget_changed'])
def test_physical_transport_caps_expiry_and_mutation_refusal(owned, failure):
    import asyncio
    import httpx
    from tools import mcp_tool as core
    from tools.agent_policy_gate import bind_mcp_connection
    from tools.mcp_tool_config import _load_mcp_config
    from tools.mcp_tool_policy import validate_schemas
    from tools.mcp_tool_budget import wrap_transport
    sent = []
    def handle(request):
        sent.append(request)
        return httpx.Response(200, content=b'x' * (40000 if failure == 'response_size' else 1))
    async def exercise(read):
        server = core.MCPServerTask('public')
        bind_mcp_connection(server)
        server._config = _load_mcp_config()['public']
        transport = wrap_transport(httpx, httpx.MockTransport(handle), server, read.contract)
        validate_schemas(server, TOOLS)
        request = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                   'params': {'name': 'list_conversations', 'arguments': {}}}
        if failure == 'deadline':
            read.deadline = 0
        elif failure == 'request_size':
            request['params']['arguments'] = {'oversized': 'x' * 40000}
        elif failure == 'mutation':
            request['params']['name'] = 'remember'
        elif failure == 'budget_changed':
            owned.raw['runtime_budget']['limits']['attempts'] = 1
            (owned.home / 'config.yaml').write_text(json.dumps(owned.raw))
        async with httpx.AsyncClient(transport=transport) as client:
            read.max_attempts = 1
            await client.post(server._config['url'], json=request)
            if failure == 'attempts':
                await client.post(server._config['url'], json=request)
    with pytest.raises((PermissionError, ValueError)):
        with agent_runtime_scope(owned.primary), user_read_scope(owned.primary, 'public') as read:
            asyncio.run(exercise(read))
    assert len(sent) == (1 if failure in {'attempts', 'response_size'} else 0)


def test_cold_profile_helper_credentials_are_loaded_before_scope(owned, monkeypatch):
    from hermes_cli import env_loader
    from agent.secret_scope import set_secret_scope, reset_secret_scope
    (owned.home / '.env').write_text('')
    owned.raw['secrets'] = {'command': {'enabled': True, 'command': "printf 'TEST_MEMORY_TOKEN=synthetic-test-only\\n'"}}
    (owned.home / 'config.yaml').write_text(json.dumps(owned.raw))
    env_loader.reset_secret_source_cache(owned.home)
    monkeypatch.delenv('TEST_MEMORY_TOKEN', raising=False)
    token = set_secret_scope({}, profile_home=str(owned.home))
    try:
        assert owned.service.list()[0].session_id == 'remote-only'
    finally:
        reset_secret_scope(token)


def test_catalog_uses_only_verified_local_names_and_marks_current(owned, protocol):
    row = replace(catalog({'conversations': ROWS})[0], title='Untitled conversation')
    with agent_runtime_scope(owned.primary):
        prepare_binding(owned.db, owned.primary, 'public', row, settings(owned.raw), local_id=owned.primary.identity.session_id)
    owned.db.set_session_title(owned.primary.identity.session_id, 'Verified local name')
    protocol.catalog = {'conversations': [{**ROWS[0], 'title': None},
        {'source': 'hermes', 'session_id': owned.specialist.identity.session_id, 'title': None}]}
    service = MemorySessions(owned.primary, 'public', local_db=owned.db, current_session=owned.primary.identity.session_id)
    rows = {r.session_id: r for r in service.list()}
    assert rows['remote-only'].title == 'Verified local name' and rows['remote-only'].current
    assert rows[owned.specialist.identity.session_id].title == 'Untitled conversation'
    assert '1 threads have no supplied name' in service.notice


def test_desktop_resume_reuses_binding_and_imports_each_window_once(owned, protocol):
    from hermes_cli.memory_conversations import open_memory_conversation
    owned.service.local_db = owned.db
    first = open_memory_conversation(owned.service, 'hermes', 'remote-only')
    root = first['conversation']['conversation_id']
    again = open_memory_conversation(owned.service, 'hermes', 'remote-only')
    assert again['conversation']['conversation_id'] == root
    assert len(owned.db.get_messages(root)) == 1
    with agent_runtime_scope(owned.primary):
        assert bound_remote(owned.db, root, owned.primary)['remote_id'] == 'remote-only'
    with owned.db._runtime_read() as conn:
        assert conn.execute('SELECT count(*) FROM runtime_conversations').fetchone()[0] == 1
    assert not (owned.home / 'conversation-outbox/outbox.sqlite3').exists()


def test_desktop_resume_busy_lease_never_imports_more_history(owned, protocol):
    from hermes_cli.memory_conversations import open_memory_conversation
    owned.service.local_db = owned.db
    first = open_memory_conversation(owned.service, 'hermes', 'remote-only')
    root = first['conversation']['conversation_id']
    assert owned.db.try_acquire_session_turn_lease(root, 'other-running-client')
    with pytest.raises(ConversationError, match='busy'):
        open_memory_conversation(owned.service, 'hermes', 'remote-only')
    assert len(owned.db.get_messages(root)) == 1


def test_desktop_resume_refreshes_new_windows_without_replaying_old_ones(owned, protocol):
    from hermes_cli.memory_conversations import open_memory_conversation
    owned.service.local_db = owned.db
    first = open_memory_conversation(owned.service, 'hermes', 'remote-only')
    root = first['conversation']['conversation_id']
    # Tail sealing can add a window without changing the catalog count.
    protocol.transcript = {**TRANSCRIPT, 'windows': [*TRANSCRIPT['windows'],
        {'index': 2, 'lineage_id': 'w2', 'text': '[user] From the other machine\n[assistant] Saved there'}]}
    second = open_memory_conversation(owned.service, 'hermes', 'remote-only')
    assert second['conversation']['conversation_id'] == root
    messages = owned.db.get_messages(root)
    assert len(messages) == 2
    assert 'From the other machine' in messages[-1]['content']
    assert 'Hello' not in messages[-1]['content']
    open_memory_conversation(owned.service, 'hermes', 'remote-only')
    assert len(owned.db.get_messages(root)) == 2
    from agent.conversation_identity import recorded_conversation_scope
    selected, _ = recorded_conversation_scope(owned.primary, owned.raw, owned.db, root)
    assert owned.db.read_runtime_conversation(selected, root)['conversation']['conversation_id'] == root


def test_desktop_resumes_existing_cli_import_without_copying_archive(owned):
    from hermes_cli.memory_conversations import open_memory_conversation
    row = owned.service.list()[0]
    root = prepare_selected(owned.cli, owned.service, row, allow_available_history=True)
    owned.service.local_db = owned.db
    result = open_memory_conversation(owned.service, row.source, row.session_id)
    assert result['conversation']['conversation_id'] == root
    assert len(owned.db.get_messages(root)) == 1


def test_desktop_rpc_real_dispatch_catalog_open_and_canonical_bind(owned, monkeypatch):
    import io
    from tui_gateway import server
    from tui_gateway.transport import StdioTransport
    peer = StdioTransport(lambda: io.StringIO(), threading.Lock())
    monkeypatch.setattr(server, '_stdio_transport', peer)
    monkeypatch.setattr(server, '_hermes_home', owned.home)
    monkeypatch.setattr(server, '_db', owned.db)
    monkeypatch.setattr(server, '_sessions', {})
    monkeypatch.setattr(server, '_schedule_agent_build', lambda sid: None)
    monkeypatch.setattr(server, '_schedule_session_cap_enforcement', lambda: None)
    monkeypatch.setattr(server, '_register_session_cwd', lambda session: None)
    def call(method, transport=peer, **params):
        return server.dispatch({'jsonrpc': '2.0', 'id': 'desktop-test', 'method': method,
                                'params': {'schema_version': 1, **params}}, transport=transport)
    assert call('runtime.memory.list')['result']['conversations'][0]['session_id'] == 'remote-only'
    opened = call('runtime.memory.open', source='hermes', session_id='remote-only')
    assert 'result' in opened, opened
    root = opened['result']['conversation']['conversation_id']
    bound = call('runtime.conversation.bind', conversation_id=root)
    assert 'result' in bound, bound
    live = bound['result']['session_id']
    assert server._sessions[live]['history']
    # Reopening unchanged history retains the live context/prompt cache.
    assert 'result' in call('runtime.memory.open', source='hermes', session_id='remote-only')
    assert live in server._sessions
    foreign = SimpleNamespace(write=lambda frame: True)
    assert call('runtime.memory.list', transport=foreign)['error']['data']['code'] == 'runtime_transport_unsupported'
    assert call('runtime.memory.list', profile='other')['error']['code'] == 4000


def test_desktop_renews_stale_policy_but_keeps_one_remote_binding(owned):
    from hermes_cli.memory_conversations import open_memory_conversation
    owned.service.local_db = owned.db
    first = open_memory_conversation(owned.service, 'hermes', 'remote-only')
    old = first['conversation']['conversation_id']
    # Simulate a session written under a prior policy. The stable human owner,
    # agent and profile remain unchanged, but strict runtime resume must reject it.
    stored = owned.db.get_session_model_config_value(old, 'agent_identity')
    stale = {**stored, 'policy_digest': 'a' * 64, 'config_digest': 'b' * 64}
    def age(conn):
        config = json.loads(conn.execute('SELECT model_config FROM sessions WHERE id=?', (old,)).fetchone()[0])
        config['agent_identity'] = stale
        conn.execute('UPDATE sessions SET model_config=? WHERE id=?', (json.dumps(config), old))
        conn.execute('UPDATE runtime_conversations SET binding_json=? WHERE conversation_id=?', (json.dumps(stale), old))
    owned.db._execute_write(age)
    second = open_memory_conversation(owned.service, 'hermes', 'remote-only')
    new = second['conversation']['conversation_id']
    assert new != old
    assert owned.db.get_session(old)['archived']
    assert len(owned.db.get_messages(new)) == 1
    assert bound_remote(owned.db, new, owned.primary)['remote_id'] == 'remote-only'
    assert bound_remote(owned.db, old, owned.primary) is None
    again = open_memory_conversation(owned.service, 'hermes', 'remote-only')
    assert again['conversation']['conversation_id'] == new
    from agent.conversation_identity import recorded_conversation_scope
    selected, _ = recorded_conversation_scope(owned.primary, owned.raw, owned.db, new)
    assert owned.db.read_runtime_conversation(selected, new)['conversation']['conversation_id'] == new
