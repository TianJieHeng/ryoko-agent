"""BE11 owned workflow lifecycle, evaluated promotion and manual pinned runs."""
from typing import Annotated, Literal
from pydantic import Field, StrictInt
from .base import Result
from .registry import method
from .runtime_v1 import RuntimeIdentifier, RuntimeSessionParams
from .runtime_results import Digest
from .artifacts import ArtifactProposalResult, ArtifactPublishResult
from .domains import DomainApproval

BoundedJSON = Annotated[str, Field(min_length=2, max_length=262144)]
Version = Annotated[StrictInt, Field(ge=1, le=2147483647)]


class WorkflowProjectParams(RuntimeSessionParams):
    project_id: RuntimeIdentifier


class WorkflowVersionParams(WorkflowProjectParams):
    workflow_id: RuntimeIdentifier
    version: Version


class WorkflowCreateParams(RuntimeSessionParams):
    command_id: RuntimeIdentifier
    definition_json: BoundedJSON


class WorkflowRecord(Result):
    workflow_id: str
    version: int
    project_id: str
    sha256: Digest
    definition_json: str
    state: Literal['draft', 'tested', 'approved', 'deprecated', 'revoked']
    revision: int
    evaluation_ref: str | None
    active_version: int | None
    head_revision: int


class WorkflowResult(Result):
    workflow: WorkflowRecord


class WorkflowListResult(Result):
    workflows: list[WorkflowRecord]
    complete: Literal[False]


class WorkflowTemplateResult(Result):
    template_id: str
    version: int
    project_id: str
    sha256: Digest
    definition_json: str


class WorkflowEvaluateParams(WorkflowVersionParams):
    command_id: RuntimeIdentifier
    expected_revision: Version
    cases_json: BoundedJSON


class WorkflowEvaluateResult(WorkflowResult):
    evaluation_json: str


class WorkflowDecisionParams(WorkflowVersionParams):
    command_id: RuntimeIdentifier
    sha256: Digest
    expected_revision: Version
    expected_head_revision: Annotated[StrictInt, Field(ge=0)]
    action: Literal['approve', 'deprecate', 'revoke', 'rollback', 'authorize_export']
    recipient: Annotated[str, Field(min_length=1, max_length=256)] | None = None


class WorkflowDecisionPrepareResult(Result):
    approval_id: str
    approval_digest: Digest
    expires_at: float
    scope_json: str
    workflow: WorkflowRecord


class WorkflowDecisionCommitParams(WorkflowDecisionParams):
    approval_id: RuntimeIdentifier
    approval_digest: Digest


class WorkflowDecisionResult(WorkflowResult):
    decision_json: str


class WorkflowExportParams(WorkflowVersionParams):
    recipient: Annotated[str, Field(min_length=1, max_length=256)]
    approval_id: RuntimeIdentifier


class WorkflowExportResult(Result):
    recipient: str
    export_json: str
    external_send_performed: Literal[False]


class WorkflowFeedbackParams(WorkflowVersionParams):
    command_id: RuntimeIdentifier
    evidence_json: BoundedJSON


class WorkflowEvidenceResult(Result):
    evidence_json: str


class WorkflowRunParams(WorkflowVersionParams):
    command_id: RuntimeIdentifier
    sha256: Digest
    mission_id: RuntimeIdentifier
    mission_revision: Version
    parameters_json: BoundedJSON


class WorkflowRunPrepareResult(Result):
    workflow_run_id: str
    pin_json: str
    proposals: list[ArtifactProposalResult]
    publication_atomic: Literal[False]


class WorkflowRunPublishParams(WorkflowRunParams):
    approvals: Annotated[list[DomainApproval], Field(min_length=2, max_length=33)]


class WorkflowRunPublishResult(Result):
    workflow_run_id: str
    state: Literal['published']
    outputs: list[ArtifactPublishResult]
    manifest: ArtifactPublishResult
    publication_atomic: Literal[False]
    mission_completed: Literal[False]


class WorkflowHistoryResult(Result):
    runs_json: str
    complete: Literal[False]


method('runtime.workflow.create', params=WorkflowCreateParams, result=WorkflowResult,
       doc='Create an immutable draft with scoped accepted-work or consent evidence; never promote or execute it.')
method('runtime.workflow.get', params=WorkflowVersionParams, result=WorkflowResult,
       doc='Read an exact granted canonical workflow version and its lifecycle.')
method('runtime.workflow.list', params=WorkflowProjectParams, result=WorkflowListResult,
       doc='Discover explicitly project-granted workflows without reading personal memory.')
method('runtime.workflow.template.create', params=WorkflowCreateParams, result=WorkflowTemplateResult,
       doc='Create a separate immutable style template; existing workflow pins and deliverables remain unchanged.')
method('runtime.workflow.evaluate', params=WorkflowEvaluateParams, result=WorkflowEvaluateResult,
       doc='Evaluate bounded local producers on varied tuning and held-out cases against recorded baseline outputs; never execute external effects.')
method('runtime.workflow.decision.prepare', params=WorkflowDecisionParams, result=WorkflowDecisionPrepareResult,
       doc='Prepare exact lifecycle/pointer/sharing approval bound to content, evaluation, destination and current policy.')
method('runtime.workflow.decision.commit', params=WorkflowDecisionCommitParams, result=WorkflowDecisionResult,
       doc='Commit the exact reviewed human workflow decision; no skill self-promotion.')
method('runtime.workflow.export', params=WorkflowExportParams, result=WorkflowExportResult,
       doc='Prepare canonical export only under an exact approved recipient grant; no external send is performed.')
method('runtime.workflow.feedback', params=WorkflowFeedbackParams, result=WorkflowEvidenceResult,
       doc='Retain corrections or failure evidence as references without training or silently modifying instructions.')
method('runtime.workflow.run.prepare', params=WorkflowRunParams, result=WorkflowRunPrepareResult,
       doc='Execute a finite local workflow pinned to immutable content, parameters, template, budget and exact ready Mission; prepare outputs.')
method('runtime.workflow.run.publish', params=WorkflowRunPublishParams, result=WorkflowRunPublishResult,
       doc='Publish only exactly approved prepared outputs through existing artifact effects; mission completion remains separately verified.')
method('runtime.workflow.runs', params=WorkflowVersionParams, result=WorkflowHistoryResult,
       doc='Inspect owned run pins and output history; never resume by resolving a changed active pointer.')
