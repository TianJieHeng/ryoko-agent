"""Owned scheduled draft inspection and fresh canonical artifact approval."""
from .method_ctx import HandlerRegistry, bind_module

_registry = HandlerRegistry()
method = _registry.method
_profile_scoped = _registry.profile_scoped


@method("runtime.schedule.output.get")
@_profile_scoped
def _schedule_output_get(rid, params):
    from .contracts.schedule_outputs import ScheduleOutputReadParams
    from agent.identity_lifecycle import agent_runtime_scope
    from hermes_cli.scheduled_workflow_outputs import read_scheduled_output
    def call(agent, db, request):
        with agent_runtime_scope(agent.runtime_context):
            return read_scheduled_output(agent.runtime_context, db,
                **request.model_dump(exclude={"schema_version", "session_id"}))
    return _artifact_request(rid, params, ScheduleOutputReadParams, call)


def _review(rid, params, publish):
    from .contracts.schedule_outputs import ScheduleOutputPrepareParams, ScheduleOutputPublishParams
    from agent.artifact_commands import begin_artifact_control, artifact_control_scope, finish_artifact_control
    from cron.durable_contract import digest
    from hermes_cli.scheduled_workflow_outputs import prepare_scheduled_output, publish_scheduled_output
    def call(agent, db, request):
        data = request.model_dump(exclude={"schema_version", "session_id", "command_id", "approval_id", "approval_digest"})
        run = begin_artifact_control(agent, request.session_id, request.command_id,
            {"mode": "scheduled_draft_review", "request_sha256": digest(data)})
        with artifact_control_scope(run):
            if not publish:
                return prepare_scheduled_output(run, **data).public_record()
            result = publish_scheduled_output(run, **data, approval_id=request.approval_id, approval_digest=request.approval_digest)
            finish_artifact_control(run, result)
            return result
    return _artifact_request(rid, params, ScheduleOutputPublishParams if publish else ScheduleOutputPrepareParams, call)


@method("runtime.schedule.output.prepare")
@_profile_scoped
def _schedule_output_prepare(rid, params):
    return _review(rid, params, False)


@method("runtime.schedule.output.publish")
@_profile_scoped
def _schedule_output_publish(rid, params):
    return _review(rid, params, True)


def register(server):
    bind_module(globals(), server)
