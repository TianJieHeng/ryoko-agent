"""Concrete ordinary-turn consumer of the registered Dots native broker edge."""
import hashlib
import json
import time

from agent.dots_adapter import (_registration, _require, dots_action, descriptor,
    content_bytes, effect_result)
from agent.result_artifacts import artifact_actor
from agent.dots_budget import native_request_budget
from tools.capability_broker import CapabilityDenied, _canonical, _digest, require_live_policy


def _run():
    from agent.runtime_commands import assert_runtime_dispatch
    run = assert_runtime_dispatch()
    _require(run is not None and require_live_policy() == run.context,
        "dots_runtime_required", "Native model tools require the admitted stable-agent turn")
    return run


def _row(run, scope, *, read=False):
    from tui_gateway.dots_surface import require_registered_surface
    row = _registration(run.context, scope, inspect=read)
    # Read-only access does not require its old mutation revision, but a model
    # observation does: stale scope cannot be mistaken for current grants.
    _require(row.revision == scope["expected_grant_revision"],
             "dots_grant_revoked", "Native grant revision changed; refresh the bounded context")
    require_registered_surface(run, row)
    return row


def _deadline(run, maximum=30):
    deadline = time.time() + maximum
    if run.budget is not None:
        run.budget.check()
        deadline = min(deadline, run.budget.deadline,
            time.time() + run.budget.policy.record["request_timeout_ms"] / 1000)
    return deadline


def _read_authority(run):
    return {**artifact_actor(run.context), "runtime_session_id": run.session_id, "run_id": run.run_id,
        "policy_digest": run.context.policy.digest, "generation": run.generation}


def _verified_content(value, authority, scope):
    _require(value["authority"] == authority and value["scope"] == scope,
             "dots_read_mismatch", "Native read returned a different identity or resource scope")
    content = value["content_json"]
    _require(len(content.encode()) <= 65536 and hashlib.sha256(content.encode()).hexdigest() == value["content_sha256"],
             "dots_read_mismatch", "Native read bytes do not match the returned immutable digest")
    parsed = json.loads(content)
    _require(_canonical(parsed) == content, "dots_read_mismatch", "Native read is not exact canonical JSON")
    return parsed


def read_page(arguments):
    from agent.project_context import project_access
    from tui_gateway.contracts.dots_effects import DotsPageReadScope, DotsPageReadResult, DotsPageDocument
    from tui_gateway.dots_bridge import read_native_page
    run = _run()
    scope = DotsPageReadScope.model_validate(arguments).model_dump()
    grant_scope = {"kind": "page", **scope}
    row = _row(run, grant_scope, read=True)
    from agent.mission_runtime import assert_mission_project_scope
    assert_mission_project_scope(run, scope["project_id"])
    authority, deadline = _read_authority(run), _deadline(run)
    with native_request_budget(run, deadline) as deadline, project_access(run.context).guard(scope["project_id"], artifact_actor(run.context), "read"):
        raw = read_native_page(row.ui_session_id, {"authority": authority, "scope": scope, "deadline_at": deadline},
            timeout=max(0, deadline - time.time()), transport=row.transport)
    _require(raw is not None, "dots_read_unavailable", "Native page read is unavailable; no cached bytes were substituted")
    value = DotsPageReadResult.model_validate(raw).model_dump()
    document = DotsPageDocument.model_validate(_verified_content(value, authority, scope)).model_dump()
    _require(scope["version"] is None or value["version"] == scope["version"],
             "dots_read_mismatch", "Native read returned another immutable page version")
    _run()
    _row(run, grant_scope, read=True)
    return {"trust": "untrusted_source", "scope": scope, "version": value["version"],
        "content_sha256": value["content_sha256"], "document": document}


def observe_computer(arguments):
    from tui_gateway.contracts.dots_effects import DotsComputerObserveScope, DotsComputerObserveResult
    from tui_gateway.dots_bridge import observe_native_computer
    from agent.dots_adapter import _validate_computer
    run = _run()
    scope = DotsComputerObserveScope.model_validate(arguments).model_dump()
    target = None
    if scope["action"] == "result":
        _require(scope["effect_id"] is not None and scope["input"] == {},
                 "dots_read_invalid", "A result read requires its exact committed effect")
        target = run.db.get_effect(scope["effect_id"], artifact_actor(run.context))
        _require(target["session_id"] == run.session_id and target["operation_type"] == "dots_computer_action"
                 and target["state"] == "confirmed", "dots_read_denied", "Native result is not a confirmed effect in this conversation")
        target_scope = json.loads(target["input_ref"]["locator"])
        _require(target_scope["executor_id"] == scope["executor_id"], "dots_read_denied", "Native result belongs to another executor")
        grant_scope = {**target_scope, "expected_grant_revision": scope["expected_grant_revision"]}
    else:
        _require(scope["effect_id"] is None, "dots_read_invalid", "Observation cannot borrow a prior effect identity")
        _validate_computer({"action": scope["action"], "input": scope["input"]})
        grant_scope = {"kind": "computer", **scope}
    row = _row(run, grant_scope, read=True)
    authority, deadline = _read_authority(run), _deadline(run)
    with native_request_budget(run, deadline) as deadline:
        raw = observe_native_computer(row.ui_session_id, {"authority": authority, "scope": scope, "deadline_at": deadline},
            timeout=max(0, deadline - time.time()), transport=row.transport)
    _require(raw is not None, "dots_read_unavailable", "Native computer observation is unavailable")
    value = DotsComputerObserveResult.model_validate(raw).model_dump()
    content = _verified_content(value, authority, scope)
    if scope["action"] == "snapshot":
        _require(value["snapshot_sha256"] == value["content_sha256"],
                 "dots_read_mismatch", "Snapshot digest must identify the exact observed snapshot bytes")
    if target is not None:
        receipt = effect_result(target, run.db, artifact_actor(run.context))["receipt"]
        _require(receipt is not None and value["content_sha256"] == receipt["result_sha256"],
                 "dots_read_mismatch", "Native result bytes differ from the committed effect receipt")
    _run()
    _row(run, grant_scope, read=True)
    return {"trust": "untrusted_source", "scope": scope, "control_revision": value["control_revision"],
        "snapshot_id": value["snapshot_id"], "snapshot_sha256": value["snapshot_sha256"],
        "content_sha256": value["content_sha256"], "content": content}


