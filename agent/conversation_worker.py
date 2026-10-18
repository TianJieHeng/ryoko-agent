"""Two-second REST outbox worker. Run independently of the model/CLI lifetime.

The harness deduplicates stable (source, session_id, turn.id) keys, so a crash
after remote acceptance can safely retry the exact queued bytes.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import logging
import os
from pathlib import Path
import signal
import stat
import threading
import time

import httpx
from ruamel.yaml import YAML

from agent.conversation_outbox import binding, database, settings, spool_directory

POLL_SECONDS = 2
MAX_RESPONSE_BYTES = 262144
logger = logging.getLogger('conversation_worker')


def read_token(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'r') as source:
        info = os.fstat(source.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077 or info.st_nlink != 1):
            raise PermissionError('Worker token must be a private owned regular file')
        value = source.read(4097).strip()
    if value.startswith('RYOKO_CONVO_PUSH_TOKEN='):
        value = value.partition('=')[2].strip()
    if not value or len(value) > 4096 or any(char.isspace() for char in value):
        raise ValueError('Invalid worker token file')
    return value


def load_settings(home):
    with (Path(home) / 'config.yaml').open() as source:
        raw = YAML(typ='safe').load(source)
    return settings(raw)


def recover_inflight(home):
    # Caller holds the exclusive worker lock. These rows may have been accepted
    # remotely: retain identical bodies and let the harness deduplicate them.
    with database(home) as conn, conn:
        conn.execute("UPDATE turns SET state='pending',next_attempt=0,last_status='restart_retry' WHERE state='sending'")


def deliver_one(home, config, client, *, now=None):
    """One bounded attempt, journaled before I/O; failures retain queued content."""
    clock_now = time.time if now is None else lambda: now
    started = clock_now()
    with database(home) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute("""SELECT t.* FROM turns t WHERE t.state='pending' AND t.next_attempt<=?
            AND t.binding=? AND NOT EXISTS (SELECT 1 FROM turns earlier
                WHERE earlier.session_id=t.session_id AND earlier.binding=t.binding
                AND earlier.seq<t.seq AND earlier.state!='sent')
            ORDER BY t.created,t.seq,t.id LIMIT 1""", (started, binding(config))).fetchone()
        if row is None:
            return False
        conn.execute("UPDATE turns SET state='sending',attempts=attempts+1,updated=? WHERE id=?", (started, row['id']))
    state, reason, receipt = 'pending', 'transport_error', None
    delay = min(120, POLL_SECONDS * 2 ** min(row['attempts'], 6))
    try:
        if hashlib.sha256(row['payload'].encode()).hexdigest() != row['payload_sha256']:
            state, reason = 'blocked', 'payload_integrity'
        else:
            token = read_token(config['token_file'])
            deadline = time.monotonic() + 15
            # No redirects and no ambient proxy/auth environment. Neither the
            # request body nor authorization header is ever written to logs.
            with client.stream('POST', config['endpoint'], content=row['payload'].encode(),
                    headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json',
                             'User-Agent': 'ryoko-agent/conversation-worker', 'Accept-Encoding': 'identity'},
                    timeout=15, follow_redirects=False) as response:
                code = response.status_code
                reason = f'http_{code}'
                if code in (401, 403):
                    delay = 60
                elif code == 429 or code >= 500:
                    pass
                elif code != 200:
                    state = 'blocked'
                else:
                    data = bytearray()
                    if response.headers.get('content-encoding', 'identity') != 'identity':
                        raise ValueError('Compressed receipt is not supported')
                    for chunk in response.iter_bytes():
                        if time.monotonic() > deadline:
                            raise ValueError('Receipt exceeded the request deadline')
                        data.extend(chunk)
                        if len(data) > MAX_RESPONSE_BYTES:
                            raise ValueError('Oversized receipt')
                    result = json.loads(data)
                    count = len(json.loads(row['payload'])['turns'])
                    if (not isinstance(result, dict) or result.get('session_id') != row['session_id']
                            or type(result.get('accepted')) is not int or result['accepted'] < 0
                            or type(result.get('duplicate')) is not int or result['duplicate'] < 0
                            or result['accepted'] + result['duplicate'] != count
                            or not isinstance(result.get('hub'), str) or not 1 <= len(result['hub']) <= 256):
                        raise ValueError('Unconfirmed receipt')
                    state, reason = 'sent', 'confirmed'
                    receipt = json.dumps({key: result[key] for key in ('session_id', 'hub', 'accepted', 'duplicate')})
    except (OSError, ValueError, httpx.HTTPError):
        # Includes malformed/oversized receipts after a possible success. The
        # retry is still the identical immutable body, with the same turn IDs.
        reason = 'transport_or_receipt_error'
    with database(home) as conn, conn:
        finished = clock_now()
        conn.execute('UPDATE turns SET state=?,last_status=?,receipt=?,next_attempt=?,updated=?, '
                     'payload=CASE WHEN ?=\'sent\' THEN \'\' ELSE payload END WHERE id=?',
                     (state, reason, receipt, finished + delay, finished, state, row['id']))
    logger.info('Conversation delivery %s (%s)', state, reason)
    return True


def status(home):
    with database(home) as conn:
        counts = {row['state']: row['n'] for row in conn.execute('SELECT state,count(*) n FROM turns GROUP BY state')}
        failures = [dict(row) for row in conn.execute(
            "SELECT state,last_status,count(*) count FROM turns WHERE state!='sent' GROUP BY state,last_status")]
    return {'counts': counts, 'outstanding': failures}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile-home', type=Path, required=True)
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--status', action='store_true')
    parser.add_argument('--retry-blocked', action='store_true')
    args = parser.parse_args(argv)
    home = args.profile_home.resolve(strict=True)
    if args.status:
        print(json.dumps(status(home)))
        return 0
    lock_path = spool_directory(home) / 'worker.lock'
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit('A conversation worker already owns this profile') from None
        recover_inflight(home)
        if args.retry_blocked:
            with database(home) as conn, conn:
                conn.execute("UPDATE turns SET state='pending',next_attempt=0 WHERE state='blocked'")
        stop = threading.Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: stop.set())
        logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
        # httpx request logs include URL metadata; keep even that out of the service log.
        logging.getLogger('httpx').setLevel(logging.WARNING)
        with httpx.Client(trust_env=False, timeout=15, follow_redirects=False) as client:
            while not stop.is_set():
                try:
                    config = load_settings(home)
                    if config is not None:
                        # A previous receipt-journal failure can leave a sending
                        # row even without a process restart. No request is in
                        # flight here, and this process owns the exclusive lock.
                        recover_inflight(home)
                        for _ in range(16):
                            if stop.is_set() or not deliver_one(home, config, client):
                                break
                except Exception as exc:
                    logger.warning('Conversation worker could not process queue (%s)', type(exc).__name__)
                if args.once:
                    break
                stop.wait(POLL_SECONDS)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
