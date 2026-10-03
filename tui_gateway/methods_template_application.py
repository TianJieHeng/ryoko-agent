"""Human-owned template application uses existing artifact effects and approvals."""
from .method_ctx import HandlerRegistry, bind_module

_registry = HandlerRegistry()
method = _registry.method
_profile_scoped = _registry.profile_scoped


@method("runtime.template.preview")
@_profile_scoped
def _template_preview(rid, params):
    from .contracts.template_application import TemplatePreviewParams
    from hermes_cli.template_application import preview_template
    return _artifact_request(rid, params, TemplatePreviewParams,
        lambda agent, db, request: preview_template(agent.runtime_context, db,
            **request.model_dump(exclude={"schema_version", "session_id"})))


def _apply_template(agent, db, request, publish=False):
    from agent.artifact_commands import begin_artifact_control, artifact_control_scope, finish_artifact_control
    from agent.result_artifacts import artifact_actor
    from hermes_cli.template_application import prepare_template
    from hermes_cli.artifact_store import publish_artifact
    from hermes_state_runtime import RuntimeStoreError
    arguments = request.model_dump(exclude={"schema_version", "session_id", "command_id", "approval_id", "approval_digest"})
    run = begin_artifact_control(agent, request.session_id, request.command_id,
                                 _artifact_control_payload(arguments, "template"))
    with artifact_control_scope(run):
        proposal, preview = prepare_template(run, **arguments,
            **({"approval_id": request.approval_id} if publish else {}))
        if not publish:
            return {"proposal": proposal.public_record(), "preview": preview}
        if proposal.approval_digest != request.approval_digest:
            raise RuntimeStoreError("approval_mismatch", "Approval differs from the exact template application")
        actor = artifact_actor(run.context)
        decision = db.get_effect_approval(request.approval_id, actor)
        if decision["status"] == "pending":
            db.resolve_effect_approval(request.approval_id, actor, holder=run.holder, generation=run.generation,
                approval_digest=request.approval_digest, choice="once")
        result = publish_artifact(run, proposal)
        finish_artifact_control(run, result)
        return result


@method("runtime.template.prepare")
@_profile_scoped
def _template_prepare(rid, params):
    from .contracts.template_application import TemplatePrepareParams
    return _artifact_request(rid, params, TemplatePrepareParams, _apply_template)


@method("runtime.template.publish")
@_profile_scoped
def _template_publish(rid, params):
    from .contracts.template_application import TemplatePublishParams
    return _artifact_request(rid, params, TemplatePublishParams,
        lambda agent, db, request: _apply_template(agent, db, request, publish=True))


def register(server):
    bind_module(globals(), server)
