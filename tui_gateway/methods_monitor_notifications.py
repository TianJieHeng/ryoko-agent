"""Owned local monitor controls; observations cannot call these human RPCs."""
from .method_ctx import HandlerRegistry, bind_module

_registry = HandlerRegistry()
method = _registry.method
_profile_scoped = _registry.profile_scoped


def _monitor_operation(agent, db, request, name):
    import json
    from agent.artifact_commands import begin_artifact_control, artifact_control_scope, finish_artifact_control
    from agent.identity_lifecycle import agent_runtime_scope
    from cron.durable_contract import canonical, digest
    from hermes_state_monitor_notifications import MonitorNotifications

    registry = MonitorNotifications(agent.runtime_context, db)
    if name == "runtime.monitor.notifications":
        with agent_runtime_scope(agent.runtime_context):
            return {"record_json": canonical(registry.get(request.project_id, request.schedule_id, agent.session_id, cursor=json.loads(request.cursor_json) if request.cursor_json else None))}
    data = request.model_dump(exclude={"session_id", "command_id", "schema_version"})
    run = begin_artifact_control(agent, request.session_id, request.command_id, {"mode": name, "request_sha256": digest(data)})
    writes = {
        "runtime.monitor.policy.set": lambda: registry.set_policy(run, request.project_id, request.schedule_id,
            request.expected_revision, json.loads(request.policy_json)),
        "runtime.monitor.snooze": lambda: registry.snooze(run, request.project_id, request.schedule_id,
            request.expected_revision, request.until_at),
        "runtime.monitor.dismiss": lambda: registry.dismiss(run, request.project_id, request.schedule_id,
            request.expected_revision, request.intent_id),
    }
    with artifact_control_scope(run):
        result = writes[name]()
        finish_artifact_control(run, result)
        return {"record_json": canonical(result)}


def _monitor_handler(name, model):
    @_profile_scoped
    def handler(rid, params):
        return _artifact_request(rid, params, model, lambda agent, db, request: _monitor_operation(agent, db, request, name))
    return handler


from .contracts.monitor_notifications import _SPECS
for _name, (_model, _doc) in _SPECS.items():
    method(_name)(_monitor_handler(_name, _model))


def register(server):
    bind_module(globals(), server)
