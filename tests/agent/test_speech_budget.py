"""Real offline worker + accepted BE03 accounts; no provider/model downloads."""
import contextvars
import json
import threading
import time
from types import SimpleNamespace

import pytest

from agent.identity_lifecycle import agent_runtime_scope
from agent.speech_local import configured_voice_ingress
from agent.budget_account import actor_for
from hermes_state_runtime import RuntimeStoreError
from tests.agent.test_budget_runtime import factory, active, policy  # noqa: F401
from tests.agent.test_speech_local import local_speech_packages, configured_home, _wait_for_file  # noqa: F401


def make_voice(factory, *, budget=None, stt_extra=None, tts_extra=None):
    make, db, home = factory
    agent, calls = make(budget=budget)
    original = json.loads((home / "config.yaml").read_text())
    config = configured_home(home, stt_extra=stt_extra, tts_extra=tts_extra)
    (home / "config.yaml").write_text(json.dumps({**original, **config}))
    with agent_runtime_scope(agent.runtime_context):
        voice = configured_voice_ingress(agent)
    return agent, voice, db, home, calls


def start(voice, owner, account, request="capture"):
    return voice.begin(owner, request_id=request, budget_account_id=account)["capture_id"]


def speak(voice, owner, account, request="speak"):
    return voice.speak(owner, "Ada at 12", request_id=request, budget_account_id=account)


def reservations(db):
    with db._runtime_read() as conn:
        return conn.execute("SELECT COUNT(*) FROM budget_reservations").fetchone()[0]


def expect(code):
    return pytest.raises(RuntimeStoreError, check=lambda exc: exc.code == code)


@pytest.mark.platforms("posix")
def test_real_speech_uses_existing_run_tree_and_replays_never_dispatch(factory, local_speech_packages):
    agent, voice, db, home, calls = make_voice(factory)
    owner = object()
    with active(agent) as run:
        account = run.budget.account_id
        with expect("speech_request_id_required"):
            voice.begin(owner)
        capture = start(voice, owner, account)
        assert not reservations(db)
        assert voice.feed(owner, capture, 0, b"\0\0")["state"] == "recording"
        assert not reservations(db)
        transcript = voice.feed(owner, capture, 1, b"", final=True)
        audio = speak(voice, owner, account)
        assert transcript["text"] == "Ada at 12" and audio["audio"]["byte_length"] == 8
        for output in (transcript, audio):
            receipt = output["budget"]
            assert receipt["account_id"] == account and receipt["root_id"] == run.budget.root_id
            assert receipt["actual"]["attempts"] == 1 and 0 < receipt["actual"]["wall_ms"] <= receipt["maxima"]["wall_ms"]
            assert receipt["state"] == "settled" and receipt["slots_released"] and not receipt["unknown_usage"]
            assert not any(receipt["reserved"].values())
        used = db.get_budget_account(account, run.budget.actor)
        assert used["consumed"]["attempts"] == 2 and used["consumed"]["tokens"] == 0
        assert used["consumed"]["cost_micros"] == 0 and not calls
        with expect("speech_request_consumed"):
            speak(voice, owner, account)
        with expect("speech_request_consumed"):
            start(voice, owner, account)
        assert reservations(db) == 2
        # Idle controls must still use this exact latest account and original
        # deadline. Completing the command is not a fresh allocation.
        db.finish_runtime_command(agent.session_id, run.command_id, holder=run.holder,
            generation=run.generation, result={"completed": True})
    with agent_runtime_scope(agent.runtime_context):
        output = speak(voice, owner, account, "idle")
        assert output["budget"]["root_id"] == used["root_id"]
        assert db.get_budget_account(account, actor_for(agent.runtime_context))["deadline"] == used["deadline"]
        assert getattr(agent, "_active_runtime_run", None) is None
        assert db.get_session_turn_lease(agent.session_id) is None
        with expect("speech_request_consumed"):
            speak(voice, owner, account, "idle")
    with active(agent) as next_run:
        with expect("speech_budget_owner_changed"):
            speak(voice, owner, account, "old-account")
        fresh = speak(voice, owner, next_run.budget.account_id, "continuation")
        assert fresh["budget"]["root_id"] == used["root_id"]
        assert db.get_budget_account(account, next_run.budget.actor)["consumed"]["attempts"] == 4


