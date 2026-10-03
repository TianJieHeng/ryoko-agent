"""Concrete Dots native-store/supervisor edge, pinned to its trusted stdio owner.

The native page store is the only byte authority. Registration identifies a
consumer already configured by its owner; it provisions nothing. The BFF must
serialize grant/control revocation with its actual effect, persist immutable
page versions and receipt in the same CAS transaction, and durably mark computer
acceptance before contacting the supervisor. A missing answer never permits
replay. Only read-only inspection can close an uncertain effect.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass, replace
from contextlib import nullcontext
from urllib.parse import urlsplit

from tools.capability_broker import ActionSpec, CapabilityDenied, _canonical, _digest, require_live_policy

DOTS_OPERATIONS = frozenset({"dots_page_publish", "dots_computer_action"})
_CONTROL_NAMES = {"page": "runtime.dots.page.publish", "computer": "runtime.dots.computer.execute"}
_LOCK = threading.RLock()
_REGISTRATIONS = {}


def _require(value, code, message):
    if not value:
        raise CapabilityDenied(code, message)


def _key(context, adapter_id):
    identity = context.identity
    return (context.profile_home, identity.principal_id, identity.profile_id, identity.agent_id,
            identity.session_id, adapter_id)


@dataclass(frozen=True)
class Registration:
    context_key: tuple
    adapter_id: str
    kind: str
    revision: int
    enabled: bool
    project_ids: tuple
    space_ids: tuple
    actions: tuple
    transport: object
    ui_session_id: str


def _owned_transport(agent, ui_session_id):
    from tui_gateway import server
    from tui_gateway.transport import StdioTransport
    transport, session = server._current_session_steer_authority(ui_session_id)
    _require(type(transport) is StdioTransport and session is not None and session.get("agent") is agent,
             "dots_transport_required", "Dots adapters require the owned attached stdio peer")
    _require(require_live_policy(require_run=False) == agent.runtime_context,
             "identity_mismatch", "Dots registration requires the live owning identity")
    return transport


def register_adapter(agent, request):
    from tui_gateway import server
    _require(server._current_rpc_method.get() == "runtime.dots.register",
             "dots_registration_required", "Only the owned registration RPC may configure an adapter")
    transport = _owned_transport(agent, request.session_id)
    context = agent.runtime_context
    if request.kind == "page":
        _require(not request.actions and set(request.project_ids) <= context.policy.project_grants,
                 "dots_grant_denied", "Native page stores cannot broaden immutable project grants")
    else:
        _require(not request.project_ids and not request.space_ids,
                 "dots_grant_denied", "Computer registrations cannot carry page grants")
        _require(all(context.policy.allows_tool("dots_computer_" + action) for action in request.actions),
                 "dots_grant_denied", "Computer actions must be in the stable agent's immutable policy")
    key = _key(context, request.adapter_id)
    registration = Registration(key, request.adapter_id, request.kind, request.revision, request.enabled,
        tuple(sorted(set(request.project_ids))), tuple(sorted(set(request.space_ids))),
        tuple(sorted(set(request.actions))), transport, request.session_id)
    with _LOCK:
        current = _REGISTRATIONS.get(key)
        if current is not None:
            _require(current.transport is transport, "dots_transport_changed",
                     "A different transport cannot replace a live adapter")
            if registration == replace(current, ui_session_id=registration.ui_session_id):
                pass
            else:
                _require(request.expected_revision == current.revision and request.revision > current.revision,
                         "dots_registration_conflict", "Adapter updates need the exact current revision and a newer revision")
        else:
            _require(request.expected_revision is None, "dots_registration_conflict", "New adapter registration has no predecessor")
            # Closed conversations retain durable effects, not live callback
            # authority. Reconnection registers a fresh scoped peer before any
            # read-only recovery and never gains mutation replay permission.
            for stale_key, stale in list(_REGISTRATIONS.items()):
                session = server._sessions.get(stale.ui_session_id)
                if session is None or session.get("transport") is not stale.transport:
                    del _REGISTRATIONS[stale_key]
            _require(len(_REGISTRATIONS) < 1024, "dots_registration_limit", "Adapter registration capacity reached")
        _REGISTRATIONS[key] = registration
    return {"adapter_id": registration.adapter_id, "kind": registration.kind, "revision": registration.revision,
            "enabled": registration.enabled, "agent_id": context.identity.agent_id, "registered": True}


def _registration(context, scope, *, inspect=False):
    from tui_gateway import server
    kind = scope["kind"]
    adapter_id = scope["store_id"] if kind == "page" else scope["executor_id"]
    with _LOCK:
        row = _REGISTRATIONS.get(_key(context, adapter_id))
    _require(row is not None and row.enabled and row.kind == kind,
             "dots_adapter_unavailable", "Native adapter is not registered and enabled for this stable agent")
    session = server._sessions.get(row.ui_session_id)
    _require(session is not None and session.get("transport") is row.transport
             and session.get("agent") is not None
             and _key(session["agent"].runtime_context, adapter_id) == row.context_key,
             "dots_transport_changed", "Native adapter's original stdio attachment is no longer live")
    _require(inspect or row.revision == scope["expected_grant_revision"],
             "dots_grant_revoked", "Adapter grants changed after the proposal")
    if kind == "page":
        _require(scope["project_id"] in row.project_ids and scope["space_id"] in row.space_ids,
                 "dots_grant_revoked", "Page is outside registered project and Space grants")
        from agent.project_context import authorize_project
        authorize_project(context, scope["project_id"], "read" if inspect else "write")
    else:
        _require(scope["action"] in row.actions and context.policy.allows_tool("dots_computer_" + scope["action"]),
                 "dots_grant_revoked", "Computer action is outside the stable agent's live grant")
    return row


def _validate_computer(proposal):
    from tui_gateway.contracts import dots_effects as contract
    models = {"navigate": contract.DotsNavigateInput, "read": contract.DotsEmptyInput,
        "snapshot": contract.DotsEmptyInput, "screenshot": contract.DotsEmptyInput,
        "click": contract.DotsClickInput, "type": contract.DotsTypeInput,
        "key": contract.DotsKeyInput, "scroll": contract.DotsScrollInput,
        "files_list": contract.DotsFilesListInput, "files_read": contract.DotsFilesReadInput,
        "files_write": contract.DotsFilesWriteInput, "exec": contract.DotsExecInput}
    supplied = proposal["input"]
    parsed = models[proposal["action"]].model_validate(supplied).model_dump()
    _require(parsed == supplied, "dots_input_changed", "Computer input must be the exact normalized action input")
    if "path" in parsed:
        path = parsed["path"]
        _require(not path.startswith("/") and "\\" not in path and "\x00" not in path
                 and ".." not in path.split("/"), "dots_path_denied", "Computer paths must be relative to its isolated workspace")
    if proposal["action"] == "navigate":
        url = urlsplit(parsed["url"])
        _require(url.scheme in {"http", "https"} and url.hostname and not url.username and not url.password,
                 "dots_url_denied", "Computer navigation requires a credential-free HTTP(S) URL")
    if "snapshotId" in parsed:
        _require(parsed["snapshotId"] == proposal["snapshot_id"], "dots_snapshot_mismatch", "Element reference must name the approved snapshot")


def proposal_scope(proposal):
    return {key: value for key, value in proposal.items() if key not in {"document", "input"}}


def content_bytes(proposal):
    value = proposal["document"] if proposal["kind"] == "page" else proposal["input"]
    return _canonical(value).encode()


def dots_action(proposal):
    from tui_gateway.contracts.dots_effects import DotsPageProposal, DotsComputerProposal
    model = DotsPageProposal if proposal.get("kind") == "page" else DotsComputerProposal
    canonical = model.model_validate(proposal).model_dump()
    _require(canonical == proposal, "dots_input_changed", "Proposal must use exact normalized fields")
    if proposal["kind"] == "computer":
        _validate_computer(proposal)
    kind = proposal["kind"]
    scope = proposal_scope(proposal)
    operation = "dots_page_publish" if kind == "page" else "dots_computer_action"
    return ActionSpec(_CONTROL_NAMES[kind], proposal, operation,
        destination="dots:" + _digest(scope), destination_purpose="dots_" + kind,
        contract_digest=_digest({"schema_version": 1, "kind": kind, "scope": scope}))


def authorize_dots_action(action):
    from agent.artifact_commands import assert_artifact_dispatch
    run = assert_artifact_dispatch()
    proposal = json.loads(action.input_json)
    _require(dots_action(proposal) == action, "dots_action_changed", "Exact Dots action contract changed")
    _registration(run.context, proposal)
    if proposal["kind"] == "page":
        from agent.mission_runtime import assert_mission_project_scope
        assert_mission_project_scope(run, proposal["project_id"])
    return run


def descriptor(proposal, run):
    content = content_bytes(proposal)
    scope = proposal_scope(proposal)
    return {"artifact_id": proposal["page_id"] if proposal["kind"] == "page" else proposal["executor_id"],
        "version": proposal["expected_head_version"] + 1 if proposal["kind"] == "page" else proposal["snapshot_id"],
        "locator": _canonical(scope), "sha256": hashlib.sha256(content).hexdigest(), "size": len(content),
        "mime": "application/vnd.ryoko.dots." + proposal["kind"] + "+json", "producing_run": run.run_id}


def _identity(effect, db, actor):
    from tui_gateway.contracts.dots_effects import DotsEffectIdentity
    ref = effect["input_ref"]
    scope = json.loads(ref["locator"])
    approval = db.get_effect_approval(effect["approval_id"], actor)
    kind = scope["kind"]
    return DotsEffectIdentity(schema_version=1, **actor, runtime_session_id=effect["session_id"],
        run_id=effect["run_id"], operation_id=effect["operation_id"], effect_id=effect["effect_id"],
        approval_id=effect["approval_id"], approval_digest=approval["approval_digest"],
        action_digest=effect["action_digest"], input_digest=effect["input_digest"],
        policy_digest=effect["policy_digest"], policy_version=effect["policy_version"],
        generation=effect["prepared_generation"], adapter_id=scope["store_id"] if kind == "page" else scope["executor_id"],
        adapter_kind=kind, grant_revision=scope["expected_grant_revision"], scope_json=ref["locator"],
        content_sha256=ref["sha256"], content_size=ref["size"]).model_dump()


def _send(row, identity, *, deadline_at, proposal=None):
    from tui_gateway.dots_bridge import send_native_request
    from tui_gateway.contracts.dots_effects import DotsEffectReceipt
    params = {"identity": identity, "deadline_at": deadline_at}
    if proposal is not None:
        params["proposal"] = proposal
        params["content_json"] = content_bytes(proposal).decode()
    remaining = min(70, deadline_at - time.time())
    _require(remaining > 0, "dots_deadline", "Native adapter deadline expired")
    raw = send_native_request(row.ui_session_id, params, timeout=remaining, transport=row.transport, inspect=proposal is None)
    _require(raw is not None, "dots_outcome_unknown", "Native adapter did not return a witnessed receipt")
    receipt = DotsEffectReceipt.model_validate(raw).model_dump()
    _require(receipt["identity"] == identity, "dots_receipt_mismatch", "Native receipt does not match the exact dispatched identity")
    if receipt["state"] == "committed":
        _require(receipt["receipt_id"] is not None and receipt["reason"] == "committed"
                 and receipt["content_sha256"] == identity["content_sha256"] and receipt["result_sha256"] is not None,
                 "dots_receipt_mismatch", "A commit requires a durable exact-content receipt")
        scope = json.loads(identity["scope_json"])
        if identity["adapter_kind"] == "page":
            _require(receipt["version"] == scope["expected_head_version"] + 1,
                     "dots_receipt_mismatch", "Native page commit must advance exactly the approved CAS head")
    elif receipt["state"] == "not_applied":
        _require(receipt["receipt_id"] is not None and receipt["reason"] not in {"committed", "unknown"},
                 "dots_receipt_mismatch", "Non-application requires a durable terminal rejection receipt")
    else:
        _require(receipt["reason"] == "unknown", "dots_receipt_mismatch", "Unknown state cannot imply success")
    return receipt


def _stored_receipt(receipt):
    return {"kind": "dots_native_receipt", "receipt_id": receipt["receipt_id"],
            "sha256": receipt["content_sha256"], "version": receipt["version"],
            "reference": receipt["result_sha256"], "state": receipt["state"], "reason": receipt["reason"]}


def _outcome(db, effect, actor, fence, receipt):
    state = {"committed": "confirmed", "not_applied": "failed", "outcome_unknown": "outcome_unknown"}[receipt["state"]]
    stored = _stored_receipt(receipt)
    if stored["sha256"] is None:
        del stored["sha256"]
    return db.record_effect_outcome(effect["effect_id"], actor, state=state,
        receipt=stored, evidence={"kind": "dots_native_receipt", "reason": receipt["reason"]}, **fence)


def dispatch_dots_effect(operation_type, *, run, input_ref, payload, operation_id, intent_key, action, approval):
    from agent.artifact_commands import assert_artifact_dispatch
    from agent.result_artifacts import artifact_actor
    from tools.capability_broker import _action_authority, _approval_binding, _approval_record
    _require(operation_type in DOTS_OPERATIONS and action is not None and action.operation_class == operation_type
             and approval is not None, "exact_approval_required", "Native mutations require their exact approved action")
    _require(authorize_dots_action(action) is run, "identity_mismatch", "Native effect belongs to a different run")
    proposal = json.loads(action.input_json)
    _require(input_ref == descriptor(proposal, run) and payload == content_bytes(proposal),
             "dots_input_changed", "Native mutation bytes differ from the exact approved proposal")
    authority = _action_authority(action)
    _db, actor, approved = _approval_record(approval, authority, action)
    _require(approved["status"] in {"approved", "consumed"}, "exact_approval_required", "Native mutation needs approval once")
    binding = _approval_binding(authority, action)
    fence = {"holder": run.holder, "generation": run.generation}
    effect = run.db.prepare_effect(run.session_id, actor, run_id=run.run_id, operation_id=operation_id,
        intent_key=intent_key, operation_type=operation_type,
        **{key: binding[key] for key in ("action_digest", "input_digest", "target_ref", "policy_digest",
                                       "policy_version", "input_revision", "artifact_revision")},
        provider_idempotency="supported" if proposal["kind"] == "page" else "unsupported",
        idempotency_key=operation_id if proposal["kind"] == "page" else None,
        input_ref=input_ref, approval_id=approval.approval_id, **fence)
    authorize_dots_action(action)
    dispatched = run.db.dispatch_effect(effect["effect_id"], actor, **fence)
    if not dispatched["dispatched_now"]:
        return effect_result(run.db.get_effect(effect["effect_id"], actor), run.db, actor)
    try:
        authorize_dots_action(action)
        row = _registration(run.context, proposal)
        identity = _identity(effect, run.db, actor)
        deadline = min(time.time() + 70, getattr(run, "deadline_at", time.time() + 70), approval.expires_at)
        if run.budget is not None:
            deadline = min(deadline, run.budget.deadline,
                time.time() + run.budget.policy.record["request_timeout_ms"] / 1000)
        guard = nullcontext()
        if proposal["kind"] == "page":
            from agent.project_context import project_access
            guard = project_access(run.context).guard(proposal["project_id"], actor, "write")
        from agent.dots_budget import native_request_budget
        with guard, native_request_budget(run, deadline) as deadline:
            authorize_dots_action(action)
            receipt = _send(row, identity, deadline_at=deadline, proposal=proposal)
        assert_artifact_dispatch(run)
        _registration(run.context, proposal)
        result = _outcome(run.db, effect, actor, fence, receipt)
    except BaseException as error:
        try:
            run.db.record_effect_outcome(effect["effect_id"], actor, state="outcome_unknown",
                evidence={"kind": "dots_adapter_exception", "reason": type(error).__name__}, **fence)
        except Exception:
            run.dispatch_blocked.set()
        raise CapabilityDenied("dots_outcome_unknown", "Native effect requires read-only receipt reconciliation", pending=True) from error
    return effect_result(run.db.get_effect(result["effect_id"], actor), run.db, actor)


def effect_result(effect, db, actor):
    receipt = None
    for evidence in reversed(effect.get("evidence", [])):
        value = evidence.get("receipt")
        if value and value.get("kind") == "dots_native_receipt":
            receipt = {"identity": _identity(effect, db, actor), "state": value["state"],
                "receipt_id": value.get("receipt_id"), "content_sha256": value.get("sha256"),
                "version": value.get("version"), "result_sha256": value.get("reference"), "reason": value["reason"]}
            break
    return {"effect_id": effect["effect_id"], "operation_id": effect["operation_id"],
            "state": effect["state"], "receipt": receipt, "replay_permitted": False}


def reconcile_dots_effect(db, effect, *, context, holder, generation, deadline_at):
    from agent.effect_reconciler import _assert_owner, _deadline
    from agent.result_artifacts import artifact_actor
    _deadline(deadline_at)
    actor = artifact_actor(context)
    _assert_owner(db, context, effect["session_id"], holder, generation)
    scope = json.loads(effect["input_ref"]["locator"])
    row = _registration(context, scope, inspect=True)
    if effect["state"] in {"prepared", "confirmed", "failed"}:
        return effect
    fence = {"holder": holder, "generation": generation}
    if effect["state"] == "dispatched":
        effect = db.record_effect_outcome(effect["effect_id"], actor, state="outcome_unknown",
            evidence={"kind": "dots_inspection", "reason": "receipt_missing"}, **fence)
    try:
        receipt = _send(row, _identity(effect, db, actor), deadline_at=deadline_at)
    except (CapabilityDenied, ValueError):
        receipt = None
    _deadline(deadline_at)
    _assert_owner(db, context, effect["session_id"], holder, generation)
    _registration(context, scope, inspect=True)
    if receipt is not None and receipt["state"] != "outcome_unknown":
        return _outcome(db, effect, actor, fence, receipt)
    if effect["state"] != "reconciliation_required":
        return db.record_effect_outcome(effect["effect_id"], actor, state="reconciliation_required",
            evidence={"kind": "dots_inspection", "reason": "receipt_unavailable"}, **fence)
    return effect
