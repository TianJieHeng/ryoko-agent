"""Owned skill reads and remote writes exercise broker, transport and durable ledger."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from tests.tools import test_mcp_budget as budgets
from tests.tools.test_mcp_budget import harness, mcp_fixture  # noqa: F401
from tools.mcp_tool_policy import schema_digest


@pytest.fixture(autouse=True)
def config(monkeypatch, request):
    base = budgets.configured
    def configured():
        raw = base()
        primary = raw['agent_identity']['agents']['primary']
        primary['allowed_tools'] += ['skills_list', 'skill_view']
        grant = primary['mcp_policies']['public']
        grant['mutation_contracts'] = {'write': {
            'input_schema': {'type': 'object', 'required': ['body'],
                             'properties': {'body': {'type': 'string'}}, 'additionalProperties': False},
            'result_schema': {'type': 'object', 'required': ['ref'],
                              'properties': {'ref': {'type': 'string', 'minLength': 1}}},
            'receipt_field': 'ref'}}
        grant['schema_digests']['write'] = schema_digest(SimpleNamespace(
            name='write', description='fixture mutation', inputSchema={'type': 'object'}, annotations=None))
        if request.node.name.startswith('test_conversation'):
            grant['mutation_contracts']['write']['input_schema'] = {'type': 'object'}
            raw['memory']['conversation_mcp'] = {'server': 'public', 'append_tool': 'write',
                                                 'source': 'hermes', 'agent': 'fixture'}
        return raw
    monkeypatch.setattr(budgets, 'configured', configured)


def server(respond):
    from tools import mcp_tool as core
    from tools.agent_policy_gate import bind_mcp_connection
    from tools.mcp_tool_discovery import _adopt_server
    from tools.mcp_tool_registration import _register_server_tools
    instance = core.MCPServerTask('public')
    bind_mcp_connection(instance)
    instance._config = {'url': 'https://fixture.invalid/mcp'}
    transport = budgets.wrap_transport(httpx, httpx.MockTransport(respond), instance, budgets.CONTRACT)
    async def call_tool(name, arguments):
        async with httpx.AsyncClient(transport=transport) as client:
            response = await client.post(instance._config['url'], json={'jsonrpc': '2.0', 'id': 1,
                'method': 'tools/call', 'params': {'name': name, 'arguments': arguments}})
        return SimpleNamespace(content=[], isError=False, structuredContent=response.json())
    instance.session = SimpleNamespace(call_tool=AsyncMock(side_effect=call_tool))
    instance._tools = [SimpleNamespace(name='write', description='fixture mutation',
                                      inputSchema={'type': 'object'}, annotations=None)]
    _adopt_server('public', instance)
    instance._registered_tool_names = _register_server_tools('public', instance, instance._config)
    return instance


def test_write_receipt_is_durable_and_same_run_does_not_replay(harness):
    from tools.registry import registry
    seen = []
    def respond(request):
        seen.append(request)
        return httpx.Response(200, json={'ref': 'fixture.1'})
    with harness.scope():
        server(respond)
        result = json.loads(registry.dispatch('mcp__public__write', {'body': 'real intent'}))
        assert result['effect']['state'] == 'confirmed'
        again = json.loads(registry.dispatch('mcp__public__write', {'body': 'real intent'}))
        assert again['status'] == 'confirmed' and len(seen) == 1
        with harness.db._runtime_read() as conn:
            row = conn.execute('SELECT * FROM runtime_effects').fetchone()
            assert row['state'] == 'confirmed'
        assert len(budgets.rows(harness)) == 1


def test_raw_mutation_cannot_bypass_effect_and_bad_arguments_do_not_send(harness):
    from tools.registry import registry
    seen = []
    with harness.scope():
        instance = server(lambda request: seen.append(request) or httpx.Response(200, json={'ref': 'x'}))
        with pytest.raises(PermissionError):
            asyncio.run(instance.session.call_tool('write', {'body': 'unowned'}))
        result = registry.dispatch('mcp__public__write', {'unexpected': 'field'})
        assert 'error' in json.loads(result) and not seen


def test_bad_receipt_blocks_replay(harness):
    from tools.registry import registry
    seen = []
    with harness.scope():
        server(lambda request: seen.append(request) or httpx.Response(200, json={'not_a_receipt': True}))
        result = json.loads(registry.dispatch('mcp__public__write', {'body': 'intent'}))
        assert result['status'] == 'outcome_unknown'
        assert harness.runs['primary'].dispatch_blocked.is_set()
        assert len(seen) == 1


def test_skills_are_profile_local_and_secrets_and_symlinks_are_not_documents(harness):
    from tools.skills_tool import skills_list, skill_view
    root = harness.home / 'skills' / 'example'
    root.mkdir(parents=True)
    (root / 'SKILL.md').write_text('---\nname: example\ndescription: owned fixture\n---\nInstructions')
    (root / 'secrets.md').write_text('must never be returned')
    (root / 'references').mkdir()
    (root / 'references' / 'escape.md').symlink_to(root / 'secrets.md')
    with harness.scope():
        assert json.loads(skills_list())['skills'][0]['name'] == 'example'
        assert 'Instructions' in json.loads(skill_view('example'))['content']
        for path in ('secrets.md', '../secrets.md', 'references/escape.md'):
            with pytest.raises((PermissionError, ValueError, OSError)):
                skill_view('example', file_path=path)


def test_conversation_capture_uses_stable_turn_ids_and_owned_transport(harness):
    from agent.conversation_mcp import sync_turn
    from agent.memory_router import RoutedMemoryManager
    seen = []
    with harness.scope():
        run = harness.runs['primary']
        run.agent._memory_manager = RoutedMemoryManager(harness.primary, harness.raw)
        def respond(request):
            seen.append(json.loads(request.content)['params']['arguments'])
            return httpx.Response(200, json={'ref': 'thread-receipt'})
        server(respond)
        result = {'final_response': 'Visible answer', 'completed': True,
                  'reasoning': 'Private reasoning must not be transmitted'}
        first = sync_turn(run, 'Visible question', result)
        second = sync_turn(run, 'Visible question', result)
        assert first['conversation_sync']['status'] == second['conversation_sync']['status'] == 'confirmed'
        assert len(seen) == 1
        turns = seen[0]['turns']
        assert [turn['content'] for turn in turns] == ['Visible question', 'Visible answer']
        assert [turn['role'] for turn in turns] == ['user', 'assistant']
        assert turns[0]['id'] != turns[1]['id'] and turns[0]['seq'] < turns[1]['seq']
        assert seen[0]['session_id'] == run.session_id


def test_conversation_capture_reports_unconfirmed_write(harness):
    from agent.conversation_mcp import sync_turn
    from agent.memory_router import RoutedMemoryManager
    with harness.scope():
        run = harness.runs['primary']
        run.agent._memory_manager = RoutedMemoryManager(harness.primary, harness.raw)
        server(lambda request: httpx.Response(200, json={'invalid': 'receipt'}))
        result = sync_turn(run, 'Question', {'final_response': 'Answer', 'completed': True})
        assert result['conversation_sync']['status'] == 'not_confirmed'
        assert run.dispatch_blocked.is_set()
