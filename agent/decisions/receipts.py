"""Redacted receipts on the existing fenced journal; no separate payload log."""
from __future__ import annotations

from contextvars import ContextVar
import threading
import time

from agent.decisions.contracts import DecisionError, digest, require, sha256


RECEIPT_ADMISSION = ContextVar("decision_receipt_admission", default=None)


class ReceiptAdmission:
    """A bounded worker's deadline also fences its eventual journal transaction."""
    def __init__(self, deadline, fence=None):
        self.deadline, self.fence = deadline, fence
        self.abandoned = threading.Event()
        self._lock = threading.Lock()
        self._writes = []

    def check_deadline(self):
        require(not self.abandoned.is_set() and time.time() < self.deadline, "receipt_deadline")

    def check(self):
        self.check_deadline()
        if self.fence is not None:
            self.fence()
        self.check_deadline()

    def register(self, run):
        state = {"run": run, "entered": False, "commit_pending": False, "finished": False}
        with self._lock:
            self.check_deadline()
            self._writes.append(state)
        return state

    def enter(self, state, *, after_append):
        with self._lock:
            self.check_deadline()
            check_decision_owner(state["run"])
            state["entered"] = True
            state["commit_pending"] = after_append

    def finish(self, state, *, failed=False):
        with self._lock:
            if failed and state["entered"]:
                state["run"].dispatch_blocked.set()
            state["finished"] = True

    def abandon(self):
        with self._lock:
            self.abandoned.set()
            for state in self._writes:
                if state["commit_pending"] and not state["finished"]:
                    # Before the final guard, abandonment guarantees rollback.
                    # After it, an unacknowledged commit cannot be called absent.
                    state["run"].dispatch_blocked.set()


def check_decision_owner(run):
    """In-memory half of the fence, safe inside a brief publication lock."""
    from agent.runtime_context import current_agent_context
    require(current_agent_context() == run.context
            and getattr(run.agent, "_active_runtime_run", None) is run
            and not run.dispatch_blocked.is_set()
            and not getattr(run.agent, "_interrupt_requested", False), "receipt_authority_mismatch")
    if run.task_scope is not None:
        require(not run.task_scope.cancelled.is_set()
                and (run.task_scope.deadline is None or time.time() < run.task_scope.deadline),
                "receipt_authority_mismatch")
    if run.budget is not None:
        require(not run.budget.blocked_reason and time.time() < run.budget.deadline,
                "receipt_authority_mismatch")


def _append_decision_event(run, event_type, payload):
    admission = RECEIPT_ADMISSION.get()
    require(admission is not None, "receipt_admission_required")
    admission.check_deadline()
    from agent.runtime_commands import assert_runtime_dispatch
    from tools.capability_broker import CapabilityDenied, require_live_policy
    from hermes_state_runtime import RuntimeStoreError
    require(assert_runtime_dispatch() is run, "receipt_authority_mismatch")
    check_decision_owner(run)
    state = admission.register(run)
    actor = {key: getattr(run.context.identity, key) for key in ("principal_id", "profile_id", "agent_id")}

    def guard(conn, *, after_append):
        admission.check_deadline()
        check_decision_owner(run)
        require(require_live_policy(require_run=False) == run.context, "receipt_authority_mismatch")
        sid = run.db._effect_run_on_conn(conn, run.session_id, actor, run.run_id,
                                        run.holder, run.generation, dispatch=True)
        command = conn.execute("SELECT run_id,status FROM runtime_commands WHERE session_id=? AND command_id=?",
                               (sid, run.command_id)).fetchone()
        require(command is not None and command["run_id"] == run.run_id and command["status"] == "claimed",
                "receipt_authority_mismatch")
        # Timeout/abandonment and the final admission transition are atomic.
        # No I/O or lifecycle lock is held by this in-memory transition.
        admission.enter(state, after_append=after_append)

    try:
        result = run.db.append_runtime_event(run.session_id, event_type, payload,
            holder=run.holder, generation=run.generation, run_id=run.run_id, transaction_guard=guard)
    except BaseException as exc:
        # A rejected guard rolls back; an actual storage/commit failure may not
        # have a known durable outcome and must stop further dispatch.
        admission.finish(state, failed=not isinstance(exc,
            (DecisionError, RuntimeStoreError, CapabilityDenied, InterruptedError)))
        raise
    admission.finish(state)
    return result


