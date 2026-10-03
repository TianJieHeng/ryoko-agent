"""Canonical display titles are repeatable metadata, never physical aliases."""
from hermes_state import SessionDB
from tests.tui_gateway.test_artifact_rpc import result
from tests.tui_gateway.test_runtime_conversations_rpc import runtime  # noqa: F401


def test_duplicate_titles_survive_compression_rename_search_and_restart(runtime):
    rt = runtime
    first = result(rt.call('create', idempotency_key='first', title='A new thought'))['conversation']
    second = result(rt.call('create', idempotency_key='second', title='A new thought'))['conversation']
    unnamed = [result(rt.call('create', idempotency_key=f'unnamed-{index}'))['conversation'] for index in range(2)]
    assert first['conversation_id'] != second['conversation_id'] and all(row['title'] == '' for row in unnamed)
    db, sid = rt.stores['a'], first['conversation_id']
    assert db.get_session_title(sid) is None and db.get_session_title(second['conversation_id']) is None
    message = {'role': 'user', 'content': 'Keep this discussion', 'message_uid': 'retained-message'}
    db.append_messages_batch(sid, [message])
    binding = db.get_session_model_config_value(sid, 'agent_identity')
    db.publish_compression_child(parent_session_id=sid, child_session_id='title-tip', source='web', messages=[message],
        model_config={'agent_identity': binding}, require_compression_lease=False)
    db.set_session_title('title-tip', 'Physical CLI alias')
    renamed = result(rt.call('rename', conversation_id=sid, idempotency_key='rename', expected_revision=1,
                             title='A shared display title'))['conversation']
    sibling = result(rt.call('rename', conversation_id=second['conversation_id'], idempotency_key='rename-second',
                             expected_revision=1, title='A shared display title'))['conversation']
    assert renamed['revision'] == sibling['revision'] == 2
    assert db.get_session_title('title-tip') == 'Physical CLI alias'
    assert result(rt.call('bind', conversation_id=sid))['conversation']['title'] == renamed['title']
    assert result(rt.call('history', conversation_id=sid))['messages'][0]['text'] == message['content']
    rt.stores['a'].close()
    rt.stores['a'] = SessionDB(rt.homes['a'] / 'state.db')
    for label, expected in [('a', {sid, second['conversation_id']}), ('b', set()), ('a', {sid, second['conversation_id']})]:
        rows = result(rt.call('list', label, query='shared display'))['conversations']
        assert {row['conversation_id'] for row in rows} == expected
    page = result(rt.call('list', query='shared display', limit=1))
    next_page = result(rt.call('list', query='shared display', limit=1, cursor=page['next_cursor']))
    assert page['conversations'][0]['conversation_id'] != next_page['conversations'][0]['conversation_id']
    # Historical receipts do not overwrite later canonical display metadata.
    assert result(rt.call('operation.get', idempotency_key='first'))['conversation'] == first
    assert result(rt.call('create', idempotency_key='first', title='A new thought'))['conversation'] == first
    assert result(rt.call('bind', conversation_id=sid))['conversation']['title'] == renamed['title']
