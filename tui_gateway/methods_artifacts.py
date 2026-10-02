"""Owned project artifact controls and bounded safe downloads."""
from .method_ctx import HandlerRegistry, bind_module

_registry = HandlerRegistry()
method = _registry.method
_profile_scoped = _registry.profile_scoped


def _artifact_request(rid, params, model, callback):
    from agent.identity_lifecycle import agent_runtime_scope
    from agent.budget_account import BudgetBlocked
    from agent.result_artifacts import ArtifactConflict
    from hermes_state_runtime import RuntimeStoreError
    from tools.capability_broker import CapabilityDenied
    request, error = _runtime_validate(rid, params, model)
    if error:
        return error
    agent, db, error = _runtime_authority(rid, request.model_dump())
    if error:
        return error
    try:
        with agent_runtime_scope(agent.runtime_context):
            return _ok(rid, callback(agent, db, request))
    except (RuntimeStoreError, CapabilityDenied) as exc:
        return _runtime_store_error(rid, exc)
    except BudgetBlocked:
        return _err(rid, 4090, "Artifact operation exceeds its admitted budget", {"code": "budget_blocked"})
    except (ArtifactConflict, OSError):
        return _err(rid, 4090, "Artifact bytes are unavailable or inconsistent", {"code": "artifact_conflict"})
    except ValueError:
        return _err(rid, 4000, "Artifact request conflicts with its validated scope", {"code": "invalid_artifact"})


def _owned_project_call(agent, request, operation, **kwargs):
    from agent import project_context
    control = project_context.project_control(agent, request.session_id)
    return getattr(project_context, operation)(control, **kwargs)


@method("runtime.project.create")
@_profile_scoped
def _runtime_project_create(rid, params):
    from tui_gateway.contracts.artifacts import RuntimeProjectCreateParams
    return _artifact_request(rid, params, RuntimeProjectCreateParams,
        lambda agent, db, request: {"project": _owned_project_call(agent, request,
            "create_owned_project", **request.model_dump(exclude={"session_id", "schema_version"}))})


@method("runtime.project.get")
@_profile_scoped
def _runtime_project_get(rid, params):
    from tui_gateway.contracts.artifacts import RuntimeProjectParams
    return _artifact_request(rid, params, RuntimeProjectParams,
        lambda agent, db, request: {"project": _owned_project_call(agent, request,
            "get_owned_project", project_id=request.project_id)})


@method("runtime.project.list")
@_profile_scoped
def _runtime_project_list(rid, params):
    from tui_gateway.contracts.runtime_v1 import RuntimeSessionParams
    return _artifact_request(rid, params, RuntimeSessionParams,
        lambda agent, db, request: {"projects": _owned_project_call(agent, request, "list_owned_projects")})


@method("runtime.project.claim")
@_profile_scoped
def _runtime_project_claim(rid, params):
    from tui_gateway.contracts.artifacts import RuntimeProjectRevisionParams
    return _artifact_request(rid, params, RuntimeProjectRevisionParams,
        lambda agent, db, request: {"project": _owned_project_call(agent, request,
            "claim_owned_project", project_id=request.project_id, expected_revision=request.expected_revision)})


@method("runtime.project.update")
@_profile_scoped
def _runtime_project_update(rid, params):
    from tui_gateway.contracts.artifacts import RuntimeProjectUpdateParams
    return _artifact_request(rid, params, RuntimeProjectUpdateParams,
        lambda agent, db, request: {"project": _owned_project_call(agent, request,
            "update_owned_project", project_id=request.project_id, expected_revision=request.expected_revision,
            changes=request.changes.model_dump(exclude_unset=True))})


@method("runtime.project.grants.set")
@_profile_scoped
def _runtime_project_grants(rid, params):
    from tui_gateway.contracts.artifacts import RuntimeProjectGrantsParams
    return _artifact_request(rid, params, RuntimeProjectGrantsParams,
        lambda agent, db, request: {"project": _owned_project_call(agent, request,
            "set_project_grants", project_id=request.project_id, expected_revision=request.expected_revision,
            grants=[grant.model_dump() for grant in request.grants])})


def _artifact_prepare_args(request):
    return request.model_dump(exclude={"schema_version", "session_id", "command_id", "approval_id", "approval_digest"})


