"""Explicit capture, bounded speech adapters, and fresh selected-frame references.

Default speech capability is unconfigured. No microphone, display, remote STT,
or OS action is silently acquired. Screen act is the finite supported workflow
of submitting user-confirmed selected text through the normal command queue.
"""
from __future__ import annotations

import base64
import hashlib
import io
import math
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from typing import Protocol

from agent.bounded_services import require

MAX_AUDIO_BYTES = 1_920_000  # 60 seconds, mono 16kHz signed 16-bit PCM
MAX_FRAME_BYTES = 1_048_576
MAX_FRAME_PIXELS = 4_194_304
FRAME_MAX_AGE = 15.0


@dataclass(frozen=True)
class SpeechDeclaration:
    adapter_id: str
    version: int
    kind: str
    processing_location: str
    streaming: bool
    max_bytes: int = MAX_AUDIO_BYTES
    max_seconds: float = 60.0

    def __post_init__(self):
        require(isinstance(self.adapter_id, str) and 0 < len(self.adapter_id) <= 128
                and type(self.version) is int and self.version > 0, "speech_invalid_adapter")
        require(self.kind in {"stt", "tts"} and self.processing_location == "local", "speech_location_unsupported")
        require(type(self.streaming) is bool and type(self.max_bytes) is int and 0 < self.max_bytes <= MAX_AUDIO_BYTES
                and type(self.max_seconds) in (int, float) and math.isfinite(self.max_seconds)
                and 0 < self.max_seconds <= 60, "speech_invalid_bound")


@dataclass(frozen=True)
class SpeechAudio:
    pcm: bytes
    sample_rate: int

    def __post_init__(self):
        require(type(self.sample_rate) is int and 8000 <= self.sample_rate <= 48000,
                "speech_output_format")
        require(type(self.pcm) is bytes and 0 < len(self.pcm) <= 1_048_576 and len(self.pcm) % 2 == 0
                and len(self.pcm) <= self.sample_rate * 2 * 30, "speech_output_bound")

    def result(self):
        return {"format": "pcm_s16le", "sample_rate": self.sample_rate, "channels": 1,
                "pcm_base64": base64.b64encode(self.pcm).decode(), "byte_length": len(self.pcm),
                "sha256": hashlib.sha256(self.pcm).hexdigest()}


class STTAdapter(Protocol):
    declaration: SpeechDeclaration

    def transcribe(self, pcm: bytes, *, final: bool, cancelled: threading.Event) -> str: ...


class TTSAdapter(Protocol):
    declaration: SpeechDeclaration

    def synthesize(self, text: str, *, cancelled: threading.Event) -> SpeechAudio: ...


