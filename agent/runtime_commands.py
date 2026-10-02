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
from contextlib import contextmanager
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
    dispatch_blocked: threading.Event = field(default_factory=threading.Event, compare=False)


_PENDING: ContextVar[_SubmittedCommand | None] = ContextVar("runtime_submitted_command", default=None)
_RUN: ContextVar[RuntimeRun | None] = ContextVar("runtime_authoritative_run", default=None)
_MAX_OUTCOME_BYTES = 240000
_SUPPORTED_API_MODES = frozenset({"chat_completions", "anthropic_messages", "bedrock_converse", "codex_responses"})


def supports_runtime_execution(agent) -> bool:
    return (getattr(agent, "api_mode", "chat_completions") in _SUPPORTED_API_MODES
            and getattr(agent, "provider", None) != "moa")


def _authority(agent):
    context = getattr(agent, "runtime_context", None)
    db = getattr(agent, "_session_db", None)
    if not isinstance(context, AgentContext) or db is None or getattr(agent, "_persist_disabled", False):
        raise RuntimeCommandError("identity_required")
    context.validate_profile_home()
    if not supports_runtime_execution(agent):
        raise RuntimeCommandError("runtime_transport_unsupported")
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
        if not isinstance(run, RuntimeRun):
            return rejected_receipt(agent, command["command_id"], "no_active_run")
        _check_run(run)
    receipt = db.submit_runtime_command(sid, actor=actor, command=command)
    if receipt["status"] == "rejected" or operation == "submit":
        return receipt
    record = db.read_runtime_command(sid, receipt["command_id"])
    if record["status"] != "accepted":
        return receipt
    if not isinstance(run, RuntimeRun):
        return receipt
    _check_run(run)
    if not db.claim_runtime_command(sid, receipt["command_id"], holder=run.holder, generation=run.generation):
        return receipt
    _check_run(run)
    if receipt.get("run_id") != run.run_id:
        # A recovered, unclaimed control belongs to the original run. It must
        # never steer or cancel whichever newer turn happens to own the lease.
        db.finish_runtime_command(sid, receipt["command_id"], holder=run.holder,
            generation=run.generation, status="blocked", result={"outcome": "target_run_ended", "applied": False})
        return receipt
    if operation == "steer":
        queued = agent.steer(command["payload"]["text"])
        result = {"outcome": "steer_queued" if queued else "steer_not_queued", "applied": False}
    else:
        requested = agent.interrupt(command["payload"].get("reason") or "Runtime cancellation requested", hard_cancel=True)
        result = {"outcome": "cancel_requested" if requested is not False else "cancel_not_requested", "provider_cancelled": False}
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
    return RuntimeRun(agent, db, sid, command_id, record["receipt"]["run_id"],
                      lease.holder, lease.generation, context)


def bind_runtime_run(run):
    if run is not None:
        run.agent._active_runtime_run = run
    return _RUN.set(run)


def reset_runtime_run(token, run):
    if run is not None and getattr(run.agent, "_active_runtime_run", None) is run:
        run.agent._active_runtime_run = None
    _RUN.reset(token)


def _check_run(run):
    if run.dispatch_blocked.is_set():
        raise RuntimeFenceError("Runtime outcome was not committed; further dispatch is blocked")
    lease = run.db.get_session_turn_lease(run.session_id)
    if (lease is None or lease["holder"] != run.holder or lease["generation"] != run.generation
            or lease["expires_at"] <= time.time()):
        raise RuntimeFenceError("Runtime owner generation is no longer current")
    record = run.db.read_runtime_command(run.session_id, run.command_id)
    if record is None or record["status"] != "claimed":
        raise RuntimeFenceError("Runtime command no longer owns dispatch")


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
    try:
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
    if error is not None:
        result = {"failed": True, "completed": False, "final_response": "",
                  "error_type": type(error).__name__}
    result = dict(result or {})
    result.pop("messages", None)  # stored transcript is the canonical message projection
    result["runtime_command_id"] = run.command_id
    if run.dispatch_blocked.is_set():
        result["outcome_uncertain"] = True
        result["failed"] = True
        result["completed"] = False
    status = "cancelled" if result.get("interrupted") else "failed" if result.get("failed") else "completed"
    stored = _bounded_outcome(result)
    run.db.finish_runtime_command(run.session_id, run.command_id, holder=run.holder,
                                  generation=run.generation, status=status, result=stored)
    snapshot = run.db.read_runtime_snapshot(run.session_id)
    run.db.publish_runtime_checkpoint(run.session_id, {
        "schema_version": 1, "config_version": run.context.config_digest,
        "policy_version": run.context.policy.digest, "runtime_version": "be02.v1",
        "prompt_projection_version": "1",
        **{key: snapshot[key] for key in ("outstanding_requests", "artifacts", "unresolved_effects")},
    }, holder=run.holder, generation=run.generation, expected_revision=snapshot["revision"],
        included_seq=snapshot["revision"])
