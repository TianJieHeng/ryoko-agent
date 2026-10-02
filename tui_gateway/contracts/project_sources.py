"""Typed owned capture, template, evidence and bounded resume controls."""
from typing import Annotated, Literal

from pydantic import ConfigDict, Field, StrictInt

from .artifacts import ArtifactReadResult, ArtifactVersionRef, PositiveVersion, RuntimeProjectParams
from .base import Params, Result
from .registry import method
from .runtime_results import Digest
from .runtime_v1 import RuntimeIdentifier, RuntimeSessionParams

SourceText = Annotated[str, Field(max_length=4096)]
SourceTime = Annotated[float, Field(ge=0, le=253402300799, allow_inf_nan=False)]
SourceValidity = Literal["current", "stale", "revoked", "unverified"]
SourceAuthority = Literal["observed", "source_claim", "user_approved", "inferred"]
SourceKind = Literal["source_span", "source_id", "decision", "constraint", "approval", "artifact_version"]


class SourceListParams(RuntimeProjectParams):
    limit: Annotated[StrictInt, Field(ge=1, le=100)] = 100


class CaptureCreateParams(RuntimeProjectParams):
    capture_id: RuntimeIdentifier
    original_ref: ArtifactVersionRef
    source_url: Annotated[str, Field(min_length=1, max_length=2048)] | None = None
    acquired_at: SourceTime | None = None
    annotation: SourceText = ""
    suggested_project_id: RuntimeIdentifier | None = None


class CaptureParams(RuntimeSessionParams):
    capture_id: RuntimeIdentifier


class CaptureFileParams(CaptureParams):
    filed_project_id: RuntimeIdentifier | None
    expected_revision: Annotated[StrictInt, Field(ge=0)]


class CaptureExtractionParams(CaptureParams):
    status: Literal["succeeded", "failed", "unavailable"]
    extracted_ref: ArtifactVersionRef | None = None
    failure_code: RuntimeIdentifier | None = None


class CaptureReadParams(CaptureParams):
    offset: Annotated[StrictInt, Field(ge=0)] = 0
    limit: Annotated[StrictInt, Field(ge=1, le=65536)] = 65536


class CaptureExtractionRecord(Result):
    sequence: int
    status: Literal["succeeded", "failed", "unavailable"]
    extracted_ref: ArtifactVersionRef | None
    failure_code: str | None
    created_at: float


class CaptureRecord(Result):
    capture_id: str
    project_id: str
    original_ref: ArtifactVersionRef
    source_url: str | None
    acquired_at: float
    annotation: str
    suggested_project_id: str | None
    filed_project_id: str | None
    revision: int
    extractions: list[CaptureExtractionRecord]


class CaptureResult(Result):
    capture: CaptureRecord


class CaptureListResult(Result):
    captures: list[CaptureRecord]
    limit: int
    limit_reached: bool
    complete: Literal[False]


class CaptureDuplicateGroup(Result):
    sha256: Digest
    capture_ids: list[str]


class CaptureDuplicatesResult(Result):
    groups: list[CaptureDuplicateGroup]
    scanned: int
    truncated: bool
    complete: Literal[False]
    consolidation_performed: Literal[False]


class TemplateSlot(Params):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: RuntimeIdentifier
    purpose: SourceText
    required: bool


class TemplateCreateParams(RuntimeProjectParams):
    template_id: RuntimeIdentifier
    version: PositiveVersion = 1
    parent_version: PositiveVersion | None = None
    baseline_ref: ArtifactVersionRef
    structure: Annotated[list[SourceText], Field(max_length=64)]
    style: dict[str, str]
    assets: Annotated[list[ArtifactVersionRef], Field(max_length=64)]
    slots: Annotated[list[TemplateSlot], Field(max_length=64)]
    exclusions: Annotated[list[SourceText], Field(max_length=64)]


class TemplateParams(RuntimeSessionParams):
    template_id: RuntimeIdentifier
    version: PositiveVersion | None = None


class TemplateRecord(Result):
    template_id: str
    version: int
    project_id: str
    baseline_ref: ArtifactVersionRef
    structure: list[str]
    style: dict[str, str]
    assets: list[ArtifactVersionRef]
    slots: list[TemplateSlot]
    exclusions: list[str]
    parent_version: int | None


class TemplateResult(Result):
    template: TemplateRecord


class TemplateListResult(Result):
    templates: list[TemplateRecord]
    limit: int
    limit_reached: bool
    complete: Literal[False]


class EvidenceCaptureRef(Params):
    capture_id: RuntimeIdentifier


class EvidenceApprovalRef(Params):
    approval_id: RuntimeIdentifier


class EvidenceNumericRange(Params):
    unit: Literal["line", "byte"]
    start: Annotated[StrictInt, Field(ge=0)]
    end: Annotated[StrictInt, Field(ge=0)]


class EvidenceSectionRange(Params):
    unit: Literal["section"]
    start: RuntimeIdentifier
    end: RuntimeIdentifier


