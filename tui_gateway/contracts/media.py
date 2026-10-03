"""Owned BE13 finite service/media ingress; no caller-supplied identity or history."""
from typing import Annotated, Literal

from pydantic import Field, StrictInt, model_validator

from .base import Result
from .registry import method
from .runtime_results import Digest
from .runtime_v1 import RuntimeIdentifier, RuntimeSessionParams, RuntimeTextPayload, RuntimeCancelPayload


class MediaResponse(Result):
    # These local adapter records are versioned separately from the RPC envelope,
    # like the existing research/workflow projections. No executable code is data.
    response_json: Annotated[str, Field(min_length=2, max_length=2 * 1024 * 1024)]


class ServicePrepareParams(RuntimeSessionParams):
    project_id: RuntimeIdentifier
    request_id: RuntimeIdentifier
    artifact_id: RuntimeIdentifier
    version: Annotated[StrictInt, Field(ge=1)]


class ServicePipelineParams(RuntimeSessionParams):
    pipeline_id: RuntimeIdentifier


class ServiceExecuteParams(ServicePipelineParams):
    manifest_sha256: Digest


class ChannelBindParams(RuntimeSessionParams):
    channel: Literal["local_jsonrpc", "voice", "screen"]


class ChannelSubmitParams(RuntimeSessionParams):
    binding_id: RuntimeIdentifier
    input_id: RuntimeIdentifier
    expected_revision: Annotated[StrictInt, Field(ge=0)] | None = None
    operation: Literal["submit", "steer", "cancel"]
    payload: RuntimeTextPayload | RuntimeCancelPayload

    @model_validator(mode="before")
    @classmethod
    def choose_payload(cls, values):
        if isinstance(values, dict):
            model = RuntimeCancelPayload if values.get("operation") == "cancel" else RuntimeTextPayload
            values = {**values, "payload": model.model_validate(values.get("payload"))}
        return values


class VoiceFeedParams(RuntimeSessionParams):
    capture_id: RuntimeIdentifier
    sequence: Annotated[StrictInt, Field(ge=0)]
    pcm_base64: Annotated[str, Field(max_length=85336)]
    final: bool = False


class VoiceSpeakParams(RuntimeSessionParams):
    text: Annotated[str, Field(min_length=1, max_length=65536)]


class VoiceSubmitParams(RuntimeSessionParams):
    binding_id: RuntimeIdentifier
    input_id: RuntimeIdentifier
    expected_revision: Annotated[StrictInt, Field(ge=0)] | None = None
    text: Annotated[str, Field(min_length=1, max_length=65536)]
    confirmed_text: Annotated[str, Field(min_length=1, max_length=65536)]


class ScreenCaptureParams(RuntimeSessionParams):
    png_base64: Annotated[str, Field(min_length=1, max_length=1398104)]
    scope: Literal["selected_window"]
    window_ref: RuntimeIdentifier


class ScreenFrameParams(RuntimeSessionParams):
    frame_id: RuntimeIdentifier


class ScreenAnnotateParams(ScreenFrameParams):
    region: Annotated[list[StrictInt], Field(min_length=4, max_length=4)]
    label: Annotated[str, Field(max_length=512)] = ""


class ScreenSubmitParams(VoiceSubmitParams):
    frame_id: RuntimeIdentifier
    region: Annotated[list[StrictInt], Field(min_length=4, max_length=4)]


for name, params, doc in (
    ("runtime.services.capabilities", RuntimeSessionParams, "Two authenticated finite local document services; no remote shell."),
    ("runtime.services.prepare", ServicePrepareParams, "Disclose exact source digest, bytes, location, route and fresh executor before transfer."),
    ("runtime.services.execute", ServiceExecuteParams, "Run or recover only uncommitted pure local stages under the exact preview digest."),
    ("runtime.services.status", ServicePipelineParams, "Read committed per-stage digest/transfer receipts, without replay."),
    ("runtime.services.output", ServicePipelineParams, "Read digest-verified private staged output; publication needs the artifact approval path."),
    ("runtime.media.capabilities", RuntimeSessionParams, "Report unconfigured speech and the bounded selected-frame workflow honestly."),
    ("runtime.voice.capture.start", RuntimeSessionParams, "Explicit bounded client-PCM push-to-talk; fail closed without a local STT adapter."),
    ("runtime.voice.capture.feed", VoiceFeedParams, "Sequenced bounded PCM and partial/final transcript feedback; never accepts a task."),
    ("runtime.voice.capture.cancel", RuntimeSessionParams, "Discard this transport's captured audio, without cancelling a mission."),
    ("runtime.voice.speak", VoiceSpeakParams, "Interruptible declared local TTS; fail closed when unconfigured."),
    ("runtime.voice.stop", RuntimeSessionParams, "Stop only speech; UI streaming and mission cancellation are separate."),
    ("runtime.voice.submit", VoiceSubmitParams, "Explicitly confirm all transcript text before normal durable command admission."),
    ("runtime.screen.capture", ScreenCaptureParams, "Explicit client selected-window PNG; bounded metadata inspection, no OS capture or OCR."),
    ("runtime.screen.inspect", ScreenFrameParams, "Inspect the currently owned fresh frame reference and dimensions."),
    ("runtime.screen.annotate", ScreenAnnotateParams, "Validate an overlay region and guide the supported selected-text workflow."),
    ("runtime.screen.submit", ScreenSubmitParams, "Submit confirmed selected text from an owned fresh region through the command queue."),
    ("runtime.channel.bind", ChannelBindParams, "Bind an owned local surface to its exact principal/project/agent/session mission."),
    ("runtime.channel.submit", ChannelSubmitParams, "Deduplicate one logical input across verified local surfaces; never replay history."),
):
    method(name, params=params, result=MediaResponse, doc=doc)
