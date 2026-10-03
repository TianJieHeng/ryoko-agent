"""Real configured factory/isolated worker with controlled local package boundaries.

The tiny packages below are deterministic test doubles, not downloaded or live
speech models. Neither the VoiceIngress adapter nor worker runner is replaced.
"""
import base64
import hashlib
import json
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from hermes_state_runtime import RuntimeStoreError


@pytest.fixture
def local_speech_packages(tmp_path, monkeypatch):
    import agent.speech_local as speech
    packages = tmp_path / "packages"
    packages.mkdir()
    (packages / "numpy.py").write_text("""
float32 = object()
class Array:
    def astype(self, dtype): return self
    def __truediv__(self, divisor): return self
def frombuffer(pcm, dtype):
    assert dtype == '<i2' and len(pcm) % 2 == 0
    return Array()
""")
    (packages / "faster_whisper.py").write_text('''
import json, os, time
from pathlib import Path
from types import SimpleNamespace
class WhisperModel:
    def __init__(self, path, *, local_files_only, device, compute_type, cpu_threads, num_workers):
        assert local_files_only is True and cpu_threads == num_workers == 1
        assert os.environ.get("HF_HUB_OFFLINE") == "1"
        assert "UNRELATED_CREDENTIAL" not in os.environ and "PYTHONPATH" not in os.environ
        self.path = Path(path)
        self.data = json.loads((self.path / "model.bin").read_text())
    def transcribe(self, audio, **kwargs):
        assert kwargs["condition_on_previous_text"] is False
        (self.path / "started").write_text(str(os.getpid()))
        if self.data.get("block"):
            time.sleep(120)
        if self.data.get("wait_for_release"):
            while not (self.path / "release").exists(): time.sleep(0.025)
        if self.data.get("network"):
            import socket
            socket.getaddrinfo("must-not-resolve.invalid", 443)
        if self.data.get("cpu"):
            while True: pass
        text = self.data.get("text", "Ada at 12")
        return iter([SimpleNamespace(text=text)]), SimpleNamespace()
''')
    (packages / "piper.py").write_text('''
import json, os, time
from pathlib import Path
class SynthesisConfig:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
class PiperVoice:
    @classmethod
    def load(cls, path, *, config_path, use_cuda):
        assert Path(path).is_file() and Path(config_path).is_file()
        assert os.environ.get("HF_HUB_OFFLINE") == "1"
        assert "UNRELATED_CREDENTIAL" not in os.environ
        voice = cls()
        voice.path = Path(path)
        voice.data = json.loads(voice.path.read_text())
        voice.rate = json.loads(Path(config_path).read_text())["audio"]["sample_rate"]
        return voice
    def synthesize_wav(self, text, wav, **kwargs):
        (self.path.parent / "tts-started").write_text(str(os.getpid()))
        if self.data.get("block"):
            time.sleep(120)
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(self.rate)
        wav.writeframes(b"\\x01\\x00\\xff\\xff" * self.data.get("frames", 2))
''')
    monkeypatch.syspath_prepend(str(packages))
    worker = Path(speech.__file__).with_name("speech_local_worker.py")
    # Only test process invocation adds a known fixture package directory. The
    # production command is fixed -I + first-party file, with no such option.
    bootstrap = f"import runpy,sys;sys.path.insert(0,{str(packages)!r});sys.argv=[{str(worker)!r},sys.argv[1]];runpy.run_path({str(worker)!r},run_name='__main__')"
    monkeypatch.setattr(speech, "_worker_command", lambda: [sys.executable, "-I", "-c", bootstrap])
    monkeypatch.setenv("UNRELATED_CREDENTIAL", "must-not-reach-worker")
    return packages


def configured_home(home, *, text="Ada at 12", stt_extra=None, tts_extra=None):
    home.mkdir(exist_ok=True)
    model = home / "whisper"
    model.mkdir(exist_ok=True)
    for name in ("config.json", "tokenizer.json", "preprocessor_config.json"):
        (model / name).write_text("{}")
    (model / "model.bin").write_text(json.dumps({"text": text, **(stt_extra or {})}))
    (home / "voice.onnx").write_text(json.dumps(tts_extra or {}))
    (home / "voice.onnx.json").write_text(json.dumps({"audio": {"sample_rate": 22050}}))
    config = {"stt": {"provider": "local", "local": {"model": str(model), "device": "cpu", "compute_type": "int8"}},
              "tts": {"provider": "piper", "piper": {"voice": str(home / "voice.onnx")}}}
    (home / "config.yaml").write_text(json.dumps(config))
    return config


def _voice(home):
    from agent.speech_local import configured_voice_ingress
    from hermes_constants import set_hermes_home_override, reset_hermes_home_override
    token = set_hermes_home_override(home)
    try:
        return configured_voice_ingress(SimpleNamespace(runtime_context=SimpleNamespace(profile_home=str(home))))
    finally:
        reset_hermes_home_override(token)


def _finish(voice, owner):
    capture = voice.begin(owner)
    return voice.feed(owner, capture["capture_id"], 0, b"\0\0", final=True)


def _wait_for_file(path):
    end = time.monotonic() + 10
    while not path.exists() and time.monotonic() < end:
        time.sleep(0.025)
    assert path.exists()


