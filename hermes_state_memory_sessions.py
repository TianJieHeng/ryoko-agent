"""Durable remote conversation bindings and atomic available-history imports."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import time

_SCHEMA = '''CREATE TABLE IF NOT EXISTS memory_session_bindings (
    remote_key TEXT PRIMARY KEY, local_root TEXT NOT NULL UNIQUE REFERENCES sessions(id) ON DELETE CASCADE,
    owner_json TEXT NOT NULL, server TEXT NOT NULL, endpoint TEXT NOT NULL,
    source TEXT NOT NULL, remote_id TEXT NOT NULL, hub TEXT NOT NULL, display_title TEXT NOT NULL,
    push_binding TEXT NOT NULL, imported_digest TEXT, imported_at REAL NOT NULL)'''


def owner_record(context):
    identity = context.identity
    return [identity.principal_id, identity.profile_id, identity.agent_id, context.profile_home]


def remote_key(context, server, conversation):
    endpoint = context.policy.mcp_policies[server]['endpoint']
    return hashlib.sha256(json.dumps([owner_record(context), server, endpoint, *conversation.key]).encode()).hexdigest()


def _exists(conn):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_session_bindings'").fetchone()


def bound_remote(db, session_id, context):
    if Path(db.db_path).resolve().parent != Path(context.profile_home).resolve():
        raise PermissionError('Conversation store belongs to another profile')
    with db._runtime_read() as conn:
        if not _exists(conn):
            return None
        root = db._session_turn_lease_key_on_conn(conn, session_id)
        row = conn.execute('SELECT * FROM memory_session_bindings WHERE local_root=?', (root,)).fetchone()
        if row is None:
            return None
        row = dict(row)
        if json.loads(row['owner_json']) != owner_record(context):
            raise PermissionError('Remote conversation owner changed')
        grant = context.policy.mcp_policies.get(row['server'])
        if not grant or grant['endpoint'] != row['endpoint']:
            raise PermissionError('Remote conversation server changed')
        return row


def find_bound_local(db, context, server, conversation):
    key = remote_key(context, server, conversation)
    with db._runtime_read() as conn:
        if not _exists(conn):
            return None
        row = conn.execute('SELECT local_root,hub FROM memory_session_bindings WHERE remote_key=?', (key,)).fetchone()
        if row is None:
            return None
        if row['hub'] and conversation.hub and row['hub'] != conversation.hub:
            raise ValueError('The remote conversation was replaced; the existing binding was retained')
        return row['local_root']


def verified_local(db, context, server, conversation, push_config):
    bound = find_bound_local(db, context, server, conversation)
    if bound:
        return bound
    # A same-shaped ID is insufficient. Require a confirmed outbox receipt for this
    # profile, destination and hub as proof that this local session originated the thread.
    row = db.get_session(conversation.session_id)
    if row is None or not conversation.hub or conversation.source != push_config['source']:
        return None
    path = Path(context.profile_home) / 'conversation-outbox/outbox.sqlite3'
    if not path.is_file() or path.is_symlink():
        return None
    from agent.conversation_outbox import binding
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as conn:
        receipts = conn.execute("SELECT receipt FROM turns WHERE session_id=? AND binding=? AND state='sent'",
                                (conversation.session_id, binding(push_config))).fetchall()
    for (receipt,) in receipts:
        value = json.loads(receipt or '{}')
        if value.get('hub') == conversation.hub and value.get('session_id') == conversation.session_id:
            return db._session_turn_lease_key(conversation.session_id)
    return None


def verify_local_owner(db, context, local_id):
    binding = db.get_session_model_config_value(local_id, 'agent_identity')
    expected = context.identity.to_record()
    fields = ('principal_id', 'profile_id', 'agent_id', 'profile_home_digest')
    if not isinstance(binding, dict) or any(binding.get(k) != expected[k] for k in fields):
        raise PermissionError('The local session does not belong to the selected memory owner')


def prepare_binding(db, context, server, conversation, push_config, *, local_id=None, capsule=None):
    """Publish messages and binding in one transaction; repeated import returns the same root."""
    from agent.conversation_outbox import binding
    from agent.agent_configuration import base_configuration
    from agent.conversation_identity import selected_conversation_scope, validate_conversation_enrollment
    from hermes_state_ids import new_session_id
    if Path(db.db_path).resolve() != Path(context.profile_home) / 'state.db':
        raise PermissionError('Imports require the profile-owned session store')
    key = remote_key(context, server, conversation)
    existing = local_id is not None
    if existing:
        verify_local_owner(db, context, local_id)
        target_context, enrollment = None, None
    else:
        if not capsule:
            raise ValueError('Remote history is required before import')
        local_id = new_session_id(hex_len=12)
        target_context, enrollment = selected_conversation_scope(context, base_configuration(), db,
                                                                 context.identity.agent_id, session_id=local_id)
    def write(conn):
        conn.execute(_SCHEMA)
        prior = conn.execute('SELECT local_root,hub FROM memory_session_bindings WHERE remote_key=?', (key,)).fetchone()
        if prior:
            if prior['hub'] and conversation.hub and prior['hub'] != conversation.hub:
                raise ValueError('Remote conversation identity changed')
            return prior['local_root']
        if existing:
            row = conn.execute('SELECT model_config FROM sessions WHERE id=?', (local_id,)).fetchone()
            if row is None:
                raise ValueError('The local session was deleted during preparation')
            recorded = json.loads(row[0] or '{}').get('agent_identity', {})
            if any(recorded.get(k) != context.identity.to_record()[k] for k in
                   ('principal_id', 'profile_id', 'agent_id', 'profile_home_digest')):
                raise PermissionError('Local session owner changed during preparation')
        else:
            validate_conversation_enrollment(conn, target_context, enrollment)
            model_config = {'agent_identity': target_context.identity.to_record(),
                            'memory_history_import': {'format': 'harness_windows_v1', 'complete': False}}
            conn.execute('INSERT INTO sessions(id,source,created_source,started_at,model_config,profile_name,display_name) '
                         'VALUES(?,?,?,?,?,?,?)', (local_id, 'cli', 'cli', time.time(), json.dumps(model_config),
                                                  db._own_profile_name(), conversation.title))
            conn.execute('INSERT INTO agent_configuration_sessions(session_id,snapshot_id,selected_agent_id) VALUES(?,?,?)',
                         (local_id, enrollment['snapshot_id'], enrollment['selected_agent_id']))
            # One explicit archival capsule: unescaped role-looking text inside remote windows
            # never becomes a system message, tool result, or forged executable history.
            inserted, tools = db._insert_message_rows(conn, local_id, [{'role': 'assistant', 'content': capsule}])
            db._bump_session_counters(conn, local_id, inserted, tools, unit=False)
        conn.execute('INSERT INTO memory_session_bindings VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                     (key, local_id, json.dumps(owner_record(context)), server,
                      context.policy.mcp_policies[server]['endpoint'], conversation.source, conversation.session_id,
                      conversation.hub, conversation.title, binding(push_config),
                      hashlib.sha256(capsule.encode()).hexdigest() if capsule else None, time.time()))
        return local_id
    return db._execute_write(write)
