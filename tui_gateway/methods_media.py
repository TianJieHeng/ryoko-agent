"""Thin owned consumers of bounded services and the existing runtime command queue."""
from .method_ctx import HandlerRegistry, bind_module

_registry = HandlerRegistry()
method = _registry.method
_profile_scoped = _registry.profile_scoped


def _media_request(rid, params, model, callback):
    from agent.identity_lifecycle import agent_runtime_scope
    from agent.runtime_commands import RuntimeCommandError, RuntimeFenceError
    from agent.budget_account import BudgetBlocked
    from agent.task_scope import TaskCancelled
    from agent.delegation_contract import DelegationError
    from hermes_state_runtime import RuntimeStoreError
    from tools.capability_broker import CapabilityDenied
    from agent.bounded_services import canonical
    request, error = _runtime_validate(rid, params, model)
    if error:
        return error
    agent, db, error = _runtime_authority(rid, request.model_dump())
    if error:
        return error
    try:
        context = agent.runtime_context
        with agent_runtime_scope(context):
            response = callback(agent, db, request)
            # A completed model call cannot disclose private media to a retired
            # transport after reconnect, identity rotation or session replacement.
            current, current_db, error = _runtime_authority(rid, request.model_dump())
            if error:
                return error
            if current is not agent or current_db is not db or current.runtime_context is not context:
                return _err(rid, 4090, "Media owner changed", {"code": "stale_owner"})
            return _ok(rid, {"response_json": canonical(response)})
    except (RuntimeStoreError, RuntimeCommandError, DelegationError, CapabilityDenied) as exc:
        return _runtime_store_error(rid, exc)
    except TaskCancelled:
        return _err(rid, 4090, "Speech operation cancelled", {"code": "speech_cancelled"})
    except BudgetBlocked:
        return _err(rid, 4090, "Speech budget cannot dispatch", {"code": "speech_budget_blocked"})
    except RuntimeFenceError:
        return _err(rid, 4090, "Media command owner is no longer current", {"code": "stale_owner"})
    except (ValueError, OSError):
        return _err(rid, 4000, "Invalid bounded media input", {"code": "invalid_media"})


def _media_services(agent, db):
    from agent.bounded_services import BoundedServices
    return BoundedServices(agent.runtime_context, db)


def _media_local_state(agent):
    from agent.media_ingress import ScreenIngress
    from agent.speech_local import configured_voice_ingress
    # The existing session lock serializes creation, not adapter processing.
    with _sessions_lock:
        if not hasattr(agent, "_bounded_voice_ingress"):
            agent._bounded_voice_ingress = configured_voice_ingress(agent)
        if not hasattr(agent, "_bounded_screen_ingress"):
            agent._bounded_screen_ingress = ScreenIngress()
        return agent._bounded_voice_ingress, agent._bounded_screen_ingress


def _media_voice_ready(agent):
    from agent.speech_local import require_speech_budget
    policy = require_speech_budget(agent)
    voice = _media_local_state(agent)[0]
    if policy is not None:
        from agent.bounded_services import require
        from agent.speech_local import LocalSTT, LocalTTS
        require(getattr(voice, "budget_agent", None) is agent
                and (voice.stt is None or type(voice.stt) is LocalSTT)
                and (voice.tts is None or type(voice.tts) is LocalTTS), "speech_budget_unsupported")
    return voice


def _media_voice_capabilities(agent):
    from agent.speech_local import speech_budget_blocked
    from agent.media_ingress import VoiceIngress
    if speech_budget_blocked(agent):
        return VoiceIngress(unsupported_reasons={kind: "speech_budget_unsupported" for kind in ("stt", "tts")}).capabilities()
    result = _media_local_state(agent)[0].capabilities()
    from agent.budget_account import budget_enabled
    result["budget"] = {"mode": "existing_run_tree" if budget_enabled(agent) else "not_configured",
        "control_fields_required": ["request_id", "budget_account_id"] if budget_enabled(agent) else [],
        "first_turn_without_account": "explicit_admission_required" if budget_enabled(agent) else "allowed",
        "admission_rpc": "runtime.voice.admit" if budget_enabled(agent) else None,
        "replay": "reject_consumed_request_no_audio_replay", "receipt_rpc": None}
    return result


def _media_voice_owner_check(agent, request):
    owner, context, db = _caller_transport(), agent.runtime_context, agent._session_db
    def check():
        transport, session = _current_session_steer_authority(request.session_id)
        return (transport is owner and session is not None and session.get("agent") is agent
                and agent.runtime_context is context and agent._session_db is db)
    return check


