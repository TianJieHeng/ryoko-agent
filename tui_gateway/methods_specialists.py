"""Transport-owned specialist inspection and canonical submit admission."""
from .method_ctx import HandlerRegistry, bind_module

_registry = HandlerRegistry()
method = _registry.method
_profile_scoped = _registry.profile_scoped


def _specialist_request(rid, params, model, callback):
    from agent.delegation_contract import DelegationError
    from agent.identity_lifecycle import agent_runtime_scope
    from agent.runtime_commands import RuntimeCommandError
    from hermes_state_runtime import RuntimeStoreError
    from tools.capability_broker import CapabilityDenied
    request, error = _runtime_validate(rid, params, model)
    if error:
        return error
    agent, _db, error = _runtime_authority(rid, request.model_dump())
    if error:
        return error
    try:
        with agent_runtime_scope(agent.runtime_context):
            return _ok(rid, callback(agent, request))
    except (DelegationError, RuntimeCommandError, RuntimeStoreError, CapabilityDenied) as exc:
        return _runtime_store_error(rid, exc)
    except (ValueError, TypeError):
        return _err(rid, 4000, "Invalid configured specialist request", {"code": "invalid_specialist"})
    except PermissionError:
        return _err(rid, 4030, "Specialist scope is not authorized", {"code": "specialist_scope_denied"})
    except OSError:
        return _err(rid, 5030, "Specialist source is unavailable", {"code": "specialist_source_unavailable"})


@method("runtime.specialist.catalog")
@_profile_scoped
def _runtime_specialist_catalog(rid, params):
    from agent.specialist_control import specialist_catalog
    from tui_gateway.contracts.specialists import SpecialistProjectParams
    return _specialist_request(rid, params, SpecialistProjectParams,
        lambda agent, request: specialist_catalog(agent, request.project_id))


@method("runtime.specialist.preview")
@_profile_scoped
def _runtime_specialist_preview(rid, params):
    from agent.specialist_control import specialist_preview
    from tui_gateway.contracts.specialists import SpecialistPreviewParams
    return _specialist_request(rid, params, SpecialistPreviewParams,
        lambda agent, request: specialist_preview(agent, request.model_dump(exclude={"session_id", "schema_version"})))


@method("runtime.specialist.handoff")
@_profile_scoped
def _runtime_specialist_handoff(rid, params):
    from agent.runtime_commands import read_command_state
    from agent.specialist_control import validate_specialist_selection
    from tui_gateway.contracts.specialists import SpecialistHandoffParams
    def handoff(agent, request):
        payload = {"text": request.selection.objective, "specialist_handoff": {
            "selection": request.selection.model_dump(), "preview_sha256": request.preview_sha256}}
        # A repeated delivery returns its original receipt even after expiry or
        # mission completion. submit_command still checks byte identity, policy
        # and command ownership; reads never turn an old claim into a launch.
        if read_command_state(agent, request.command_id) is None:
            validate_specialist_selection(agent, payload)
        transport, session = _current_session_steer_authority(request.session_id)
        if transport is None or session is None or session.get("agent") is not agent:
            from agent.runtime_commands import RuntimeCommandError
            raise RuntimeCommandError("identity_mismatch")
        envelope = {"schema_version": 1, "command_id": request.command_id,
                    "idempotency_key": request.idempotency_key, "expected_revision": request.expected_revision,
                    "operation": "submit", "payload": payload}
        return _submit_runtime_prompt(rid, request.session_id, session, agent, envelope)
    return _specialist_request(rid, params, SpecialistHandoffParams, handoff)


@method("runtime.specialist.status")
@_profile_scoped
def _runtime_specialist_status(rid, params):
    from agent.specialist_control import specialist_status
    from tui_gateway.contracts.specialists import SpecialistStatusParams
    return _specialist_request(rid, params, SpecialistStatusParams,
        lambda agent, request: specialist_status(agent, request.command_id))


def register(server):
    bind_module(globals(), server)