def _artifact_control_payload(arguments, mode):
    import hashlib
    import json
    # Command identity binds every exact requested byte without copying source
    # bodies into the hot command journal.
    encoded = json.dumps(arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return {"mode": mode, "project_id": arguments["project_id"], "request_id": arguments["request_id"],
            "request_sha256": hashlib.sha256(encoded).hexdigest(), "request_size": len(encoded)}


def _artifact_control_operation(agent, db, request, *, mode, publish=False):
    from agent.artifact_commands import begin_artifact_control, artifact_control_scope, finish_artifact_control
    from agent.result_artifacts import artifact_actor
    from hermes_cli import artifact_store
    from hermes_state_runtime import RuntimeStoreError
    functions = {"write": artifact_store.prepare_markdown, "edit": artifact_store.prepare_markdown_edit,
                 "merge": artifact_store.prepare_markdown_merge}
    arguments = _artifact_prepare_args(request)
    run = begin_artifact_control(agent, request.session_id, request.command_id,
                                 _artifact_control_payload(arguments, mode))
    with artifact_control_scope(run):
        proposal = functions[mode](run, **arguments,
            **({"approval_id": request.approval_id} if publish else {}))
        preview = proposal.public_record()
        if not publish:
            return preview
        if preview["approval_digest"] != request.approval_digest:
            raise RuntimeStoreError("approval_mismatch", "Approval does not match this exact publication")
        actor = artifact_actor(run.context)
        decision = db.get_effect_approval(request.approval_id, actor)
        if decision["status"] == "pending":
            db.resolve_effect_approval(request.approval_id, actor, holder=run.holder,
                generation=run.generation, approval_digest=request.approval_digest, choice="once")
        result = artifact_store.publish_markdown(run, proposal)
        finish_artifact_control(run, result)
        return result


@method("runtime.artifact.prepare")
@_profile_scoped
def _runtime_artifact_prepare(rid, params):
    from tui_gateway.contracts.artifacts import ArtifactPrepareParams
    return _artifact_request(rid, params, ArtifactPrepareParams,
        lambda agent, db, request: _artifact_control_operation(agent, db, request, mode="write"))


@method("runtime.artifact.publish")
@_profile_scoped
def _runtime_artifact_publish(rid, params):
    from tui_gateway.contracts.artifacts import ArtifactPublishParams
    return _artifact_request(rid, params, ArtifactPublishParams,
        lambda agent, db, request: _artifact_control_operation(agent, db, request, mode="write", publish=True))


@method("runtime.artifact.edit.prepare")
@_profile_scoped
def _runtime_artifact_edit_prepare(rid, params):
    from tui_gateway.contracts.artifacts import ArtifactEditParams
    return _artifact_request(rid, params, ArtifactEditParams,
        lambda agent, db, request: _artifact_control_operation(agent, db, request, mode="edit"))


@method("runtime.artifact.edit.publish")
@_profile_scoped
def _runtime_artifact_edit_publish(rid, params):
    from tui_gateway.contracts.artifacts import ArtifactEditPublishParams
    return _artifact_request(rid, params, ArtifactEditPublishParams,
        lambda agent, db, request: _artifact_control_operation(agent, db, request, mode="edit", publish=True))


@method("runtime.artifact.merge.prepare")
@_profile_scoped
def _runtime_artifact_merge_prepare(rid, params):
    from tui_gateway.contracts.artifacts import ArtifactMergeParams
    return _artifact_request(rid, params, ArtifactMergeParams,
        lambda agent, db, request: _artifact_control_operation(agent, db, request, mode="merge"))


@method("runtime.artifact.merge.publish")
@_profile_scoped
def _runtime_artifact_merge_publish(rid, params):
    from tui_gateway.contracts.artifacts import ArtifactMergePublishParams
    return _artifact_request(rid, params, ArtifactMergePublishParams,
        lambda agent, db, request: _artifact_control_operation(agent, db, request, mode="merge", publish=True))


@method("runtime.artifact.get")
@_profile_scoped
def _runtime_artifact_get(rid, params):
    from hermes_cli.artifact_store import read_artifact
    from tui_gateway.contracts.artifacts import ArtifactReadParams
    return _artifact_request(rid, params, ArtifactReadParams,
        lambda agent, db, request: read_artifact(agent.runtime_context, db, request.project_id,
            request.artifact_id, request.version, offset=request.offset, limit=request.limit))


@method("runtime.artifact.status")
@_profile_scoped
def _runtime_artifact_status(rid, params):
    from agent.artifact_commands import artifact_control_status
    from tui_gateway.contracts.artifacts import ArtifactCommandParams
    return _artifact_request(rid, params, ArtifactCommandParams,
        lambda agent, db, request: artifact_control_status(agent, request.session_id, request.command_id))


@method("runtime.artifact.cancel")
@_profile_scoped
def _runtime_artifact_cancel(rid, params):
    from agent.artifact_commands import cancel_artifact_control
    from tui_gateway.contracts.artifacts import ArtifactCommandParams
    return _artifact_request(rid, params, ArtifactCommandParams,
        lambda agent, db, request: cancel_artifact_control(agent, request.session_id, request.command_id))


@method("runtime.artifact.recovery.get")
@_profile_scoped
def _runtime_artifact_recovery_get(rid, params):
    from hermes_cli.artifact_store import read_artifact_recovery
    from tui_gateway.contracts.artifacts import ArtifactRecoveryParams
    return _artifact_request(rid, params, ArtifactRecoveryParams,
        lambda agent, db, request: read_artifact_recovery(agent.runtime_context, db, request.project_id,
            request.effect_id, offset=request.offset, limit=request.limit))


def register(server):
    bind_module(globals(), server)
