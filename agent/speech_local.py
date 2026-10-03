"""Configured, offline speech at the client-PCM boundary, never host playback.

Only first-party fixed worker code runs. Configuration selects installed packages
and existing model data, not Python imports, executables, remote routes or grants.
The worker owns finite CPU/wall/file bounds and exits after each request.
"""
from __future__ import annotations

import base64
from contextlib import nullcontext
import importlib.util
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

from agent.bounded_services import require
from agent.media_ingress import SpeechAudio, SpeechDeclaration, VoiceIngress
from hermes_state_runtime import RuntimeStoreError

MAX_TEXT_BYTES = 4096
MAX_OUTPUT_BYTES = 1_048_576
MAX_WORKER_BYTES = 2_700_000
WALL_SECONDS = 30.0
CPU_SECONDS = 20
_WORKER_SLOTS = threading.BoundedSemaphore(2)


def _worker_command():
    # -I excludes CWD, PYTHONPATH and user-site packages. No caller-controlled code.
    return [sys.executable, "-I", str(Path(__file__).with_name("speech_local_worker.py"))]


def _package_present(name):
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _resource_limits_available():
    try:
        import resource
        return all(hasattr(resource, key) for key in ("RLIMIT_CPU", "RLIMIT_FSIZE", "RLIMIT_CORE"))
    except ImportError:
        return False


def _section(config, name):
    value = config.get(name, {})
    require(isinstance(value, dict), "speech_config_invalid")
    return value


def _existing_file(path):
    require(path.is_file() and path.stat().st_size > 0, "speech_model_missing")
    return str(path.resolve(strict=True))


def _local_path(value, home):
    require(isinstance(value, str) and value and "://" not in value, "speech_model_path_required")
    path = Path(value).expanduser()
    return path if path.is_absolute() else home / path


def _stt_options(config, home):
    stt = _section(config, "stt")
    require(stt.get("enabled", True) is True, "speech_disabled")
    require(stt.get("provider") == "local", "speech_local_provider_not_configured")
    local = _section(stt, "local")
    model = _local_path(local.get("model"), home)
    require(model.is_dir(), "speech_model_path_required")
    # Requiring a complete local tokenizer avoids the library's tokenizer Hub fallback.
    for filename in ("model.bin", "config.json", "tokenizer.json", "preprocessor_config.json"):
        _existing_file(model / filename)
    require(_package_present("faster_whisper") and _package_present("numpy"), "speech_package_missing")
    device, compute = local.get("device", "cpu"), local.get("compute_type", "int8")
    require(device in {"cpu", "auto", "cuda"} and compute in {
        "auto", "default", "int8", "int8_float32", "int8_float16", "int8_bfloat16", "int16", "float16", "float32", "bfloat16"},
        "speech_config_invalid")
    language = local.get("language") or stt.get("language") or None
    require(language is None or isinstance(language, str) and len(language) <= 16, "speech_config_invalid")
    # Preserve the local route's silence hardening without importing its downloader.
    kwargs = {"beam_size": 5, "condition_on_previous_text": False,
              "vad_filter": local.get("vad", True) is not False}
    if kwargs["vad_filter"]:
        silence = local.get("vad_min_silence_ms", 500)
        require(type(silence) is int and 0 < silence <= 10000, "speech_config_invalid")
        kwargs["vad_parameters"] = {"min_silence_duration_ms": silence}
    if language:
        kwargs["language"] = language
    for source, target, default in (("no_speech_prob_threshold", "no_speech_threshold", 0.6),
                                    ("logprob_threshold", "log_prob_threshold", -1.0)):
        value = local.get(source, default)
        require(type(value) in (int, float) and math.isfinite(value), "speech_config_invalid")
        kwargs[target] = value
    prompt = local.get("initial_prompt", "")
    require(isinstance(prompt, str) and len(prompt.encode()) <= MAX_TEXT_BYTES, "speech_config_invalid")
    if prompt:
        kwargs["initial_prompt"] = prompt
    return {"kind": "stt", "model": str(model.resolve()), "device": device, "compute_type": compute,
            "transcribe_kwargs": kwargs}


