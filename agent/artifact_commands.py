"""Owned artifact controls, unable to dispatch inference or arbitrary tools.

The existing command journal and turn lease own these bounded human operations.
A pending approval survives the request, but never renews its lease or retries an
uncertain mutation. Reconnection can inspect/reopen only the exact live claim.
Finite connected-source reads use a separate host-owned, budgeted egress adapter.
"""
from __future__ import annotations

import time
import threading
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from hermes_state_runtime import RuntimeStoreError

CONTROL_TTL_SECONDS = 300
_CONTROL = ContextVar("runtime_artifact_control", default=None)
_CONTROL_RPCS = frozenset({"runtime.artifact.prepare", "runtime.artifact.publish",
    "runtime.artifact.bytes.prepare", "runtime.artifact.bytes.publish",
    "runtime.artifact.edit.prepare", "runtime.artifact.edit.publish",
    "runtime.artifact.merge.prepare", "runtime.artifact.merge.publish",
    "runtime.template.prepare", "runtime.template.publish",
    "runtime.artifact.status", "runtime.artifact.cancel", "runtime.domain.prepare", "runtime.domain.publish",
    "runtime.brief.prepare", "runtime.brief.publish",
    "runtime.workflow.create", "runtime.workflow.template.create", "runtime.workflow.evaluate",
    "runtime.workflow.decision.prepare", "runtime.workflow.decision.commit", "runtime.workflow.feedback",
    "runtime.workflow.run.prepare", "runtime.workflow.run.publish",
    "runtime.schedule.create", "runtime.schedule.import", "runtime.schedule.update",
    "runtime.schedule.grant", "runtime.schedule.reconcile",
    "runtime.schedule.output.prepare", "runtime.schedule.output.publish",
    "runtime.monitor.policy.set", "runtime.monitor.snooze", "runtime.monitor.dismiss",
    "runtime.inbox.prepare", "runtime.commitment.accept", "runtime.commitment.decline", "runtime.commitment.update",
    "runtime.correspondence.draft", "runtime.correspondence.receipt", "runtime.voice.admit",
    "runtime.sources.prepare", "runtime.sources.publish", "runtime.sources.preview"})


@dataclass(frozen=True)
class ArtifactControlRun:
    agent: Any
    db: Any
    context: Any
    session_id: str
    command_id: str
    run_id: str
    holder: str
    generation: int
    deadline_at: float
    ui_session_id: str
    budget: Any = None
    dispatch_blocked: threading.Event = field(default_factory=threading.Event, compare=False)


def _owned_agent(agent, ui_session_id):
    from tui_gateway import server
    from agent.runtime_commands import _RUN
    if server._current_rpc_method.get() not in _CONTROL_RPCS or _RUN.get() is not None:
        raise RuntimeStoreError("artifact_control_required", "Only an explicit owned artifact RPC can mint control authority")
    transport, session = server._current_session_steer_authority(ui_session_id)
    if transport is None or session is None or session.get("agent") is not agent:
        raise RuntimeStoreError("identity_mismatch", "Artifact control requires its owned live transport")
    from gateway.durable_outbox import _authority
    return _authority(agent)


def begin_artifact_control(agent, ui_session_id, command_id, payload):
    """Accept/claim an explicit human operation, or reopen its exact unexpired claim."""
    context, db, sid, actor = _owned_agent(agent, ui_session_id)
    if getattr(agent, "_active_runtime_run", None) is not None:
        raise RuntimeStoreError("artifact_owner_busy", "An active inference run owns this conversation")
    policy = getattr(agent, "_runtime_budget_policy", None)
    command = {"schema_version": 1, "command_id": command_id, "idempotency_key": command_id,
               "expected_revision": None, "operation": "artifact", "payload": payload}
    receipt = db.submit_runtime_command(sid, actor, command,
        budget_policy_json=policy.snapshot if policy is not None else None)
    record = db.read_runtime_command(sid, command_id)
    if record["status"] not in {"accepted", "claimed"}:
        raise RuntimeStoreError("artifact_control_finished", "Artifact control has a recorded terminal outcome")
    new_claim = record["status"] == "accepted"
    if new_claim:
        holder = "artifact-control:" + uuid.uuid4().hex
        if not db.try_acquire_session_turn_lease(sid, holder, ttl_seconds=CONTROL_TTL_SECONDS):
            raise RuntimeStoreError("artifact_owner_busy", "Another operation owns this conversation")
        lease = db.get_session_turn_lease(sid)
        try:
            if not db.claim_runtime_command(sid, command_id, holder=holder, generation=lease["generation"]):
                raise RuntimeStoreError("claim_conflict", "Artifact control was claimed concurrently")
        except BaseException:
            db.release_session_turn_lease(sid, holder, generation=lease["generation"])
            raise
        record = db.read_runtime_command(sid, command_id)
    lease = db.get_session_turn_lease(sid)
    if (lease is None or lease["holder"] != record["claimed_holder"]
            or lease["generation"] != record["claimed_generation"] or lease["expires_at"] <= time.time()):
        raise RuntimeStoreError("artifact_stale_owner", "Expired or replaced artifact control cannot replay")
    from agent.budget_account import create_run_budget, BudgetBlocked
    try:
        budget = create_run_budget(agent, db, context, receipt["run_id"], lease["holder"], lease["generation"], command_id)
    except BudgetBlocked:
        # Admission failed before any artifact preparation or dispatch. Preserve
        # the recorded denial without holding an idle conversation lease.
        if new_claim:
            db.finish_runtime_command(sid, command_id, holder=lease["holder"], generation=lease["generation"],
                                      status="blocked", result={"blocked": True, "reason": "budget_unavailable"})
            db.release_session_turn_lease(sid, lease["holder"], generation=lease["generation"])
        # A reopened claim may already have dispatched; do not relabel its
        # uncertainty as a clean admission denial.
        raise
    return ArtifactControlRun(agent, db, context, lease["conversation_id"], command_id,
        receipt["run_id"], lease["holder"], lease["generation"],
        min(lease["expires_at"], lease["acquired_at"] + CONTROL_TTL_SECONDS), ui_session_id, budget)


