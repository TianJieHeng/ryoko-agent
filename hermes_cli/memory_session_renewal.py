"""Continue old-policy memory threads in a fresh execution without changing remote identity."""
import json
import time


def needs_renewal(db, context, root):
    from agent.agent_configuration import base_configuration, _read_session
    from agent.agent_identity import resolve_agent_context, IdentityPolicyError
    with db._runtime_read() as conn:
        frozen, _, _ = _read_session(conn, base_configuration(), root)
    try:
        resolve_agent_context(frozen, session_id=root, profile_home=context.profile_home,
                              stored_binding=db.get_session_model_config_value(root, 'agent_identity'))
        return False
    except IdentityPolicyError:
        return True


def renew(db, context, root, tip, holder):
    from agent.agent_configuration import base_configuration
    from agent.conversation_identity import selected_conversation_scope, validate_conversation_enrollment
    from hermes_state_memory_sessions import verify_local_owner, owner_record
    from hermes_state_ids import new_session_id
    from hermes_cli.memory_sessions import ConversationError
    verify_local_owner(db, context, root)
    for sid in db.get_compression_chain(root):
        verify_local_owner(db, context, sid)
    # Keep saved human/assistant text; never carry tools, approvals, provider
    # state or old system instructions into the new execution policy.
    history = [{'role': row['role'], 'content': row['content']}
               for row in db.get_messages_as_conversation(tip, include_ancestors=True)
               if row.get('role') in {'user', 'assistant'} and row.get('content')]
    if len(json.dumps(history).encode()) > 2_000_000:
        raise ConversationError('The saved conversation is too large for policy renewal; nothing was replaced.')
    new_id = new_session_id(hex_len=12)
    selected, enrollment = selected_conversation_scope(context, base_configuration(), db,
                                                       context.identity.agent_id, session_id=new_id)
    def write(conn):
        lease = conn.execute('SELECT holder,expires_at FROM session_turn_leases WHERE conversation_id=?', (root,)).fetchone()
        if not lease or lease['holder'] != holder or lease['expires_at'] <= time.time():
            raise ConversationError('This conversation became busy; try reopening it when the response finishes.')
        remote = conn.execute('SELECT * FROM memory_session_bindings WHERE local_root=?', (root,)).fetchone()
        if remote is None or json.loads(remote['owner_json']) != owner_record(context):
            raise PermissionError('The memory conversation owner changed')
        validate_conversation_enrollment(conn, selected, enrollment)
        config = {'agent_identity': selected.identity.to_record(),
                  'memory_history_import': {'format': 'harness_windows_v1', 'complete': False},
                  'memory_previous_execution': root}
        conn.execute('INSERT INTO sessions(id,source,created_source,started_at,model_config,profile_name,display_name) '
                     'VALUES(?,?,?,?,?,?,?)', (new_id, 'cli', 'cli', time.time(), json.dumps(config),
                                               db._own_profile_name(), remote['display_title']))
        conn.execute('INSERT INTO agent_configuration_sessions(session_id,snapshot_id,selected_agent_id) VALUES(?,?,?)',
                     (new_id, enrollment['snapshot_id'], enrollment['selected_agent_id']))
        count, tools = db._insert_message_rows(conn, new_id, history)
        db._bump_session_counters(conn, new_id, count, tools, unit=False)
        # One binding, one remote hub. The original execution remains an archive.
        conn.execute('UPDATE memory_session_bindings SET local_root=? WHERE local_root=?', (new_id, root))
        conn.execute('UPDATE sessions SET archived=1 WHERE id=?', (root,))
        conn.execute('UPDATE runtime_conversations SET revision=revision+1,updated_at=? WHERE conversation_id=?', (time.time(), root))
        return new_id
    return db._execute_write(write)
