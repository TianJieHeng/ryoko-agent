"""Durable capture and the real REST sender, with a local in-memory HTTP transport."""
import json

import httpx
import pytest

from agent.conversation_outbox import database, enqueue, settings, capture_turn
from agent.conversation_worker import deliver_one, read_token, recover_inflight, status
from tests.tools import test_mcp_budget as budgets
from tests.tools.test_mcp_budget import harness, mcp_fixture  # noqa: F401


def config_for(home):
    token = home / 'worker.env'
    token.write_text('RYOKO_CONVO_PUSH_TOKEN=synthetic-worker-only\n')
    token.chmod(0o600)
    return {'enabled': True, 'endpoint': 'https://memory.fixture/v1/conversations/turns',
            'token_file': str(token), 'source': 'hermes', 'agent': 'fixture'}


@pytest.fixture(autouse=True)
def configure(monkeypatch, tmp_path):
    original = budgets.configured
    def configured():
        raw = original()
        raw['memory']['conversation_push'] = config_for(tmp_path)
        return raw
    monkeypatch.setattr(budgets, 'configured', configured)


def records(home):
    with database(home) as conn:
        return [dict(row) for row in conn.execute('SELECT * FROM turns ORDER BY seq')]


def receipt(request, *, duplicate=False):
    body = json.loads(request.content)
    assert request.headers['authorization'] == 'Bearer synthetic-worker-only'
    return httpx.Response(200, json={'session_id': body['session_id'], 'hub': 'hub-fixture',
        'accepted': 0 if duplicate else len(body['turns']), 'duplicate': len(body['turns']) if duplicate else 0})


def test_capture_admission_completion_restart_and_delivery(harness):
    from agent.memory_router import RoutedMemoryManager
    with harness.scope():
        run = harness.runs['primary']
        run.agent._memory_manager = RoutedMemoryManager(harness.primary, harness.raw)
        assert capture_turn(run, 'Question') is None
        assert len(records(harness.home)) == 1  # queued before model response
        result = capture_turn(run, 'Question', {'final_response': 'Answer', 'reasoning': 'do not transmit'})
        assert result['conversation_sync']['status'] == 'queued'
        assert len(records(harness.home)) == 2
        # Same callback twice cannot insert or replace a message.
        capture_turn(run, 'Question', {'final_response': 'Answer'})
    cfg = settings(harness.raw)
    sent = []
    def handler(request):
        sent.append(json.loads(request.content))
        return receipt(request)
    # A new connection after capture models an independent process/restart.
    recover_inflight(harness.home)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert deliver_one(harness.home, cfg, client)
        assert deliver_one(harness.home, cfg, client)
        assert not deliver_one(harness.home, cfg, client)
    assert [body['turns'][0]['content'] for body in sent] == ['Question', 'Answer']
    assert all(row['payload'] == '' and row['receipt'] for row in records(harness.home))
    assert status(harness.home)['counts'] == {'sent': 2}


def test_lost_ack_retries_identical_bytes_and_blocks_later_turns(harness):
    cfg = settings(harness.raw)
    enqueue(harness.home, cfg, 'session', 'command', 100, [('user', 'Question'), ('assistant', 'Answer')])
    seen = []
    def handler(request):
        seen.append(request.content)
        if len(seen) == 1:
            raise httpx.ReadTimeout('lost acknowledgement')
        return receipt(request, duplicate=True)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert deliver_one(harness.home, cfg, client, now=1000)
        assert not deliver_one(harness.home, cfg, client, now=1001)
        assert deliver_one(harness.home, cfg, client, now=1002)
    assert seen[0] == seen[1]
    assert [row['state'] for row in records(harness.home)] == ['sent', 'pending']


def test_crash_after_send_recovers_stable_payload(harness):
    cfg = settings(harness.raw)
    enqueue(harness.home, cfg, 'session', 'command', 100, [('user', 'Question')])
    before = records(harness.home)[0]['payload']
    with database(harness.home) as conn, conn:
        conn.execute("UPDATE turns SET state='sending'")
    recover_inflight(harness.home)
    with httpx.Client(transport=httpx.MockTransport(lambda request: receipt(request, duplicate=True))) as client:
        assert deliver_one(harness.home, cfg, client)
    assert records(harness.home)[0]['state'] == 'sent'
    assert json.loads(before)['turns'][0]['id'] == 'command:user'


@pytest.mark.parametrize('code', [401, 403, 429, 500, 302, 400])
def test_errors_retain_queue_and_never_follow_redirects(harness, code):
    cfg = settings(harness.raw)
    enqueue(harness.home, cfg, 'session', 'command', 100, [('user', 'Question')])
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(code, headers={'Location': 'https://other.fixture/steal'})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert deliver_one(harness.home, cfg, client, now=1000)
    row = records(harness.home)[0]
    assert len(requests) == 1 and row['payload'] and row['receipt'] is None
    assert row['state'] == ('blocked' if code in (302, 400) else 'pending')


