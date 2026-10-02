"""Cancellation uses real existing hooks and never invents remote acknowledgments."""
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from agent.interrupt_control import InterruptControlMixin
from agent.task_scope import TaskCancelled, create_task_scope


class Agent(InterruptControlMixin):
    def __init__(self):
        self._interrupt_requested = False
        self._interrupt_message = None
        self._hard_interrupt_requested = threading.Event()
        self._execution_thread_id = None
        self._active_children = []
        self._active_children_lock = threading.Lock()
        self._tool_worker_threads = set()
        self._tool_worker_threads_lock = threading.Lock()
        self.quiet_mode = True
        self.api_mode = "chat_completions"


def test_stop_reaches_provider_wait_tool_and_child_without_claiming_remote_stop():
    from tools.interrupt import is_interrupted, set_interrupt
    agent, child = Agent(), Agent()
    child_scope = create_task_scope(child, "child")
    child._active_runtime_run = SimpleNamespace(task_scope=child_scope)
    agent._active_children.append(child)
    provider_aborted = threading.Event()
    waiter = threading.Event()
    tool_ready, tool_stopped = threading.Event(), threading.Event()
    agent._active_request_abort = lambda reason: provider_aborted.set()
    agent._runtime_cancel_waits = waiter.set

    def tool():
        tid = threading.get_ident()
        with agent._tool_worker_threads_lock:
            agent._tool_worker_threads.add(tid)
        tool_ready.set()
        try:
            until = time.monotonic() + 3
            while time.monotonic() < until:
                if is_interrupted():
                    tool_stopped.set()
                    return
                time.sleep(0.01)
        finally:
            set_interrupt(False)
    worker = threading.Thread(target=tool)
    worker.start()
    assert tool_ready.wait(2)
    scope = create_task_scope(agent, "parent")
    result = scope.request_cancel("stop")
    worker.join(3)
    assert provider_aborted.is_set() and waiter.is_set() and tool_stopped.is_set()
    assert child._hard_interrupt_requested.is_set() and child_scope.cancelled.is_set()
    assert result["local_state"] == "requested" and result["upstream_ack"] is None
    assert result["remote_effects_undone"] is False
    assert scope.request_cancel("retry")["request_id"] == result["request_id"]
    with pytest.raises(TaskCancelled):
        scope.check_cancelled()
    result = scope.complete(partial_result_available=True)
    assert result["local_state"] == "requested" and "child:child" in result["pending_handles"]
    child_scope.complete()
    result = scope.receipt()
    assert result["local_state"] == "stopped" and result["partial_result_available"]
    assert result["upstream_ack"] is None


def test_owned_process_cleanup_preserves_earlier_same_owner_and_foreign_process(tmp_path, monkeypatch):
    from tools.process_registry import ProcessRegistry
    registry = ProcessRegistry()
    monkeypatch.setattr("tools.process_registry.process_registry", registry)
    processes = []
    def spawn(owner):
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        processes.append(proc)
        registry.adopt_local(proc, command="harmless sleep fixture", cwd=str(tmp_path),
                             task_id="shared-environment", owner_task_id=owner, notify_on_complete=False)
        return proc
    try:
        earlier = spawn("same-conversation-task")
        agent = Agent()
        agent._current_task_id = "same-conversation-task"
        scope = create_task_scope(agent, "new-run")
        owned, foreign = spawn("same-conversation-task"), spawn("other-principal")
        scope.request_cancel("stop")
        assert owned.wait(timeout=5) is not None
        assert earlier.poll() is None and foreign.poll() is None
        assert not (registry.snapshot_running_ids(agent._current_task_id) - scope._baseline_processes)
        scope.complete()
    finally:
        for proc in processes:
            if proc.poll() is None:
                proc.terminate()
            proc.wait(timeout=5)


def test_deadline_watchdog_interrupts_blocked_request_and_stops_at_completion():
    agent = Agent()
    aborted = threading.Event()
    agent._active_request_abort = lambda reason: aborted.set()
    scope = create_task_scope(agent, "deadline", deadline=time.time() + 0.05)
    scope.start_deadline_watchdog()
    try:
        assert aborted.wait(2)
        assert scope.cancelled.is_set()
    finally:
        scope.complete(partial_result_available=True)
    assert scope._deadline_handle.cancelled
