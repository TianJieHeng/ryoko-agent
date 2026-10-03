"""Output reference receipts and bounded user context controls, owned by SessionDB."""
OUTPUT_CONTEXT_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS runtime_output_contexts (
    session_id TEXT NOT NULL, run_id TEXT NOT NULL, actor_json TEXT NOT NULL,
    record_json TEXT NOT NULL, created_at REAL NOT NULL,
    PRIMARY KEY(session_id,run_id)
);
CREATE TABLE IF NOT EXISTS runtime_context_controls (
    control_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, actor_json TEXT NOT NULL,
    record_json TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_output_context_owner ON runtime_output_contexts(session_id,created_at);
CREATE INDEX IF NOT EXISTS idx_context_control_owner ON runtime_context_controls(session_id,created_at);
"""
