"""User-triggered review controls; no model route, scheduling or action dispatch."""
from .method_ctx import HandlerRegistry, bind_module

_registry = HandlerRegistry()
method = _registry.method
_profile_scoped = _registry.profile_scoped


def _opportunity_call(agent, db, request, operation):
    from agent.project_opportunities import opportunity_control
    from hermes_state_opportunities import OpportunityRegistry
    registry = OpportunityRegistry(agent.runtime_context, db)
    arguments = request.model_dump(exclude={"schema_version", "session_id"})
    controls = [opportunity_control(agent, db, request.session_id, "runtime.opportunity." + operation)] if operation in {"discover", "disposition"} else []
    return getattr(registry, operation)(*controls, **arguments)


def _opportunity_handler(model, operation):
    @_profile_scoped
    def handle(rid, params):
        from .contracts import opportunities
        return _artifact_request(rid, params, getattr(opportunities, model),
            lambda agent, db, request: _opportunity_call(agent, db, request, operation))
    return handle


for _name, _model in {"discover": "OpportunityDiscoverParams", "list": "OpportunityListParams",
                      "disposition": "OpportunityDispositionParams", "history": "OpportunityHistoryParams"}.items():
    method("runtime.opportunity." + _name)(_opportunity_handler(_model, _name))
del _name, _model


def register(server):
    bind_module(globals(), server)
