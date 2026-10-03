"""Additive monitor policy/notice projection; delivery_obligations owns attempts."""
MONITOR_NOTIFICATION_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS durable_monitor_policies (
 schedule_key TEXT NOT NULL, version INTEGER NOT NULL, policy_revision INTEGER NOT NULL,
 policy_json TEXT NOT NULL, owner_binding_json TEXT NOT NULL, destination_session TEXT NOT NULL,
 command_id TEXT NOT NULL, remaining INTEGER NOT NULL, snoozed_until REAL, created_at REAL NOT NULL,
 PRIMARY KEY(schedule_key,version)
);
CREATE TABLE IF NOT EXISTS durable_monitor_notices (
 intent_id TEXT PRIMARY KEY, schedule_key TEXT NOT NULL, version INTEGER NOT NULL,
 policy_revision INTEGER, fingerprint TEXT NOT NULL, payload_json TEXT NOT NULL,
 state TEXT NOT NULL, delivery_id TEXT, dismissed_at REAL, created_at REAL NOT NULL,
 UNIQUE(schedule_key,version,fingerprint)
);
CREATE INDEX IF NOT EXISTS monitor_notice_pending ON durable_monitor_notices(schedule_key,version,state,created_at);
CREATE TABLE IF NOT EXISTS durable_monitor_batches (
 delivery_id TEXT PRIMARY KEY, schedule_key TEXT NOT NULL, version INTEGER NOT NULL,
 policy_revision INTEGER NOT NULL, destination_session TEXT NOT NULL,
 payload_json TEXT NOT NULL, sha256 TEXT NOT NULL, created_at REAL NOT NULL
);
"""
