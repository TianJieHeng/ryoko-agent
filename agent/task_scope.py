"""Owned cancellation over existing agent, wait and process handles.

An interrupt requests local cooperation. Neither a closed socket nor a finished
Python worker acknowledges provider cancellation or undoes an external effect.
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


class TaskCancelled(InterruptedError):
    pass


@dataclass
class TaskScope:
    agent: Any
    run_id: str
    deadline: float | None = None
    cancelled: threading.Event = field(default_factory=threading.Event)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    _requested_at: float | None = None
    _request_id: str | None = None
    _completed: bool = False
    _partial: bool = False
    _upstream_ack: bool | None = None
    _pending_handles: set[str] = field(default_factory=set)
    _baseline_processes: frozenset = field(default_factory=frozenset, repr=False)
    _deadline_handle: Any = field(default=None, repr=False)
    _child_scopes: list = field(default_factory=list, repr=False)

    def __post_init__(self):
        from tools.process_registry import process_registry
        self._baseline_processes = frozenset(
            item["session_id"] for item in process_registry.list_sessions() if item["status"] == "running")

    def start_deadline_watchdog(self):
        if self.deadline is None or self._deadline_handle is not None:
            return
        from agent.periodic_scheduler import schedule
        def tick():
            if self._completed or self.cancelled.is_set():
                return False
            if time.time() >= self.deadline:
                self.request_cancel("Runtime deadline exceeded")
                return False
        self._deadline_handle = schedule(tick, min(0.25, max(0.01, self.deadline - time.time())))

    def stop_deadline_watchdog(self):
        if self._deadline_handle is not None:
            self._deadline_handle.cancel()

    def check_cancelled(self) -> None:
        if self.deadline is not None and time.time() >= self.deadline:
            self.request_cancel("Runtime deadline exceeded")
        if self.cancelled.is_set() or getattr(self.agent, "_interrupt_requested", False):
            raise TaskCancelled("Runtime task cancellation requested")

    def request_cancel(self, reason: str = "Runtime cancellation requested") -> dict:
        with self._lock:
            if self._requested_at is not None or self._completed:
                return self.receipt()
            self._requested_at, self._request_id = time.time(), uuid.uuid4().hex
            # Close admission before invoking callbacks: an interrupted waiter can
            # resume immediately on a different thread.
            self.cancelled.set()
        self._request_handle("agent", lambda: self.agent.interrupt(reason, hard_cancel=True))
        cancel_waits = getattr(self.agent, "_runtime_cancel_waits", None)
        if callable(cancel_waits):
            self._request_handle("human_wait", cancel_waits)
        self._stop_owned_processes()
        children_lock = getattr(self.agent, "_active_children_lock", None)
        if children_lock is not None:
            with children_lock:
                children = list(self.agent._active_children)
            for child in children:
                run = getattr(child, "_active_runtime_run", None)
                scope = getattr(run, "task_scope", None)
                if scope is not None and scope is not self:
                    self._child_scopes.append(scope)
                    self._request_handle("child:" + scope.run_id, lambda scope=scope: scope.request_cancel(reason))
                else:
                    with self._lock:
                        self._pending_handles.add("child_completion")
        return self.receipt()

    def _request_handle(self, name, callback):
        try:
            acknowledged = callback()
        except Exception:
            acknowledged = False
        if acknowledged is False:
            with self._lock:
                self._pending_handles.add(name)

    def _stop_owned_processes(self):
        # The existing registry owns PID identity checks and process-tree cleanup.
        # Match the actual spawning task, never the shared sandbox/session key.
        owner = getattr(self.agent, "_current_task_id", None)
        if not isinstance(owner, str) or not owner:
            return
        from tools.process_registry import process_registry
        self._request_handle("owned_processes", lambda: process_registry.kill_started_since(
            owner, self._baseline_processes, source="runtime_cancel"))
        if process_registry.snapshot_running_ids(owner) - self._baseline_processes:
            with self._lock:
                self._pending_handles.add("owned_processes")

    def acknowledge_upstream(self, stopped: bool) -> None:
        """Only a provider's explicit acknowledgment may set this field."""
        if type(stopped) is not bool:
            raise ValueError("upstream acknowledgment must be boolean")
        with self._lock:
            self._upstream_ack = stopped

    def complete(self, *, partial_result_available: bool = False) -> dict:
        self.stop_deadline_watchdog()
        if self.cancelled.is_set():
            self._stop_owned_processes()
        with self._lock:
            self._completed = True
            self._partial = partial_result_available
        return self.receipt()

    def receipt(self, *, pending_effect_ids=()) -> dict:
        with self._lock:
            pending = set(self._pending_handles)
            pending.update("child:" + scope.run_id for scope in self._child_scopes if not scope._completed)
            tracker_lock = getattr(self.agent, "_tool_worker_threads_lock", None)
            if tracker_lock is not None:
                with tracker_lock:
                    worker_ids = set(self.agent._tool_worker_threads)
                if worker_ids & {thread.ident for thread in threading.enumerate()}:
                    pending.add("tool_threads")
            return {"request_id": self._request_id, "requested_at": self._requested_at,
                    "local_state": "stopped" if self._completed and not pending else "requested" if self.cancelled.is_set() else "running",
                    "upstream_ack": self._upstream_ack,
                    "pending_effect_ids": sorted(set(pending_effect_ids)),
                    "pending_handles": sorted(pending),
                    "partial_result_available": self._partial,
                    "remote_effects_undone": False}


def create_task_scope(agent, run_id: str, deadline: float | None = None) -> TaskScope:
    return TaskScope(agent, run_id, deadline)
