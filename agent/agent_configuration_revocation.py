"""Monotonic managed-authority floors, independent of frozen prompt snapshots."""
from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3

from agent.agent_identity import IdentityBinding

MAX_AUTHORITY_ANCESTORS = 64


def narrows_authority(previous, current):
    before, after = previous["config"], current["config"]
    return (current["archived"] or
            any(before[name] and not after[name] for name in ("memory_allowed", "research_allowed")) or
            bool(set(before["project_grants"]) - set(after["project_grants"])))


def revoke_before(conn, owner_key, agent_id, revision):
    conn.execute("INSERT INTO agent_configuration_revocations VALUES(?,?,?) ON CONFLICT(owner_key,agent_id) "
                 "DO UPDATE SET revoked_before_revision=MAX(revoked_before_revision,excluded.revoked_before_revision)",
                 (owner_key, agent_id, revision))


def _authority_root(conn, physical_id, binding, deny):
    """Compression keeps its original enrollment; unrelated forks cannot borrow it."""
    if physical_id == binding.session_id:
        return physical_id
    from hermes_state_compression import _CHAIN_STEP_SQL, _CHAIN_CAP
    cursor, visited = binding.session_id, set()
    for _ in range(_CHAIN_CAP):
        if cursor in visited:
            deny("Compressed authority ancestry contains a cycle")
        visited.add(cursor)
        row = conn.execute("SELECT model_config FROM sessions WHERE id=?", (cursor,)).fetchone()
        if row is None or json.loads(row[0] or "{}").get("agent_identity") != binding.to_record():
            deny("Compressed authority has a mismatched identity")
        if cursor == physical_id:
            return binding.session_id
        child = conn.execute(_CHAIN_STEP_SQL, (cursor,)).fetchone()
        if child is None:
            deny("Physical parent is not a proved compression continuation")
        cursor = child[0]
    deny("Compressed authority ancestry exceeds its bound")


def assert_managed_authority_current(context, base):
    """Reject revoked effects without replacing their cached policy or instructions."""
    from agent.agent_configuration import _owner, _snapshot_records
    from tools.capability_broker import CapabilityDenied

    path = Path(context.profile_home) / "state.db"
    if not path.exists():
        return
    owner_key, _ = _owner(base)

    def deny(message):
        raise CapabilityDenied("agent_configuration_revoked", message)

    with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='agent_configuration_revocations'").fetchone() is None:
            return
        floors = {row["agent_id"]: row["revoked_before_revision"] for row in conn.execute(
            "SELECT * FROM agent_configuration_revocations WHERE owner_key=?", (owner_key,))}
        if not floors:
            return
        binding, seen = context.identity, set()
        session_id = binding.session_id
        for _ in range(MAX_AUTHORITY_ANCESTORS):
            session_id = _authority_root(conn, session_id, binding, deny)
            if session_id in seen:
                deny("Agent authority ancestry contains a cycle")
            seen.add(session_id)
            enrollment = conn.execute("SELECT * FROM agent_configuration_sessions WHERE session_id=?", (session_id,)).fetchone()
            borrowed = None
            if enrollment is None and len(seen) == 1 and context.configuration_session_id is not None:
                source_id = context.configuration_session_id
                source = conn.execute("SELECT model_config FROM sessions WHERE id=?", (source_id,)).fetchone()
                raw = json.loads(source[0] or "{}") if source else {}
                if not raw.get("agent_identity"):
                    deny("Transient authority has no persisted configuration source")
                source_binding = IdentityBinding.from_record(raw["agent_identity"])
                if any(getattr(source_binding, key) != getattr(context.identity, key) for key in
                       ("principal_id", "profile_id", "profile_home_digest", "config_digest")):
                    deny("Transient configuration source belongs to another authority")
                source_id = _authority_root(conn, source_id, source_binding, deny)
                enrollment = conn.execute("SELECT * FROM agent_configuration_sessions WHERE session_id=?", (source_id,)).fetchone()
                borrowed = source_id, source_binding
            records = _snapshot_records(conn, base, enrollment["snapshot_id"] if enrollment else None)
            candidates = {binding.agent_id}
            # A delegated stable specialist is checked under its own identity,
            # as well as the root ceiling inherited through its enrollment.
            if enrollment is not None:
                candidates.add(enrollment["selected_agent_id"])
            if binding.parent_agent_id is not None:
                candidates.add(binding.parent_agent_id)
            if borrowed is not None:
                candidates.add(borrowed[1].agent_id)
            for agent_id in candidates:
                record = records.get(agent_id)
                if record is not None and record["revision"] < floors.get(agent_id, 0):
                    deny("Agent permissions were narrowed or archived; start a newly authorized session")
            if borrowed is not None:
                session_id, binding = borrowed
                continue
            row = conn.execute("SELECT parent_session_id FROM sessions WHERE id=?", (session_id,)).fetchone()
            parent_id = row[0] if row else None
            if not parent_id:
                return
            parent = conn.execute("SELECT model_config FROM sessions WHERE id=?", (parent_id,)).fetchone()
            raw = json.loads(parent[0] or "{}") if parent else {}
            if not raw.get("agent_identity"):
                deny("Delegated authority has no persisted parent identity")
            parent_binding = IdentityBinding.from_record(raw["agent_identity"])
            if (any(getattr(parent_binding, field) != getattr(context.identity, field)
                    for field in ("principal_id", "profile_id", "profile_home_digest")) or
                    (binding.parent_agent_id is not None and parent_binding.agent_id != binding.parent_agent_id)):
                deny("Delegated authority belongs to another parent or profile")
            session_id, binding = parent_id, parent_binding
        deny("Agent authority ancestry exceeds its bound")


def owned_configuration_management(context, db, ui_session_id):
    """A settings repair exception never applies to model dispatch or approvals."""
    from agent.runtime_commands import _RUN
    from agent.runtime_context import current_agent_context
    from tui_gateway import server

    allowed = {"runtime.agent." + operation for operation in ("list", "get", "create", "update", "archive")}
    if (not ui_session_id or _RUN.get() is not None or server._current_rpc_method.get() not in allowed
            or current_agent_context() != context):
        return False
    transport, session = server._current_session_steer_authority(ui_session_id)
    agent = session.get("agent") if session else None
    return bool(transport is not None and getattr(agent, "runtime_context", None) == context
                and getattr(agent, "_session_db", None) is db
                and db.get_session_model_config_value(agent.session_id, "agent_identity") == context.identity.to_record())
