"""Additive bounded capture projections and reversible review receipts on SessionDB."""
CAPTURE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS artifact_capture_indexes (
    capture_id TEXT PRIMARY KEY,
    record_json TEXT NOT NULL,
    text_content TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS artifact_capture_consolidations (
    capture_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    consolidated_into TEXT,
    created_at REAL NOT NULL,
    PRIMARY KEY(capture_id,revision)
);
CREATE TABLE IF NOT EXISTS artifact_capture_batches (
    batch_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    actor_json TEXT NOT NULL,
    request_digest TEXT NOT NULL,
    preview_digest TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at REAL NOT NULL
);
"""
