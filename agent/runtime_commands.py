"""Durable commands around the existing turn loop, using its sole writer lease.

Acceptance is not execution. A claimed command is never automatically replayed:
process death after a provider/tool call may leave its outcome uncertain. Only an
unclaimed command may be resumed; reads and duplicate delivery never run effects.
"""
from __future__ import annotations

import hashlib
import json
import time
import threading
import uuid
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from agent.runtime_context import AgentContext, current_agent_context


class RuntimeCommandError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class RuntimeFenceError(InterruptedError):
    """The durable writer no longer authorizes this dispatch."""


@dataclass(frozen=True)
class _SubmittedCommand:
    agent: Any
    command_id: str


@dataclass(frozen=True)
class RuntimeRun:
    agent: Any
    db: Any
    session_id: str
    command_id: str
    run_id: str
    holder: str
    generation: int
    context: AgentContext
    budget: Any = None
    task_scope: Any = None
    dispatch_blocked: threading.Event = field(default_factory=threading.Event, compare=False)
    control_lock: threading.RLock = field(default_factory=threading.RLock, compare=False)


_PENDING: ContextVar[_SubmittedCommand | None] = ContextVar("runtime_submitted_command", default=None)
_RUN: ContextVar[RuntimeRun | None] = ContextVar("runtime_authoritative_run", default=None)
_MAX_OUTCOME_BYTES = 240000


def runtime_execution_denial(agent) -> str | None:
    if isinstance(getattr(agent, "runtime_context", None), AgentContext):
        # Only owned local response surfaces are certified here. Gateway/cron
        # external delivery happens after the turn context exits and requires
        # the separate recipient-bound BE06 delivery adapter.
        if getattr(agent, "platform", None) not in {None, "cli", "tui", "desktop", "subagent"}:
            return "runtime_delivery_transport_unsupported"
        import openai
        # BE05 certifies the guarded HTTPX chat route only. A runtime mutation
        # must not jump into a native adapter that bypasses recipient checks.
        if getattr(agent, "api_mode", None) != "chat_completions" or type(getattr(agent, "client", None)) is not openai.OpenAI:
            return "runtime_transport_unsupported"
    from agent.provider_capabilities import provider_capabilities_for
    return None if provider_capabilities_for(agent).durable_execution else "runtime_transport_unsupported"


def supports_runtime_execution(agent) -> bool:
    return runtime_execution_denial(agent) is None


def _authority(agent):
    context = getattr(agent, "runtime_context", None)
    db = getattr(agent, "_session_db", None)
    if not isinstance(context, AgentContext) or db is None or getattr(agent, "_persist_disabled", False):
        raise RuntimeCommandError("identity_required")
    context.validate_profile_home()
    if denial := runtime_execution_denial(agent):
        raise RuntimeCommandError(denial)
    if not callable(getattr(type(db), "submit_runtime_command", None)):
        raise RuntimeCommandError("durable_store_required")
    sid = str(agent.session_id)
    stored = db.get_session_model_config_value(sid, "agent_identity")
    if stored != context.identity.to_record():
        raise RuntimeCommandError("identity_mismatch")
    return context, db, sid


def _envelope(agent, envelope):
    context, db, session_id = _authority(agent)
    expected = {"schema_version", "command_id", "idempotency_key", "expected_revision", "operation", "payload"}
    if not isinstance(envelope, dict) or set(envelope) != expected:
        raise RuntimeCommandError("invalid_command")
    if type(envelope["schema_version"]) is not int or envelope["schema_version"] != 1:
        raise RuntimeCommandError("unsupported_schema")
    operation, payload = envelope["operation"], envelope["payload"]
    if not isinstance(payload, dict):
        raise RuntimeCommandError("invalid_command")
    if operation in {"submit", "steer"}:
        if (set(payload) != {"text"} or not isinstance(payload["text"], str)
                or not payload["text"].strip() or len(payload["text"]) > 65536):
            raise RuntimeCommandError("invalid_command")
    elif operation == "cancel":
        if set(payload) - {"reason"} or not isinstance(payload.get("reason", ""), str) or len(payload.get("reason", "")) > 1024:
            raise RuntimeCommandError("invalid_command")
    elif operation == "approval":
        raise RuntimeCommandError("approval_unsupported")
    else:
        raise RuntimeCommandError("unsupported_operation")
    identity = context.identity
    actor = {"principal_id": identity.principal_id, "agent_id": identity.agent_id, "profile_id": identity.profile_id}
    command = {**envelope, "identity_binding": dict(actor)}
    return db, session_id, actor, command


