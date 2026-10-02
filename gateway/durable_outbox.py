"""Owned local-runtime result delivery, independent of inference and tool dispatch.

The immutable full result is recoverable even when its notification never reaches
an attached client. A successful transport write is not a client/human receipt.
"""
from __future__ import annotations

import base64
import json

from hermes_state_runtime import RuntimeStoreError, _require

MAX_RESULT_CHUNK_BYTES = 65536
# Keep the user-facing result, not a second transcript/provider-state store.
_RESULT_FIELDS = frozenset({
    "final_response", "completed", "failed", "partial", "interrupted", "runtime_status",
    "runtime_command_id", "runtime_budget", "cancellation", "budget_blocked", "outcome_uncertain",
    "error_type", "error", "failure_reason", "billing_block", "response_previewed",
    "response_transformed", "attachments", "artifact_refs", "artifacts", "media_files", "media",
})


def _authority(agent):
    from agent.runtime_context import AgentContext
    context = getattr(agent, "runtime_context", None)
    db = getattr(agent, "_session_db", None)
    _require(isinstance(context, AgentContext) and db is not None,
             "identity_required", "A bound runtime identity and store are required")
    context.validate_profile_home()
    from agent.identity_lifecycle import agent_runtime_scope
    from tools.capability_broker import require_live_policy
    with agent_runtime_scope(context):
        _require(require_live_policy(require_run=False) == context,
                 "identity_mismatch", "Runtime policy is no longer current")
    sid = str(agent.session_id)
    _require(db.get_session_model_config_value(sid, "agent_identity") == context.identity.to_record(),
             "identity_mismatch", "Runtime session identity changed")
    actor = {key: getattr(context.identity, key) for key in ("principal_id", "profile_id", "agent_id")}
    return context, db, sid, actor


def commit_result(run, result, status):
    """Publish full immutable bytes, then commit visibility plus completion and intent."""
    from agent.result_artifacts import publish_result_artifact
    from agent.runtime_commands import _bounded_outcome, _json_value
    result = {key: value for key, value in result.items() if key in _RESULT_FIELDS}
    encoded = json.dumps(result, default=_json_value, ensure_ascii=False, allow_nan=False,
                         sort_keys=True, separators=(",", ":")).encode("utf-8")
    actor = {key: getattr(run.context.identity, key) for key in ("principal_id", "profile_id", "agent_id")}
    from hermes_state_delivery import result_artifact_id
    artifact_id = result_artifact_id(actor, run.run_id, run.command_id)
    descriptor = publish_result_artifact(run, encoded, artifact_id)
    reference = run.db.commit_runtime_result(run.session_id, run.command_id, actor=actor,
        descriptor=descriptor, holder=run.holder, generation=run.generation,
        status=status, result_summary=_bounded_outcome(result))
    return reference


def load_committed_result(agent, command_id):
    from agent.identity_lifecycle import agent_runtime_scope
    from agent.result_artifacts import read_result_artifact
    context, db, sid, actor = _authority(agent)
    descriptor = db.read_runtime_result_artifact(sid, actor, command_id)
    with agent_runtime_scope(context):
        data = read_result_artifact(context, descriptor)
    result = json.loads(data)
    _require(isinstance(result, dict), "invalid_artifact", "Committed result must be a JSON object")
    return result


def read_result(agent, command_id, offset=0, limit=MAX_RESULT_CHUNK_BYTES):
    """Byte chunks are digest-checked against the complete immutable result each read."""
    from agent.identity_lifecycle import agent_runtime_scope
    from agent.result_artifacts import read_result_artifact
    _require(type(offset) is int and offset >= 0 and type(limit) is int
             and 1 <= limit <= MAX_RESULT_CHUNK_BYTES,
             "invalid_command", "Result chunk bounds are invalid")
    context, db, sid, actor = _authority(agent)
    location = db.locate_runtime_result(sid, actor, command_id)
    descriptor = location["descriptor"]
    _require(offset <= descriptor["size"], "invalid_command", "Result offset exceeds its size")
    with agent_runtime_scope(context):
        data = read_result_artifact(context, descriptor)
    chunk = data[offset:offset + limit]
    next_offset = offset + len(chunk)
    return {"command_id": command_id, "publication_state": location["publication_state"],
            "delivery_id": location["delivery_id"], **{key: descriptor[key] for key in (
                "artifact_id", "version", "sha256", "size", "mime")},
            "offset": offset, "data_base64": base64.b64encode(chunk).decode("ascii"),
            "next_offset": next_offset, "eof": next_offset == len(data)}


def delivery_status(agent, delivery_id):
    _context, db, sid, actor = _authority(agent)
    return db.read_runtime_delivery(sid, actor, delivery_id)


def acknowledge_delivery(agent, delivery_id, attempt_token, sha256, *,
                         text_received=False, artifact_received=False):
    _context, db, sid, actor = _authority(agent)
    return db.acknowledge_runtime_delivery(sid, actor, delivery_id, attempt_token=attempt_token,
        sha256=sha256, text_received=text_received, artifact_received=artifact_received)


def _owned_transport(agent, ui_session_id, transport):
    from tui_gateway import server
    with server._sessions_lock:
        session = server._sessions.get(ui_session_id)
        _require(transport is not None and session is not None and session.get("agent") is agent
                 and server._session_transport_contains(session, transport),
                 "delivery_transport_not_owned", "Delivery requires the session's attached transport")
    return server


def deliver_result(agent, delivery_id, ui_session_id, transport):
    """Send one result reference over the actual attached JSON-RPC transport."""
    _context, db, sid, actor = _authority(agent)
    server = _owned_transport(agent, ui_session_id, transport)
    claim = db.claim_runtime_delivery(sid, actor, delivery_id)
    if claim is None:
        return db.read_runtime_delivery(sid, actor, delivery_id)
    descriptor = claim["artifact"]
    payload = {"delivery_id": delivery_id, "command_id": claim["command_id"],
               "attempt_token": claim["attempt_token"], **{key: descriptor[key] for key in (
                   "artifact_id", "version", "sha256", "size", "mime")}}
    try:
        _owned_transport(agent, ui_session_id, transport)
        # Use the same validated frame and replay stamping as ordinary TUI events,
        # but write only to the authenticated destination captured for this attempt.
        frame = server._event_frame("runtime.result.available", ui_session_id, payload)
        from tui_gateway.event_replay import _stamp_event
        _stamp_event(frame)
        accepted = transport.write(frame) is True
    except BaseException:
        db.finish_runtime_delivery_attempt(sid, actor, delivery_id, claim["attempt_token"], accepted=False)
        raise
    return db.finish_runtime_delivery_attempt(sid, actor, delivery_id, claim["attempt_token"],
        accepted=accepted, definitely_not_sent=False)


def retry_delivery(agent, delivery_id, ui_session_id, transport):
    _owned_transport(agent, ui_session_id, transport)
    _context, db, sid, actor = _authority(agent)
    db.repair_runtime_delivery(sid, actor, delivery_id)
    return deliver_result(agent, delivery_id, ui_session_id, transport)