def _media_submit_command(rid, agent, request, operation, payload):
    from agent.channel_handoff import ChannelHandoff
    from agent.runtime_commands import submit_command
    from agent.bounded_services import require
    envelope = ChannelHandoff(agent).envelope(request.binding_id, request.input_id,
        operation, payload, request.expected_revision)
    transport, session = _current_session_steer_authority(request.session_id)
    require(transport is not None and session is not None and session.get("agent") is agent, "identity_mismatch")
    return (_submit_runtime_prompt(rid, request.session_id, session, agent, envelope)
            if operation == "submit" else submit_command(agent, envelope))


@method("runtime.services.capabilities")
@_profile_scoped
def _media_service_capabilities(rid, params):
    from tui_gateway.contracts.runtime_v1 import RuntimeSessionParams
    return _media_request(rid, params, RuntimeSessionParams,
        lambda agent, db, request: {"services": _media_services(agent, db).capabilities()})


@method("runtime.services.prepare")
@_profile_scoped
def _media_service_prepare(rid, params):
    from tui_gateway.contracts.media import ServicePrepareParams
    return _media_request(rid, params, ServicePrepareParams,
        lambda agent, db, request: _media_services(agent, db).prepare(agent.session_id, request.project_id,
            request.request_id, request.artifact_id, request.version))


@method("runtime.services.execute")
@_profile_scoped
def _media_service_execute(rid, params):
    from tui_gateway.contracts.media import ServiceExecuteParams
    return _media_request(rid, params, ServiceExecuteParams,
        lambda agent, db, request: _media_services(agent, db).execute(request.pipeline_id, request.manifest_sha256))


@method("runtime.services.status")
@_profile_scoped
def _media_service_status(rid, params):
    from tui_gateway.contracts.media import ServicePipelineParams
    return _media_request(rid, params, ServicePipelineParams,
        lambda agent, db, request: _media_services(agent, db).status(request.pipeline_id))


@method("runtime.services.output")
@_profile_scoped
def _media_service_output(rid, params):
    from tui_gateway.contracts.media import ServicePipelineParams
    return _media_request(rid, params, ServicePipelineParams,
        lambda agent, db, request: _media_services(agent, db).output(request.pipeline_id))


@method("runtime.media.capabilities")
@_profile_scoped
def _media_capabilities(rid, params):
    from tui_gateway.contracts.runtime_v1 import RuntimeSessionParams
    from agent.media_ingress import FRAME_MAX_AGE, MAX_FRAME_BYTES, MAX_FRAME_PIXELS
    return _media_request(rid, params, RuntimeSessionParams,
        lambda agent, db, request: {"voice": _media_voice_capabilities(agent),
            "screen": {"capture": "explicit_client_selected_window_png", "inspection": "dimensions_digest_regions",
                "freshness": "server_receipt_age_client_acquisition_unverified", "max_age_seconds": FRAME_MAX_AGE,
                "max_bytes": MAX_FRAME_BYTES, "max_pixels": MAX_FRAME_PIXELS, "ocr": False,
                "act_workflow": "confirmed_selected_text_to_task", "os_actions": False},
            "channels": ["local_jsonrpc", "voice", "screen"], "external_channel_adapters": "unconfigured"})


@method("runtime.voice.admit")
@_profile_scoped
def _media_voice_admit(rid, params):
    from agent.speech_control import admit_voice_control
    from tui_gateway.contracts.media import VoiceAdmissionParams
    return _media_request(rid, params, VoiceAdmissionParams,
        lambda agent, db, request: admit_voice_control(agent, request.session_id,
            request.request_id, _media_voice_owner_check(agent, request)))


@method("runtime.voice.capture.start")
@_profile_scoped
def _media_voice_start(rid, params):
    from tui_gateway.contracts.media import VoiceCaptureStartParams
    return _media_request(rid, params, VoiceCaptureStartParams,
        lambda agent, db, request: _media_voice_ready(agent).begin(_caller_transport(),
            request_id=request.request_id, budget_account_id=request.budget_account_id,
            owner_check=_media_voice_owner_check(agent, request)))


@method("runtime.voice.capture.feed")
@_profile_scoped
def _media_voice_feed(rid, params):
    import base64
    from tui_gateway.contracts.media import VoiceFeedParams
    return _media_request(rid, params, VoiceFeedParams,
        lambda agent, db, request: _media_voice_ready(agent).feed(_caller_transport(), request.capture_id,
            request.sequence, base64.b64decode(request.pcm_base64, validate=True), final=request.final))


