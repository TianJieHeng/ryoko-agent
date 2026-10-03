"""Fixed subprocess entrypoint for offline, finite STT/TTS. No plugins or playback.

Imports only installed speech packages after applying hard process limits. Input
contains bounded data and server-resolved local assets, never executable code.
"""
from __future__ import annotations

import base64
import io
import json
from pathlib import Path
import sys
import wave

MAX_AUDIO_BYTES = 1_048_576
MAX_WIRE_BYTES = 2_700_000


class OutputBound(ValueError):
    pass


class ModelLoadFailed(ValueError):
    pass


class _BoundedWave(io.BytesIO):
    def write(self, data):
        if self.tell() + len(data) > MAX_AUDIO_BYTES + 4096:
            raise OutputBound()
        return super().write(data)


def _transcribe(request):
    from faster_whisper import WhisperModel
    import numpy as np
    try:
        model = WhisperModel(request["model"], device=request["device"], compute_type=request["compute_type"],
                             local_files_only=True, cpu_threads=1, num_workers=1)
    except ImportError:
        raise
    except Exception as exc:
        raise ModelLoadFailed() from exc
    pcm = base64.b64decode(request["pcm_base64"], validate=True)
    if not 0 < len(pcm) <= 1_920_000 or len(pcm) % 2:
        raise OutputBound()
    audio = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
    segments, _ = model.transcribe(audio, **request["transcribe_kwargs"])
    text, size = [], 0
    for segment in segments:
        try:
            if (float(segment.no_speech_prob) > request["transcribe_kwargs"]["no_speech_threshold"]
                    and float(segment.avg_logprob) < request["transcribe_kwargs"]["log_prob_threshold"]):
                continue
        except (AttributeError, ValueError, TypeError):
            pass
        part = segment.text.strip()
        size += len(part.encode()) + 1
        if size > 65536:
            raise OutputBound()
        text.append(part)
    return {"text": " ".join(text).strip()}


def _synthesize(request):
    from piper import PiperVoice
    try:
        voice = PiperVoice.load(request["model"], config_path=request["model"] + ".json", use_cuda=request["use_cuda"])
    except ImportError:
        raise
    except Exception as exc:
        raise ModelLoadFailed() from exc
    kwargs = {}
    if request["synthesis_kwargs"]:
        from piper import SynthesisConfig
        kwargs["syn_config"] = SynthesisConfig(**request["synthesis_kwargs"])
    if len(request["text"].encode()) > 4096:
        raise OutputBound()
    output = _BoundedWave()
    with wave.open(output, "wb") as wav:
        voice.synthesize_wav(request["text"], wav, **kwargs)
    with wave.open(io.BytesIO(output.getvalue()), "rb") as wav:
        if (wav.getnchannels() != 1 or wav.getsampwidth() != 2 or wav.getframerate() != request["sample_rate"]
                or wav.getnframes() > wav.getframerate() * 30 or wav.getnframes() * 2 > MAX_AUDIO_BYTES):
            raise OutputBound()
        pcm = wav.readframes(wav.getnframes())
        if not pcm or len(pcm) != wav.getnframes() * 2:
            raise OutputBound()
    return {"pcm_base64": base64.b64encode(pcm).decode(), "sample_rate": request["sample_rate"]}


def _offline_audit(event, args):
    if event in {"socket.connect", "socket.connect_ex", "socket.getaddrinfo", "socket.bind", "subprocess.Popen", "os.system"}:
        raise PermissionError("Offline speech worker")


def main():
    root = Path(sys.argv[1])
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_WIRE_BYTES, MAX_WIRE_BYTES))
        request_path = root / "input.json"
        if request_path.stat().st_size > MAX_WIRE_BYTES:
            raise OutputBound()
        request = json.loads(request_path.read_bytes())
        cpu = min(20, max(1, int(request["cpu_seconds"])))
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
    except (ImportError, OSError, ValueError):
        (root / "output.json").write_text('{"error":"speech_resource_limits_unsupported"}', encoding="utf-8")
        return
    sys.addaudithook(_offline_audit)
    try:
        result = {"stt": _transcribe, "tts": _synthesize}[request["kind"]](request)
    except ImportError:
        result = {"error": "speech_package_missing"}
    except OutputBound:
        result = {"error": "speech_output_bound"}
    except ModelLoadFailed:
        result = {"error": "speech_model_load_failed"}
    except Exception:
        # Model/library exceptions may contain private text or local paths.
        result = {"error": "speech_operation_failed"}
    (root / "output.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
