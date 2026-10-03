"""Additive BE12 inbox candidates and accepted obligations on SessionDB."""

COMMITMENT_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS commitment_inbox_previews (
    preview_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, owner_json TEXT NOT NULL,
    record_json TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS commitment_candidates (
    candidate_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, owner_json TEXT NOT NULL,
    record_json TEXT NOT NULL, revision INTEGER NOT NULL,
    commitment_id TEXT, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS accepted_commitments (
    commitment_id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL UNIQUE,
    project_id TEXT NOT NULL, owner_json TEXT NOT NULL, record_json TEXT NOT NULL,
    acceptance_json TEXT NOT NULL, state TEXT NOT NULL, revision INTEGER NOT NULL,
    created_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_commitments_scope_state
    ON accepted_commitments(project_id,owner_json,state,updated_at);
CREATE TABLE IF NOT EXISTS commitment_history (
    commitment_id TEXT NOT NULL, revision INTEGER NOT NULL,
    record_json TEXT NOT NULL, created_at REAL NOT NULL,
    PRIMARY KEY(commitment_id,revision)
);
CREATE TABLE IF NOT EXISTS commitment_correspondence (
    correspondence_id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
    owner_json TEXT NOT NULL, record_json TEXT NOT NULL, state TEXT NOT NULL,
    created_at REAL NOT NULL
);
"""
