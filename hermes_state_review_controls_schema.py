"""Additive owner pause, exact approval review and immutable mission history."""
REVIEW_CONTROLS_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS runtime_owner_controls (
 principal_id TEXT NOT NULL, profile_id TEXT NOT NULL,
 revision INTEGER NOT NULL, paused INTEGER NOT NULL,
 updated_at REAL NOT NULL,
 PRIMARY KEY(principal_id,profile_id)
);
CREATE TABLE IF NOT EXISTS runtime_owner_control_operations (
 principal_id TEXT NOT NULL, profile_id TEXT NOT NULL, operation_id TEXT NOT NULL,
 digest TEXT NOT NULL, receipt_json TEXT NOT NULL,
 PRIMARY KEY(principal_id,profile_id,operation_id)
);
CREATE TABLE IF NOT EXISTS runtime_approval_decisions (
 approval_id TEXT PRIMARY KEY, choice TEXT NOT NULL, resolved_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS runtime_approval_reviews (
 approval_id TEXT PRIMARY KEY, review_json TEXT NOT NULL, review_digest TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runtime_mission_history (
 mission_id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
 principal_id TEXT NOT NULL, profile_id TEXT NOT NULL, agent_id TEXT NOT NULL,
 project_id TEXT, revision INTEGER NOT NULL, record_json TEXT NOT NULL,
 archived_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runtime_mission_history_session ON runtime_mission_history(session_id,archived_at);
"""