def rejected_receipt(agent, command_id: str, code: str) -> dict:
    _context, db, sid = _authority(agent)
    return {"schema_version": 1, "command_id": command_id, "status": "rejected",
            "durable_revision": db.read_runtime_snapshot(sid)["revision"], "run_id": None,
            "conflict": {"code": code, "message": code.replace("_", " ")}}


def read_command_state(agent, command_id: str) -> dict | None:
    _context, db, sid = _authority(agent)
    return db.read_runtime_command(sid, command_id)


def submit_command(agent, envelope: dict) -> dict:
    run = getattr(agent, "_active_runtime_run", None)
    if isinstance(run, RuntimeRun) and isinstance(envelope, dict) and envelope.get("operation") != "submit":
        with run.control_lock:
            return _submit_command(agent, envelope, expected_run=run)
    return _submit_command(agent, envelope)


def _control_owner_still_active(db, sid, actor, command_id, run):
    try:
        _check_run(run)
    except RuntimeFenceError:
        record = db.settle_inactive_runtime_control(sid, actor, command_id)
        if record["status"] == "blocked":
            return False
        raise
    return True


def _submit_command(agent, envelope: dict, *, expected_run=None) -> dict:
    """Accept once; controls record requests against the current local owner.

    A submit is executed by the existing host pipeline inside
    ``bind_submitted_command``. A steer/cancel completion records that a request
    was queued, never that the provider applied it or stopped an external effect.
    """
    db, sid, actor, command = _envelope(agent, envelope)
    operation = command["operation"]
    run = getattr(agent, "_active_runtime_run", None)
    existing = db.read_runtime_command(sid, command["command_id"])
    if operation != "submit" and existing is None:
        if expected_run is not None and run is not expected_run:
            return rejected_receipt(agent, command["command_id"], "target_run_ended")
        if not isinstance(run, RuntimeRun):
            if operation == "cancel":
                from agent.admission import AdmissionQueue
                queued = AdmissionQueue(db).cancel_command(agent, envelope)
                if queued is not None:
                    return queued
            return rejected_receipt(agent, command["command_id"], "no_active_run")
        try:
            _check_run(run)
        except RuntimeFenceError:
            return rejected_receipt(agent, command["command_id"], "no_active_run")
    policy = getattr(agent, "_runtime_budget_policy", None)
    from hermes_state_runtime import RuntimeStoreError
    try:
        receipt = db.submit_runtime_command(sid, actor=actor, command=command,
            budget_policy_json=policy.snapshot if policy is not None and operation == "submit" else None)
    except RuntimeStoreError as exc:
        if exc.code == "no_active_run":
            return rejected_receipt(agent, command["command_id"], "no_active_run")
        raise
    if receipt["status"] == "rejected" or operation == "submit":
        return receipt
    record = db.read_runtime_command(sid, receipt["command_id"])
    if record["status"] != "accepted":
        if record["status"] == "blocked" and (record.get("result") or {}).get("outcome") == "target_run_ended":
            return rejected_receipt(agent, command["command_id"], "target_run_ended")
        return receipt
    if not isinstance(run, RuntimeRun):
        record = db.settle_inactive_runtime_control(sid, actor, receipt["command_id"])
        if record["status"] == "blocked":
            return rejected_receipt(agent, command["command_id"], "target_run_ended")
        return receipt
    if not _control_owner_still_active(db, sid, actor, receipt["command_id"], run):
        return rejected_receipt(agent, command["command_id"], "target_run_ended")
    try:
        claimed = db.claim_runtime_command(sid, receipt["command_id"], holder=run.holder, generation=run.generation)
    except RuntimeStoreError as exc:
        if exc.code == "stale_owner":
            settled = db.settle_inactive_runtime_control(sid, actor, receipt["command_id"])
            if settled["status"] == "blocked":
                return rejected_receipt(agent, command["command_id"], "target_run_ended")
        raise
    if not claimed:
        return receipt
    if not _control_owner_still_active(db, sid, actor, receipt["command_id"], run):
        return rejected_receipt(agent, command["command_id"], "target_run_ended")
    if receipt.get("run_id") != run.run_id:
        # A recovered, unclaimed control belongs to the original run. It must
        # never steer or cancel whichever newer turn happens to own the lease.
        db.finish_runtime_command(sid, receipt["command_id"], holder=run.holder,
            generation=run.generation, status="blocked", result={"outcome": "target_run_ended", "applied": False})
        return receipt
    if operation == "steer":
        queued = agent.steer(command["payload"]["text"])
        result = {"outcome": "steer_queued" if queued else "steer_not_queued", "applied": False}
        if getattr(agent, "_mission_finalizing_run_id", None) == run.run_id:
            result["missed_steer"] = True
    else:
        from agent.admission import AdmissionQueue
        cancelled_queued = AdmissionQueue(db).cancel_session(sid)
        from agent.task_scope import create_task_scope
        scope = run.task_scope or create_task_scope(agent, run.run_id)
        cancellation = scope.request_cancel(command["payload"].get("reason") or "Runtime cancellation requested")
        result = {"outcome": "cancel_requested", "provider_cancelled": False, "cancellation": cancellation, "cancelled_queued": cancelled_queued}
    db.finish_runtime_command(sid, receipt["command_id"], holder=run.holder,
                              generation=run.generation, result=result)
    return receipt


