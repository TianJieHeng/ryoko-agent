"""Authenticated durable decisions and evidence inspection; no effect dispatch API."""

from .method_ctx import HandlerRegistry, bind_module

_registry = HandlerRegistry()
method = _registry.method
_profile_scoped = _registry.profile_scoped


def _runtime_effect_actor(agent):
    from agent.result_artifacts import artifact_actor
    return artifact_actor(agent.runtime_context)


def _runtime_effect_reference_digest(value):
    import hashlib
    return hashlib.sha256(value.encode()).hexdigest()


def _runtime_approval_public(row):
    import time
    binding = row["binding"]
    result = {key: row[key] for key in ("approval_id", "run_id", "approval_digest", "status", "expires_at",
                                       "created_at", "resolved_at", "consumed_at")}
    result.update({key: binding[key] for key in ("action_digest", "input_digest", "policy_version", "policy_digest")})
    result.update(target_digest=_runtime_effect_reference_digest(binding["target_ref"]),
        input_revision_digest=_runtime_effect_reference_digest(binding["input_revision"]),
        artifact_revision_digest=_runtime_effect_reference_digest(binding["artifact_revision"]),
        expired=row["expires_at"] <= time.time())
    if row["status"] == "invalidated":
        for key in ("invalidation_reason", "mission_id", "mission_revision", "invalidated_at"):
            if key in row:
                result[key] = row[key]
    return result


def _runtime_effect_public(row):
    fields = ("effect_id", "run_id", "operation_id", "state", "action_digest", "input_digest",
              "policy_version", "policy_digest", "generation", "approval_id", "provider_idempotency", "created_at", "updated_at")
    result = {key: row[key] for key in fields}
    result.update(target_digest=_runtime_effect_reference_digest(row["target_ref"]),
        operation_type=row["operation_type"] if row["operation_type"] in {"artifact_publish", "project_artifact_publish", "mission_test_execution"} else "unsupported",
        exactly_once_external=False, replay_permitted=False)
    return result


def _runtime_effect_evidence_public(row):
    return [{**{key: item[key] for key in ("sequence", "generation", "from_state", "state", "created_at")},
             "receipt_available": item["receipt"] is not None,
             "receipt_sha256": (item["receipt"] or {}).get("sha256")}
            for item in row.get("evidence", [])]


def _runtime_effect_require_session(agent, db, row):
    from hermes_state_runtime import RuntimeStoreError
    if row["session_id"] != db.read_runtime_snapshot(agent.session_id)["session_id"]:
        raise RuntimeStoreError("identity_mismatch", "Record belongs to another owned conversation")


def _runtime_effect_policy(agent):
    from agent.agent_identity import IdentityPolicyError
    from tools.capability_broker import CapabilityDenied, require_live_policy
    try:
        if require_live_policy(require_run=False) != agent.runtime_context:
            raise CapabilityDenied("identity_mismatch", "Runtime policy does not match the owned agent")
    except IdentityPolicyError as exc:
        raise CapabilityDenied("policy_revoked", "Runtime policy is no longer valid") from exc


def _runtime_approval_owner(agent, db, row):
    from agent.budget_account import BudgetBlocked
    from agent.runtime_commands import RuntimeFenceError, RuntimeRun, _check_run
    from agent.task_scope import TaskCancelled
    from tools.capability_broker import CapabilityDenied
    run = getattr(agent, "_active_runtime_run", None)
    if (not isinstance(run, RuntimeRun) or run.agent is not agent or run.db is not db
            or run.context != agent.runtime_context or run.run_id != row["run_id"]
            or run.dispatch_blocked.is_set()):
        raise CapabilityDenied("approval_owner_unavailable", "Approval requires its active trusted runtime owner")
    try:
        _check_run(run)
    except RuntimeFenceError as exc:
        raise CapabilityDenied("stale_owner", "Approval owner generation is no longer current") from exc
    except TaskCancelled as exc:
        raise CapabilityDenied("run_cancelled", "The runtime task was cancelled") from exc
    except BudgetBlocked as exc:
        raise CapabilityDenied("budget_blocked", "The runtime budget no longer authorizes work") from exc
    binding = row["binding"]
    if (binding["holder"] != run.holder or binding["generation"] != run.generation
            or binding["policy_digest"] != agent.runtime_context.policy.digest
            or binding["policy_version"] != str(agent.runtime_context.policy.policy_version)):
        raise CapabilityDenied("approval_mismatch", "Approval owner or policy changed")
    return run


@method("runtime.approvals.list")
@_profile_scoped
def _runtime_approvals_list(rid, params):
    from hermes_state_runtime import RuntimeStoreError
    from tui_gateway.contracts.runtime_effects import RuntimeApprovalListParams
    request, error = _runtime_validate(rid, params, RuntimeApprovalListParams)
    if error:
        return error
    agent, db, error = _runtime_authority(rid, request.model_dump())
    if error:
        return error
    try:
        rows = db.list_effect_approvals(agent.session_id, _runtime_effect_actor(agent),
                                       run_id=request.run_id, limit=request.limit + 1)
        return _ok(rid, {"approvals": [_runtime_approval_public(row) for row in rows[:request.limit]],
                         "limit": request.limit, "truncated": len(rows) > request.limit, "complete": False})
    except RuntimeStoreError as exc:
        return _runtime_store_error(rid, exc)


