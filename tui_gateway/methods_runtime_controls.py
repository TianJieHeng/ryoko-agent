"""Owned pause/resume controls use the live transport solely to resolve authority."""
from .method_ctx import HandlerRegistry, bind_module
_registry = HandlerRegistry()
method = _registry.method
_profile_scoped = _registry.profile_scoped


def _runtime_control_request(rid, params, operation):
    from hermes_state_runtime import RuntimeStoreError
    from hermes_state_runtime_controls import RuntimeOwnerControls
    from tools.capability_broker import CapabilityDenied
    from agent.identity_lifecycle import agent_runtime_scope
    from tui_gateway.contracts.runtime_controls import RuntimeControlGetParams, RuntimeControlParams
    model = RuntimeControlGetParams if operation == "get" else RuntimeControlParams
    request, error = _runtime_validate(rid, params, model)
    if error:
        return error
    agent, db, error = _runtime_authority(rid, request.model_dump())
    if error:
        return error
    controls = RuntimeOwnerControls(db, _runtime_effect_actor(agent))
    try:
        if operation == "get":
            return _ok(rid, controls.get(request.operation_id))
        with agent_runtime_scope(agent.runtime_context):
            _runtime_effect_policy(agent)
            return _ok(rid, controls.set_paused(operation == "pause", operation_id=request.operation_id,
                                               expected_revision=request.expected_revision))
    except (RuntimeStoreError, CapabilityDenied) as exc:
        return _runtime_store_error(rid, exc)


def _runtime_control_handler(operation):
    def handler(rid, params):
        return _runtime_control_request(rid, params, operation)
    handler.__name__ = "_runtime_control_" + operation
    return handler


for _operation in ("get", "pause", "resume"):
    method("runtime.control." + _operation)(_profile_scoped(_runtime_control_handler(_operation)))


def register(server):
    bind_module(globals(), server)
