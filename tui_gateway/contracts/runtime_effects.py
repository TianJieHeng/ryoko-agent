"""Owned exact approvals and effect inspection, without dispatch or replay authority."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, StrictInt

from .base import Result
from .registry import method
from .runtime_v1 import RuntimeIdentifier, RuntimeSessionParams

RuntimeEffectDigest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
RuntimeEffectState = Literal["prepared", "dispatched", "confirmed", "failed", "outcome_unknown", "reconciliation_required"]


class RuntimeApprovalListParams(RuntimeSessionParams):
    run_id: RuntimeIdentifier | None = None
    limit: Annotated[StrictInt, Field(ge=1, le=200)] = 100


class RuntimeApprovalResolveParams(RuntimeSessionParams):
    approval_id: RuntimeIdentifier
    approval_digest: RuntimeEffectDigest
    choice: Literal["once", "deny"]


class RuntimeApprovalRecord(Result):
    approval_id: str
    run_id: str
    approval_digest: str
    action_digest: str
    input_digest: str
    target_digest: str
    input_revision_digest: str
    artifact_revision_digest: str
    policy_version: str
    policy_digest: str
    status: Literal["pending", "approved", "denied", "consumed", "invalidated"]
    expires_at: float
    expired: bool
    created_at: float
    resolved_at: float | None
    consumed_at: float | None
    invalidation_reason: str | None = None
    mission_id: str | None = None
    mission_revision: int | None = None
    invalidated_at: float | None = None


class RuntimeApprovalListResult(Result):
    approvals: list[RuntimeApprovalRecord]
    limit: int
    truncated: bool
    complete: Literal[False]


class RuntimeApprovalResolveResult(Result):
    approval: RuntimeApprovalRecord
    dispatch_performed: Literal[False]


class RuntimeEffectListParams(RuntimeApprovalListParams):
    unresolved_only: bool = False


class RuntimeEffectParams(RuntimeSessionParams):
    effect_id: RuntimeIdentifier


class RuntimeEffectEvidence(Result):
    sequence: int
    generation: int
    from_state: RuntimeEffectState
    state: RuntimeEffectState
    created_at: float
    receipt_available: bool
    receipt_sha256: str | None


class RuntimeEffectRecord(Result):
    effect_id: str
    run_id: str
    operation_id: str
    operation_type: Literal["artifact_publish", "project_artifact_publish", "mission_test_execution", "unsupported"]
    state: RuntimeEffectState
    action_digest: str
    input_digest: str
    target_digest: str
    policy_version: str
    policy_digest: str
    generation: int
    approval_id: str | None
    provider_idempotency: Literal["supported", "unsupported"]
    created_at: float
    updated_at: float
    exactly_once_external: Literal[False]
    replay_permitted: Literal[False]


class RuntimeEffectListResult(Result):
    effects: list[RuntimeEffectRecord]
    limit: int
    truncated: bool
    complete: Literal[False]


class RuntimeEffectGetResult(Result):
    effect: RuntimeEffectRecord
    evidence: list[RuntimeEffectEvidence]


class RuntimeEffectReconcileResult(RuntimeEffectGetResult):
    inspection_only: Literal[True]
    dispatch_performed: Literal[False]


method("runtime.approvals.list", params=RuntimeApprovalListParams, result=RuntimeApprovalListResult,
       doc="Read an owned oldest-first bounded approval snapshot. This is not complete history or permission to act.")
method("runtime.approval.resolve", params=RuntimeApprovalResolveParams, result=RuntimeApprovalResolveResult,
       doc="Record one exact human decision for the owned live run. Repeated answers are rejected; this never dispatches.")
method("runtime.effects.list", params=RuntimeEffectListParams, result=RuntimeEffectListResult,
       doc="Read an owned oldest-first bounded effect snapshot, never a claim of complete history.")
method("runtime.effect.get", params=RuntimeEffectParams, result=RuntimeEffectGetResult,
       doc="Inspect one owned effect and its bounded receipt metadata without exposing private input or paths.")
method("runtime.effect.reconcile", params=RuntimeEffectParams, result=RuntimeEffectReconcileResult,
       doc="Inspect local artifact state under a bounded server-owned lease and record evidence. Never replay a mutation.")
