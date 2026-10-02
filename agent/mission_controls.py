"""Bounded human mission controls under the existing authoritative session lease.

Control RPCs cannot mint a model/tool run. A current run keeps its original budget
and generation; an idle control acquires and releases a short metadata-only lease.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import time
import uuid

from hermes_state_runtime import RuntimeStoreError

_CONTROL_RPCS = frozenset({"runtime.mission.create", "runtime.mission.revise", "runtime.mission.pause",
    "runtime.mission.resume", "runtime.mission.cancel", "runtime.mission.accept", "runtime.mission.verify",
    "command.dispatch", "slash.exec"})
_MISSION_CONTROL = ContextVar("owned_mission_control", default=None)
CONTROL_TTL_SECONDS = 30


@dataclass(frozen=True)
class MissionControl:
    agent: object
    db: object
    context: object
    session_id: str
    actor: dict
    holder: str
    generation: int
    ui_session_id: str
    expires_at: float


def _mission_control_actor(context):
    return {key: getattr(context.identity, key) for key in ("principal_id", "profile_id", "agent_id")}


def assert_mission_user_control(session_id, actor, holder, generation):
    """State mutations requiring user acceptance must prove this actual live control."""
    control = _MISSION_CONTROL.get()
    if not isinstance(control, MissionControl):
        raise RuntimeStoreError("mission_user_control_required", "Mission acceptance requires an owned human control")
    from tui_gateway import server
    from agent.runtime_context import current_agent_context
    from tools.capability_broker import require_live_policy
    if (server._current_rpc_method.get() not in _CONTROL_RPCS or control.expires_at <= time.time()
            or current_agent_context() != control.context
            or session_id != control.session_id or actor != control.actor
            or holder != control.holder or generation != control.generation):
        raise RuntimeStoreError("mission_user_control_required", "Mission control scope expired or changed")
    require_live_policy(require_run=False)
    transport, session = server._current_session_steer_authority(control.ui_session_id)
    if transport is None or session is None or session.get("agent") is not control.agent:
        raise RuntimeStoreError("identity_mismatch", "Mission control requires its current owned transport")
    lease = control.db.get_session_turn_lease(control.session_id)
    if (lease is None or lease["holder"] != holder or lease["generation"] != generation
            or lease["expires_at"] <= time.time()):
        raise RuntimeStoreError("mission_stale_owner", "Mission control has lost its session lease")
    return control


@contextmanager
def mission_user_control(agent, ui_session_id):
    """Derive authority only from a bound owned RPC, never client actor or generation."""
    from tui_gateway import server
    from agent.runtime_commands import _RUN, RuntimeRun
    from agent.runtime_context import current_agent_context
    from gateway.durable_outbox import _authority
    from tools.capability_broker import require_live_policy
    if server._current_rpc_method.get() not in _CONTROL_RPCS or _RUN.get() is not None:
        raise RuntimeStoreError("mission_user_control_required", "Mission controls require an explicit owned RPC")
    transport, session = server._current_session_steer_authority(ui_session_id)
    if transport is None or session is None or session.get("agent") is not agent:
        raise RuntimeStoreError("identity_mismatch", "Mission control requires its current owned transport")
    context, db, sid, actor = _authority(agent)
    if current_agent_context() != context:
        raise RuntimeStoreError("identity_mismatch", "Mission control has no exact active identity")
    require_live_policy(require_run=False)
    active = getattr(agent, "_active_runtime_run", None)
    acquired = False
    if active is not None:
        if (not isinstance(active, RuntimeRun) or active.agent is not agent or active.db is not db
                or active.context != context or active.session_id != sid):
            raise RuntimeStoreError("mission_owner_busy", "A different runtime owner holds this conversation")
        from agent.runtime_commands import _check_run, _assert_run_ownership, RuntimeFenceError
        from agent.budget_account import BudgetBlocked
        from agent.task_scope import TaskCancelled
        try:
            _check_run(active)
        except (BudgetBlocked, TaskCancelled):
            if server._current_rpc_method.get() not in {"runtime.mission.pause", "runtime.mission.cancel"}:
                raise RuntimeStoreError("mission_run_blocked", "The active run is cancelled or exceeds its existing budget") from None
            _assert_run_ownership(active)
        except RuntimeFenceError:
            raise RuntimeStoreError("mission_stale_owner", "The active runtime claim changed") from None
        holder, generation = active.holder, active.generation
    else:
        holder = "mission-control:" + uuid.uuid4().hex
        if not db.try_acquire_session_turn_lease(sid, holder, ttl_seconds=CONTROL_TTL_SECONDS):
            raise RuntimeStoreError("mission_owner_busy", "Another operation owns this conversation")
        acquired = True
        generation = db.get_session_turn_lease(sid)["generation"]
    control = MissionControl(agent, db, context, sid, actor, holder, generation, ui_session_id,
                             time.time() + CONTROL_TTL_SECONDS)
    token = _MISSION_CONTROL.set(control)
    try:
        assert_mission_user_control(sid, actor, holder, generation)
        yield control
    finally:
        _MISSION_CONTROL.reset(token)
        if acquired:
            db.release_session_turn_lease(sid, holder, generation=generation)
