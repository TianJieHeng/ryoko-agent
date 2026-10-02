"""Authenticated result retrieval and delivery controls."""
from .method_ctx import HandlerRegistry, bind_module

_registry = HandlerRegistry()
method = _registry.method
_profile_scoped = _registry.profile_scoped


def _runtime_result_call(rid, params, model, operation):
    from agent.result_artifacts import ArtifactConflict
    from hermes_state_runtime import RuntimeStoreError
    from tools.capability_broker import CapabilityDenied

    request, error = _runtime_validate(rid, params, model)
    if error:
        return error
    agent, _db, error = _runtime_authority(rid, request.model_dump())
    if error:
        return error
    try:
        return _ok(rid, operation(agent, request))
    except RuntimeStoreError as exc:
        return _runtime_store_error(rid, exc)
    except CapabilityDenied as exc:
        return _err(rid, 4030, "Runtime request rejected: " + exc.code, {"code": exc.code})
    except (ArtifactConflict, OSError):
        return _err(rid, 4090, "Runtime artifact unavailable or inconsistent",
                    {"code": "artifact_unavailable"})


@method("runtime.result.get")
@_profile_scoped
def _runtime_result_get(rid, params):
    from gateway.durable_outbox import read_result
    from tui_gateway.contracts.runtime_results import RuntimeResultGetParams
    return _runtime_result_call(rid, params, RuntimeResultGetParams,
        lambda agent, request: read_result(agent, request.command_id, request.offset, request.limit))


@method("runtime.delivery.status")
@_profile_scoped
def _runtime_delivery_status(rid, params):
    from gateway.durable_outbox import delivery_status
    from tui_gateway.contracts.runtime_results import RuntimeDeliveryParams
    return _runtime_result_call(rid, params, RuntimeDeliveryParams,
        lambda agent, request: delivery_status(agent, request.delivery_id))


@method("runtime.delivery.retry")
@_profile_scoped
def _runtime_delivery_retry(rid, params):
    from gateway.durable_outbox import retry_delivery
    from tui_gateway.contracts.runtime_results import RuntimeDeliveryParams

    def retry(agent, request):
        transport, _session = _current_session_steer_authority(request.session_id)
        return retry_delivery(agent, request.delivery_id, request.session_id, transport)
    return _runtime_result_call(rid, params, RuntimeDeliveryParams, retry)


@method("runtime.delivery.ack")
@_profile_scoped
def _runtime_delivery_ack(rid, params):
    from gateway.durable_outbox import acknowledge_delivery
    from tui_gateway.contracts.runtime_results import RuntimeDeliveryAckParams
    return _runtime_result_call(rid, params, RuntimeDeliveryAckParams,
        lambda agent, request: acknowledge_delivery(agent, request.delivery_id,
            request.attempt_token, request.sha256, text_received=request.text_received,
            artifact_received=request.artifact_received))


def register(server):
    bind_module(globals(), server)