@pytest.mark.platforms("posix")
def test_configured_factory_real_package_worker_profile_routes_and_missing_prerequisites(tmp_path, monkeypatch, local_speech_packages):
    from agent.speech_local import configured_voice_ingress
    import agent.speech_local as speech
    homes = [tmp_path / "a", tmp_path / "b"]
    for home, text in zip(homes, ["Ada at 12", "Bea at 120"]):
        configured_home(home, text=text)
    owner = object()
    for home, expected in [(homes[0], "Ada at 12"), (homes[1], "Bea at 120"), (homes[0], "Ada at 12")]:
        voice = _voice(home)
        capabilities = voice.capabilities()
        assert capabilities["push_to_talk"] and capabilities["tts"] and not capabilities["remote_processing"]
        assert capabilities["health"] == "prerequisites_verified_not_loaded"
        assert not (home / "whisper" / "started").exists()
        transcript = _finish(voice, owner)
        assert transcript["text"] == expected and transcript["confirmation_required"] and not transcript["accepted_as_task"]
        (home / "whisper" / "started").unlink()
        output = voice.speak(owner, "Read back my numbers")
        pcm = base64.b64decode(output["audio"]["pcm_base64"], validate=True)
        assert pcm == b"\x01\x00\xff\xff" * 2
        assert output["audio"]["byte_length"] == len(pcm) and output["audio"]["sha256"] == hashlib.sha256(pcm).hexdigest()
        assert output["playback"] == "client" and output["state"] == "ready"
        assert not voice.stop_speech(owner)["mission_cancelled"]
    monkeypatch.setattr(speech, "_package_present", lambda _name: False)
    assert _voice(homes[0]).capabilities()["unsupported_reasons"] == {"stt": "speech_package_missing", "tts": "speech_package_missing"}
    (homes[0] / "whisper" / "tokenizer.json").unlink()
    (homes[0] / "voice.onnx").unlink()
    reasons = _voice(homes[0]).capabilities()["unsupported_reasons"]
    assert reasons == {"stt": "speech_model_missing", "tts": "speech_model_missing"}
    (homes[0] / "config.yaml").write_text(json.dumps({"stt": {"provider": "openai"}, "tts": {"provider": "command"}}))
    assert set(_voice(homes[0]).capabilities()["unsupported_reasons"].values()) == {"speech_local_provider_not_configured"}


@pytest.mark.platforms("posix")
def test_worker_cancellation_deadlines_output_bounds_and_budget_never_bypass(tmp_path, monkeypatch, local_speech_packages):
    import agent.speech_local as speech
    home, owner = tmp_path / "home", object()
    configured_home(home, stt_extra={"block": True}, tts_extra={"block": True})
    voice = _voice(home)
    errors = []
    def transcription():
        try:
            _finish(voice, owner)
        except RuntimeStoreError as exc:
            errors.append(exc.code)
    thread = threading.Thread(target=transcription)
    thread.start()
    _wait_for_file(home / "whisper" / "started")
    assert voice.cancel_capture(owner)["state"] == "discarded"
    thread.join(5)
    assert not thread.is_alive() and errors == ["speech_cancelled"]
    pid = int((home / "whisper" / "started").read_text())
    import psutil
    assert not psutil.pid_exists(pid)
    def synthesis():
        try:
            voice.speak(owner, "Hello")
        except RuntimeStoreError as exc:
            errors.append(exc.code)
    thread = threading.Thread(target=synthesis)
    thread.start()
    _wait_for_file(home / "tts-started")
    with pytest.raises(RuntimeStoreError, check=lambda exc: exc.code == "speech_not_owned"):
        voice.stop_speech(object())
    assert not voice.stop_speech(owner)["mission_cancelled"]
    thread.join(5)
    assert not thread.is_alive() and errors[-1] == "speech_cancelled"
    assert not psutil.pid_exists(int((home / "tts-started").read_text()))
    monkeypatch.setattr(speech, "WALL_SECONDS", 0.5)
    with pytest.raises(RuntimeStoreError, check=lambda exc: exc.code == "speech_deadline_exceeded"):
        _finish(voice, owner)
    assert voice.capture is None
    monkeypatch.setattr(speech, "WALL_SECONDS", 10)
    configured_home(home, text="x" * 65537, tts_extra={"frames": 300000})
    voice = _voice(home)
    with pytest.raises(RuntimeStoreError, check=lambda exc: exc.code == "speech_output_bound"):
        _finish(voice, owner)
    with pytest.raises(RuntimeStoreError, check=lambda exc: exc.code == "speech_output_bound"):
        voice.speak(owner, "Hello")
    assert not voice.speaking
    configured_home(home, stt_extra={"network": True})
    with pytest.raises(RuntimeStoreError, check=lambda exc: exc.code == "speech_operation_failed"):
        _finish(_voice(home), owner)
    (home / "whisper" / "model.bin").write_text("not a valid model")
    voice = _voice(home)
    assert voice.capabilities()["health"] == "prerequisites_verified_not_loaded"
    with pytest.raises(RuntimeStoreError, check=lambda exc: exc.code == "speech_model_load_failed"):
        _finish(voice, owner)
    config = configured_home(home, stt_extra={"cpu": True})
    monkeypatch.setattr(speech, "CPU_SECONDS", 1)
    with pytest.raises(RuntimeStoreError, check=lambda exc: exc.code == "speech_worker_failed"):
        _finish(_voice(home), owner)
    assert not psutil.pid_exists(int((home / "whisper" / "started").read_text()))
    config["runtime_budget"] = {"schema_version": 1}
    (home / "config.yaml").write_text(json.dumps(config))
    assert set(_voice(home).capabilities()["unsupported_reasons"].values()) == {"speech_budget_unsupported"}
