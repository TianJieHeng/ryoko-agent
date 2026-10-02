"""Durable dispatch and read-only recovery for explicitly certified effects.

The first provider is local immutable result publication. A missing file does
not prove failure: a former writer may have stopped before or after acceptance.
Recovery therefore never invokes the mutation, and never guesses from model text.
"""
from __future__ import annotations

import hashlib
import math
import time
from contextlib import nullcontext

from agent.result_artifacts import (
    ArtifactConflict, _publish_bytes, artifact_actor, descriptor_digest,
    read_result_artifact, validate_artifact_descriptor,
)
from tools.capability_broker import CapabilityDenied, require_live_policy


def _assert_owner(db, context, session_id, holder, generation):
    if require_live_policy(require_run=False) != context:
        raise CapabilityDenied("effect_scope_mismatch", "Effect recovery requires its live owning context")
    lease = db.get_session_turn_lease(context.identity.session_id)
    if lease is not None and lease["conversation_id"] != session_id:
        raise CapabilityDenied("effect_scope_mismatch", "Effect belongs to another session")
    if (lease is None or lease["holder"] != holder or lease["generation"] != generation
            or lease["expires_at"] <= time.time()):
        raise CapabilityDenied("effect_stale_owner", "Effect owner generation is no longer current")


def _target(descriptor):
    return f"artifact:{descriptor['artifact_id']}:{descriptor['version']}"


def dispatch_certified_effect(operation_type, *, run, input_ref, payload, operation_id, intent_key,
                               action=None, approval=None):
    """Called by the broker; provider classification never comes from model hints."""
    if operation_type not in {"artifact_publish", "project_artifact_publish"}:
        raise CapabilityDenied("effect_adapter_unsupported", "No durable adapter certifies this mutation")
    if operation_type == "project_artifact_publish":
        from agent.artifact_commands import assert_artifact_dispatch
        from tools.capability_broker import _action_authority, _approval_binding, _approval_record, _authorize_action
        authority_check = assert_artifact_dispatch
        if action is None or action.operation_class != operation_type or approval is None:
            raise CapabilityDenied("exact_approval_required", "Project publication needs its exact approved action", pending=True)
        _authorize_action(action)
        authority = _action_authority(action)
        _db, _actor, record = _approval_record(approval, authority, action)
        if record["status"] not in {"approved", "consumed"}:
            raise CapabilityDenied("exact_approval_required", "Project publication has not been approved once", pending=True)
        binding = _approval_binding(authority, action)
        intent_options = {key: binding[key] for key in ("action_digest", "input_revision", "artifact_revision")}
        intent_options["approval_id"] = approval.approval_id
    else:
        from agent.runtime_commands import assert_runtime_finalization
        authority_check = assert_runtime_finalization
        intent_options = {"input_revision": input_ref["sha256"], "artifact_revision": str(input_ref["version"])}
    authority_check(run)
    descriptor = validate_artifact_descriptor(run.context, input_ref)
    if operation_type == "project_artifact_publish":
        import json
        if json.loads(action.input_json)["descriptor"] != descriptor:
            raise CapabilityDenied("capability_scope_mismatch", "Artifact bytes differ from the approved descriptor")
    if descriptor["producing_run"] != run.run_id:
        raise CapabilityDenied("effect_scope_mismatch", "Artifact belongs to a different producing run")
    # Validate bytes before preparing intent, but do not create a staging file.
    if (not isinstance(payload, bytes) or len(payload) != descriptor["size"]
            or hashlib.sha256(payload).hexdigest() != descriptor["sha256"]):
        raise ArtifactConflict("Artifact bytes changed before dispatch")
    actor = artifact_actor(run.context)
    fence = {"holder": run.holder, "generation": run.generation}
    effect = run.db.prepare_effect(run.session_id, actor, run_id=run.run_id,
        operation_id=operation_id, intent_key=intent_key, operation_type=operation_type,
        input_digest=descriptor_digest(descriptor), target_ref=_target(descriptor),
        policy_digest=run.context.policy.digest, policy_version=str(run.context.policy.policy_version),
        provider_idempotency="supported", idempotency_key=descriptor_digest({"target": _target(descriptor)}),
        input_ref=descriptor, **intent_options, **fence)
    authority_check(run)
    dispatched = run.db.dispatch_effect(effect["effect_id"], actor, **fence)
    if not dispatched["dispatched_now"]:
        if dispatched["state"] == "confirmed":
            # Historical confirmation is not permission to serve corrupted bytes.
            read_result_artifact(run.context, descriptor)
            return dispatched
        raise CapabilityDenied("effect_reconciliation_required", "Prior publication needs read-only reconciliation", pending=True)
    authority_check(run)
    guard = nullcontext()
    if operation_type == "project_artifact_publish":
        from agent.project_context import project_access
        guard = project_access(run.context).guard(json.loads(action.input_json)["project_id"], actor, "write")
    try:
        with guard:
            authority_check(run)
            if operation_type == "project_artifact_publish":
                _authorize_action(action)
            receipt = _publish_bytes(run.context, descriptor, payload)
    except BaseException as error:
        # This is deliberately not 'failed': even fsync can report an error after
        # the namespace entry became visible. A journal outage leaves dispatched.
        try:
            run.db.record_effect_outcome(effect["effect_id"], actor, state="outcome_unknown",
                evidence={"kind": "adapter_exception", "reason": type(error).__name__}, **fence)
        except Exception as journal_error:
            run.dispatch_blocked.set()
            raise CapabilityDenied("effect_outcome_unrecorded", "Publication outcome needs reconciliation", pending=True) from journal_error
        raise
    try:
        stored_receipt = {"kind": "local_fsync", "reference": receipt["locator"],
                          "sha256": receipt["sha256"], "size": receipt["size"]}
        return run.db.record_effect_outcome(effect["effect_id"], actor, state="confirmed",
            receipt=stored_receipt, evidence={**stored_receipt, "kind": "local_atomic_publication"}, **fence)
    except Exception as error:
        run.dispatch_blocked.set()
        raise CapabilityDenied("effect_outcome_unrecorded", "Published bytes need read-only receipt reconciliation", pending=True) from error