def _tts_options(config, home):
    tts = _section(config, "tts")
    require(tts.get("provider") == "piper", "speech_local_provider_not_configured")
    piper = _section(tts, "piper")
    voice = piper.get("voice") or "en_US-lessac-medium"
    require(isinstance(voice, str) and "://" not in voice, "speech_config_invalid")
    if voice.endswith(".onnx"):
        model = _local_path(voice, home)
    else:
        require(Path(voice).name == voice and voice not in {".", ".."}, "speech_config_invalid")
        from hermes_constants import get_hermes_dir
        directory = piper.get("voices_dir")
        root = _local_path(directory, home) if directory else get_hermes_dir("cache/piper-voices", "piper_voices_cache", home=home)
        model = root / (voice + ".onnx")
    model_path = _existing_file(model)
    metadata = Path(model_path + ".json")
    _existing_file(metadata)
    require(metadata.stat().st_size <= 65536, "speech_config_invalid")
    info = json.loads(metadata.read_text(encoding="utf-8"))
    rate = info.get("audio", {}).get("sample_rate")
    require(type(rate) is int and 8000 <= rate <= 48000, "speech_config_invalid")
    require(_package_present("piper"), "speech_package_missing")
    require(type(piper.get("use_cuda", False)) is bool, "speech_config_invalid")
    # Do not silently ignore customized synthesis parameters.
    knobs = {key: piper[key] for key in ("length_scale", "noise_scale", "noise_w_scale", "volume", "normalize_audio", "speaker_id") if key in piper}
    require(all(type(value) in (bool, int, float) and (type(value) is bool or math.isfinite(value))
                for value in knobs.values()), "speech_config_invalid")
    return {"kind": "tts", "model": model_path, "sample_rate": rate,
            "use_cuda": piper.get("use_cuda", False), "synthesis_kwargs": knobs}


