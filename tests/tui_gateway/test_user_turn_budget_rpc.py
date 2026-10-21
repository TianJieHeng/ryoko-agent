"""Only explicit owner input can cross a task budget boundary."""
from dataclasses import replace
from copy import copy

from tests.tui_gateway.test_runtime_rpc import envelope, runtime  # noqa: F401


def test_user_turn_marker_is_durable_owned_and_not_available_to_children(runtime):
    for label in ('a', 'b', 'a'):
        runtime.sessions[f'live-{label}']['running'] = True
        cid = f'user-{label}'
        request = envelope(cid, expected_revision=None, new_user_turn=True)
        response = runtime.call('runtime.command', label, **request)
        assert response['result']['status'] == 'accepted'
        agent = runtime.agents[label]
        stored = agent._session_db.read_runtime_command(agent.session_id, cid)
        assert stored['command']['new_user_turn'] is True
        assert runtime.call('runtime.command', label, **request)['result'] == response['result']
    foreign = runtime.call('runtime.command', 'a', via=runtime.peers['b'],
                           **envelope('foreign', expected_revision=None, new_user_turn=True))
    assert 'error' in foreign
    agent = runtime.agents['a']
    original = agent.runtime_context
    child_policy = replace(original.policy, role='child', memory_backend='builtin')
    child_identity = replace(original.identity, agent_id='child_' + 'a' * 32,
                             session_id='child-session', lifecycle='ephemeral',
                             parent_agent_id=original.identity.agent_id, policy_digest=child_policy.digest)
    child = copy(agent)
    child.runtime_context = replace(original, identity=child_identity, policy=child_policy)
    child.session_id = child_identity.session_id
    child._session_db.create_session(child.session_id, source='subagent')
    child._session_db.claim_session_agent_identity(child.session_id, child_identity.to_record())
    runtime.sessions['live-a']['agent'] = child
    denied = runtime.call('runtime.command', **envelope('child', expected_revision=None, new_user_turn=True))
    assert denied['error']['code'] == 4000
    assert child._session_db.read_runtime_command(child.session_id, 'child') is None
    runtime.sessions['live-a']['agent'] = agent
    # Existing unmarked clients preserve their command digest and inheritance.
    plain = runtime.call('runtime.command', **envelope('plain', expected_revision=None))
    assert plain['result']['status'] == 'accepted'
    assert 'new_user_turn' not in agent._session_db.read_runtime_command(agent.session_id, 'plain')['command']
