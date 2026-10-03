"""Additive FE11 review metadata on SessionDB; not a task or memory authority."""

OPPORTUNITY_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS opportunity_candidates (
    candidate_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, owner_json TEXT NOT NULL,
    kind TEXT NOT NULL, source_id TEXT NOT NULL, evidence_digest TEXT NOT NULL,
    record_json TEXT NOT NULL, revision INTEGER NOT NULL, disposition TEXT NOT NULL,
    created_at REAL NOT NULL, updated_at REAL NOT NULL,
    UNIQUE(owner_json,project_id,kind,source_id)
);
CREATE INDEX IF NOT EXISTS idx_opportunity_scope
    ON opportunity_candidates(owner_json,project_id,disposition,updated_at);
CREATE TABLE IF NOT EXISTS opportunity_history (
    candidate_id TEXT NOT NULL, revision INTEGER NOT NULL, project_id TEXT NOT NULL,
    owner_json TEXT NOT NULL, event TEXT NOT NULL, record_json TEXT NOT NULL,
    created_at REAL NOT NULL, PRIMARY KEY(candidate_id,revision)
);
CREATE TABLE IF NOT EXISTS opportunity_requests (
    request_key TEXT PRIMARY KEY, owner_json TEXT NOT NULL, operation TEXT NOT NULL,
    request_digest TEXT NOT NULL, response_json TEXT NOT NULL, created_at REAL NOT NULL
);
"""