@contextmanager
def bind_submitted_command(agent, receipt: dict):
    """Bind inside the admitted worker, never in a shared mutable session slot."""
    token = _PENDING.set(_SubmittedCommand(agent, receipt["command_id"]))
    try:
        yield
    finally:
        _PENDING.reset(token)


def prepare_turn_command(agent, user_message: Any) -> dict | None:
    """Legacy turns are untouched; each strict logical turn gets a fresh key."""
    if not isinstance(getattr(agent, "runtime_context", None), AgentContext):
        return None
    pending = _PENDING.get()
    if pending is not None and pending.agent is agent:
        _PENDING.set(None)
        record = read_command_state(agent, pending.command_id)
        if record is None or record["command"]["operation"] != "submit":
            raise RuntimeCommandError("unknown_submission")
        return record
    command_id = uuid.uuid4().hex
    # Normal surfaces may carry multimodal content. The journal identifies the
    # logical input; the existing transcript remains the prompt projection.
    text = user_message if isinstance(user_message, str) else json.dumps(user_message, ensure_ascii=False)
    if not text.strip():
        text = "[empty turn]"
    receipt = submit_command(agent, {"schema_version": 1, "command_id": command_id,
        "idempotency_key": command_id, "expected_revision": None, "operation": "submit",
        "payload": {"text": text}})
    if receipt["status"] == "rejected":
        raise RuntimeCommandError((receipt.get("conflict") or {}).get("code", "command_rejected"))
    return read_command_state(agent, receipt["command_id"])


def recorded_turn_result(agent, record: dict) -> dict:
    if isinstance(record.get("result"), dict) and record["status"] in {"completed", "failed", "cancelled", "blocked"}:
        result = dict(record["result"])
        if result.get("runtime_result"):
            from gateway.durable_outbox import load_committed_result
            loaded = load_committed_result(agent, record["receipt"]["command_id"])
            loaded["runtime_result"] = result["runtime_result"]
            result = loaded
        if result.get("output_omitted"):
            return {"completed": False, "final_response": "", "messages": [],
                    "runtime_status": "recorded_output_unavailable",
                    "runtime_command_id": record["receipt"]["command_id"],
                    "recorded_output": result}
        if "messages" not in result:
            result["messages"] = agent._session_db.get_messages_as_conversation(agent.session_id, include_row_ids=True)
        return result
    return {"completed": False, "final_response": "", "messages": [],
            "runtime_status": "outcome_uncertain" if record["status"] == "claimed" else "pending",
            "runtime_command_id": record["receipt"]["command_id"]}