@method("runtime.approval.resolve")
@_profile_scoped
def _runtime_approval_resolve(rid, params):
    from agent.identity_lifecycle import agent_runtime_scope
    from hermes_state_runtime import RuntimeStoreError
    from tools.capability_broker import CapabilityDenied
    from tui_gateway.contracts.runtime_effects import RuntimeApprovalResolveParams
    request, error = _runtime_validate(rid, params, RuntimeApprovalResolveParams)
    if error:
        return error
    agent, db, error = _runtime_authority(rid, request.model_dump())
    if error:
        return error
    try:
        actor = _runtime_effect_actor(agent)
        row = db.get_effect_approval(request.approval_id, actor)
        _runtime_effect_require_session(agent, db, row)
        with agent_runtime_scope(agent.runtime_context):
            _runtime_effect_policy(agent)
            run = _runtime_approval_owner(agent, db, row)
            decision = db.resolve_effect_approval(request.approval_id, actor, holder=run.holder,
                generation=run.generation, approval_digest=request.approval_digest, choice=request.choice)
        return _ok(rid, {"approval": _runtime_approval_public(decision), "dispatch_performed": False})
    except (RuntimeStoreError, CapabilityDenied) as exc:
        return _runtime_store_error(rid, exc)


@method("runtime.effects.list")
@_profile_scoped
def _runtime_effects_list(rid, params):
    from hermes_state_runtime import RuntimeStoreError
    from tui_gateway.contracts.runtime_effects import RuntimeEffectListParams
    request, error = _runtime_validate(rid, params, RuntimeEffectListParams)
    if error:
        return error
    agent, db, error = _runtime_authority(rid, request.model_dump())
    if error:
        return error
    try:
        rows = db.list_effects(agent.session_id, _runtime_effect_actor(agent), run_id=request.run_id,
                              unresolved_only=request.unresolved_only, limit=request.limit + 1)
        return _ok(rid, {"effects": [_runtime_effect_public(row) for row in rows[:request.limit]],
                         "limit": request.limit, "truncated": len(rows) > request.limit, "complete": False})
    except RuntimeStoreError as exc:
        return _runtime_store_error(rid, exc)


@method("runtime.effect.get")
@_profile_scoped
def _runtime_effect_get(rid, params):
    from hermes_state_runtime import RuntimeStoreError
    from tui_gateway.contracts.runtime_effects import RuntimeEffectParams
    request, error = _runtime_validate(rid, params, RuntimeEffectParams)
    if error:
        return error
    agent, db, error = _runtime_authority(rid, request.model_dump())
    if error:
        return error
    try:
        row = db.get_effect(request.effect_id, _runtime_effect_actor(agent))
        _runtime_effect_require_session(agent, db, row)
        return _ok(rid, {"effect": _runtime_effect_public(row), "evidence": _runtime_effect_evidence_public(row)})
    except RuntimeStoreError as exc:
        return _runtime_store_error(rid, exc)


@method("runtime.effect.reconcile")
@_profile_scoped
def _runtime_effect_reconcile(rid, params):
    import time
    import uuid
    from agent.effect_reconciler import reconcile_effect
    from agent.identity_lifecycle import agent_runtime_scope
    from hermes_state_runtime import RuntimeStoreError
    from tools.capability_broker import CapabilityDenied
    from tui_gateway.contracts.runtime_effects import RuntimeEffectParams
    request, error = _runtime_validate(rid, params, RuntimeEffectParams)
    if error:
        return error
    agent, db, error = _runtime_authority(rid, request.model_dump())
    if error:
        return error
    holder, generation = "rpc-reconcile:" + uuid.uuid4().hex, None
    try:
        actor = _runtime_effect_actor(agent)
        row = db.get_effect(request.effect_id, actor)
        _runtime_effect_require_session(agent, db, row)
        if row["operation_type"] not in {"artifact_publish", "project_artifact_publish"}:
            raise CapabilityDenied("effect_adapter_unsupported", "Only local immutable artifact inspection is supported")
        with agent_runtime_scope(agent.runtime_context):
            _runtime_effect_policy(agent)
            if not db.try_acquire_session_turn_lease(agent.session_id, holder, ttl_seconds=30, patience_s=0.5):
                raise CapabilityDenied("effect_owner_busy", "The active runtime owner must finish before reconciliation")
            lease = db.get_session_turn_lease(agent.session_id)
            if lease is None or lease["holder"] != holder:
                raise CapabilityDenied("stale_owner", "Reconciliation lease is no longer current")
            generation = lease["generation"]
            reconcile_effect(db, request.effect_id, context=agent.runtime_context, holder=holder,
                             generation=generation, deadline_at=min(time.time() + 25, lease["expires_at"]))
            row = db.get_effect(request.effect_id, actor)
        return _ok(rid, {"effect": _runtime_effect_public(row), "evidence": _runtime_effect_evidence_public(row),
                         "inspection_only": True, "dispatch_performed": False})
    except (RuntimeStoreError, CapabilityDenied) as exc:
        return _runtime_store_error(rid, exc)
    finally:
        if generation is not None:
            db.release_session_turn_lease(agent.session_id, holder, generation=generation)


def register(server):
    bind_module(globals(), server)
