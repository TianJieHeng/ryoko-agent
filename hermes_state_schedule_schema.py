"""BE12 additive tables, installed by the canonical SessionDB schema owner."""
SCHEDULE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS durable_schedules (
 schedule_key TEXT PRIMARY KEY, schedule_id TEXT NOT NULL, project_id TEXT NOT NULL,
 owner_json TEXT NOT NULL, owner_binding_json TEXT NOT NULL,
 version INTEGER NOT NULL, revision INTEGER NOT NULL DEFAULT 1, state TEXT NOT NULL,
 next_due REAL, remaining_checks INTEGER NOT NULL, health TEXT NOT NULL DEFAULT 'unknown',
 last_success REAL, last_error TEXT, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS durable_schedule_versions (
 schedule_key TEXT NOT NULL, version INTEGER NOT NULL, definition_json TEXT NOT NULL,
 sha256 TEXT NOT NULL, session_id TEXT NOT NULL, created_at REAL NOT NULL,
 PRIMARY KEY(schedule_key,version)
);
CREATE TABLE IF NOT EXISTS durable_occurrences (
 occurrence_id TEXT PRIMARY KEY, schedule_key TEXT NOT NULL, version INTEGER NOT NULL,
 due_at REAL NOT NULL, session_id TEXT NOT NULL, command_id TEXT NOT NULL, run_id TEXT,
 state TEXT NOT NULL, generation INTEGER, holder TEXT, accepted_at REAL NOT NULL,
 deadline_at REAL NOT NULL, result_json TEXT, delivery_state TEXT NOT NULL DEFAULT 'not_requested',
 UNIQUE(schedule_key,version,due_at)
);
CREATE TABLE IF NOT EXISTS durable_schedule_imports (
 schedule_key TEXT PRIMARY KEY, declaration_json TEXT NOT NULL, command_id TEXT NOT NULL,
 run_id TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS durable_schedule_due ON durable_schedules(state,next_due);
CREATE INDEX IF NOT EXISTS durable_occurrence_owner ON durable_occurrences(schedule_key,state);
CREATE TABLE IF NOT EXISTS durable_monitor_state (
 schedule_key TEXT NOT NULL, version INTEGER NOT NULL, baseline_json TEXT NOT NULL,
 source_refs_json TEXT NOT NULL, last_success REAL NOT NULL,
 PRIMARY KEY(schedule_key,version)
);
CREATE TABLE IF NOT EXISTS durable_monitor_observations (
 occurrence_id TEXT PRIMARY KEY, schedule_key TEXT NOT NULL, version INTEGER NOT NULL,
 observed_at REAL NOT NULL, state TEXT NOT NULL, record_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS durable_monitor_intents (
 intent_id TEXT PRIMARY KEY, occurrence_id TEXT NOT NULL, kind TEXT NOT NULL,
 state TEXT NOT NULL, record_json TEXT NOT NULL, created_at REAL NOT NULL,
 UNIQUE(occurrence_id,kind)
);
CREATE TABLE IF NOT EXISTS durable_condition_grants (
 grant_id TEXT PRIMARY KEY, schedule_key TEXT NOT NULL, version INTEGER NOT NULL,
 target_digest TEXT NOT NULL, expires_at REAL NOT NULL, max_age_seconds INTEGER NOT NULL,
 remaining INTEGER NOT NULL, state TEXT NOT NULL, created_at REAL NOT NULL
);
"""

# Command schedules share the registry but keep occurrence slots separate from
# the finite-adapter table's unique due_at constraint (two manual runs can share a millisecond).
SCHEDULE_SCHEMA_SQL += """
CREATE TABLE IF NOT EXISTS durable_command_schedule_bindings (
 schedule_key TEXT NOT NULL, version INTEGER NOT NULL, session_id TEXT NOT NULL,
 budget_policy_json TEXT, PRIMARY KEY(schedule_key,version)
);
CREATE TABLE IF NOT EXISTS durable_command_occurrences (
 occurrence_id TEXT PRIMARY KEY, schedule_key TEXT NOT NULL, version INTEGER NOT NULL,
 slot TEXT NOT NULL, due_at REAL NOT NULL, session_id TEXT NOT NULL,
 command_id TEXT, state TEXT NOT NULL, created_at REAL NOT NULL, detail_json TEXT NOT NULL,
 UNIQUE(schedule_key,version,slot)
);
CREATE INDEX IF NOT EXISTS durable_command_occurrence_owner ON durable_command_occurrences(schedule_key,state);
CREATE TABLE IF NOT EXISTS durable_schedule_cutovers (
 schedule_key TEXT PRIMARY KEY, source_id TEXT NOT NULL, retirement_receipt TEXT NOT NULL,
 command_id TEXT NOT NULL, created_at REAL NOT NULL
);
"""
SCHEDULE_SCHEMA_SQL += """
CREATE TABLE IF NOT EXISTS durable_schedule_manual_requests (
 session_id TEXT NOT NULL, request_id TEXT NOT NULL, intent_sha256 TEXT NOT NULL,
 occurrence_id TEXT NOT NULL, PRIMARY KEY(session_id,request_id)
);
"""