def current_artifact_run():
    run = _CONTROL.get()
    if run is not None:
        return run
    from agent.runtime_commands import _RUN
    return _RUN.get()


def assert_artifact_dispatch(run=None):
    from agent.runtime_context import current_agent_context
    from agent.runtime_commands import RuntimeRun, assert_runtime_dispatch
    from tools.capability_broker import require_live_policy
    run = current_artifact_run() if run is None else run
    if isinstance(run, RuntimeRun):
        if assert_runtime_dispatch() is not run:
            raise RuntimeStoreError("identity_mismatch", "Artifact run differs from active runtime")
        return run
    if not isinstance(run, ArtifactControlRun) or _CONTROL.get() is not run:
        raise RuntimeStoreError("artifact_control_required", "An admitted artifact control is required")
    if current_agent_context() != run.context or require_live_policy(require_run=False) != run.context:
        raise RuntimeStoreError("identity_mismatch", "Artifact context changed")
    _owned_agent(run.agent, run.ui_session_id)
    if run.dispatch_blocked.is_set():
        raise RuntimeStoreError("artifact_reconciliation_required", "Artifact journal outcome is unresolved")
    if time.time() >= run.deadline_at or bool(getattr(run.agent, "_interrupt_requested", False)):
        raise RuntimeStoreError("artifact_control_cancelled", "Artifact control expired or was interrupted")
    lease = run.db.get_session_turn_lease(run.session_id)
    record = run.db.read_runtime_command(run.session_id, run.command_id)
    if (lease is None or lease["holder"] != run.holder or lease["generation"] != run.generation
            or lease["expires_at"] <= time.time() or record is None or record["status"] != "claimed"
            or record["claimed_holder"] != run.holder or record["claimed_generation"] != run.generation
            or record["receipt"]["run_id"] != run.run_id or record["command"]["operation"] != "artifact"):
        raise RuntimeStoreError("artifact_stale_owner", "Artifact control no longer owns dispatch")
    if run.budget is not None:
        run.budget.check()
    return run


@contextmanager
def artifact_control_scope(run):
    from agent.identity_lifecycle import agent_runtime_scope
    token = _CONTROL.set(run)
    try:
        with agent_runtime_scope(run.context):
            assert_artifact_dispatch(run)
            yield run
    finally:
        _CONTROL.reset(token)


def finish_artifact_control(run, result, *, status="completed"):
    assert_artifact_dispatch(run)
    if status not in {"completed", "cancelled", "failed", "blocked"}:
        raise RuntimeStoreError("invalid_command", "Invalid artifact control outcome")
    run.db.finish_runtime_command(run.session_id, run.command_id, holder=run.holder,
        generation=run.generation, status=status, result=result)
    run.db.release_session_turn_lease(run.session_id, run.holder, generation=run.generation)


def artifact_control_status(agent, ui_session_id, command_id):
    _context, db, sid, _actor = _owned_agent(agent, ui_session_id)
    record = db.read_runtime_command(sid, command_id)
    if record is None or record["command"]["operation"] != "artifact":
        raise RuntimeStoreError("command_not_found", "Owned artifact control was not found")
    if isinstance(record["result"], dict) and record["result"].get("project_id"):
        from agent.identity_lifecycle import agent_runtime_scope
        from agent.project_context import authorize_project
        with agent_runtime_scope(_context):
            authorize_project(_context, record["result"]["project_id"], "read")
    lease = db.get_session_turn_lease(sid)
    live = bool(record["status"] == "claimed" and lease is not None
        and lease["holder"] == record["claimed_holder"]
        and lease["generation"] == record["claimed_generation"] and lease["expires_at"] > time.time())
    return {"command_id": command_id, "run_id": record["receipt"]["run_id"],
            "status": record["status"], "owner_live": live,
            "expires_at": lease["expires_at"] if live else None,
            "result": record["result"]}


def cancel_artifact_control(agent, ui_session_id, command_id):
    _context, db, sid, actor = _owned_agent(agent, ui_session_id)
    status = artifact_control_status(agent, ui_session_id, command_id)
    if status["status"] in {"completed", "cancelled", "failed", "blocked"}:
        return status
    record = db.read_runtime_command(sid, command_id)
    if not status["owner_live"]:
        # No successor adoption or new generation just to claim a cancellation.
        raise RuntimeStoreError("artifact_stale_owner", "Expired control remains unresolved for inspection")
    db.finish_runtime_command(sid, command_id, holder=record["claimed_holder"],
        generation=record["claimed_generation"], status="cancelled",
        result={"cancel_requested": True, "effects_undone": False})
    db.release_session_turn_lease(sid, record["claimed_holder"], generation=record["claimed_generation"])
    return artifact_control_status(agent, ui_session_id, command_id)
