"""Immutable, owner-scoped UI configuration on the existing SessionDB."""
AGENT_CONFIGURATION_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS agent_configuration_snapshots (
    snapshot_id TEXT PRIMARY KEY, owner_key TEXT NOT NULL, base_sha256 TEXT NOT NULL,
    revision INTEGER NOT NULL, records_json TEXT NOT NULL, created_at REAL NOT NULL,
    UNIQUE(owner_key, revision)
);
CREATE TABLE IF NOT EXISTS agent_configuration_heads (
    owner_key TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL, revision INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_configuration_sessions (
    session_id TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE, snapshot_id TEXT,
    selected_agent_id TEXT NOT NULL, startup_json TEXT
);
CREATE TABLE IF NOT EXISTS agent_configuration_revocations (
    owner_key TEXT NOT NULL, agent_id TEXT NOT NULL,
    revoked_before_revision INTEGER NOT NULL,
    PRIMARY KEY(owner_key,agent_id)
);
"""
