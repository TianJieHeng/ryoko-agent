"""Dots' owned native page/computer proposals, exact dispatch and receipt recovery."""
from .method_ctx import HandlerRegistry, bind_module

_registry = HandlerRegistry()
method = _registry.method
_profile_scoped = _registry.profile_scoped


def _dots_call(rid, params, model, callback):
    from agent.dots_adapter import _owned_transport
    def owned(agent, db, request):
        _owned_transport(agent, request.session_id)
        return callback(agent, db, request)
    return _artifact_request(rid, params, model, owned)


@method("runtime.dots.register")
@_profile_scoped
def _dots_register(rid, params):
    from agent.dots_adapter import register_adapter
    from tui_gateway.contracts.dots_effects import DotsRegistrationParams
    return _dots_call(rid, params, DotsRegistrationParams, lambda agent, db, request: register_adapter(agent, request))


def _dots_operation(agent, db, request, *, publish):
    import hashlib
    from agent.artifact_commands import begin_artifact_control, artifact_control_scope, finish_artifact_control
    from agent.dots_adapter import dots_action, descriptor, content_bytes, effect_result, _registration
    from agent.result_artifacts import artifact_actor
    from hermes_state_runtime import RuntimeStoreError
    from tools.capability_broker import (_digest, preview_action, recover_approval_preview,
        resolve_approval, invoke_effect_dispatch, CapabilityDenied)
    proposal = request.proposal.model_dump()
    action = dots_action(proposal)
    _registration(agent.runtime_context, proposal)
    payload = {"mode": "dots_" + proposal["kind"], "request_sha256": _digest(proposal)}
    existing = db.read_runtime_command(agent.session_id, request.command_id)
    if existing is not None and existing["command"]["payload"] != payload:
        raise RuntimeStoreError("idempotency_conflict", "Command already names a different immutable proposal")
    if publish and existing is not None and existing["status"] in {"completed", "blocked", "failed"}:
        result = existing["result"]
        if isinstance(result, dict) and result.get("effect_id"):
            effect = db.get_effect(result["effect_id"], artifact_actor(agent.runtime_context))
            approval = db.get_effect_approval(effect["approval_id"], artifact_actor(agent.runtime_context))
            if (effect["approval_id"] != request.approval_id or approval["approval_digest"] != request.approval_digest):
                raise RuntimeStoreError("approval_mismatch", "Completed operation has a different exact approval")
            return effect_result(effect, db, artifact_actor(agent.runtime_context))
    run = begin_artifact_control(agent, request.session_id, request.command_id, payload)
    with artifact_control_scope(run):
        actor = artifact_actor(run.context)
        approval_id = "dots-" + _digest({"run_id": run.run_id, "action_digest": action.digest})[:40]
        try:
            db.get_effect_approval(approval_id, actor)
        except RuntimeStoreError as exc:
            if exc.code != "approval_not_found":
                raise
            approval = preview_action(action, approval_id=approval_id)
        else:
            approval = recover_approval_preview(approval_id, action)
        ref = descriptor(proposal, run)
        if not publish:
            return {"command_id": request.command_id, "run_id": run.run_id, "operation_id": request.command_id,
                "action_digest": action.digest, "input_digest": hashlib.sha256(action.input_json.encode()).hexdigest(),
                "content_sha256": ref["sha256"], "approval_id": approval.approval_id,
                "approval_digest": approval.approval_digest, "expires_at": approval.expires_at}
        if request.approval_id != approval.approval_id or request.approval_digest != approval.approval_digest:
            raise RuntimeStoreError("approval_mismatch", "Decision does not name the exact native proposal")
        decision = db.get_effect_approval(approval.approval_id, actor)
        if decision["status"] == "pending":
            resolve_approval(approval, request.approval_digest, "once")
        try:
            result = invoke_effect_dispatch(action.operation_class, run=run, input_ref=ref,
                payload=content_bytes(proposal), operation_id=request.command_id, intent_key=request.command_id,
                action=action, approval=approval)
        except CapabilityDenied as exc:
            if exc.code != "dots_outcome_unknown":
                raise
            effects = db.list_effects(run.session_id, actor, run_id=run.run_id)
            matching = [item for item in effects if item["operation_id"] == request.command_id]
            if len(matching) != 1:
                raise
            result = effect_result(db.get_effect(matching[0]["effect_id"], actor), db, actor)
        try:
            finish_artifact_control(run, result, status="completed" if result["state"] == "confirmed" else "blocked")
        finally:
            # Read-only reconciliation may immediately claim the conversation.
            # A stale/unfinished command still cannot adopt a fresh generation.
            db.release_session_turn_lease(run.session_id, run.holder, generation=run.generation)
        return result


@method("runtime.dots.page.prepare")
@_profile_scoped
def _dots_page_prepare(rid, params):
    from tui_gateway.contracts.dots_effects import DotsPagePrepareParams
    return _dots_call(rid, params, DotsPagePrepareParams,
        lambda agent, db, request: _dots_operation(agent, db, request, publish=False))


@method("runtime.dots.page.publish")
@_profile_scoped
def _dots_page_publish(rid, params):
    from tui_gateway.contracts.dots_effects import DotsPagePublishParams
    return _dots_call(rid, params, DotsPagePublishParams,
        lambda agent, db, request: _dots_operation(agent, db, request, publish=True))


@method("runtime.dots.computer.prepare")
@_profile_scoped
def _dots_computer_prepare(rid, params):
    from tui_gateway.contracts.dots_effects import DotsComputerPrepareParams
    return _dots_call(rid, params, DotsComputerPrepareParams,
        lambda agent, db, request: _dots_operation(agent, db, request, publish=False))


@method("runtime.dots.computer.execute")
@_profile_scoped
def _dots_computer_execute(rid, params):
    from tui_gateway.contracts.dots_effects import DotsComputerExecuteParams
    return _dots_call(rid, params, DotsComputerExecuteParams,
        lambda agent, db, request: _dots_operation(agent, db, request, publish=True))


def _dots_reconcile(agent, db, request):
    import time
    import uuid
    from agent.dots_adapter import DOTS_OPERATIONS, effect_result
    from agent.effect_reconciler import reconcile_effect
    from agent.result_artifacts import artifact_actor
    from tools.capability_broker import CapabilityDenied
    actor = artifact_actor(agent.runtime_context)
    row = db.get_effect(request.effect_id, actor)
    if (row["operation_type"] not in DOTS_OPERATIONS
            or row["session_id"] != db.read_runtime_snapshot(agent.session_id)["session_id"]):
        raise CapabilityDenied("identity_mismatch", "Native effect is outside this owned conversation")
    holder = "dots-reconcile:" + uuid.uuid4().hex
    if not db.try_acquire_session_turn_lease(agent.session_id, holder, ttl_seconds=30, patience_s=0.5):
        raise CapabilityDenied("effect_owner_busy", "The active owner must finish before read-only reconciliation")
    lease = db.get_session_turn_lease(agent.session_id)
    try:
        reconcile_effect(db, request.effect_id, context=agent.runtime_context, holder=holder,
            generation=lease["generation"], deadline_at=min(time.time() + 25, lease["expires_at"]))
        return effect_result(db.get_effect(request.effect_id, actor), db, actor)
    finally:
        db.release_session_turn_lease(agent.session_id, holder, generation=lease["generation"])


@method("runtime.dots.effect.reconcile")
@_profile_scoped
def _dots_effect_reconcile(rid, params):
    from tui_gateway.contracts.dots_effects import DotsReconcileParams
    return _dots_call(rid, params, DotsReconcileParams, _dots_reconcile)


def register(server):
    bind_module(globals(), server)
