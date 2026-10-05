"""Process-wide bounded native inference and independent receipt workers.

A closed client socket does not prove that remote inference stopped. Once a
batch's completion is unknown, that physical destination remains quarantined
for this process. Recreating a turn's client never resets that fact.
"""
from __future__ import annotations

from concurrent.futures import Future, TimeoutError
from contextvars import copy_context
from dataclasses import dataclass
import threading
import time

from agent.decisions.contracts import DecisionError, number, require


@dataclass(frozen=True, slots=True)
class PlannerMetrics:
    batch_count: int = 0
    question_count: int = 0
    inference_ms: float = 0.0
    receipt_ms: float = 0.0
    admission_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    remote_unknown: bool = False

    def __post_init__(self):
        for name, limit in (("batch_count", 3), ("question_count", 64),
                            ("input_tokens", 3_000_000_000), ("output_tokens", 3_000_000_000)):
            value = getattr(self, name)
            require(type(value) is int and 0 <= value <= limit, "invalid_batch_metrics")
        for name in ("inference_ms", "receipt_ms", "admission_ms"):
            number(getattr(self, name), 0, 3_600_000, "invalid_batch_metrics")
        require(type(self.remote_unknown) is bool, "invalid_batch_metrics")

    def plus(self, other):
        return PlannerMetrics(self.batch_count + other.batch_count,
            self.question_count + other.question_count,
            self.inference_ms + other.inference_ms, self.receipt_ms + other.receipt_ms,
            self.admission_ms + other.admission_ms, self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens, self.remote_unknown or other.remote_unknown)


@dataclass
class _Destination:
    inflight: bool = False
    failures: int = 0
    open_until: float = 0.0
    remote_unknown: bool = False


class SharedBatchAdmission:
    """One live worker per physical destination, across all client instances.

    The bounded registry deliberately has no reset/eviction for quarantined
    destinations. Recovery requires separately qualified operator evidence; no
    model output, cooldown, new client, or transport-local flag can clear it.
    """
    def __init__(self, *, max_destinations=64):
        require(type(max_destinations) is int and 1 <= max_destinations <= 64, "invalid_capacity")
        self._max_destinations = max_destinations
        self._lock = threading.Lock()
        self._destinations = {}

    def submit(self, key, callback, *, timeout=None, completion_unknown=None):
        require(isinstance(key, str) and 0 < len(key) <= 512, "batch_admission_key_required")
        with self._lock:
            state = self._destinations.get(key)
            if state is None:
                require(len(self._destinations) < self._max_destinations, "node_capacity")
                state = self._destinations[key] = _Destination()
            require(not state.remote_unknown, "remote_completion_unknown")
            require(state.open_until <= time.monotonic(), "circuit_open")
            require(not state.inflight, "node_capacity")
            state.inflight = True
        future = Future()
        expires = None if timeout is None else time.monotonic() + timeout

        def work():
            result, error = None, None
            try:
                result = callback()
            except DecisionError as exc:
                error = DecisionError(exc.code)
            except Exception:
                error = DecisionError("node_unavailable")
            finally:
                own_unknown = ((error is not None and error.code in
                    {"remote_completion_unknown", "deadline_exceeded"}) or
                    (expires is not None and time.monotonic() >= expires))
                if completion_unknown is not None:
                    try:
                        own_unknown |= completion_unknown() is True
                    except Exception:
                        own_unknown = True
                with self._lock:
                    state.remote_unknown |= own_unknown
                    # Snapshot this submission BEFORE admitting the next one.
                    # A later client's quarantine must not be attributed here.
                    future.decision_remote_unknown = own_unknown
                    state.inflight = False
            # Publish only after release, so dependent stages cannot race it.
            if error is not None:
                future.set_exception(error)
            else:
                future.set_result(result)

        thread = threading.Thread(target=copy_context().run, args=(work,),
                                  daemon=True, name="native-decision-batch")
        try:
            thread.start()
        except RuntimeError:
            with self._lock:
                state.inflight = False
            raise DecisionError("node_capacity") from None
        return future

    def settle(self, key, *, failed, failure_limit, cooldown_seconds, remote_unknown=False):
        with self._lock:
            state = self._destinations[key]
            state.remote_unknown |= remote_unknown
            if failed:
                state.failures += 1
                if state.failures >= failure_limit:
                    state.open_until = time.monotonic() + cooldown_seconds
            else:
                state.failures = 0
                state.open_until = 0

    def remote_unknown(self, key):
        with self._lock:
            state = self._destinations.get(key)
            return bool(state and state.remote_unknown)


class ReceiptWorkers:
    """Bound an entire receipt batch independently of stuck inference work."""
    def __init__(self, capacity=4):
        require(type(capacity) is int and 1 <= capacity <= 16, "invalid_capacity")
        self._capacity = threading.BoundedSemaphore(capacity)

    def persist(self, sink, receipts, *, deadline, fence=None):
        from agent.decisions.receipts import RECEIPT_ADMISSION, ReceiptAdmission
        require(sink is not None, "receipt_unavailable")
        remaining = deadline - time.time()
        require(remaining >= .001, "receipt_deadline")
        require(self._capacity.acquire(blocking=False), "receipt_capacity")
        admission, future = ReceiptAdmission(deadline, fence), Future()

        def work():
            error = None
            token = RECEIPT_ADMISSION.set(admission)
            try:
                for receipt in receipts:
                    admission.check()
                    sink(receipt)
                admission.check_deadline()
            except Exception:
                error = DecisionError("receipt_unavailable")
            finally:
                RECEIPT_ADMISSION.reset(token)
                self._capacity.release()
            if error is not None:
                future.set_exception(error)
            else:
                future.set_result(True)

        thread = threading.Thread(target=copy_context().run, args=(work,),
                                  daemon=True, name="decision-batch-receipts")
        try:
            thread.start()
        except RuntimeError:
            self._capacity.release()
            raise DecisionError("receipt_capacity") from None
        try:
            result = future.result(timeout=max(0, deadline - time.time()))
            admission.check_deadline()
            return result
        except TimeoutError:
            admission.abandon()
            raise DecisionError("receipt_deadline") from None
        except BaseException:
            admission.abandon()
            raise


SHARED_BATCH_ADMISSION = SharedBatchAdmission()
SHARED_RECEIPT_WORKERS = ReceiptWorkers()
