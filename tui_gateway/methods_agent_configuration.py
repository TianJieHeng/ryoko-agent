"""Owned identity settings; no configuration mutation is model-accessible."""
from .method_ctx import HandlerRegistry, bind_module

_registry = HandlerRegistry()
method = _registry.method
_profile_scoped = _registry.profile_scoped


def _request(rid, params, model, callback):
    from agent.agent_configuration import AgentConfigurationRegistry
    return _artifact_request(rid, params, model,
        lambda agent, db, request: callback(AgentConfigurationRegistry(agent.runtime_context, db,
            management_session_id=request.session_id), request))


@method('runtime.agent.list')
@_profile_scoped
def _agent_list(rid, params):
    from .contracts.runtime_v1 import RuntimeSessionParams
    return _request(rid, params, RuntimeSessionParams, lambda registry, request: {'agents': registry.list(), 'activation': 'next_session'})


@method('runtime.agent.get')
@_profile_scoped
def _agent_get(rid, params):
    from .contracts.agent_configuration import AgentConfigurationParams
    return _request(rid, params, AgentConfigurationParams, lambda registry, request: {'agent': registry.get(request.agent_id)})


def _mutate(rid, params, operation, model):
    return _request(rid, params, model, lambda registry, request: {'agent': registry.mutate(operation,
        **request.model_dump(exclude={'schema_version', 'session_id'}))})


@method('runtime.agent.create')
@_profile_scoped
def _agent_create(rid, params):
    from .contracts.agent_configuration import AgentConfigurationCreateParams
    return _mutate(rid, params, 'create', AgentConfigurationCreateParams)


@method('runtime.agent.update')
@_profile_scoped
def _agent_update(rid, params):
    from .contracts.agent_configuration import AgentConfigurationUpdateParams
    return _mutate(rid, params, 'update', AgentConfigurationUpdateParams)


@method('runtime.agent.archive')
@_profile_scoped
def _agent_archive(rid, params):
    from .contracts.agent_configuration import AgentConfigurationArchiveParams
    return _mutate(rid, params, 'archive', AgentConfigurationArchiveParams)



@method('runtime.agent.session.get')
@_profile_scoped
def _agent_session_get(rid, params):
    from .contracts.runtime_v1 import RuntimeSessionParams
    from agent.agent_configuration_status import session_configuration_status
    return _artifact_request(rid, params, RuntimeSessionParams,
        lambda agent, db, request: session_configuration_status(agent.runtime_context, db))


def register(server):
    bind_module(globals(), server)
