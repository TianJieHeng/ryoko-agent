"""Explicit owned BE11 workflow APIs. No model-accessible approval entry point."""
from .method_ctx import HandlerRegistry, bind_module

_registry = HandlerRegistry()
method = _registry.method
_profile_scoped = _registry.profile_scoped


def _control(agent, request, payload, callback, *, finish=True):
    from agent.artifact_commands import begin_artifact_control, artifact_control_scope, finish_artifact_control
    from hermes_state_workflows import digest, canonical
    run = begin_artifact_control(agent, request.session_id, request.command_id,
        {"mode": "workflow", "request_sha256": digest(payload), "request_size": len(canonical(payload).encode())})
    with artifact_control_scope(run):
        result = callback(run)
        if finish:
            # Large canonical content stays in its dedicated workflow record;
            # command history keeps just bounded operation outcome metadata.
            finish_artifact_control(run, {"project_id": result.get("workflow", {}).get("project_id") or payload.get("project_id"),
                "response_json": canonical({"workflow_operation": payload["operation"],
                    "workflow_run_id": result.get("workflow_run_id"), "state": result.get("state", "completed")})})
        return result


def _record_request(request, *, exclude=()):
    return request.model_dump(exclude={"schema_version", "session_id", "command_id", *exclude})


def _version(registry, request):
    return registry.get(request.project_id, request.workflow_id, request.version)


@method('runtime.workflow.create')
@_profile_scoped
def _workflow_create(rid, params):
    from .contracts.workflows import WorkflowCreateParams
    def call(agent, db, request):
        import json
        from agent.workflow_contract import WorkflowVersion
        from hermes_state_workflows import WorkflowRegistry
        definition = WorkflowVersion.from_record(json.loads(request.definition_json))
        return _control(agent, request, {"operation": "create", "project_id": definition.project_id, "definition": definition.to_record()},
            lambda run: {"workflow": WorkflowRegistry(run.context, db).create(run, definition)})
    return _artifact_request(rid, params, WorkflowCreateParams, call)


@method('runtime.workflow.template.create')
@_profile_scoped
def _workflow_template_create(rid, params):
    from .contracts.workflows import WorkflowCreateParams
    def call(agent, db, request):
        import json
        from agent.workflow_contract import TemplateVersion
        from hermes_state_workflows import WorkflowRegistry
        template = TemplateVersion.from_record(json.loads(request.definition_json))
        return _control(agent, request, {"operation": "template", "project_id": template.project_id, "definition": template.to_record()},
            lambda run: WorkflowRegistry(run.context, db).create_template(run, template))
    return _artifact_request(rid, params, WorkflowCreateParams, call)


@method('runtime.workflow.get')
@_profile_scoped
def _workflow_get(rid, params):
    from .contracts.workflows import WorkflowVersionParams
    from hermes_state_workflows import WorkflowRegistry
    return _artifact_request(rid, params, WorkflowVersionParams,
        lambda agent, db, request: {"workflow": _version(WorkflowRegistry(agent.runtime_context, db), request)})


@method('runtime.workflow.list')
@_profile_scoped
def _workflow_list(rid, params):
    from .contracts.workflows import WorkflowProjectParams
    from hermes_state_workflows import WorkflowRegistry
    return _artifact_request(rid, params, WorkflowProjectParams,
        lambda agent, db, request: {"workflows": WorkflowRegistry(agent.runtime_context, db).list(request.project_id), "complete": False})


@method('runtime.workflow.evaluate')
@_profile_scoped
def _workflow_evaluate(rid, params):
    from .contracts.workflows import WorkflowEvaluateParams
    def call(agent, db, request):
        import json
        from agent.workflow_runtime import evaluate_workflow
        from hermes_state_workflows import WorkflowRegistry, require
        row = _version(WorkflowRegistry(agent.runtime_context, db), request)
        require(row['revision'] == request.expected_revision, 'workflow_revision_conflict', 'Evaluation revision changed')
        cases = json.loads(request.cases_json)
        return _control(agent, request, {"operation": "evaluate", **_record_request(request)},
            lambda run: evaluate_workflow(run, row, cases))
    return _artifact_request(rid, params, WorkflowEvaluateParams, call)