@pytest.mark.platforms("posix")
def test_empty_cancelled_refused_capture_and_spawn_failure_do_not_hold_budget(factory, local_speech_packages, monkeypatch):
    agent, voice, db, home, _ = make_voice(factory)
    owner = object()
    with active(agent) as run:
        account = run.budget.account_id
        capture = start(voice, owner, account, "empty")
        with expect("speech_invalid_pcm"):
            voice.feed(owner, capture, 0, b"", final=True)
        start(voice, owner, account, "cancel")
        voice.cancel_capture(owner)
        capture = start(voice, owner, account, "oversize")
        with expect("speech_invalid_pcm"):
            voice.feed(owner, capture, 0, b"\0" * 64002, final=True)
        voice.cancel_capture(owner)
        with expect("speech_input_bound"):
            voice.speak(owner, "x" * 4097, request_id="oversize", budget_account_id=account)
        assert reservations(db) == 0
        def unavailable(*args, **kwargs):
            raise OSError("test spawn refused")
        monkeypatch.setattr("agent.speech_local.subprocess.Popen", unavailable)
        with pytest.raises(OSError):
            speak(voice, owner, account, "spawn-refusal")
        ledger = db.get_budget_account(account, run.budget.actor)
        assert not ledger["unknown_usage"] and not any(ledger["reserved"].values())
        assert ledger["consumed"]["attempts"] == 0
        assert not (home / "tts-started").exists()


@pytest.mark.platforms("posix")
def test_shared_executor_slot_cancel_cpu_timeout_and_original_deadline(factory, local_speech_packages, monkeypatch):
    configured = policy()
    configured["limits"]["executor_slots"] = 1
    agent, voice, db, home, calls = make_voice(factory, budget=configured, tts_extra={"block": True})
    owner = object()
    with active(agent) as run:
        errors = []
        def blocking():
            try:
                speak(voice, owner, run.budget.account_id, "blocking")
            except BaseException as exc:
                errors.append(exc)
        thread = threading.Thread(target=contextvars.copy_context().run, args=(blocking,))
        thread.start()
        _wait_for_file(home / "tts-started")
        # A separate local ingress still shares the durable pool, not its own counter.
        second = configured_voice_ingress(agent)
        with expect("budget_exhausted"):
            speak(second, object(), run.budget.account_id, "parallel")
        voice.stop_speech(owner)
        thread.join(5)
        assert not thread.is_alive() and len(errors) == 1 and errors[0].code == "speech_cancelled"
        ledger = db.get_budget_account(run.budget.account_id, run.budget.actor)
        assert ledger["consumed"]["attempts"] == 1 and not any(ledger["reserved"].values())
        monkeypatch.setattr("agent.speech_local.WALL_SECONDS", 2.0)
        with expect("speech_deadline_exceeded"):
            speak(voice, owner, run.budget.account_id, "deadline")
        assert not db.get_budget_account(run.budget.account_id, run.budget.actor)["unknown_usage"]
        monkeypatch.setattr("agent.speech_local.WALL_SECONDS", 10.0)
        monkeypatch.setattr("agent.speech_local.CPU_SECONDS", 1)
        (home / "whisper" / "model.bin").write_text(json.dumps({"cpu": True}))
        capture = start(voice, owner, run.budget.account_id, "cpu-bound")
        with expect("speech_worker_failed"):
            voice.feed(owner, capture, 0, b"\0\0", final=True)
        import psutil
        assert not psutil.pid_exists(int((home / "whisper" / "started").read_text()))
        assert db.get_budget_account(run.budget.account_id, run.budget.actor)["consumed"]["attempts"] == 3
        # Remaining original deadline too small means no reservation and no work.
        before = reservations(db)
        monkeypatch.setattr("agent.speech_budget.time", SimpleNamespace(time=lambda: run.budget.deadline - 0.8))
        with expect("speech_budget_deadline_insufficient"):
            speak(voice, owner, run.budget.account_id, "late")
        assert reservations(db) == before and not calls


