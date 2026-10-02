"""Owned, profile-bound access to the durable journal. Token replay stays separate."""

from .method_ctx import HandlerRegistry, bind_module

_registry = HandlerRegistry()
method = _registry.method
_profile_scoped = _registry.profile_scoped

_RUNTIME_EVENT_PAYLOAD_FIELDS = {
    "command.accepted": ("command_id", "operation"),
    "command.claimed": ("command_id",),
    "command.completed": ("command_id",),
    "command.failed": ("command_id",),
    "command.blocked": ("command_id",),
    "command.cancelled": ("command_id",),
    "checkpoint.published": ("checkpoint_id", "included_seq"),
}


def _runtime_validate(rid, params, model):
    from pydantic import ValidationError

    try:
        return model.model_validate(params), None
    except ValidationError as exc:
        # ValidationError's normal string includes the submitted input, which
        # can contain a private prompt. Only the field paths belong in errors.
        paths = sorted({".".join(map(str, error["loc"])) or "params" for error in exc.errors()})
        unsupported = "schema_version" in params and (
            type(params["schema_version"]) is not int or params["schema_version"] != 1)
        code = "unsupported_schema" if unsupported else "invalid_command"
        return None, _err(rid, 4000, "Invalid runtime parameters: " + ", ".join(paths),
                          {"code": code, "supported_versions": [1]})


def _runtime_authority(rid, params):
    from agent.agent_identity import IdentityPolicyError
    from agent.runtime_context import AgentContext

    session, error = _sess_nowait(params, rid)
    if error:
        return None, None, error
    transport, owned = _current_session_steer_authority(params["session_id"])
    if transport is None or owned is not session:
        return None, None, _err(rid, 4001, "session not found or not owned by this transport")
    agent = session.get("agent")
    context = getattr(agent, "runtime_context", None)
    db = getattr(agent, "_session_db", None)
    if not isinstance(context, AgentContext) or db is None:
        return None, None, _err(rid, 4030, "Durable runtime requires a bound agent identity and session store",
                                {"code": "identity_required"})
    try:
        context.validate_profile_home()
    except IdentityPolicyError:
        return None, None, _err(rid, 4030, "Runtime identity does not match the active profile",
                                {"code": "identity_mismatch"})
    sid = getattr(agent, "session_id", None)
    # Compression rotates the transcript key while preserving this immutable
    # binding; the store resolves its journal through the compression root.
    if not sid or db.get_session_model_config_value(sid, "agent_identity") != context.identity.to_record():
        return None, None, _err(rid, 4030, "Runtime identity does not match the stored session",
                                {"code": "identity_mismatch"})
    return agent, db, None


def _runtime_store_error(rid, exc):
    # Do not expose SQLite paths, provider data, or raw exception context.
    codes = {
        "unsupported_schema": 4000,
        "invalid_command": 4000,
        "identity_mismatch": 4030,
        "identity_required": 4030,
        "durable_store_required": 5030,
        "idempotency_conflict": 4090,
        "revision_conflict": 4090,
        "session_not_found": 4001,
        "runtime_transport_unsupported": 5010,
        "runtime_delivery_transport_unsupported": 5010,
        "unsupported_operation": 5010,
    }
    return _err(rid, codes.get(exc.code, 4090), "Runtime request rejected: " + exc.code,
                {"code": exc.code})


def _runtime_snapshot_projection(snapshot):
    from tui_gateway.contracts.runtime_v1 import (
        MissionSnapshot, RuntimeArtifactReference, RuntimeOutstandingRequest,
        RuntimeSnapshotState, RuntimeUnresolvedEffect, RuntimeUnresolvedInvocation, RuntimeReferenceCounts,
    )

    projection = {key: snapshot.get(key) for key in MissionSnapshot.model_fields}
    projection["state"] = {key: snapshot["state"][key] for key in RuntimeSnapshotState.model_fields}
    references = {
        "outstanding_requests": RuntimeOutstandingRequest,
        "artifacts": RuntimeArtifactReference,
        "unresolved_effects": RuntimeUnresolvedEffect,
        "unresolved_invocations": RuntimeUnresolvedInvocation,
    }
    for key, model in references.items():
        projection[key] = [{field: value[field] for field in model.model_fields} for value in snapshot.get(key, [])]
    projection["reference_counts"] = {
        key: snapshot.get("reference_counts", {}).get(key, 0) for key in RuntimeReferenceCounts.model_fields}
    projection["reference_limit"] = snapshot.get("reference_limit", 100)
    projection["references_truncated"] = snapshot.get("references_truncated", False)
    return projection


