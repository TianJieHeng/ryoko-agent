"""Declared media boundaries never turn capture or barge-in into mission authority."""
import io

import pytest
from PIL import Image

from agent.media_ingress import ScreenIngress, SpeechDeclaration, VoiceIngress
from hermes_state_runtime import RuntimeStoreError


def png():
    output = io.BytesIO()
    Image.new("RGB", (80, 60), "white").save(output, format="PNG")
    return output.getvalue()


def test_unconfigured_and_owned_bounded_streaming_speech_is_not_task_acceptance(monkeypatch):
    owner, foreign = object(), object()
    empty = VoiceIngress()
    assert empty.capabilities()["unsupported"] == ["stt", "tts"]
    with pytest.raises(RuntimeStoreError, check=lambda error: error.code == "speech_adapter_unconfigured"):
        empty.begin(owner)
    calls = []

    class LocalSTT:
        declaration = SpeechDeclaration("test.local.stt", 1, "stt", "local", True, max_bytes=8)

        def transcribe(self, pcm, *, final):
            calls.append((pcm, final))
            return "Book for Ada at 12" if final else "Book for Ada"

    class LocalTTS:
        declaration = SpeechDeclaration("test.local.tts", 1, "tts", "local", False)

        def start(self, text):
            calls.append(("speak", text))

        def stop(self):
            calls.append(("stop",))

    voice = VoiceIngress(stt=LocalSTT(), tts=LocalTTS())
    voice.speak(owner, "Listening")
    with pytest.raises(RuntimeStoreError, check=lambda error: error.code == "speech_not_owned"):
        voice.stop_speech(foreign)
    capture = voice.begin(owner)["capture_id"]
    assert calls[-1] == ("stop",)
    partial = voice.feed(owner, capture, 0, b"\0\0")
    assert partial["text"] and not partial["accepted_as_task"] and partial["confirmation_required"]
    with pytest.raises(RuntimeStoreError, check=lambda error: error.code == "speech_sequence_conflict"):
        voice.feed(owner, capture, 0, b"\0\0")
    with pytest.raises(RuntimeStoreError, check=lambda error: error.code == "speech_capture_not_owned"):
        voice.feed(foreign, capture, 1, b"\0\0")
    with pytest.raises(RuntimeStoreError, check=lambda error: error.code == "speech_input_bound"):
        voice.feed(owner, capture, 1, b"\0" * 8)
    final = voice.feed(owner, capture, 1, b"\0\0", final=True)
    assert final["final"] and voice.capture is None and not final["accepted_as_task"]
    assert not voice.stop_speech(owner)["mission_cancelled"]
    voice.begin(owner)
    assert voice.cancel_capture(owner)["state"] == "discarded" and voice.capture is None
    with pytest.raises(RuntimeStoreError, check=lambda error: error.code == "speech_location_unsupported"):
        SpeechDeclaration("unapproved.remote", 1, "stt", "remote", True)


def test_screen_scope_region_confirmation_and_current_frame_freshness(monkeypatch):
    import agent.media_ingress as media
    now = [100.0]
    monkeypatch.setattr(media.time, "monotonic", lambda: now[0])
    screen, owner, foreign = ScreenIngress(), object(), object()
    with pytest.raises(RuntimeStoreError, check=lambda error: error.code == "screen_capture_scope"):
        screen.capture(owner, png(), "whole_desktop", "window")
    frame = screen.capture(owner, png(), "selected_window", "window")
    annotation = screen.annotate(owner, frame["frame_id"], [1, 2, 20, 30], "Inspect selection")
    assert annotation["frame_sha256"] == frame["sha256"] and not annotation["os_action_supported"]
    with pytest.raises(RuntimeStoreError, check=lambda error: error.code == "screen_region_invalid"):
        screen.annotate(owner, frame["frame_id"], [70, 0, 20, 2], "bad")
    with pytest.raises(RuntimeStoreError, check=lambda error: error.code == "screen_frame_not_owned"):
        screen.inspect(foreign, frame["frame_id"])
    with pytest.raises(RuntimeStoreError, check=lambda error: error.code == "media_confirmation_required"):
        screen.confirm(owner, frame["frame_id"], [0, 0, 10, 10], "Pay 12", "Pay 120")
    assert screen.confirm(owner, frame["frame_id"], [0, 0, 10, 10], "Find Ada", "Find Ada") == "Find Ada"
    replacement = screen.capture(owner, png(), "selected_window", "window")
    with pytest.raises(RuntimeStoreError, check=lambda error: error.code == "screen_frame_not_owned"):
        screen.inspect(owner, frame["frame_id"])
    now[0] += media.FRAME_MAX_AGE + 1
    with pytest.raises(RuntimeStoreError, check=lambda error: error.code == "screen_frame_stale"):
        screen.confirm(owner, replacement["frame_id"], [0, 0, 10, 10], "Find Ada", "Find Ada")
