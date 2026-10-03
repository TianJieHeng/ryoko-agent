"""Canonical trusted-ingress conversation ownership and durable mutation receipts."""
CONVERSATION_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS runtime_conversations (
    conversation_id TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
    schema_version INTEGER NOT NULL DEFAULT 1,
    owner_key TEXT NOT NULL,
    binding_json TEXT NOT NULL,
    display_title TEXT,
    revision INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runtime_conversations_owner
    ON runtime_conversations(owner_key, created_at, conversation_id);
CREATE TABLE IF NOT EXISTS runtime_conversation_operations (
    owner_key TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_digest TEXT NOT NULL,
    operation TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY(owner_key, idempotency_key)
);
CREATE TABLE IF NOT EXISTS runtime_command_messages (
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    command_id TEXT NOT NULL,
    message_uid TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('user','assistant','tool')),
    kind TEXT NOT NULL CHECK(kind IN ('input','output')),
    first_row_id INTEGER NOT NULL,
    PRIMARY KEY(session_id, message_uid)
);
CREATE INDEX IF NOT EXISTS idx_runtime_command_messages_command
    ON runtime_command_messages(session_id, command_id, first_row_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_runtime_command_messages_input
    ON runtime_command_messages(session_id, command_id) WHERE kind='input';
"""


def backfill_conversation_display_titles(cursor):
    """Populate only pre-column canonical rows, leaving legacy aliases intact."""
    columns = {row[1] for row in cursor.execute('PRAGMA table_info(runtime_conversations)')}
    if 'display_title' not in columns:
        return
    # Avoid taking the writer lock again on every already-migrated open.
    if cursor.execute('SELECT 1 FROM runtime_conversations WHERE display_title IS NULL LIMIT 1').fetchone() is None:
        return
    from hermes_state_compression import _CHAIN_STEP_SQL
    rows = cursor.execute('SELECT c.conversation_id,s.title FROM runtime_conversations c '
                          'JOIN sessions s ON s.id=c.conversation_id WHERE c.display_title IS NULL').fetchall()
    for conversation_id, title in rows:
        current, seen = conversation_id, set()
        # Compression may have moved the legacy unique alias from root to tip.
        while title is None and current not in seen and len(seen) < 1000:
            seen.add(current)
            child = cursor.execute(_CHAIN_STEP_SQL, (current,)).fetchone()
            if child is None:
                break
            current = child['id']
            title = cursor.execute('SELECT title FROM sessions WHERE id=?', (current,)).fetchone()[0]
        cursor.execute('UPDATE runtime_conversations SET display_title=? WHERE conversation_id=? AND display_title IS NULL',
                       (title or '', conversation_id))