def _runtime_event_projection(event):
    from pydantic import ValidationError
    from tui_gateway.contracts.runtime_v1 import RuntimeEventEnvelope, RuntimePhysicalAttempt

    projection = {key: event[key] for key in RuntimeEventEnvelope.model_fields}
    projection["payload"] = {key: event["payload"][key]
                             for key in _RUNTIME_EVENT_PAYLOAD_FIELDS.get(event["type"], ())
                             if key in event["payload"]}
    if event["type"] in {"model.started", "model.completed", "model.failed"}:
        attempt = event["payload"].get("physical_attempt")
        if isinstance(attempt, dict):
            try:
                projection["payload"]["physical_attempt"] = RuntimePhysicalAttempt.model_validate(attempt).model_dump()
            except ValidationError:
                # Unknown/malformed private metadata is never forwarded wholesale.
                pass
    if event["type"] == "runtime.state":
        mission_state = event["payload"].get("mission_state")
        mission_revision = event["payload"].get("mission_revision")
        if isinstance(mission_state, str) and mission_state in {"ready", "working", "waiting_for_user", "waiting_for_source",
                "ready_to_review", "completed", "partially_completed", "paused", "cancelled", "failed"}:
            projection["payload"]["mission_state"] = mission_state
        if type(mission_revision) is int and mission_revision > 0:
            projection["payload"]["mission_revision"] = mission_revision
    if event["type"] == "effect.recorded":
        state = event["payload"].get("state")
        if isinstance(state, str) and state in {"prepared", "dispatched", "confirmed", "failed", "outcome_unknown", "reconciliation_required"}:
            projection["payload"]["effect_state"] = state
        projection["payload"]["operation_type"] = (
            event["payload"]["operation_type"] if event["payload"].get("operation_type") in ("artifact_publish", "project_artifact_publish", "mission_test_execution") else "unsupported")
    if event["type"] in {"approval.requested", "approval.resolved"}:
        status = event["payload"].get("status")
        if isinstance(status, str) and status in {"pending", "approved", "denied", "consumed", "invalidated"}:
            projection["payload"]["approval_status"] = status
        if status == "invalidated":
            reason = event["payload"].get("invalidation_reason")
            revision = event["payload"].get("mission_revision")
            if isinstance(reason, str) and reason in {"mission_changed", "input_changed", "target_changed", "plan_step_changed"}:
                projection["payload"]["invalidation_reason"] = reason
            if type(revision) is int and revision > 0:
                projection["payload"]["mission_revision"] = revision
        expiry = event["payload"].get("expires_at")
        if type(expiry) in (int, float) and 0 < expiry <= 253402300799:
            projection["payload"]["expires_at"] = expiry
    result = event["payload"].get("result")
    if (event["type"] == "command.completed" and isinstance(result, dict)
            and result.get("outcome") in ("steer_queued", "steer_not_queued", "cancel_requested", "cancel_not_requested")):
        projection["payload"]["control_outcome"] = result["outcome"]
    if isinstance(result, dict):
        cancellation = result.get("cancellation")
        if isinstance(cancellation, dict):
            from tui_gateway.contracts.runtime_v1 import RuntimeCancellation
            projection["payload"]["cancellation"] = {
                field: cancellation[field] for field in RuntimeCancellation.model_fields if field in cancellation}
        if result.get("admission_state") in {"expired", "cancelled", "rejected"}:
            projection["payload"]["admission_state"] = result["admission_state"]
    return projection


