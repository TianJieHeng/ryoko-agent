"""Bounded controls for one run; duplicate delivery never retries an effect."""
from __future__ import annotations

from hermes_state_runtime import RuntimeStoreError


def submit_targeted_control(agent, envelope, db, sid, actor, command, *, expected_run=None):
    from agent.runtime_commands import RuntimeRun, rejected_receipt

    # Validate duplicate content through the journal, but never re-claim/re-apply
    # an accepted or uncertain control on retry, including after a process crash.
    if db.read_runtime_command(sid, command["command_id"]) is not None:
        return db.submit_runtime_command(sid, actor=actor, command=command)
    if command["operation"] == "cancel":
        from agent.admission import AdmissionQueue
        queued = AdmissionQueue(db).cancel_command(agent, envelope)
        if queued is not None:
            return queued
    run = getattr(agent, "_active_runtime_run", None)
    if (not isinstance(run, RuntimeRun) or run.run_id != command["target_run_id"]
            or expected_run is not None and run is not expected_run):
        return rejected_receipt(agent, command["command_id"], "target_run_ended")

    with run.control_lock:
        return _submit_active_target(agent, db, sid, actor, command, run)


def _submit_active_target(agent, db, sid, actor, command, run):
    from agent.runtime_commands import RuntimeFenceError, _check_run, rejected_receipt

    claimed, receipt = False, None

    def claim(conn, root_sid, _encoded, receipt):
        nonlocal claimed
        # This runs only on first acceptance, in the same transaction as the
        # target, revision and live lease checks. A raced duplicate skips it.
        claimed = db._claim_runtime_command_on_conn(conn, root_sid, receipt["command_id"],
                                                    holder=run.holder, generation=run.generation)

    try:
        if getattr(agent, "_active_runtime_run", None) is not run:
            raise RuntimeFenceError("Control target is no longer the active local run")
        _check_run(run)
        receipt = db.submit_runtime_command(sid, actor=actor, command=command, admission=claim)
        if not claimed:
            return receipt
        if getattr(agent, "_active_runtime_run", None) is not run:
            raise RuntimeFenceError("Control target is no longer the active local run")
        _check_run(run)
    except (InterruptedError, RuntimeStoreError) as exc:
        if isinstance(exc, RuntimeStoreError) and exc.code not in {"target_run_ended", "stale_owner"}:
            raise
        # Once committed, acceptance is immutable even if its local owner
        # changes before application. Preserve claimed/uncertain for inspection;
        # neither a retry nor a read may apply it to a successor.
        return receipt if receipt is not None else rejected_receipt(
            agent, command["command_id"], "target_run_ended")

    if command["operation"] == "steer":
        queued = agent.steer(command["payload"]["text"])
        result = {"outcome": "steer_queued" if queued else "steer_not_queued", "applied": False}
        if getattr(agent, "_mission_finalizing_run_id", None) == run.run_id:
            result["missed_steer"] = True
    else:
        from agent.task_scope import create_task_scope
        scope = run.task_scope or create_task_scope(agent, run.run_id)
        cancellation = scope.request_cancel(command["payload"].get("reason") or "Runtime cancellation requested")
        result = {"outcome": "cancel_requested", "provider_cancelled": False,
                  "cancellation": cancellation, "cancelled_queued": 0}
    db.finish_runtime_command(sid, receipt["command_id"], holder=run.holder,
                              generation=run.generation, result=result)
    return receipt