class VoiceIngress:
    def __init__(self, *, stt: STTAdapter | None = None, tts: TTSAdapter | None = None,
                 unsupported_reasons: dict | None = None, budget_admission=None):
        for adapter, kind in ((stt, "stt"), (tts, "tts")):
            if adapter is not None:
                require(isinstance(adapter.declaration, SpeechDeclaration)
                        and adapter.declaration.kind == kind, "speech_invalid_adapter")
        self.stt, self.tts = stt, tts
        self.budget_admission = budget_admission
        self.unsupported_reasons = unsupported_reasons or {}
        self.lock = threading.RLock()
        self.owner = None
        self.capture = None
        self.speech_owner = None
        self.speech = None
        self.speaking = False

    def capabilities(self):
        unsupported = [name for name, value in (("stt", self.stt), ("tts", self.tts)) if value is None]
        return {"push_to_talk": self.stt is not None, "stt": asdict(self.stt.declaration) if self.stt else None,
                "tts": asdict(self.tts.declaration) if self.tts else None, "unsupported": unsupported,
                "unsupported_reasons": {name: self.unsupported_reasons.get(name, "speech_adapter_unconfigured")
                                        for name in unsupported},
                "health": "prerequisites_verified_not_loaded" if self.stt or self.tts else "unavailable",
                "playback": "client", "backend_playback": False, "processing_host": "gateway_backend",
                "limits": {"pcm_sample_rate": 16000, "pcm_channels": 1, "pcm_format": "pcm_s16le",
                    "max_chunk_bytes": 64000, "max_audio_bytes": MAX_AUDIO_BYTES, "max_capture_seconds": 60,
                    "max_text_bytes": 4096, "max_output_bytes": 1_048_576, "max_synthesis_seconds": 30},
                "remote_processing": False, "capture": "explicit_client_pcm_only", "mission_cancellation": "runtime.command_only"}

    def begin(self, owner, *, request_id=None, budget_account_id=None, owner_check=None):
        with self.lock:
            require(self.stt is not None, "speech_adapter_unconfigured")
            if self.capture is not None and time.monotonic() - self.capture["started"] > self.stt.declaration.max_seconds:
                self.capture["cancelled"].set()
                self.capture, self.owner = None, None
            require(owner is not None and self.capture is None, "speech_capture_busy")
            budget = (self.budget_admission("stt", request_id, budget_account_id, owner_check)
                      if self.budget_admission else None)
            self.stop_speech(owner)
            self.owner = owner
            self.capture = {"id": uuid.uuid4().hex, "started": time.monotonic(), "pcm": bytearray(), "sequence": 0,
                            "cancelled": threading.Event(), "processing": False, "budget": budget}
            return {"capture_id": self.capture["id"], "state": "recording", "processing_location": "local"}

    def feed(self, owner, capture_id, sequence, pcm, *, final=False):
        with self.lock:
            require(self.stt is not None and self.capture is not None, "speech_capture_missing")
            capture = self.capture
            require(self.owner is owner and capture["id"] == capture_id, "speech_capture_not_owned")
            require(not capture["processing"], "speech_capture_busy")
            require(type(final) is bool, "speech_invalid_pcm")
            require(type(sequence) is int and sequence == capture["sequence"], "speech_sequence_conflict")
            require(type(pcm) is bytes and len(pcm) % 2 == 0 and len(pcm) <= 64000, "speech_invalid_pcm")
            if time.monotonic() - capture["started"] > self.stt.declaration.max_seconds:
                capture["cancelled"].set()
                self.capture, self.owner = None, None
                require(False, "speech_capture_expired")
            if capture["budget"] is not None:
                try:
                    capture["budget"].account()
                except BaseException:
                    capture["cancelled"].set()
                    self.capture, self.owner = None, None
                    raise
            require(len(capture["pcm"]) + len(pcm) <= self.stt.declaration.max_bytes, "speech_input_bound")
            capture["pcm"].extend(pcm)
            capture["sequence"] += 1
            capture["processing"] = True
        # Cancellation must remain callable while native model code is running.
        failed = True
        try:
            text = ""
            if final or self.stt.declaration.streaming:
                kwargs = {"budget": capture["budget"]} if capture["budget"] is not None else {}
                text = self.stt.transcribe(bytes(capture["pcm"]), final=final, cancelled=capture["cancelled"], **kwargs)
                require(isinstance(text, str) and len(text.encode("utf-8")) <= 65536, "speech_output_bound")
            with self.lock:
                require(self.capture is capture and not capture["cancelled"].is_set(), "speech_cancelled")
                failed = False
                return {"capture_id": capture_id, "state": "transcribed" if final else "recording",
                        "sequence": capture["sequence"], "text": text, "final": final,
                        "accepted_as_task": False, "confirmation_required": True, "processing_location": "local",
                        **({"budget": capture["budget"].receipt()} if final and capture["budget"] is not None else {})}
        finally:
            with self.lock:
                capture["processing"] = False
                if self.capture is capture and (final or failed):
                    capture["cancelled"].set()
                    self.capture, self.owner = None, None

    def cancel_capture(self, owner):
        with self.lock:
            require(self.capture is None or self.owner is owner, "speech_capture_not_owned")
            if self.capture is not None:
                self.capture["cancelled"].set()
            self.capture, self.owner = None, None
            return {"state": "discarded", "mission_cancelled": False}

    def speak(self, owner, text, *, request_id=None, budget_account_id=None, owner_check=None):
        with self.lock:
            require(self.tts is not None, "speech_adapter_unconfigured")
            require(owner is not None and isinstance(text, str) and text.strip()
                    and len(text.encode()) <= min(4096, self.tts.declaration.max_bytes), "speech_input_bound")
            require(not self.speaking or self.speech_owner is owner, "speech_not_owned")
            require(self.speech is None or self.speech["ready"], "speech_busy")
            budget = (self.budget_admission("tts", request_id, budget_account_id, owner_check)
                      if self.budget_admission else None)
            speech = {"id": uuid.uuid4().hex, "cancelled": threading.Event(), "ready": False}
            self.speech, self.speech_owner, self.speaking = speech, owner, True
        try:
            kwargs = {"budget": budget} if budget is not None else {}
            audio = self.tts.synthesize(text, cancelled=speech["cancelled"], **kwargs)
            require(isinstance(audio, SpeechAudio), "speech_output_format")
            with self.lock:
                require(self.speech is speech and not speech["cancelled"].is_set(), "speech_cancelled")
                speech["ready"] = True
                return {"state": "ready", "speech_id": speech["id"], "playback": "client", "audio": audio.result(),
                        "processing_location": "local", "mission_cancelled": False,
                        **({"budget": budget.receipt()} if budget is not None else {})}
        except BaseException:
            with self.lock:
                if self.speech is speech:
                    self.stop_speech(owner)
            raise

    def stop_speech(self, owner):
        with self.lock:
            require(not self.speaking or self.speech_owner is owner, "speech_not_owned")
            if self.speech is not None:
                self.speech["cancelled"].set()
            self.speech, self.speaking, self.speech_owner = None, False, None
            return {"state": "stopped", "mission_cancelled": False, "streaming_stopped": False}