@method("runtime.capabilities")
@_profile_scoped
def _runtime_capabilities(rid, params):
    from agent.runtime_commands import runtime_execution_denial
    from agent.provider_capabilities import provider_capabilities_for
    from agent.tool_view import ToolView
    from tui_gateway.contracts.runtime_v1 import RuntimeCapabilitiesParams

    request, error = _runtime_validate(rid, params, RuntimeCapabilitiesParams)
    if error:
        return error
    agent, _db, error = _runtime_authority(rid, request.model_dump())
    if error:
        return error
    from dataclasses import asdict
    from agent.admission import AdmissionPolicy
    denial = runtime_execution_denial(agent)
    executable = denial is None
    tool_view = getattr(agent, "tool_view", None)
    if (not isinstance(tool_view, ToolView)
            or tool_view.session_policy_version != agent.runtime_context.policy.digest):
        tool_view = None
    descriptions = {
        "submit": "existing agent turn pipeline; acceptance is not completion",
        "steer": "queues a correction for an active local run; application is not guaranteed",
        "cancel": "requests interruption of an active local run; external cancellation is not guaranteed",
    }
    operations = [{"operation": operation, "accepts_commands": executable, "executes": executable,
                   "effects_enabled": False, "reason": reason if executable else denial}
                  for operation, reason in descriptions.items()]
    operations.append({"operation": "approval", "accepts_commands": False, "executes": False,
                       "effects_enabled": False, "reason": "use runtime.approval.resolve for an exact durable request"})
    return _ok(rid, {"schema_versions": [1], "operations": operations, "strict_identity_required": True,
                     "durable_replay": True, "max_events": 200,
                     "admission": {"scope": "profile_store", **asdict(AdmissionPolicy())} if executable else None,
                     "provider": provider_capabilities_for(agent).to_record(),
                     "tool_view": tool_view.to_record() if tool_view is not None else None,
                     "cursor_policy": "snapshot_required_on_expired_or_unknown_cursor"})


@method("runtime.command")
@_profile_scoped
def _runtime_command(rid, params):
    from agent.runtime_commands import RuntimeCommandError, RuntimeFenceError, submit_command
    from hermes_state_runtime import RuntimeStoreError
    from tui_gateway.contracts.runtime_v1 import RuntimeCommandParams

    request, error = _runtime_validate(rid, params, RuntimeCommandParams)
    if error:
        return error
    agent, db, error = _runtime_authority(rid, request.model_dump())
    if error:
        return error
    if request.operation == "approval":
        snapshot = db.read_runtime_snapshot(agent.session_id)
        return _ok(rid, {"schema_version": 1, "command_id": request.command_id, "status": "rejected",
                         "durable_revision": snapshot["revision"], "run_id": None,
                         "conflict": {"code": "operation_not_supported",
                                      "message": "Approval authorization requires BE05/BE06"}})
    envelope = request.model_dump(exclude={"session_id"})
    try:
        if request.operation == "submit":
            transport, session = _current_session_steer_authority(request.session_id)
            if transport is None or session is None:
                return _err(rid, 4001, "session not found or not owned by this transport")
            return _ok(rid, _submit_runtime_prompt(rid, request.session_id, session, agent, envelope))
        return _ok(rid, submit_command(agent, envelope))
    except (RuntimeStoreError, RuntimeCommandError) as exc:
        return _runtime_store_error(rid, exc)
    except RuntimeFenceError:
        return _err(rid, 4090, "Runtime owner generation is no longer current", {"code": "stale_owner"})


@method("runtime.snapshot")
@_profile_scoped
def _runtime_snapshot(rid, params):
    from hermes_state_runtime import RuntimeStoreError
    from tui_gateway.contracts.runtime_v1 import RuntimeSessionParams

    request, error = _runtime_validate(rid, params, RuntimeSessionParams)
    if error:
        return error
    agent, db, error = _runtime_authority(rid, request.model_dump())
    if error:
        return error
    try:
        from agent.admission import AdmissionQueue
        return _ok(rid, _runtime_snapshot_projection(AdmissionQueue(db).runtime_snapshot(agent.session_id)))
    except RuntimeStoreError as exc:
        return _runtime_store_error(rid, exc)


@method("runtime.events.since")
@_profile_scoped
def _runtime_events_since(rid, params):
    from hermes_state_runtime import RuntimeStoreError
    from tui_gateway.contracts.runtime_v1 import RuntimeEventsSinceParams

    request, error = _runtime_validate(rid, params, RuntimeEventsSinceParams)
    if error:
        return error
    agent, db, error = _runtime_authority(rid, request.model_dump())
    if error:
        return error
    try:
        replay = db.replay_runtime_events(agent.session_id, cursor=request.cursor, limit=request.limit)
    except RuntimeStoreError as exc:
        return _runtime_store_error(rid, exc)
    if replay.get("snapshot") is not None:
        from agent.admission import AdmissionQueue
        replay["snapshot"] = AdmissionQueue(db).runtime_snapshot(agent.session_id)
        replay["last_cursor"] = replay["snapshot"]["last_cursor"]
    return _ok(rid, {"status": replay["status"], "events": [_runtime_event_projection(e) for e in replay["events"]],
                     "snapshot": _runtime_snapshot_projection(replay["snapshot"]) if replay.get("snapshot") else None,
                     "last_cursor": replay["last_cursor"], "has_more": replay["has_more"]})


def register(server):
    bind_module(globals(), server)