@method("runtime.voice.capture.cancel")
@_profile_scoped
def _media_voice_capture_cancel(rid, params):
    from tui_gateway.contracts.runtime_v1 import RuntimeSessionParams
    return _media_request(rid, params, RuntimeSessionParams,
        lambda agent, db, request: _media_local_state(agent)[0].cancel_capture(_caller_transport()))


@method("runtime.voice.speak")
@_profile_scoped
def _media_voice_speak(rid, params):
    from tui_gateway.contracts.media import VoiceSpeakParams
    return _media_request(rid, params, VoiceSpeakParams,
        lambda agent, db, request: _media_voice_ready(agent).speak(_caller_transport(), request.text,
            request_id=request.request_id, budget_account_id=request.budget_account_id,
            owner_check=_media_voice_owner_check(agent, request)))


@method("runtime.voice.stop")
@_profile_scoped
def _media_voice_stop(rid, params):
    from tui_gateway.contracts.runtime_v1 import RuntimeSessionParams
    return _media_request(rid, params, RuntimeSessionParams,
        lambda agent, db, request: _media_local_state(agent)[0].stop_speech(_caller_transport()))


def _media_confirmed_voice(rid, agent, request):
    from agent.bounded_services import require
    require(request.text == request.confirmed_text and request.text.strip(), "media_confirmation_required")
    return _media_submit_command(rid, agent, request, "submit", {"text": request.text})


@method("runtime.voice.submit")
@_profile_scoped
def _media_voice_submit(rid, params):
    from tui_gateway.contracts.media import VoiceSubmitParams
    return _media_request(rid, params, VoiceSubmitParams,
        lambda agent, db, request: _media_confirmed_voice(rid, agent, request))


@method("runtime.screen.capture")
@_profile_scoped
def _media_screen_capture(rid, params):
    import base64
    from tui_gateway.contracts.media import ScreenCaptureParams
    return _media_request(rid, params, ScreenCaptureParams,
        lambda agent, db, request: _media_local_state(agent)[1].capture(_caller_transport(),
            base64.b64decode(request.png_base64, validate=True), request.scope, request.window_ref))


@method("runtime.screen.inspect")
@_profile_scoped
def _media_screen_inspect(rid, params):
    from tui_gateway.contracts.media import ScreenFrameParams
    return _media_request(rid, params, ScreenFrameParams,
        lambda agent, db, request: _media_local_state(agent)[1].inspect(_caller_transport(), request.frame_id))


@method("runtime.screen.annotate")
@_profile_scoped
def _media_screen_annotate(rid, params):
    from tui_gateway.contracts.media import ScreenAnnotateParams
    return _media_request(rid, params, ScreenAnnotateParams,
        lambda agent, db, request: _media_local_state(agent)[1].annotate(_caller_transport(),
            request.frame_id, request.region, request.label))


def _media_confirmed_screen(rid, agent, request):
    screen = _media_local_state(agent)[1]
    # A newer frame cannot replace the verified region between confirmation
    # and durable acceptance. This does not hold the lock during execution.
    with screen.lock:
        text = screen.confirm(_caller_transport(), request.frame_id,
            request.region, request.text, request.confirmed_text)
        return _media_submit_command(rid, agent, request, "submit", {"text": text})


@method("runtime.screen.submit")
@_profile_scoped
def _media_screen_submit(rid, params):
    from tui_gateway.contracts.media import ScreenSubmitParams
    return _media_request(rid, params, ScreenSubmitParams,
        lambda agent, db, request: _media_confirmed_screen(rid, agent, request))


@method("runtime.channel.bind")
@_profile_scoped
def _media_channel_bind(rid, params):
    from agent.channel_handoff import ChannelHandoff
    from tui_gateway.contracts.media import ChannelBindParams
    return _media_request(rid, params, ChannelBindParams,
        lambda agent, db, request: ChannelHandoff(agent).bind(request.channel))


@method("runtime.channel.submit")
@_profile_scoped
def _media_channel_submit(rid, params):
    from tui_gateway.contracts.media import ChannelSubmitParams
    return _media_request(rid, params, ChannelSubmitParams,
        lambda agent, db, request: _media_submit_command(rid, agent, request,
            request.operation, request.payload.model_dump()))


def register(server):
    bind_module(globals(), server)