def test_malformed_receipt_never_marks_delivered(harness):
    cfg = settings(harness.raw)
    enqueue(harness.home, cfg, 'session', 'command', 100, [('user', 'Question')])
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={
            'session_id': 'other-session', 'hub': 'hub', 'accepted': 1, 'duplicate': 0}))) as client:
        assert deliver_one(harness.home, cfg, client)
    assert records(harness.home)[0]['state'] == 'pending'


def test_destination_change_does_not_retarget_old_queue_and_profiles_are_separate(harness, tmp_path):
    cfg = settings(harness.raw)
    other = tmp_path / 'other-profile'
    other.mkdir()
    enqueue(harness.home, cfg, 'session', 'command', 100, [('user', 'A')])
    enqueue(other, cfg, 'session', 'command', 100, [('user', 'B')])
    assert json.loads(records(harness.home)[0]['payload'])['turns'][0]['content'] == 'A'
    assert json.loads(records(other)[0]['payload'])['turns'][0]['content'] == 'B'
    def forbidden(request):
        pytest.fail('Old queue must never move to a different endpoint')
    with httpx.Client(transport=httpx.MockTransport(forbidden)) as client:
        assert not deliver_one(harness.home, {**cfg, 'endpoint': 'https://other.fixture/v1/conversations/turns'}, client)
    assert json.loads(records(harness.home)[0]['payload'])['turns'][0]['content'] == 'A'


def test_token_permissions_and_changed_turn_ids_fail_closed(harness):
    cfg = settings(harness.raw)
    enqueue(harness.home, cfg, 'session', 'command', 100, [('user', 'Original')])
    with pytest.raises(ValueError, match='different content'):
        enqueue(harness.home, cfg, 'session', 'command', 100, [('user', 'Changed')])
    from pathlib import Path
    token = Path(cfg['token_file'])
    token.chmod(0o644)
    with pytest.raises(PermissionError):
        read_token(token)


def test_child_never_captures_personal_conversation(harness):
    with harness.scope(harness.child):
        run = harness.runs[harness.child.identity.agent_id]
        result = capture_turn(run, 'child text', {'final_response': 'child output'})
        assert 'conversation_sync' not in result
    assert records(harness.home) == []


def test_secrets_are_redacted_before_the_queue_is_written(harness):
    cfg = settings(harness.raw)
    secret = 'synthetic-' + 'opaque0123456789abcdef'
    enqueue(harness.home, cfg, 'session', 'command', 100,
            [('user', 'Authorization: Bearer ' + secret + ' https://name:passphrase@example.test/path')])
    payload = records(harness.home)[0]['payload']
    assert secret not in payload and 'passphrase' not in payload
    assert 'example.test/path' in payload


def test_oversized_turn_is_reported_without_partial_queueing(harness):
    from agent.conversation_outbox import MAX_REQUEST_BYTES
    cfg = settings(harness.raw)
    with pytest.raises(ValueError, match='request bound'):
        enqueue(harness.home, cfg, 'session', 'command', 100,
                [('user', 'Valid text'), ('assistant', 'x' * MAX_REQUEST_BYTES)])
    assert records(harness.home) == []


def test_private_worker_credential_is_not_needed_by_capture(harness):
    from pathlib import Path
    cfg = settings(harness.raw)
    Path(cfg['token_file']).unlink()
    enqueue(harness.home, cfg, 'session', 'command', 100, [('user', 'Queued without exposing credential')])
    def forbidden(request):
        pytest.fail('Missing credential must not send a request')
    with httpx.Client(transport=httpx.MockTransport(forbidden)) as client:
        assert deliver_one(harness.home, cfg, client)
    assert records(harness.home)[0]['state'] == 'pending'


def test_auto_title_change_between_admission_and_completion_preserves_capture(harness):
    from agent.memory_router import RoutedMemoryManager
    with harness.scope():
        run = harness.runs['primary']
        run.agent._memory_manager = RoutedMemoryManager(harness.primary, harness.raw)
        capture_turn(run, 'Question')
        first = records(harness.home)[0]['payload_sha256']
        harness.db.set_session_title(run.session_id, 'A newly generated title')
        result = capture_turn(run, 'Question', {'final_response': 'Answer'})
        assert result['conversation_sync']['status'] == 'queued'
    queued = records(harness.home)
    assert len(queued) == 2 and queued[0]['payload_sha256'] == first
