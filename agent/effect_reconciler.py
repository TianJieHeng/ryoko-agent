"""Durable dispatch and read-only recovery for explicitly certified effects.

The first provider is local immutable result publication. A missing file does
not prove failure: a former writer may have stopped before or after acceptance.
Recovery therefore never invokes the mutation, and never guesses from model text.
"""
from __future__ import annotations

import hashlib
import math
import time

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


def dispatch_certified_effect(operation_type, *, run, input_ref, payload, operation_id, intent_key):
    """Called by the broker; provider classification never comes from model hints."""
    if operation_type != "artifact_publish":
        raise CapabilityDenied("effect_adapter_unsupported", "No durable adapter certifies this mutation")
    from agent.runtime_commands import assert_runtime_finalization
    assert_runtime_finalization(run)
    descriptor = validate_artifact_descriptor(run.context, input_ref)
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
        input_revision=descriptor["sha256"], artifact_revision=str(descriptor["version"]),
        provider_idempotency="supported", idempotency_key=descriptor_digest({"target": _target(descriptor)}),
        input_ref=descriptor, **fence)
    assert_runtime_finalization(run)
    dispatched = run.db.dispatch_effect(effect["effect_id"], actor, **fence)
    if not dispatched["dispatched_now"]:
        if dispatched["state"] == "confirmed":
            # Historical confirmation is not permission to serve corrupted bytes.
            read_result_artifact(run.context, descriptor)
            return dispatched
        raise CapabilityDenied("effect_reconciliation_required", "Prior publication needs read-only reconciliation", pending=True)
    assert_runtime_finalization(run)
    try:
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
    if effect["operation_type"] == "artifact_publish":
        descriptor = effect["input_ref"]
        try:
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
