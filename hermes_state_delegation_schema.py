"""BE13 bounded run-tree/handoff metadata beside the existing async ledger."""
DELEGATION_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS delegation_roots (
 root_run_id TEXT PRIMARY KEY,
 principal_id TEXT NOT NULL,
 profile_id TEXT NOT NULL,
 limits_json TEXT NOT NULL,
 created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS delegation_handoffs (
 child_id TEXT PRIMARY KEY,
 root_run_id TEXT NOT NULL REFERENCES delegation_roots(root_run_id),
 parent_run_id TEXT NOT NULL,
 parent_session_id TEXT NOT NULL,
 child_session_id TEXT NOT NULL UNIQUE,
 parent_agent_id TEXT NOT NULL,
 child_agent_id TEXT NOT NULL,
 handoff_json TEXT NOT NULL,
 handoff_sha256 TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('accepted','running','completed','failed','cancelled','orphaned','partial','unknown')),
 owner_holder TEXT NOT NULL,
 owner_generation INTEGER NOT NULL,
 async_delegation_id TEXT,
 completion_json TEXT,
 delivery_state TEXT NOT NULL DEFAULT 'pending' CHECK(delivery_state IN ('pending','claimed','delivered')),
 delivery_claim TEXT,
 delivery_generation INTEGER,
 created_at REAL NOT NULL,
 updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS delegation_handoffs_root ON delegation_handoffs(root_run_id,state);
CREATE INDEX IF NOT EXISTS delegation_handoffs_async ON delegation_handoffs(async_delegation_id);
"""
