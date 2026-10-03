"""Real owner RPC, two-home configuration revisions and frozen constructors."""
import json
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

from agent.agent_configuration import configured_agent_selection
from agent.identity_lifecycle import identity_construction, agent_runtime_scope, identity_config
from tests.agent.test_identity_lifecycle import config, write_config
from tests.tui_gateway.test_artifact_rpc import result, denied


@identity_construction
def construct(agent, session_id=None, session_db=None, parent_session_id=None, side_agent=False,
              skip_memory=False, skip_background_review=False, platform=None, ephemeral_system_prompt=None):
    agent.session_id, agent._session_db = session_id, session_db
    agent._session_init_model_config = {}
    agent._cached_system_prompt = ephemeral_system_prompt or 'original prefix'
    agent.memory_skipped = skip_memory


@pytest.fixture
def identities(tmp_path, monkeypatch):
    from hermes_state import SessionDB
    from tui_gateway import server
    from hermes_constants import set_hermes_home_override, reset_hermes_home_override
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    homes, dbs, agents, peers, sessions = {}, {}, {}, {}, {}
    for label in ('a', 'b'):
        home = tmp_path / label
        raw = config('primary')
        raw['agent_identity']['profile_id'] = 'profile_' + label
        raw['delegation'] = None if label == 'a' else {'specialists': None}
        raw['agent_identity']['agents']['assistant']['allowed_tools'] = ['memory', 'todo_list']
        write_config(home, raw)
        token = set_hermes_home_override(str(home))
        try:
            db = SessionDB(home / 'state.db')
            agent = SimpleNamespace()
            construct(agent, session_id='primary-session', session_db=db)
        finally:
            reset_hermes_home_override(token)
        peer = SimpleNamespace(write=lambda frame: True)
        homes[label], dbs[label], agents[label], peers[label] = home, db, agent, peer
        sessions['live-' + label] = {'agent': agent, 'profile_home': str(home), 'transport': peer,
            'session_key': agent.session_id, 'history': [], 'history_lock': threading.RLock()}
    monkeypatch.setenv('HERMES_HOME', str(homes['a']))
    monkeypatch.setattr(server, '_sessions', sessions)
    def call(method, label='a', **params):
        return server.dispatch({'jsonrpc': '2.0', 'id': 'config', 'method': method,
            'params': {'schema_version': 1, 'session_id': 'live-' + label, **params}}, transport=peers[label])
    def create_session(agent_id, sid, label='a'):
        token = set_hermes_home_override(str(homes[label]))
        try:
            agent = SimpleNamespace()
            with configured_agent_selection(agent_id):
                construct(agent, session_id=sid, session_db=dbs[label])
            return agent
        finally:
            reset_hermes_home_override(token)
    yield SimpleNamespace(call=call, homes=homes, dbs=dbs, agents=agents, sessions=sessions,
                          create_session=create_session)
    for db in dbs.values():
        db.close()


def test_owner_revisions_names_and_copies_cannot_change_memory_authority(identities):
    rt = identities
    original_config = (rt.homes['a'] / 'config.yaml').read_bytes()
    rows = result(rt.call('runtime.agent.list'))['agents']
    primary, specialist = (next(row for row in rows if row['agent_id'] == key) for key in ('primary', 'assistant'))
    assert primary['role'] == 'primary' and primary['builtin_memory_namespace'] is None
    assert primary['personal_memory_mutation_supported'] is False
    renamed = result(rt.call('runtime.agent.update', agent_id='assistant', expected_revision=1,
        config={**specialist['config'], 'name': 'primary', 'instructions': 'Only specialist instructions'}))['agent']
    assert renamed['role'] == 'specialist' and renamed['memory_backend'] == 'builtin'
    assert renamed['builtin_memory_namespace'] == specialist['builtin_memory_namespace']
    assert renamed['active_session_revision'] == 1 and renamed['revision'] == 2
    for field, value in [('role', 'primary'), ('memory_backend', 'personal_mcp'), ('agent_id', 'primary')]:
        denied(rt.call('runtime.agent.update', agent_id='assistant', expected_revision=2,
               config={**specialist['config'], field: value}))
    denied(rt.call('runtime.agent.create', copy_from_agent_id='primary', config=specialist['config']), 'agent_copy_denied')
    fresh_identity = result(rt.call('runtime.agent.create', config=specialist['config']))['agent']
    assert fresh_identity['role'] == 'specialist' and fresh_identity['memory_backend'] == 'builtin'
    assert fresh_identity['builtin_memory_namespace'] != specialist['builtin_memory_namespace']

    denied(rt.call('runtime.agent.update', agent_id='assistant', expected_revision=1,
                   config=specialist['config']), 'revision_conflict')
    denied(rt.call('runtime.agent.update', agent_id='assistant', expected_revision=2,
                   config={**specialist['config'], 'project_grants': ['not-granted']}), 'agent_grant_escalation')
    copied = result(rt.call('runtime.agent.create', copy_from_agent_id='assistant', config=renamed['config']))['agent']
    assert copied['agent_id'] != specialist['agent_id'] and copied['role'] == 'specialist'
    assert copied['builtin_memory_namespace'] != specialist['builtin_memory_namespace']
    denied(rt.call('runtime.agent.get', 'b', agent_id=copied['agent_id']), 'agent_not_found')
    for label, expected in [('a', 2), ('b', 1), ('a', 2)]:
        row = result(rt.call('runtime.agent.get', label, agent_id='assistant'))['agent']
        assert row['revision'] == expected
    assert (rt.homes['a'] / 'config.yaml').read_bytes() == original_config
    a = rt.create_session('assistant', 'specialist-a')
    b = rt.create_session(copied['agent_id'], 'specialist-b')
    from tools.individual_memory_store import IndividualMemoryStore
    from agent.individual_memory_scope import IndividualMemoryScope
    from agent.agent_identity import IdentityPolicyError
    with agent_runtime_scope(a.runtime_context):
        store = IndividualMemoryStore(a.runtime_context)
        store.load_from_disk()
        store.add('memory', 'Private to this identity')
        namespace = store.namespace_id
    with agent_runtime_scope(b.runtime_context):
        other = IndividualMemoryStore(b.runtime_context)
        other.load_from_disk()
        assert other.memory_entries == [] and other.namespace_id != namespace
        with pytest.raises(IdentityPolicyError):
            _ = store.memory_entries
    with agent_runtime_scope(rt.agents['a'].runtime_context):
        with pytest.raises(IdentityPolicyError):
            IndividualMemoryScope.from_context(rt.agents['a'].runtime_context)
        from hermes_state import SessionDB
        from agent.agent_configuration import AgentConfigurationRegistry
        from hermes_state_runtime import RuntimeStoreError
        alternate = SessionDB(rt.homes['a'] / 'alternate.db')
        try:
            with pytest.raises(RuntimeStoreError):
                AgentConfigurationRegistry(rt.agents['a'].runtime_context, alternate)
            with configured_agent_selection('assistant'), pytest.raises(RuntimeStoreError):
                construct(SimpleNamespace(), session_id='alternate-managed', session_db=alternate)
        finally:
            alternate.close()
    # Owning a specialist transport is not authority to edit other identities.
    rt.sessions['live-a']['agent'] = a
    denied(rt.call('runtime.agent.list'), 'agent_configuration_owner_required')


