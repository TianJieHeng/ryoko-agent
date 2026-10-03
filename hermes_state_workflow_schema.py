"""Additive BE11 schema; no workflow data lives outside SessionDB."""
WORKFLOW_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS workflow_versions (
    workflow_key TEXT NOT NULL, version INTEGER NOT NULL, project_id TEXT NOT NULL,
    owner_json TEXT NOT NULL, definition_json TEXT NOT NULL, sha256 TEXT NOT NULL,
    state TEXT NOT NULL, revision INTEGER NOT NULL, evaluation_ref TEXT,
    created_at REAL NOT NULL, PRIMARY KEY(workflow_key,version)
);
CREATE TABLE IF NOT EXISTS workflow_heads (
    workflow_key TEXT PRIMARY KEY, version INTEGER NOT NULL, revision INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS workflow_templates (
    template_key TEXT NOT NULL, version INTEGER NOT NULL, project_id TEXT NOT NULL,
    owner_json TEXT NOT NULL, definition_json TEXT NOT NULL, sha256 TEXT NOT NULL,
    created_at REAL NOT NULL, PRIMARY KEY(template_key,version)
);
CREATE TABLE IF NOT EXISTS workflow_evaluations (
    evaluation_id TEXT PRIMARY KEY, workflow_key TEXT NOT NULL, version INTEGER NOT NULL,
    record_json TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS workflow_evidence (
    evidence_id TEXT PRIMARY KEY, workflow_key TEXT NOT NULL, version INTEGER NOT NULL,
    record_json TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS workflow_runs (
    workflow_run_id TEXT PRIMARY KEY, workflow_key TEXT NOT NULL, version INTEGER NOT NULL,
    session_id TEXT NOT NULL, run_id TEXT NOT NULL, owner_json TEXT NOT NULL,
    record_json TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_workflow_runs_version ON workflow_runs(workflow_key,version);
CREATE TABLE IF NOT EXISTS workflow_decisions (
    approval_id TEXT PRIMARY KEY, workflow_key TEXT NOT NULL, version INTEGER NOT NULL,
    record_json TEXT NOT NULL, created_at REAL NOT NULL
);
"""
