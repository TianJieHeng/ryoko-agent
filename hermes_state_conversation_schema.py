"""Canonical trusted-ingress conversation ownership and durable mutation receipts."""
CONVERSATION_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS runtime_conversations (
    conversation_id TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
    schema_version INTEGER NOT NULL DEFAULT 1,
    owner_key TEXT NOT NULL,
    binding_json TEXT NOT NULL,
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
"""
