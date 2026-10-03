"""Owned mission intent, deterministic evidence and explicit human acceptance."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, StrictInt, StrictBool

from .base import JsonValue, Result
from .registry import method
from .runtime_v1 import RuntimeIdentifier, RuntimeSessionParams
from .runtime_results import Digest

MissionText = Annotated[str, Field(max_length=4096)]
MissionState = Literal["ready", "working", "waiting_for_user", "waiting_for_source", "ready_to_review",
                       "completed", "partially_completed", "paused", "cancelled", "failed"]


class MissionArtifactRef(Result):
    artifact_id: RuntimeIdentifier
    version: Annotated[StrictInt, Field(ge=1, lt=2**31)]
    digest: Digest


class MissionReadParameters(Result):
    require_current_head: StrictBool = True
    require_current_dependencies: StrictBool = True


class MissionSectionParameters(MissionReadParameters):
    required_sections: Annotated[list[MissionText], Field(min_length=1, max_length=64)]
    nonempty: StrictBool = True


class MissionTextParameters(MissionReadParameters):
    contains: Annotated[list[MissionText], Field(max_length=64)] = Field(default_factory=list)
    excludes: Annotated[list[MissionText], Field(max_length=64)] = Field(default_factory=list)
    equals: MissionText | None = None


class MissionSchemaParameters(MissionReadParameters):
    # Only this deliberately open JSON Schema value is generic; rows and evidence are typed.
    schema_: dict[str, JsonValue] = Field(alias="schema")


class MissionLinkedSection(Result):
    artifact_id: RuntimeIdentifier
    heading: MissionText


class MissionLinkedParameters(MissionReadParameters):
    sections: Annotated[list[MissionLinkedSection], Field(max_length=64)] = Field(default_factory=list)
    tokens: Annotated[list[MissionText], Field(max_length=64)] = Field(default_factory=list)


class MissionTestParameters(Result):
    command: MissionText | None = None
    evidence_ref: Annotated[str, Field(max_length=1024)] | None = None


class MissionIsolatedTestParameters(Result):
    adapter: Literal["isolated_python_v1"]
    code: Annotated[str, Field(min_length=1, max_length=8192)]
    code_sha256: Digest


class MissionEmptyParameters(Result):
    pass


class MissionCriterionBase(Result):
    criterion_id: RuntimeIdentifier
    description: MissionText = ""
    artifact_refs: Annotated[list[MissionArtifactRef], Field(max_length=16)] = Field(default_factory=list)
    required: StrictBool = True


class MissionExistenceCriterion(MissionCriterionBase):
    kind: Literal["existence"]
    parameters: MissionReadParameters = Field(default_factory=MissionReadParameters)


class MissionSectionCriterion(MissionCriterionBase):
    kind: Literal["markdown_sections"]
    parameters: MissionSectionParameters


class MissionTextCriterion(MissionCriterionBase):
    kind: Literal["text_exact"]
    parameters: MissionTextParameters


class MissionSchemaCriterion(MissionCriterionBase):
    kind: Literal["json_schema"]
    parameters: MissionSchemaParameters


class MissionLinkedCriterion(MissionCriterionBase):
    kind: Literal["linked_consistency"]
    parameters: MissionLinkedParameters


class MissionTestCriterion(MissionCriterionBase):
    kind: Literal["test_execution"]
    parameters: MissionIsolatedTestParameters | MissionTestParameters = Field(default_factory=MissionTestParameters)


class MissionUserCriterion(MissionCriterionBase):
    kind: Literal["user_acceptance"]
    parameters: MissionEmptyParameters = Field(default_factory=MissionEmptyParameters)


MissionCriterion = Annotated[MissionExistenceCriterion | MissionSectionCriterion | MissionTextCriterion |
    MissionSchemaCriterion | MissionLinkedCriterion | MissionTestCriterion | MissionUserCriterion, Field(discriminator="kind")]


class MissionDeliverable(Result):
    deliverable_id: RuntimeIdentifier
    description: MissionText = ""
    artifact_ref: MissionArtifactRef | None = None
    required: StrictBool = True


class MissionDependency(Result):
    dependency_id: RuntimeIdentifier
    kind: Literal["artifact", "evidence", "input", "mission"]
    reference: RuntimeIdentifier
    version: str | StrictInt | None = None
    digest: Digest | None = None
    status: Annotated[str, Field(max_length=128)] | None = None


class MissionPlanStep(Result):
    step_id: RuntimeIdentifier
    description: MissionText = ""
    status: Literal["pending", "working", "completed", "blocked", "skipped"] = "pending"
    checkpoint: StrictBool = False
    depends_on: Annotated[list[RuntimeIdentifier], Field(max_length=100)] = Field(default_factory=list)
    input_digests: Annotated[list[Digest], Field(max_length=100)] = Field(default_factory=list)
    target_refs: Annotated[list[RuntimeIdentifier], Field(max_length=100)] = Field(default_factory=list)
    approval_ids: Annotated[list[RuntimeIdentifier], Field(max_length=100)] = Field(default_factory=list)


class MissionLegacyContract(Result):
    outcome: MissionText = ""
    verification: MissionText = ""
    constraints: MissionText = ""
    boundaries: MissionText = ""
    stop_when: MissionText = ""


class MissionHistoricalGate(Result):
    command: MissionText
    timeout_seconds: Annotated[StrictInt, Field(ge=1, le=3600)] = 300
    max_retries: Annotated[StrictInt, Field(ge=0, le=10)] = 3


class MissionIntent(Result):
    outcome: Annotated[str, Field(min_length=1, max_length=16384)]
    project_id: RuntimeIdentifier | None = None
    deliverables: Annotated[list[MissionDeliverable], Field(max_length=100)] = Field(default_factory=list)
    acceptance: Annotated[list[MissionCriterion], Field(max_length=32)] = Field(default_factory=list)
    scope_ref: RuntimeIdentifier | None = None
    budget_ref: RuntimeIdentifier | None = None
    deadline: Annotated[float, Field(ge=0, le=253402300799, allow_inf_nan=False)] | None = None
    dependencies: Annotated[list[MissionDependency], Field(max_length=64)] = Field(default_factory=list)
    plan_steps: Annotated[list[MissionPlanStep], Field(max_length=100)] = Field(default_factory=list)
    policy: Literal["direct", "reviewed"] = "reviewed"
    risk: Literal["unknown", "low", "consequential"] = "unknown"
    uncertainty: Literal["unknown", "low", "high"] = "unknown"
    max_turns: Annotated[StrictInt, Field(ge=1, le=100)] = 20
    no_progress_limit: Annotated[StrictInt, Field(ge=1, le=5)] = 2
    legacy_contract: MissionLegacyContract = Field(default_factory=MissionLegacyContract)
    subgoals: Annotated[list[MissionText], Field(max_length=100)] = Field(default_factory=list)
    gates: Annotated[list[MissionHistoricalGate], Field(max_length=100)] = Field(default_factory=list)


class MissionCreateParams(RuntimeSessionParams):
    previous_mission_id: RuntimeIdentifier | None = None
    previous_revision: Annotated[StrictInt, Field(ge=1)] | None = None
    mission_id: RuntimeIdentifier
    contract: MissionIntent


class MissionGetParams(RuntimeSessionParams):
    mission_id: RuntimeIdentifier | None = None


class MissionRevisionParams(MissionGetParams):
    expected_revision: Annotated[StrictInt, Field(ge=1)]


class MissionReviseParams(MissionRevisionParams):
    contract: MissionIntent
    changed_inputs: Annotated[list[Digest], Field(max_length=100)] = Field(default_factory=list)
    changed_targets: Annotated[list[RuntimeIdentifier], Field(max_length=100)] = Field(default_factory=list)
    changed_plan_steps: Annotated[list[RuntimeIdentifier], Field(max_length=100)] = Field(default_factory=list)


class MissionControlParams(MissionRevisionParams):
    reason: Annotated[str, Field(max_length=1024)] = ""


class MissionListParams(RuntimeSessionParams):
    limit: Annotated[StrictInt, Field(ge=1, le=100)] = 100


class MissionReceiptListParams(MissionListParams):
    mission_id: RuntimeIdentifier | None = None


class MissionEvidenceDependency(Result):
    kind: Literal["artifact", "evidence", "capture"]
    reference: RuntimeIdentifier
    version: str | StrictInt | None = None
    digest: Digest | None = None
    status: str | None = None
    head_version: int | None = None
    metadata_digest: Digest | None = None


class MissionVerificationDetails(Result):
    reason_codes: list[str] = Field(default_factory=list)
    checks: list[str] = Field(default_factory=list)
    dependencies: list[MissionEvidenceDependency] = Field(default_factory=list)
    inputs_digest: Digest | None = None


class MissionVerificationReceipt(Result):
    receipt_id: str
    criterion_id: str
    criterion_digest: Digest
    artifact_refs: list[MissionArtifactRef]
    verifier: str
    evidence_ref: str
    result: Literal["pass", "fail", "blocked", "unsupported"]
    observed_at: float
    details: MissionVerificationDetails
    mission_revision: int | None = None
    created_at: float | None = None


# Mission state is independent of the existing runtime command MissionSnapshot.
# Filled explicitly from the authoritative mission row, never inferred from a stopped model.
class MissionEffectRef(Result):
    effect_id: str
    state: Literal["prepared", "dispatched", "confirmed", "failed", "outcome_unknown", "reconciliation_required"]


class MissionDeliveryRef(Result):
    delivery_id: str
    state: str


class MissionMissedSteer(Result):
    revision: int
    run_id: str | None = None
    effect_ids: list[str]
    reason: Literal["effect_already_dispatched", "turn_already_finalizing"]


class MissionRecord(MissionIntent):
    schema_version: Literal[1]
    mission_id: str
    session_id: str
    agent_id: str
    revision: int
    state: MissionState
    execution_status: str
    acceptance_status: str
    delivery_status: str
    next_step: str
    blockers: list[str]
    artifact_refs: list[MissionArtifactRef]
    effect_refs: list[MissionEffectRef]
    delivery_refs: list[MissionDeliveryRef]
    effect_refs_total: int = 0
    effect_refs_truncated: bool = False
    delivery_refs_total: int = 0
    delivery_refs_truncated: bool = False
    verification_current: bool | None = None
    turns_used: int
    consecutive_no_progress: int
    verification_rounds: int
    last_run_id: str | None
    paused_reason: str | None
    recovery_choices: list[str]
    missed_steer: list[MissionMissedSteer]
    created_at: float
    updated_at: float
    legacy_imported: bool
    archived: bool = False
    archived_at: float | None = None


class MissionResult(Result):
    mission: MissionRecord
    dispatch_performed: Literal[False] = False


class MissionGetResult(Result):
    mission: MissionRecord | None


class MissionListResult(Result):
    missions: list[MissionRecord]
    limit: int
    limit_reached: bool
    complete: Literal[False]


class MissionReceiptListResult(Result):
    receipts: list[MissionVerificationReceipt]
    limit: int
    limit_reached: bool
    complete: Literal[False]


class MissionVerifyResult(MissionResult):
    receipts: list[MissionVerificationReceipt]


method("runtime.mission.create", params=MissionCreateParams, result=MissionResult,
       doc="Create bounded mission intent under an owned user control; never dispatch or reset a budget.")
method("runtime.mission.get", params=MissionGetParams, result=MissionGetResult,
       doc="Read the owned authoritative mission independently from runtime command state.")
method("runtime.mission.list", params=MissionListParams, result=MissionListResult,
       doc="Read a bounded actor- and project-authorized mission list, without completeness inference.")
method("runtime.mission.revise", params=MissionReviseParams, result=MissionResult,
       doc="CAS-revise intent while preserving prior evidence, retained versions and budget ceilings.")
for operation in ("pause", "resume", "cancel"):
    method("runtime.mission." + operation, params=MissionControlParams, result=MissionResult,
           doc="Apply an explicit owned mission control with exact revision and existing budget scope.")
method("runtime.mission.accept", params=MissionRevisionParams, result=MissionResult,
       doc="Record explicit human acceptance only after current deterministic verification succeeds.")
method("runtime.mission.verify", params=MissionRevisionParams, result=MissionVerifyResult,
       doc="Run bounded deterministic checks over retained artifact bytes; no model, shell or effect dispatch.")
method("runtime.mission.receipts.list", params=MissionReceiptListParams, result=MissionReceiptListResult,
       doc="Read a bounded immutable verification receipt list; missing evidence is never a pass.")

method("runtime.mission.history", params=MissionListParams, result=MissionListResult,
       doc="Read current and immutable archived missions for this canonical conversation. Controls always target the active exact mission.")