class _LocalWorker:
    def __init__(self, options, home, agent=None):
        self.options, self.home, self.agent = options, home, agent
        self.declaration = SpeechDeclaration("local." + ("faster_whisper" if options["kind"] == "stt" else "piper"),
                                             1, options["kind"], "local", False,
                                             max_bytes=1_920_000 if options["kind"] == "stt" else MAX_OUTPUT_BYTES,
                                             max_seconds=60 if options["kind"] == "stt" else 30)

    def _run(self, payload, cancelled, budget=None):
        from tools.environments.local import served_profile_child_env
        from agent.speech_budget import speech_policy, SpeechBudgetBinding
        if self.agent is None:
            from agent.runtime_context import current_agent_context
            from hermes_cli.config import load_config
            require(current_agent_context() is None and not load_config().get("runtime_budget"),
                    "speech_budget_account_required")
        if self.agent is not None:
            policy = speech_policy(self.agent)
            require(policy is None or isinstance(budget, SpeechBudgetBinding)
                    and budget.agent is self.agent and budget.kind == self.options["kind"],
                    "speech_budget_account_required")
        require(not cancelled.is_set(), "speech_cancelled")
        require(_WORKER_SLOTS.acquire(blocking=False), "speech_worker_busy")
        worker_status = {"terminated": True}
        try:
            scoped = served_profile_child_env(target_home=self.home, inherit_credentials=False)
            env = {key: scoped[key] for key in ("HERMES_HOME", "SYSTEMROOT", "WINDIR", "LANG", "LC_ALL") if key in scoped}
            env.update({"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1",
                        "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1"})
            with tempfile.TemporaryDirectory(prefix="hermes-bounded-speech-") as temporary:
                root = Path(temporary)
                env.update({"HOME": temporary, "TMPDIR": temporary, "HF_HOME": temporary})
                with budget.execution(WALL_SECONDS) if budget is not None else nullcontext() as execution:
                    return self._process(root, env, payload, cancelled, execution, worker_status)
        finally:
            # Without proof of termination neither the tree nor host slot is a
            # refund. Restart/reconciliation is required for an unknown worker.
            if worker_status["terminated"]:
                _WORKER_SLOTS.release()

    def _process(self, root, env, payload, cancelled, budget, worker_status):
        process, uncertain, spawned, spawn_pending = None, False, False, False
        started = time.monotonic()
        maximum = budget.maximum_ms / 1000 if budget is not None else WALL_SECONDS + 0.5
        deadline = started + maximum
        try:
            # Wall allocation includes startup and termination, not just inference.
            cpu = min(CPU_SECONDS, max(1, math.floor(maximum - 0.5)))
            data = json.dumps({**self.options, **payload, "cpu_seconds": cpu}).encode()
            require(len(data) <= MAX_WORKER_BYTES, "speech_input_bound")
            (root / "input.json").write_bytes(data)
            require(not cancelled.is_set(), "speech_cancelled")
            if budget is not None:
                budget.dispatch()
                deadline = min(deadline, time.monotonic() + max(0, budget.deadline - time.time()))
            spawn_pending = True
            try:
                process = subprocess.Popen([*_worker_command(), str(root)], cwd=str(root), env=env,
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except OSError:
                spawn_pending = False  # Popen rejected creation, no process handle.
                raise
            spawned, spawn_pending = True, False
            while process.poll() is None:
                require(not cancelled.is_set(), "speech_cancelled")
                require(time.monotonic() < deadline - 0.5, "speech_deadline_exceeded")
                if budget is not None:
                    budget.check()
                cancelled.wait(0.025)
            require(not cancelled.is_set(), "speech_cancelled")
            if budget is not None:
                budget.check()
            require(process.returncode == 0, "speech_worker_failed")
            output = root / "output.json"
            require(output.is_file() and output.stat().st_size <= MAX_WORKER_BYTES, "speech_output_bound")
            result = json.loads(output.read_bytes())
            require(isinstance(result, dict), "speech_worker_failed")
            if "error" in result:
                code = result["error"]
                require(False, code if code in {"speech_package_missing", "speech_model_load_failed",
                    "speech_output_bound", "speech_operation_failed", "speech_resource_limits_unsupported"} else "speech_worker_failed")
            return result
        finally:
            # Interruption inside process creation without a handle is not proof
            # that nothing started. Keep the maximum and concurrency slot.
            uncertain = spawn_pending
            if process is not None and process.poll() is None:
                try:
                    process.kill()
                    process.wait(timeout=max(0.01, min(0.5, deadline - time.monotonic())))
                except (OSError, subprocess.TimeoutExpired):
                    uncertain = process.poll() is None
            worker_status["terminated"] = not uncertain
            if budget is not None:
                # A retired writer cannot refund. A failed settlement leaves the
                # durable dispatched reservation and unknown slot intact.
                budget.settle(math.ceil((time.monotonic() - started) * 1000),
                              spawned=spawned, uncertain=uncertain)
            require(not uncertain, "speech_termination_unknown")


class LocalSTT(_LocalWorker):
    def transcribe(self, pcm, *, final, cancelled, budget=None):
        require(final and type(pcm) is bytes and 0 < len(pcm) <= self.declaration.max_bytes and len(pcm) % 2 == 0,
                "speech_invalid_pcm")
        return self._run({"pcm_base64": base64.b64encode(pcm).decode()}, cancelled, budget).get("text")


class LocalTTS(_LocalWorker):
    def synthesize(self, text, *, cancelled, budget=None):
        require(isinstance(text, str) and text.strip() and len(text.encode()) <= MAX_TEXT_BYTES, "speech_input_bound")
        result = self._run({"text": text}, cancelled, budget)
        return SpeechAudio(base64.b64decode(result["pcm_base64"], validate=True), result["sample_rate"])


def speech_budget_blocked(agent):
    from agent.speech_budget import speech_budget_blocked as blocked
    return blocked(agent)


def require_speech_budget(agent):
    from agent.speech_budget import speech_policy
    return speech_policy(agent)


def configured_voice_ingress(agent):
    """Read scoped config; report prerequisites without loading or invoking models."""
    from hermes_cli.config import load_config
    config = load_config()
    reasons, adapters = {}, {}
    if speech_budget_blocked(agent):
        return VoiceIngress(unsupported_reasons={kind: "speech_budget_unsupported" for kind in ("stt", "tts")})
    if not _resource_limits_available():
        return VoiceIngress(unsupported_reasons={kind: "speech_resource_limits_unsupported" for kind in ("stt", "tts")})
    home = Path(agent.runtime_context.profile_home)
    for kind, resolve, adapter in (("stt", _stt_options, LocalSTT), ("tts", _tts_options, LocalTTS)):
        try:
            adapters[kind] = adapter(resolve(config, home), home, agent)
        except RuntimeStoreError as exc:
            reasons[kind] = exc.code
        except (OSError, ValueError, TypeError, AttributeError):
            reasons[kind] = "speech_config_invalid"
    from agent.speech_budget import admit_speech
    voice = VoiceIngress(**adapters, unsupported_reasons=reasons,
        budget_admission=lambda kind, request, account, check: admit_speech(agent, kind, request, account, check))
    voice.budget_agent = agent
    return voice
