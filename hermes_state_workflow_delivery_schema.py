"""Reviewed specialist installations are immutable records with CAS heads."""

WORKFLOW_DELIVERY_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS workflow_deliveries (
    delivery_id TEXT PRIMARY KEY, delivery_key TEXT NOT NULL,
    principal_id TEXT NOT NULL, profile_id TEXT NOT NULL,
    specialist_id TEXT NOT NULL, project_id TEXT NOT NULL,
    workflow_key TEXT NOT NULL, version INTEGER NOT NULL,
    delivery_revision INTEGER NOT NULL, record_json TEXT NOT NULL,
    created_at REAL NOT NULL, UNIQUE(delivery_key,delivery_revision)
);
CREATE TABLE IF NOT EXISTS workflow_delivery_heads (
    delivery_key TEXT PRIMARY KEY, delivery_id TEXT NOT NULL,
    revision INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_workflow_deliveries_specialist
    ON workflow_deliveries(principal_id,profile_id,specialist_id,project_id);
"""