def _deadline(deadline_at):
    now = time.time()
    if (type(deadline_at) not in (int, float) or not math.isfinite(deadline_at)
            or not now < deadline_at <= now + 60):
        raise CapabilityDenied("reconciliation_deadline", "Reconciliation requires a live deadline within sixty seconds")


def reconcile_effect(db, effect_id, *, context, holder, generation, deadline_at):
    """Inspect one exact current state; update evidence only, never repeat an effect."""
    _deadline(deadline_at)
    actor = artifact_actor(context)
    effect = db.get_effect(effect_id, actor)
    if effect is None:
        raise CapabilityDenied("effect_not_found", "Effect is unavailable in this actor scope")
    _assert_owner(db, context, effect["session_id"], holder, generation)
    fence = {"holder": holder, "generation": generation}
    if effect["state"] in {"prepared", "confirmed", "failed"}:
        return effect
    if effect["state"] == "dispatched":
        effect = db.record_effect_outcome(effect_id, actor, state="outcome_unknown",
            evidence={"kind": "read_only_reconciliation", "reason": "receipt_missing"}, **fence)
    evidence = {"kind": "read_only_reconciliation", "reason": "adapter_unsupported"}
    state = "reconciliation_required"
    receipt = None
    if effect["operation_type"] in {"artifact_publish", "project_artifact_publish"}:
        descriptor = effect["input_ref"]
        try:
            if effect["operation_type"] == "project_artifact_publish":
                from agent.project_context import project_access
                db.read_artifact_reservation(descriptor["artifact_id"], descriptor["version"], actor,
                                             access=project_access(context))
            validate_artifact_descriptor(context, descriptor)
            if (descriptor_digest(descriptor) != effect["input_digest"]
                    or descriptor["producing_run"] != effect["run_id"]
                    or _target(descriptor) != effect["target_ref"]):
                raise ArtifactConflict("Recorded artifact scope changed")
            read_result_artifact(context, descriptor)
            receipt = {"reference": descriptor["locator"], "sha256": descriptor["sha256"],
                       "size": descriptor["size"], "kind": "provider_current_state"}
            evidence = {"reason": "matching_current_bytes", **receipt}
            state = "confirmed"
        except (ArtifactConflict, OSError) as error:
            evidence = {"kind": "read_only_reconciliation", "reason": type(error).__name__}
    if time.time() >= deadline_at:
        raise CapabilityDenied("reconciliation_deadline", "Reconciliation deadline elapsed; outcome remains unresolved")
    _assert_owner(db, context, effect["session_id"], holder, generation)
    if state == effect["state"]:
        return db.get_effect(effect_id, actor)
    return db.record_effect_outcome(effect_id, actor, state=state, receipt=receipt,
                                    evidence=evidence, **fence)