@pytest.mark.platforms("posix")
def test_unacknowledged_local_stop_retains_reservation_and_real_child_is_reaped(factory, local_speech_packages, monkeypatch):
    import agent.speech_local as local
    from agent.speech_budget import admit_speech
    import subprocess
    configured = policy()
    configured["limits"]["executor_slots"] = 1
    agent, voice, db, home, _ = make_voice(factory, budget=configured, tts_extra={"block": True})
    original, processes = local.subprocess.Popen, []
    monkeypatch.setattr(local, "_WORKER_SLOTS", threading.BoundedSemaphore(2))
    class LostAcknowledgment:
        def __init__(self, *args, **kwargs):
            self.process = original(*args, **kwargs)
            processes.append(self.process)
            self.killed = False
        def poll(self):
            return None if self.killed else self.process.poll()
        def kill(self):
            self.process.kill()
            self.killed = True
        def wait(self, timeout):
            # Model loss of the host termination acknowledgment, not failure
            # of the real process boundary to issue its kill.
            raise subprocess.TimeoutExpired("fixture", timeout)
    monkeypatch.setattr(local.subprocess, "Popen", LostAcknowledgment)
    with active(agent) as run:
        owner, errors = object(), []
        binding = admit_speech(agent, "tts", "uncertain", run.budget.account_id)
        def work():
            try:
                speak(voice, owner, run.budget.account_id, "uncertain")
            except BaseException as exc:
                errors.append(exc)
        thread = threading.Thread(target=contextvars.copy_context().run, args=(work,))
        thread.start()
        _wait_for_file(home / "tts-started")
        voice.stop_speech(owner)
        thread.join(5)
        for process in processes:
            process.wait(timeout=5)
        assert not thread.is_alive() and errors[0].code == "speech_termination_unknown"
        receipt = binding.receipt()
        assert receipt["unknown_usage"] and not receipt["slots_released"] and receipt["state"] == "unknown"
        assert receipt["reserved"]["executor_slots"] == 1 and receipt["reserved"]["attempts"] == 1
        assert receipt["actual"]["attempts"] == 1
        with expect("budget_exhausted"):
            speak(voice, owner, run.budget.account_id, "no-free-refund")
        assert len(processes) == 1


@pytest.mark.platforms("posix")
def test_actual_overrun_is_not_capped_and_blocks_further_work(factory, local_speech_packages, monkeypatch):
    import agent.speech_local as local
    agent, voice, db, _, _ = make_voice(factory)
    original, processes = local.subprocess.Popen, []
    real_monotonic = time.monotonic
    def spawn(*args, **kwargs):
        process = original(*args, **kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr(local.subprocess, "Popen", spawn)
    # Inject host scheduler delay between proven completion and accounting.
    monkeypatch.setattr(local, "time", SimpleNamespace(time=time.time,
        monotonic=lambda: real_monotonic() + (60 if processes and processes[-1].returncode is not None else 0)))
    with active(agent) as run:
        owner = object()
        output = speak(voice, owner, run.budget.account_id, "overrun")
        receipt = output["budget"]
        assert receipt["debt"] and receipt["actual"]["wall_ms"] > receipt["maxima"]["wall_ms"]
        assert receipt["consumed"]["wall_ms"] == receipt["actual"]["wall_ms"]
        with expect("budget_debt"):
            speak(voice, owner, run.budget.account_id, "after-debt")
        assert len(processes) == 1


@pytest.mark.platforms("posix")
def test_mission_cancellation_reaps_speech_and_charges_completed_local_work(factory, local_speech_packages):
    from agent.budget_account import BudgetBlocked
    from agent.task_scope import TaskCancelled
    agent, voice, db, home, _ = make_voice(factory, tts_extra={"block": True})
    with active(agent) as run:
        errors = []
        def work():
            try:
                speak(voice, object(), run.budget.account_id, "mission-stop")
            except BaseException as exc:
                errors.append(exc)
        thread = threading.Thread(target=contextvars.copy_context().run, args=(work,))
        thread.start()
        _wait_for_file(home / "tts-started")
        run.task_scope.request_cancel("Explicit mission cancellation")
        thread.join(5)
        assert not thread.is_alive() and len(errors) == 1
        assert isinstance(errors[0], (BudgetBlocked, TaskCancelled))
        ledger = db.get_budget_account(run.budget.account_id, run.budget.actor)
        assert ledger["consumed"]["attempts"] == 1 and ledger["consumed"]["wall_ms"] > 0
        assert not ledger["unknown_usage"] and not any(ledger["reserved"].values())
        import psutil
        assert not psutil.pid_exists(int((home / "tts-started").read_text()))
