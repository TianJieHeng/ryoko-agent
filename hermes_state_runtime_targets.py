"""Exact run controls use the journal and lease, never the last-run projection."""
from __future__ import annotations

from hermes_state_runtime import _require


def control_target_on_conn(db, conn, sid, command, *, queued_cancel=False,
                           holder=None, generation=None):
    """Validate the immutable target in its acceptance/claim writer transaction."""
    target = command["target_run_id"]
    row = conn.execute("SELECT * FROM runtime_commands WHERE session_id=? AND run_id=? "
        "AND json_extract(command_json,'$.operation')='submit'", (sid, target)).fetchone()
    _require(row is not None, "target_run_ended", "Target run is unavailable")
    if queued_cancel:
        queued = conn.execute("SELECT 1 FROM runtime_admission_queue WHERE session_id=? AND command_id=? "
            "AND state IN ('queued','running')", (sid, row["command_id"])).fetchone()
        _require(row["status"] == "accepted" and queued is not None, "target_run_ended",
                 "Target run is no longer queued and unclaimed")
    else:
        _require(row["status"] == "claimed", "target_run_ended", "Target run is no longer active")
        db._runtime_fence_on_conn(conn, sid, row["claimed_holder"], row["claimed_generation"])
        if holder is not None or generation is not None:
            _require(row["claimed_holder"] == holder and row["claimed_generation"] == generation,
                     "stale_owner", "Target run belongs to a different lease generation")
    return target
