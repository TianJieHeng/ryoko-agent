"""Owned delivery controls reuse the existing artifact command and approval fence."""
from .method_ctx import HandlerRegistry, bind_module

_registry = HandlerRegistry()
method = _registry.method
_profile_scoped = _registry.profile_scoped


def _workflow_delivery_request(rid, params, commit):
    from .contracts.workflow_delivery import WorkflowDeliveryParams, WorkflowDeliveryCommitParams

    def call(agent, db, request):
        from hermes_state_workflow_delivery import WorkflowDeliveryRegistry
        payload = _record_request(request, exclude={"approval_id", "approval_digest"})

        def apply(run):
            registry = WorkflowDeliveryRegistry(run.context, db)
            if commit:
                return {"delivery": registry.commit(run, payload, request.approval_id, request.approval_digest)}
            prepared = registry.prepare(run, payload)
            prepared.pop("binding")
            return prepared

        return _control(agent, request, {"operation": "delivery", **payload}, apply, finish=commit)

    return _artifact_request(rid, params, WorkflowDeliveryCommitParams if commit else WorkflowDeliveryParams, call)


@method("runtime.workflow.delivery.prepare")
@_profile_scoped
def _workflow_delivery_prepare(rid, params):
    return _workflow_delivery_request(rid, params, False)


@method("runtime.workflow.delivery.commit")
@_profile_scoped
def _workflow_delivery_commit(rid, params):
    return _workflow_delivery_request(rid, params, True)


@method("runtime.workflow.delivery.list")
@_profile_scoped
def _workflow_delivery_list(rid, params):
    from .contracts.workflow_delivery import WorkflowDeliveryListParams
    from hermes_state_workflow_delivery import WorkflowDeliveryRegistry
    return _artifact_request(rid, params, WorkflowDeliveryListParams,
        lambda agent, db, request: {"deliveries": WorkflowDeliveryRegistry(agent.runtime_context, db).list(
            request.project_id, request.specialist_id), "complete": True})


def register(server):
    bind_module(globals(), server)