class ScreenIngress:
    def __init__(self):
        self.lock = threading.RLock()
        self.frame = None

    def capture(self, owner, data, scope, window_ref):
        from PIL import Image
        require(owner is not None and scope == "selected_window" and isinstance(window_ref, str)
                and 0 < len(window_ref) <= 256, "screen_capture_scope")
        require(type(data) is bytes and 0 < len(data) <= MAX_FRAME_BYTES, "screen_frame_bound")
        with Image.open(io.BytesIO(data)) as image:
            require(image.format == "PNG" and 0 < image.width * image.height <= MAX_FRAME_PIXELS,
                    "screen_frame_format")
            image.verify()
            width, height = image.size
        with Image.open(io.BytesIO(data)) as image:
            image.load()
        with self.lock:
            self.frame = {"frame_id": uuid.uuid4().hex, "sha256": hashlib.sha256(data).hexdigest(),
                "width": width, "height": height, "scope": scope, "window_ref": window_ref,
                "captured_at": time.monotonic(), "owner": owner}
            return self.inspect(owner, self.frame["frame_id"])

    def _fresh(self, owner, frame_id):
        frame = self.frame
        require(frame is not None and frame["owner"] is owner and frame["frame_id"] == frame_id, "screen_frame_not_owned")
        require(time.monotonic() - frame["captured_at"] <= FRAME_MAX_AGE, "screen_frame_stale")
        return frame

    def inspect(self, owner, frame_id):
        with self.lock:
            frame = self._fresh(owner, frame_id)
            return {key: frame[key] for key in ("frame_id", "sha256", "width", "height", "scope", "window_ref")}

    def annotate(self, owner, frame_id, region, label):
        with self.lock:
            frame = self._fresh(owner, frame_id)
            require(isinstance(region, (list, tuple)) and len(region) == 4 and all(type(v) is int for v in region), "screen_region_invalid")
            x, y, width, height = region
            require(x >= 0 and y >= 0 and width > 0 and height > 0
                    and x + width <= frame["width"] and y + height <= frame["height"], "screen_region_invalid")
            require(isinstance(label, str) and len(label) <= 512, "screen_annotation_bound")
            return {"frame_id": frame_id, "frame_sha256": frame["sha256"], "region": list(region), "label": label,
                    "guide": "Review the selected text, confirm names and numbers, then explicitly submit it as a task",
                    "workflow": "confirmed_selected_text_to_task", "os_action_supported": False}

    def confirm(self, owner, frame_id, region, text, confirmed_text):
        self.annotate(owner, frame_id, region, "selected task")
        require(isinstance(text, str) and text.strip() and len(text.encode()) <= 65536
                and text == confirmed_text, "media_confirmation_required")
        return text