class EvidenceCreateParams(RuntimeProjectParams):
    anchor_id: RuntimeIdentifier
    kind: SourceKind
    source_ref: ArtifactVersionRef | EvidenceCaptureRef | EvidenceApprovalRef
    source_version: RuntimeIdentifier
    range_ref: EvidenceNumericRange | EvidenceSectionRange | None = None
    captured_at: SourceTime | None = None
    authority: SourceAuthority = "source_claim"
    validity: SourceValidity = "unverified"
    fresh_until: SourceTime | None = None
    annotation: SourceText = ""


class EvidenceParams(RuntimeSessionParams):
    anchor_id: RuntimeIdentifier


class EvidenceRecord(Result):
    anchor_id: str
    project_id: str
    kind: SourceKind
    source_ref: ArtifactVersionRef | EvidenceCaptureRef | EvidenceApprovalRef
    source_version: str
    range_ref: EvidenceNumericRange | EvidenceSectionRange | None
    captured_at: float
    authority: SourceAuthority
    validity: SourceValidity
    effective_validity: SourceValidity
    fresh_until: float | None
    annotation: str
    grants_execution: Literal[False]


class EvidenceResult(Result):
    evidence: EvidenceRecord


class EvidenceListResult(Result):
    evidence: list[EvidenceRecord]
    limit: int
    limit_reached: bool
    complete: Literal[False]


class ProjectResumeParams(RuntimeProjectParams):
    mission_limit: Annotated[StrictInt, Field(ge=1, le=100)] = 8
    artifact_limit: Annotated[StrictInt, Field(ge=1, le=100)] = 16
    source_limit: Annotated[StrictInt, Field(ge=1, le=100)] = 16
    evidence_limit: Annotated[StrictInt, Field(ge=1, le=100)] = 32


class ResumeProject(Result):
    project_id: str
    revision: int
    purpose: str
    purpose_truncated: bool


class ResumeMission(Result):
    session_id: str
    run_id: str
    revision: int
    status: Literal["idle", "accepted", "claimed", "completed", "failed", "blocked", "cancelled"]


class ResumeArtifact(Result):
    artifact_id: str
    version: int
    sha256: Digest
    mime: str
    size: int
    filed_version: int
    derived_validity: Literal["current", "stale"]


class ResumeCapture(Result):
    capture_id: str
    original_ref: ArtifactVersionRef
    acquired_at: float
    extraction_status: Literal["not_attempted", "succeeded", "failed", "unavailable"]
    annotation: str
    annotation_truncated: bool


class ResumeEvidence(Result):
    anchor_id: str
    kind: SourceKind
    source_ref: ArtifactVersionRef | EvidenceCaptureRef | EvidenceApprovalRef
    source_version: str
    range_ref: EvidenceNumericRange | EvidenceSectionRange | None
    captured_at: float
    authority: SourceAuthority
    validity: SourceValidity
    effective_validity: SourceValidity
    fresh_until: float | None
    annotation: str
    annotation_truncated: bool
    execution_authority: Literal[False]
    freshness: Literal["unspecified", "stale", "within_declared_window"]


class ResumeBlocker(Result):
    kind: Literal["mission", "artifact", "capture", "evidence", "project"]
    reference: str
    code: str


class ResumeLimits(Result):
    missions: int
    artifacts: int
    sources: int
    evidence: int


class ResumeTruncated(Result):
    missions: bool
    artifacts: bool
    sources: bool
    evidence: bool


class ProjectResumeResult(Result):
    project: ResumeProject
    missions: list[ResumeMission]
    artifacts: list[ResumeArtifact]
    sources: list[ResumeCapture]
    evidence: list[ResumeEvidence]
    blockers: list[ResumeBlocker]
    limits: ResumeLimits
    complete: Literal[False]
    project_revision_stable: bool
    assembled_at: float
    truncated: ResumeTruncated


method("runtime.capture.create", params=CaptureCreateParams, result=CaptureResult)
method("runtime.capture.get", params=CaptureParams, result=CaptureResult)
method("runtime.capture.list", params=SourceListParams, result=CaptureListResult)
method("runtime.capture.file", params=CaptureFileParams, result=CaptureResult)
method("runtime.capture.extraction.record", params=CaptureExtractionParams, result=CaptureResult)
method("runtime.capture.read", params=CaptureReadParams, result=ArtifactReadResult,
       doc="Read retained original bytes with digest verification and plain-text-only preview.")
method("runtime.capture.duplicates", params=SourceListParams, result=CaptureDuplicatesResult,
       doc="Propose matching-content review without changing or deleting any original.")
method("runtime.template.create", params=TemplateCreateParams, result=TemplateResult)
method("runtime.template.get", params=TemplateParams, result=TemplateResult)
method("runtime.template.list", params=SourceListParams, result=TemplateListResult)
method("runtime.evidence.create", params=EvidenceCreateParams, result=EvidenceResult)
method("runtime.evidence.get", params=EvidenceParams, result=EvidenceResult)
method("runtime.evidence.list", params=SourceListParams, result=EvidenceListResult)
method("runtime.resume.get", params=ProjectResumeParams, result=ProjectResumeResult,
       doc="Assemble bounded authorized project references and blockers without private memory or a completeness claim.")