def persist_decision_event(run, event_type, payload, *, deadline=None, fence=None):
    """Keep optional journal I/O off the lifecycle thread and its control lock."""
    from agent.decisions.batching import SHARED_RECEIPT_WORKERS
    deadline = time.time() + .05 if deadline is None else deadline
    if run.budget is not None:
        deadline = min(deadline, run.budget.deadline)
    result = []
    SHARED_RECEIPT_WORKERS.persist(lambda row: result.append(_append_decision_event(run, event_type, row)),
                                  (payload,), deadline=deadline, fence=fence)
    return result[0]


def receipt_for(request, result, contract, bundle, policy, *, route, reason, elapsed_ms):
    body = {"schema_version": 1, "point_id": request.point_id, "contract_version": request.contract_version,
        "contract_digest": request.contract_digest, "question_id": request.question_id,
        "request_id": request.request_id, "input_digest": request.state_packet.input_digest,
        "scope_digest": request.state_packet.scope_digest, "classification": request.state_packet.classification,
        "model_digest": bundle.model_digest, "calibration_digest": bundle.calibration_digest,
        "service_digest": bundle.service_digest, "mode": policy.mode,
        "thresholds": {"select": policy.threshold, **dict(policy.effect_thresholds)}, "point_gate_digest": policy.gate_digest,
        "live_options": list(request.live_options), "distribution": dict(result.distribution) if result else None,
        "selected": result.selected if result else None, "unclear": result.unclear if result else True,
        "actual_route": route, "fallback": reason, "incumbent": contract.fallback,
        "latency_ms": elapsed_ms, "node_latency_ms": result.latency_ms if result else None,
        "recorded_at": time.time(), "outcome": None, "raw_state_retained": False}
    return {**body, "receipt_id": digest(body)}


class JournalSink:
    """Construction does not capture authority; each write rechecks the live run."""
    durable = True

    def __init__(self, run):
        self.run = run

    def __call__(self, receipt):
        require(receipt["scope_digest"] == scope_digest(self.run.context), "receipt_scope_mismatch")
        if RECEIPT_ADMISSION.get() is None:
            return persist_decision_event(self.run, "decision.observed", receipt)
        return _append_decision_event(self.run, "decision.observed", receipt)

    def annotate(self, receipt_id, *, label, outcome, source_digest):
        from agent.runtime_commands import assert_runtime_dispatch
        from agent.decisions.contracts import label as valid_label
        from tools.capability_broker import require_live_policy
        sha256(receipt_id)
        sha256(source_digest)
        valid_label(label)
        require(outcome in {"correct", "incorrect", "unresolved", "recovered"}, "invalid_outcome")
        run = assert_runtime_dispatch()
        require(run is self.run and require_live_policy() == run.context, "receipt_authority_mismatch")
        with run.db._runtime_read() as conn:
            rows = conn.execute("SELECT payload_json FROM runtime_events WHERE session_id=? AND type='decision.observed' "
                                "ORDER BY seq DESC LIMIT 1000", (run.session_id,)).fetchall()
        import json
        found = next((json.loads(row[0]) for row in rows if json.loads(row[0]).get("receipt_id") == receipt_id), None)
        require(found is not None and found["scope_digest"] == scope_digest(run.context)
                and label in found["live_options"], "unknown_receipt_or_label")
        return run.db.append_runtime_event(run.session_id, "decision.outcome",
            {"receipt_id": receipt_id, "label": label, "outcome": outcome, "source_digest": source_digest},
            holder=run.holder, generation=run.generation, run_id=run.run_id)


def scope_digest(context):
    return digest({"identity": context.identity.to_record(), "policy_digest": context.policy.digest,
                   "profile_home_digest": digest(context.profile_home)})