def _workflow_decision(rid, params, commit):
    from .contracts.workflows import WorkflowDecisionParams, WorkflowDecisionCommitParams
    def call(agent, db, request):
        from hermes_cli.workflows import prepare_decision, commit_decision
        payload = _record_request(request, exclude={"approval_id", "approval_digest"})
        def apply(run):
            if commit:
                return commit_decision(run, payload, request.approval_id, request.approval_digest)
            prepared = prepare_decision(run, payload)
            prepared.pop('binding')
            return prepared
        return _control(agent, request, {"operation": "decision", **payload}, apply, finish=commit)
    return _artifact_request(rid, params, WorkflowDecisionCommitParams if commit else WorkflowDecisionParams, call)


@method('runtime.workflow.decision.prepare')
@_profile_scoped
def _workflow_decision_prepare(rid, params):
    return _workflow_decision(rid, params, False)


@method('runtime.workflow.decision.commit')
@_profile_scoped
def _workflow_decision_commit(rid, params):
    return _workflow_decision(rid, params, True)


@method('runtime.workflow.export')
@_profile_scoped
def _workflow_export(rid, params):
    from .contracts.workflows import WorkflowExportParams
    from hermes_cli.workflows import export_workflow
    return _artifact_request(rid, params, WorkflowExportParams,
        lambda agent, db, request: export_workflow(agent.runtime_context, db, **_record_request(request)))


@method('runtime.workflow.feedback')
@_profile_scoped
def _workflow_feedback(rid, params):
    from .contracts.workflows import WorkflowFeedbackParams
    def call(agent, db, request):
        import json
        import uuid
        from hermes_state_workflows import WorkflowRegistry, require, canonical
        from hermes_cli.domain_media import _read
        registry = WorkflowRegistry(agent.runtime_context, db)
        row, evidence = _version(registry, request), json.loads(request.evidence_json)
        require(isinstance(evidence, dict) and set(evidence) == {"kind", "artifact_ref"}
                and evidence["kind"] in {"correction", "failure"}, "workflow_feedback_invalid", "Correction or failure requires an exact artifact reference")
        _read(agent.runtime_context, db, request.project_id, evidence["artifact_ref"])
        evidence = {**evidence, "evidence_id": "workflow-evidence-" + uuid.uuid4().hex,
                    "workflow_sha256": row["sha256"], "training_performed": False}
        return _control(agent, request, {"operation": "feedback", **_record_request(request)},
            lambda run: {"evidence_json": canonical(registry.save_feedback(run, row, evidence))})
    return _artifact_request(rid, params, WorkflowFeedbackParams, call)


def _workflow_run(rid, params, publish):
    from .contracts.workflows import WorkflowRunParams, WorkflowRunPublishParams
    def call(agent, db, request):
        import json
        from hermes_cli.workflows import prepare_workflow_run, publish_workflow_run
        from hermes_state_workflows import canonical
        payload = _record_request(request, exclude={"approvals", "parameters_json"})
        payload['parameters'] = json.loads(request.parameters_json)
        def apply(run):
            if publish:
                return publish_workflow_run(run, payload, [item.model_dump() for item in request.approvals])
            saved, proposals = prepare_workflow_run(run, payload)
            return {"workflow_run_id": saved["workflow_run_id"], "pin_json": canonical(saved["pin"]),
                    "proposals": [proposal.public_record() for proposal in proposals], "publication_atomic": False}
        return _control(agent, request, {"operation": "run", **payload}, apply, finish=publish)
    return _artifact_request(rid, params, WorkflowRunPublishParams if publish else WorkflowRunParams, call)


@method('runtime.workflow.run.prepare')
@_profile_scoped
def _workflow_run_prepare(rid, params):
    return _workflow_run(rid, params, False)


@method('runtime.workflow.run.publish')
@_profile_scoped
def _workflow_run_publish(rid, params):
    return _workflow_run(rid, params, True)


@method('runtime.workflow.runs')
@_profile_scoped
def _workflow_runs(rid, params):
    from .contracts.workflows import WorkflowVersionParams
    from hermes_cli.workflows import run_history
    from hermes_state_workflows import canonical
    return _artifact_request(rid, params, WorkflowVersionParams,
        lambda agent, db, request: {"runs_json": canonical(run_history(agent.runtime_context, db,
            request.project_id, request.workflow_id, request.version)), "complete": False})


def register(server):
    bind_module(globals(), server)
    from . import methods_workflow_delivery
    methods_workflow_delivery.register(server)