def _exact_review(run, row, action, approval_id):
    from hermes_state_runtime import RuntimeStoreError
    from tools.capability_broker import preview_action, recover_approval_preview, resolve_approval
    from tui_gateway.contracts.dots_effects import DotsApprovalResult
    from tui_gateway.dots_bridge import request_native_approval
    actor = artifact_actor(run.context)
    try:
        existing = run.db.get_effect_approval(approval_id, actor)
    except RuntimeStoreError as exc:
        if exc.code != "approval_not_found":
            raise
        existing = None
    ttl = min(300, run.budget.deadline - time.time()) if run.budget is not None else 300
    preview = (preview_action(action, approval_id=approval_id, ttl_seconds=ttl) if existing is None
               else recover_approval_preview(approval_id, action))
    if existing is not None and existing["status"] == "approved":
        return preview
    if existing is not None:
        pending = existing["status"] == "pending"
        raise CapabilityDenied("exact_approval_pending" if pending else "exact_approval_denied",
            "Inspect the original exact approval; do not resubmit its human decision", pending=pending)
    from tools.approval_context import get_current_session_key, _get_approval_timeout
    from tools.approval_human_wait import human_wait_window
    try:
        with human_wait_window(get_current_session_key()):
            raw = request_native_approval(row.ui_session_id, {
                "authority": _read_authority(run), "approval_id": preview.approval_id,
                "approval_digest": preview.approval_digest, "action_digest": action.digest,
                "expires_at": preview.expires_at},
                timeout=max(0, min(_get_approval_timeout(), preview.expires_at - time.time())), transport=row.transport)
    except OSError as exc:
        raise CapabilityDenied("exact_approval_pending", "The review transport failed; inspect the original decision", pending=True) from exc
    if raw is None:
        raise CapabilityDenied("exact_approval_pending", "The pinned owner has not returned an exact human decision", pending=True)
    answer = DotsApprovalResult.model_validate(raw)
    _require(answer.approval_id == preview.approval_id and answer.approval_digest == preview.approval_digest,
             "approval_mismatch", "The human answer names a different exact review")
    resolve_approval(preview, answer.approval_digest, answer.choice)
    _require(answer.choice == "once", "exact_approval_denied", "This exact native action was denied")
    return preview


def propose(arguments, *, kind):
    from hermes_state_runtime import RuntimeStoreError
    from tui_gateway.contracts.dots_effects import DotsPageToolProposal, DotsComputerToolProposal
    from tools.capability_broker import invoke_effect_dispatch
    run = _run()
    model = DotsPageToolProposal if kind == "page" else DotsComputerToolProposal
    request = model.model_validate(arguments)
    proposal = request.proposal.model_dump()
    _row(run, proposal)
    action = dots_action(proposal)
    operation = "dots-tool-" + _digest({"session": run.session_id, "request_id": request.request_id})[:40]
    approval_id = "dots-review-" + _digest({"session": run.session_id, "request_id": request.request_id})[:40]
    actor = artifact_actor(run.context)
    try:
        existing = run.db.get_effect_approval(approval_id, actor)
    except RuntimeStoreError as exc:
        if exc.code != "approval_not_found":
            raise
        existing = None
    if existing is not None:
        _require(existing["binding"]["action_digest"] == action.digest,
                 "idempotency_conflict", "The request ID already names different exact proposal bytes")
        if existing["status"] == "consumed":
            effect = run.db.get_effect(existing["consumer_id"], actor)
            _require(effect["session_id"] == run.session_id and effect["operation_id"] == operation
                     and effect["action_digest"] == action.digest,
                     "idempotency_conflict", "Prior effect differs from the exact proposal")
            return effect_result(effect, run.db, actor)
    approval = _exact_review(run, _row(run, proposal), action, approval_id)
    _row(run, proposal)
    try:
        return invoke_effect_dispatch(action.operation_class, run=run, input_ref=descriptor(proposal, run),
            payload=content_bytes(proposal), operation_id=operation, intent_key=operation, action=action, approval=approval)
    except CapabilityDenied as exc:
        if exc.code != "dots_outcome_unknown":
            raise
        decision = run.db.get_effect_approval(approval_id, actor)
        if not decision["consumer_id"]:
            raise
        return effect_result(run.db.get_effect(decision["consumer_id"], actor), run.db, actor)
