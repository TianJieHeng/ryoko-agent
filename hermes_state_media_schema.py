"""BE13 local bounded-service receipts and authenticated channel mappings."""

MEDIA_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS bounded_service_pipelines (
    pipeline_id TEXT PRIMARY KEY, owner_json TEXT NOT NULL, session_id TEXT NOT NULL,
    project_id TEXT NOT NULL, request_id TEXT NOT NULL, manifest_json TEXT NOT NULL,
    manifest_sha256 TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS bounded_service_stages (
    pipeline_id TEXT NOT NULL, stage INTEGER NOT NULL, receipt_json TEXT NOT NULL,
    output_bytes BLOB NOT NULL, created_at REAL NOT NULL,
    PRIMARY KEY(pipeline_id,stage),
    FOREIGN KEY(pipeline_id) REFERENCES bounded_service_pipelines(pipeline_id)
);
CREATE TABLE IF NOT EXISTS runtime_channel_bindings (
    binding_id TEXT PRIMARY KEY, owner_json TEXT NOT NULL, session_id TEXT NOT NULL,
    project_id TEXT NOT NULL, mission_id TEXT NOT NULL, channel TEXT NOT NULL,
    created_at REAL NOT NULL
);
"""
