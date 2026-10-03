"""Owned RPCs for precise output context references and future-boundary controls."""
from .method_ctx import HandlerRegistry, bind_module

_registry = HandlerRegistry()
method = _registry.method
_profile_scoped = _registry.profile_scoped


def _output_influence_operation(agent, request, operation):
    from agent import output_influences
    from hermes_state_runtime import RuntimeStoreError
    from tools.individual_memory_store import IndividualMemoryError
    try:
        return getattr(output_influences, operation)(agent,
            **request.model_dump(exclude={"session_id", "schema_version"}))
    except IndividualMemoryError as exc:
        raise RuntimeStoreError(exc.code, "Owned memory control was rejected") from exc
    except PermissionError as exc:
        raise RuntimeStoreError("memory_owner_denied", "Memory ownership or grants changed") from exc


def _output_influence_handler(model_name, operation):
    @_profile_scoped
    def handler(rid, params):
        from tui_gateway.contracts import output_influences as contracts
        return _artifact_request(rid, params, getattr(contracts, model_name),
            lambda agent, db, request: _output_influence_operation(agent, request, operation))
    return handler


_OUTPUT_INFLUENCE_METHODS = {
    "runtime.memory.output.list": ("RuntimeSessionParams", "inspect_output"),
    "runtime.memory.output.get": ("OutputContextParams", "inspect_output"),
    "runtime.memory.output.control": ("OutputControlParams", "control_output"),
    "runtime.memory.output.control.get": ("OutputControlGetParams", "inspect_control"),
}
for _output_method, _output_spec in _OUTPUT_INFLUENCE_METHODS.items():
    method(_output_method)(_output_influence_handler(*_output_spec))
del _output_method, _output_spec


def register(server):
    bind_module(globals(), server)