def claim_turn_command(agent, record, lease):
    if record is None:
        return None
    if lease is None or not isinstance(lease.generation, int):
        raise RuntimeCommandError("durable_lease_required")
    context, db, sid = _authority(agent)
    command_id = record["receipt"]["command_id"]
    if not db.claim_runtime_command(sid, command_id, holder=lease.holder, generation=lease.generation):
        return None
    from agent.budget_account import create_run_budget, BudgetBlocked, BudgetPolicyError
    from agent.task_scope import create_task_scope
    run_id = record["receipt"]["run_id"]
    try:
        budget = create_run_budget(agent, db, context, run_id, lease.holder, lease.generation, command_id)
    except (BudgetBlocked, BudgetPolicyError, ValueError) as exc:
        db.finish_runtime_command(sid, command_id, holder=lease.holder,
            generation=lease.generation, status="blocked", result={"completed": False,
            "runtime_status": "blocked", "final_response": str(exc), "budget_blocked": True})
        return None
    scope = create_task_scope(agent, run_id, deadline=budget.deadline if budget else None)
    return RuntimeRun(agent, db, sid, command_id, run_id,
                      lease.holder, lease.generation, context, budget=budget, task_scope=scope)


def bind_runtime_run(run):
    if run is not None:
        run.agent._active_runtime_run = run
        if run.task_scope is not None:
            run.task_scope.start_deadline_watchdog()
    return _RUN.set(run)


def reset_runtime_run(token, run):
    if run is not None and run.task_scope is not None:
        run.task_scope.stop_deadline_watchdog()
    if run is not None and getattr(run.agent, "_active_runtime_run", None) is run:
        run.agent._active_runtime_run = None
    if run is not None and getattr(run.agent, "_mission_finalizing_run_id", None) == run.run_id:
        run.agent._mission_finalizing_run_id = None
    _RUN.reset(token)


def _check_run(run):
    if run.budget is not None:
        run.budget.check()
    if run.task_scope is not None:
        run.task_scope.check_cancelled()
    if run.dispatch_blocked.is_set():
        raise RuntimeFenceError("Runtime outcome was not committed; further dispatch is blocked")
    _assert_run_ownership(run)


def _assert_run_ownership(run):
    lease = run.db.get_session_turn_lease(run.session_id)
    if (lease is None or lease["holder"] != run.holder or lease["generation"] != run.generation
            or lease["expires_at"] <= time.time()):
        raise RuntimeFenceError("Runtime owner generation is no longer current")
    record = run.db.read_runtime_command(run.session_id, run.command_id)
    if (record is None or record["status"] != "claimed"
            or record["receipt"]["run_id"] != run.run_id):
        raise RuntimeFenceError("Runtime command no longer owns dispatch")


def assert_runtime_finalization(run):
    """Authorize bounded private result persistence, never model/tool dispatch.

    Cancellation/exhausted budgets stop new work, but must not erase output
    already produced. Identity, policy, command ownership and generation still
    apply; no stale or revoked owner may use this finalization-only path.
    """
    if (not isinstance(run, RuntimeRun) or _RUN.get() is not run
            or current_agent_context() != run.context
            or getattr(run.agent, "_active_runtime_run", None) is not run):
        raise RuntimeFenceError("Finalization requires its exact bound runtime owner")
    from tools.capability_broker import require_live_policy
    require_live_policy(require_run=False)
    _assert_run_ownership(run)
    return run


def assert_runtime_dispatch(agent=None):
    """Recheck real durable ownership on every actual dispatch, including workers."""
    run = _RUN.get()
    context = current_agent_context()
    if run is None:
        if context is not None or isinstance(getattr(agent, "runtime_context", None), AgentContext):
            raise RuntimeFenceError("Strict dispatch requires an admitted durable command")
        return None
    if context != run.context or (agent is not None and agent is not run.agent):
        raise RuntimeFenceError("Runtime dispatch identity does not match its command")
    if not supports_runtime_execution(run.agent):
        raise RuntimeFenceError("Runtime transport changed to an unsupported execution route")
    _check_run(run)
    return run


def _json_value(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "__dict__"):
        return {key: item for key, item in vars(value).items() if not key.startswith("_")}
    raise TypeError("unsupported outcome type")


def _bounded_outcome(value):
    encoded = json.dumps(value, default=_json_value, ensure_ascii=False, allow_nan=False)
    if len(encoded.encode()) <= _MAX_OUTCOME_BYTES:
        return json.loads(encoded)
    # Never replay an omitted output. The transcript/provider projection owns
    # large data; the digest is evidence only, not a pretend reconstructible blob.
    return {"output_omitted": True, "sha256": hashlib.sha256(encoded.encode()).hexdigest(),
            "bytes": len(encoded.encode()), "reason": "inline_outcome_limit"}


