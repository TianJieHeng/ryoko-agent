"""Explicit bounded project opportunity review, without execution authority."""
from typing import Annotated, Literal

from pydantic import Field, StrictInt

from .base import Result
from .registry import method
from .runtime_results import Digest
from .runtime_v1 import RuntimeIdentifier, RuntimeSessionParams

OpportunityDisposition = Literal["proposed", "saved", "dismissed", "accepted"]
OpportunityKind = Literal["stale_artifact", "waiting_check", "workflow_draft"]
OpportunityLimit = Annotated[StrictInt, Field(ge=1, le=50)]
OpportunityRevision = Annotated[StrictInt, Field(ge=1)]


class OpportunitySelectionParams(RuntimeSessionParams):
    project_ids: Annotated[list[RuntimeIdentifier], Field(min_length=1, max_length=8)]
    limit: OpportunityLimit = 20


class OpportunityDiscoverParams(OpportunitySelectionParams):
    request_id: RuntimeIdentifier
    scan_limit_per_source: OpportunityLimit = 20


class OpportunityListParams(OpportunitySelectionParams):
    dispositions: Annotated[list[OpportunityDisposition], Field(min_length=1, max_length=4)] = ["proposed", "saved"]


class OpportunityEvidenceRef(Result):
    store: Literal["runtime_artifact_versions", "accepted_commitments", "workflow_versions"]
    project_id: str
    record_id: str
    version: int | None
    revision: int | None
    sha256: Digest | None
    detail: str


class OpportunityAction(Result):
    kind: Literal["review_artifact", "review_commitment", "evaluate_workflow"]
    target_id: str
    target_version: int | None
    description: str


class OpportunityConfidence(Result):
    level: Literal["deterministic_rule_match"]
    explanation: str


class OpportunityCandidate(Result):
    candidate_id: str
    project_id: str
    authorized_project_refs: list[str]
    kind: OpportunityKind
    title: str
    evidence_refs: list[OpportunityEvidenceRef]
    evidence_digest: Digest
    suggested_action: OpportunityAction
    benefit: str
    effort: str
    confidence: OpportunityConfidence
    disposition: OpportunityDisposition
    revision: int
    changed_source_reason: str | None
    evidence_current: bool
    created_at: float
    updated_at: float
    execution_authorized: Literal[False]


class OpportunityScanBound(Result):
    project_id: str
    kind: OpportunityKind
    scanned: int
    limit_reached: bool


class OpportunityDiscoverResult(Result):
    candidates: list[OpportunityCandidate]
    project_ids: list[str]
    scanned: list[OpportunityScanBound]
    suppressed_count: int
    limit: int
    result_limit_reached: bool
    complete: Literal[False]
    discovery_mode: Literal["bounded_local_rules"]
    observed_at: float
    tasks_created: Literal[False]


class OpportunityListResult(Result):
    candidates: list[OpportunityCandidate]
    project_ids: list[str]
    limit: int
    result_limit_reached: bool
    complete: Literal[False]


class OpportunityCandidateParams(RuntimeSessionParams):
    project_id: RuntimeIdentifier
    candidate_id: RuntimeIdentifier


class OpportunityDispositionParams(OpportunityCandidateParams):
    request_id: RuntimeIdentifier
    expected_revision: OpportunityRevision
    expected_evidence_digest: Digest
    disposition: Literal["saved", "dismissed", "accepted"]


class OpportunityDispositionResult(Result):
    candidate: OpportunityCandidate
    tasks_created: Literal[False]
    execution_authorized: Literal[False]
    next_step: Literal["review_recorded", "open_existing_review_control"]


class OpportunityHistoryParams(OpportunityCandidateParams):
    limit: OpportunityLimit = 20


class OpportunityHistoryEntry(Result):
    event: Literal["discovered", "source_changed", "saved", "dismissed", "accepted"]
    candidate: OpportunityCandidate
    recorded_at: float


class OpportunityHistoryResult(Result):
    history: list[OpportunityHistoryEntry]
    limit: int
    result_limit_reached: bool
    complete: Literal[False]


method("runtime.opportunity.discover", params=OpportunityDiscoverParams, result=OpportunityDiscoverResult,
       doc="Explicit selected-project bounded local evidence review; no background scanning or task creation.")
method("runtime.opportunity.list", params=OpportunityListParams, result=OpportunityListResult,
       doc="Read retained candidate dispositions with current evidence guards, without discovering new work.")
method("runtime.opportunity.disposition", params=OpportunityDispositionParams, result=OpportunityDispositionResult,
       doc="Record an idempotent CAS human save/dismiss/accept choice. Acceptance never approves or dispatches work.")
method("runtime.opportunity.history", params=OpportunityHistoryParams, result=OpportunityHistoryResult,
       doc="Read bounded owner/project candidate and disposition history under live project grants.")
