"""Durable, profile-owned queue for the harness's existing live-turn REST API."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import sqlite3
import stat
import time
from urllib.parse import urlsplit

logger = logging.getLogger('run_agent')
MAX_REQUEST_BYTES = 65536


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def settings(raw):
    config = raw.get('memory', {}).get('conversation_push')
    if config is None:
        return None
    if not isinstance(config, dict):
        raise ValueError('Conversation push configuration must be a mapping')
    if config.get('enabled') is False:
        return None
    required = {'enabled', 'endpoint', 'token_file', 'source', 'agent'}
    if set(config) != required or config['enabled'] is not True:
        raise ValueError('Invalid memory.conversation_push configuration')
    if any(not isinstance(config[key], str) or not config[key] for key in required - {'enabled'}):
        raise ValueError('Conversation push settings must be nonempty strings')
    url = urlsplit(config['endpoint'])
    if (url.scheme != 'https' or not url.hostname or url.username or url.password
            or url.query or url.fragment or url.path != '/v1/conversations/turns'):
        raise ValueError('Conversation endpoint must be an HTTPS /v1/conversations/turns URL')
    if not Path(config['token_file']).is_absolute():
        raise ValueError('Conversation worker token_file must be absolute')
    identity = raw.get('agent_identity', {})
    primary = identity.get('primary_agent_id')
    policy = identity.get('agents', {}).get(primary, {})
    if policy.get('role') != 'primary' or policy.get('memory_backend') != 'personal_mcp':
        raise ValueError('Conversation push requires an explicit primary personal-memory owner')
    owner = [identity.get('principal_id'), identity.get('profile_id'), primary]
    if any(not isinstance(value, str) or not value for value in owner):
        raise ValueError('Conversation push owner is incomplete')
    return {**config, 'owner': owner}


def binding(config):
    # Rotation of the credential does not change the queue's destination.
    return hashlib.sha256(canonical({key: config[key] for key in
        ('endpoint', 'source', 'agent', 'owner')}).encode()).hexdigest()


def spool_directory(home):
    root = Path(home) / 'conversation-outbox'
    root.mkdir(mode=0o700, exist_ok=True)
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise PermissionError('Conversation outbox must be a private owned directory')
    return root


@contextmanager
def database(home):
    path = spool_directory(home) / 'outbox.sqlite3'
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077 or info.st_nlink != 1):
            raise PermissionError('Conversation queue must be a private regular file')
    finally:
        os.close(fd)
    conn = sqlite3.connect(path, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA synchronous=FULL')
        conn.execute('''CREATE TABLE IF NOT EXISTS turns (
            id TEXT PRIMARY KEY, session_id TEXT NOT NULL, seq INTEGER NOT NULL,
            binding TEXT NOT NULL, payload TEXT NOT NULL, payload_sha256 TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
            next_attempt REAL NOT NULL DEFAULT 0, last_status TEXT, receipt TEXT,
            created REAL NOT NULL, updated REAL NOT NULL)''')
        conn.execute('CREATE INDEX IF NOT EXISTS turns_pending ON turns(state,next_attempt)')
        conn.commit()
        yield conn
    finally:
        conn.close()


def enqueue(home, config, session_id, command_id, timestamp, rows):
    """Commit immutable turns before returning; re-delivery cannot change an ID."""
    from agent.redact import redact_for_egress, redact_sensitive_text
    prepared = []
    for role, content in rows:
        if role not in ('user', 'assistant'):
            raise ValueError('Only visible user and assistant turns are captured')
        if isinstance(content, list):
            content = '\n'.join(part['text'] for part in content if isinstance(part, dict)
                and part.get('type') == 'text' and isinstance(part.get('text'), str))
        if not isinstance(content, str) or not content.strip():
            continue
        content = redact_for_egress(redact_sensitive_text(content, force=True, redact_url_credentials=True))
        turn = {'id': f'{command_id}:{role}', 'seq': int(timestamp * 1000000) * 2 + (role == 'assistant'),
                'role': role, 'content': content,
                'ts': datetime.fromtimestamp(timestamp, timezone.utc).isoformat()}
        payload = canonical({'source': config['source'], 'session_id': session_id,
                             'agent': config['agent'], 'turns': [turn]})
        if len(payload.encode()) > MAX_REQUEST_BYTES:
            raise ValueError('Conversation turn exceeds the request bound; it was not truncated')
        key = hashlib.sha256(canonical([config['source'], session_id, turn['id']]).encode()).hexdigest()
        prepared.append((key, turn['seq'], payload, hashlib.sha256(payload.encode()).hexdigest()))
    now = time.time()
    with database(home) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        for key, seq, payload, digest in prepared:
            old = conn.execute('SELECT payload_sha256,binding FROM turns WHERE id=?', (key,)).fetchone()
            if old is not None:
                if old['payload_sha256'] != digest or old['binding'] != binding(config):
                    raise ValueError('Conversation turn ID already has different content or destination')
                continue
            conn.execute('INSERT INTO turns(id,session_id,seq,binding,payload,payload_sha256,created,updated) '
                         'VALUES(?,?,?,?,?,?,?,?)', (key, session_id, seq, binding(config), payload, digest, now, now))
    return len(prepared)


def capture_turn(run, user_message, result=None):
    """Queue on admission and completion; the model never receives the REST bearer."""
    from agent.identity_lifecycle import identity_config
    raw = identity_config()
    configured = raw.get('memory', {}).get('conversation_push')
    if configured is None or (isinstance(configured, dict) and configured.get('enabled') is False):
        return result
    if run.context.policy.role != 'primary' or run.context.policy.memory_backend != 'personal_mcp':
        return result
    try:
        from agent.runtime_commands import assert_runtime_dispatch
        if assert_runtime_dispatch() is not run:
            raise PermissionError('Conversation capture belongs to a different turn')
        run.agent._memory_manager.assert_owner()
        config = settings(raw)
        owner = run.context.identity
        if config['owner'] != [owner.principal_id, owner.profile_id, owner.agent_id]:
            raise PermissionError('Conversation capture owner mismatch')
        timestamp = run.db.read_runtime_run_accepted_at(run.session_id, run.run_id)
        if timestamp is None:
            raise ValueError('Conversation capture requires durable acceptance time')
        rows = [('user', user_message)]
        if isinstance(result, dict):
            rows.append(('assistant', result.get('final_response')))
        count = enqueue(run.context.profile_home, config, run.session_id, run.command_id, timestamp, rows)
        status = {'status': 'queued' if count else 'no_visible_text', 'session_id': run.session_id}
    except Exception as exc:
        logger.warning('Conversation could not be queued (%s).', type(exc).__name__)
        status = {'status': 'not_queued', 'error_type': type(exc).__name__}
    return {**result, 'conversation_sync': status} if isinstance(result, dict) else result