def test_session_prefix_grants_and_restart_remain_pinned_until_new_session(identities):
    from tools.capability_broker import CapabilityDenied, require_live_policy
    rt = identities
    original = result(rt.call('runtime.agent.get', agent_id='assistant'))['agent']
    first = result(rt.call('runtime.agent.update', agent_id='assistant', expected_revision=1,
        config={**original['config'], 'instructions': 'First frozen instructions'}))['agent']
    old = rt.create_session('assistant', 'old-session')
    prefix, policy = old._cached_system_prompt, old.runtime_context.policy
    second = result(rt.call('runtime.agent.update', agent_id='assistant', expected_revision=first['revision'],
        config={**first['config'], 'instructions': 'New session instructions', 'memory_allowed': False}))['agent']
    assert second['authority_revocation_revision'] == second['revision']
    assert second['active_session_revision_revoked'] is True
    assert old._cached_system_prompt == prefix and old.runtime_context.policy == policy
    with agent_runtime_scope(old.runtime_context):
        assert 'memory' in identity_config()['agent_identity']['agents']['assistant']['allowed_tools']
        with pytest.raises(CapabilityDenied, match='narrowed or archived'):
            require_live_policy(require_run=False)
    # Revocation blocks execution, while the same owned specialist transport
    # can still inspect the exact active-versus-desired activation metadata.
    rt.sessions['live-old'] = {**rt.sessions['live-a'], 'agent': old, 'session_key': old.session_id}
    status = result(rt.call('runtime.agent.session.get', session_id='live-old'))
    from tui_gateway.contracts.agent_configuration import AgentSessionConfiguration
    AgentSessionConfiguration.model_validate(status)
    assert status['agent_id'] == 'assistant' and status['role'] == 'specialist'
    assert status['active_configuration_revision'] == first['revision']
    assert status['desired_configuration_revision'] == second['revision']
    assert status['authority_current'] is False and status['revocation_code'] == 'agent_configuration_revoked'
    assert status['authority_revocation_revision'] == second['revision']
    assert status['startup_frozen'] and status['execution_authority'] is False
    assert status['active_workflows'] == status['desired_workflows'] == []
    assert 'First frozen instructions' not in json.dumps(status)
    assert 'New session instructions' not in json.dumps(status)
    assert old._cached_system_prompt == prefix and old.runtime_context.policy == policy
    # A process-local cache cannot supply the resumed pin: close/reopen SQLite.
    from hermes_state import SessionDB
    rt.dbs['a'].close()
    rt.dbs['a'] = SessionDB(rt.homes['a'] / 'state.db')
    rt.agents['a']._session_db = rt.dbs['a']
    with pytest.raises(CapabilityDenied, match='narrowed or archived'):
        rt.create_session('assistant', 'old-session')
    assert second['authority_revocation_revision'] == second['revision']
    assert second['active_session_revision_revoked'] is True
    assert old._cached_system_prompt == prefix and old.runtime_context.policy == policy
    fresh = rt.create_session('assistant', 'new-session')
    assert 'New session instructions' in fresh._cached_system_prompt and fresh.memory_skipped
    assert 'memory' not in fresh.runtime_context.policy.allowed_tools
    archived = result(rt.call('runtime.agent.archive', agent_id='assistant', expected_revision=second['revision']))['agent']
    assert archived['archived'] and archived['builtin_memory_namespace'] == original['builtin_memory_namespace']
    with pytest.raises(CapabilityDenied, match='narrowed or archived'):
        rt.create_session('assistant', 'old-session')
    from hermes_state_runtime import RuntimeStoreError
    with pytest.raises(RuntimeStoreError, match='archived or missing'):
        rt.create_session('assistant', 'never-admitted')
    denied(rt.call('runtime.agent.archive', agent_id='primary', expected_revision=1), 'agent_primary_immutable')