def _record_operation_event(run, event_type, payload, **correlations):
    try:
        return run.db.append_runtime_event(run.session_id, event_type, payload,
                                          holder=run.holder, generation=run.generation, **correlations)
    except Exception as exc:
        run.dispatch_blocked.set()
        raise RuntimeFenceError("Runtime operation journal failed; outcome requires reconciliation") from exc


def invoke_runtime_operation(kind: str, callback, *, agent=None, name: str = "", operation_id: str | None = None):
    run = assert_runtime_dispatch(agent)
    if run is None:
        return callback()
    operation_id = operation_id or uuid.uuid4().hex
    common = {"run_id": run.run_id, "operation_id": operation_id}
    payload = {"command_id": run.command_id, "kind": kind, "name": name, "phase": "started"}
    _record_operation_event(run, f"{kind}.started", payload, **common)
    assert_runtime_dispatch(agent)
    from agent.budget_account import budget_tool_scope
    try:
        with budget_tool_scope(run, name) if kind == "tool" else nullcontext():
            result = callback()
    except BaseException as exc:
        _record_operation_event(run, f"{kind}.failed", {
            **payload, "phase": "failed", "error_type": type(exc).__name__}, **common)
        raise
    try:
        outcome = _bounded_outcome(result)
    except Exception as exc:
        run.dispatch_blocked.set()
        raise RuntimeFenceError("Runtime output could not be recorded") from exc
    _record_operation_event(run, f"{kind}.completed", {
        **payload, "phase": "completed", "result": outcome}, **common)
    return result


def finish_turn_command(run, result=None, error=None):
    if run is None:
        return
    with run.control_lock:
        return _finish_turn_command(run, result=result, error=error)


def _finish_turn_command(run, result=None, error=None):
    original_result = result
    if run is None:
        return
    if error is not None:
        result = {"failed": True, "completed": False, "final_response": "",
                  "error_type": type(error).__name__}
    result = dict(result or {})
    if "mission" not in result:
        from agent.mission_runtime import finalize_mission_result
        result = finalize_mission_result(run, result)
    result.pop("messages", None)  # stored transcript is the canonical message projection
    result["runtime_command_id"] = run.command_id
    if run.dispatch_blocked.is_set():
        result["outcome_uncertain"] = True
        result["failed"] = True
        result["completed"] = False
    if run.budget is not None:
        if result.get("interrupted") or (run.task_scope is not None and run.task_scope.cancelled.is_set()):
            run.budget.db.close_budget_account(run.budget.account_id, run.budget.actor,
                                               state="cancelled", **run.budget.fence)
        result["runtime_budget"] = run.budget.status()
        if run.budget.blocked_reason:
            result["completed"] = False
            result["runtime_status"] = "partial" if result.get("final_response") else "blocked"
            result["budget_blocked"] = True
            if not result.get("final_response"):
                result["final_response"] = run.budget.blocked_reason
    if run.task_scope is not None:
        result["cancellation"] = run.task_scope.complete(partial_result_available=bool(result.get("final_response")))
    status = ("blocked" if result.get("budget_blocked") else "cancelled" if result.get("interrupted")
              else "failed" if result.get("failed") else "completed")
    # The caller's partial response/artifacts remain usable with explicit budget status.
    if isinstance(original_result, dict):
        original_result.update(result)
    from gateway.durable_outbox import commit_result
    reference = commit_result(run, result, status)
    from agent.mission_runtime import refresh_mission_result
    refresh_mission_result(run.agent, result)
    if isinstance(original_result, dict):
        if "mission" in result:
            original_result["mission"] = result["mission"]
        original_result["runtime_result"] = reference
    snapshot = run.db.read_runtime_snapshot(run.session_id)
    run.db.publish_runtime_checkpoint(run.session_id, {
        "schema_version": 1, "config_version": run.context.config_digest,
        "policy_version": run.context.policy.digest, "runtime_version": "be06.v1",
        "prompt_projection_version": "1",
        **{key: snapshot[key] for key in ("outstanding_requests", "artifacts", "unresolved_effects", "unresolved_invocations")},
    }, holder=run.holder, generation=run.generation, expected_revision=snapshot["revision"],
        included_seq=snapshot["revision"])
