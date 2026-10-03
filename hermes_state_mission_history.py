"""Sequential immutable mission history on one canonical conversation root.

The active slot and its lease remain unchanged. Replacing it is explicit CAS,
requires terminal intent and no accepted/claimed work, and preserves all evidence,
effect, approval, budget and delivery records under their original identities.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
import json
import time

from agent.mission_contract import bounded_json, identifier, require
from hermes_state_effects import _actor




@contextmanager
def replacement_guard(db, session_id, actor, access, project_id):
    with db._runtime_read() as conn:
        sid = db._mission_owner_on_conn(conn, session_id, actor)
        previous = conn.execute("SELECT project_id FROM runtime_missions WHERE session_id=?", (sid,)).fetchone()
    projects = {project_id}
    if previous:
        projects.add(previous[0])
    with ExitStack() as stack:
        for project in sorted(p for p in projects if p is not None):
            db._artifact_access(actor, project, access, "write")
            stack.enter_context(access.guard(project, actor, "write"))
        yield


def archive_previous_on_conn(db, conn, sid, actor, previous_mission_id, previous_revision, generation, access):
    row, record = db._mission_row_on_conn(conn, sid, actor, expected_revision=previous_revision)
    require(record["mission_id"] == previous_mission_id, "Previous mission changed", "mission_identity_conflict")
    require(record["state"] in {"completed", "cancelled", "failed"}, "Finish or cancel the current mission first", "mission_not_terminal")
    require(conn.execute("SELECT 1 FROM runtime_commands WHERE session_id=? AND status IN ('accepted','claimed') LIMIT 1",
                         (sid,)).fetchone() is None, "Accepted work still owns this mission", "mission_owner_busy")
    require(conn.execute("SELECT COUNT(*) FROM runtime_mission_history").fetchone()[0] < 65536,
            "Retained mission history capacity reached", "mission_capacity")
    # Snapshot bounded current references. The underlying unbounded audit tables
    # remain retained and independently inspectable; no uncertainty is discarded.
    db._mission_live_refs_on_conn(conn, record, actor)
    record["verification_current"] = db._mission_verified_on_conn(conn, record, actor, access)
    now = time.time()
    record.update(archived=True, archived_at=now)
    conn.execute("INSERT INTO runtime_mission_history VALUES(?,?,?,?,?,?,?,?,?)",
        (record["mission_id"], sid, *(actor[key] for key in ("principal_id", "profile_id", "agent_id")),
         record["project_id"], record["revision"], bounded_json(record), now))
    conn.execute("DELETE FROM runtime_missions WHERE mission_id=?", (previous_mission_id,))


def require_active_target_on_conn(conn, sid, mission_id):
    active = conn.execute("SELECT mission_id FROM runtime_missions WHERE session_id=?", (sid,)).fetchone()
    require(active is not None, "Mission does not exist", "mission_not_found")
    if mission_id is None:
        require(conn.execute("SELECT 1 FROM runtime_mission_history WHERE session_id=? LIMIT 1", (sid,)).fetchone() is None,
                "An exact active mission ID is required after replacement", "mission_id_required")
    else:
        identifier(mission_id)
        require(active[0] == mission_id, "Control targets a non-active mission", "mission_identity_conflict")
    return active[0]


def require_active_target(db, session_id, actor, mission_id):
    with db._runtime_read() as conn:
        sid = db._mission_owner_on_conn(conn, session_id, _actor(actor))
        return require_active_target_on_conn(conn, sid, mission_id)


def get_archived_mission(db, session_id, actor, mission_id, access):
    actor = _actor(actor)
    identifier(mission_id)
    with db._runtime_read() as conn:
        sid = db._mission_owner_on_conn(conn, session_id, actor)
        row = conn.execute("SELECT * FROM runtime_mission_history WHERE mission_id=? AND session_id=?",
                           (mission_id, sid)).fetchone()
        if row is None:
            return None
        require(all(row[key] == actor[key] for key in actor), "Mission owner differs", "identity_mismatch")
        project = row["project_id"]
        record = json.loads(row["record_json"])
        require(record.get("schema_version") == 1, "Unsupported mission history schema", "unsupported_schema")
    if project is not None:
        db._artifact_access(actor, project, access, "read")
        with access.guard(project, actor, "read"):
            return record
    return record


def list_conversation_missions(db, session_id, actor, *, access, limit=100):
    actor = _actor(actor)
    require(type(limit) is int and 1 <= limit <= 100, "Mission history page exceeds bound")
    with db._runtime_read() as conn:
        sid = db._mission_owner_on_conn(conn, session_id, actor)
        identities = [row[0] for row in conn.execute("SELECT mission_id,updated_at AS at FROM runtime_missions WHERE session_id=? "
            "UNION ALL SELECT mission_id,archived_at AS at FROM runtime_mission_history WHERE session_id=? ORDER BY at DESC,mission_id LIMIT ?",
            (sid, sid, limit))]
    return [db.get_mission(session_id, actor, mission_id=mission_id, access=access) for mission_id in identities]
