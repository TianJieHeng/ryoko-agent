"""Chunked flood failures are ambiguous and remain held without external replay."""
import sqlite3

from gateway import delivery_ledger as dl


def record(oid, *, profile=None):
    dl.record_obligation(obligation_id=oid, session_key='session-' + oid, platform='telegram',
                         chat_id='123', thread_id='77', content='.' * 3000, adapter_profile=profile)
    dl.mark_failed(oid, 'flood_control:185')


def read(oid):
    with sqlite3.connect(dl._db_path()) as conn:
        conn.row_factory = sqlite3.Row
        return dict(conn.execute('SELECT * FROM delivery_obligations WHERE obligation_id=?', (oid,)).fetchone())


def test_flood_can_have_partial_chunks_and_is_never_automatically_replayed():
    record('due')
    record('other', profile='other')
    record('blocked')
    dl.mark_failed('blocked', 'Forbidden: bot was blocked by the user')
    stamp = read('due')['updated_at']
    assert dl.sweep_failed_for_runtime('telegram', now=stamp + 184) == []
    assert dl.sweep_failed_for_runtime('telegram', now=stamp + 186) == []
    assert dl.pending_retries(now=stamp + 186) == []
    assert read('due')['state'] == 'outcome_unknown'
    assert read('due')['attempts'] == read('other')['attempts'] == read('blocked')['attempts'] == 0
    assert read('due')['content'] == '.' * 3000


def test_inherited_failed_flood_row_is_held_without_losing_payload_or_owner():
    record('boot')
    original = read('boot')
    # Simulate an old version's failed row before its uncertainty mapping existed.
    with sqlite3.connect(dl._db_path()) as conn:
        conn.execute("UPDATE delivery_obligations SET state='failed',owner_pid=NULL,owner_started_at=NULL,adapter_profile=NULL")
    assert dl.sweep_recoverable(now=original['updated_at'] + 200,
                               deliverable_targets={('telegram', None)}) == []
    held = read('boot')
    assert held['state'] == 'outcome_unknown' and held['attempts'] == 0
    assert held['content'] == original['content']
    assert held['session_key'] == original['session_key']
    assert held['chat_id'] == original['chat_id']
    assert dl.sweep_recoverable(now=original['updated_at'] + 300) == []
