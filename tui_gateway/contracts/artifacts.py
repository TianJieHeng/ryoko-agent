"""Owned project authority and immutable Markdown artifact control contracts."""
from typing import Annotated, Literal

from pydantic import Field, StrictInt

from .base import Params, Result
from .registry import method
from .runtime_v1 import RuntimeIdentifier, RuntimeSessionParams
from .runtime_results import Digest

PositiveVersion = Annotated[StrictInt, Field(ge=1)]


class ArtifactVersionRef(Params):
    artifact_id: RuntimeIdentifier
    version: PositiveVersion


class ProjectSourceRef(Params):
    capture_id: RuntimeIdentifier


class ProjectMissionRef(Params):
    session_id: RuntimeIdentifier
    run_id: RuntimeIdentifier


class ProjectGrant(Params):
    principal_id: RuntimeIdentifier
    agent_id: RuntimeIdentifier
    permissions: Annotated[list[Literal["read", "write", "share"]], Field(min_length=1, max_length=3)]


class RuntimeProjectFolder(Result):
    path: str
    label: str | None
    is_primary: bool
    added_at: int


class RuntimeProjectRecord(Result):
    id: str
    project_id: str
    slug: str
    name: str
    description: str | None
    icon: str | None
    color: str | None
    board_slug: str | None
    primary_path: str | None
    archived: bool
    created_at: int
    folders: list[RuntimeProjectFolder]
    revision: int
    owner_principal_id: str | None
    purpose: str
    source_refs: list[ProjectSourceRef]
    canonical_artifact_refs: list[ArtifactVersionRef]
    active_mission_refs: list[ProjectMissionRef]
    grants: list[ProjectGrant]


class RuntimeProjectResult(Result):
    project: RuntimeProjectRecord


class RuntimeProjectListResult(Result):
    projects: list[RuntimeProjectRecord]


class RuntimeProjectCreateParams(RuntimeSessionParams):
    name: Annotated[str, Field(min_length=1, max_length=256)]
    slug: Annotated[str, Field(min_length=1, max_length=64)] | None = None
    folders: Annotated[list[str], Field(max_length=100)] = []
    primary_path: str | None = None
    purpose: Annotated[str, Field(max_length=8192)] = ""
    grants: Annotated[list[ProjectGrant], Field(max_length=100)] = []


class RuntimeProjectParams(RuntimeSessionParams):
    project_id: RuntimeIdentifier


class RuntimeProjectRevisionParams(RuntimeProjectParams):
    expected_revision: Annotated[StrictInt, Field(ge=0)]


class RuntimeProjectChanges(Params):
    purpose: Annotated[str, Field(max_length=8192)] | None = None
    source_refs: Annotated[list[ProjectSourceRef], Field(max_length=100)] | None = None
    canonical_artifact_refs: Annotated[list[ArtifactVersionRef], Field(max_length=100)] | None = None
    active_mission_refs: Annotated[list[ProjectMissionRef], Field(max_length=100)] | None = None


class RuntimeProjectUpdateParams(RuntimeProjectRevisionParams):
    changes: RuntimeProjectChanges


class RuntimeProjectGrantsParams(RuntimeProjectRevisionParams):
    grants: Annotated[list[ProjectGrant], Field(max_length=100)]


class ArtifactPrepareParams(RuntimeProjectParams):
    command_id: RuntimeIdentifier
    request_id: RuntimeIdentifier
    content: Annotated[str, Field(max_length=65536)]
    artifact_id: RuntimeIdentifier | None = None
    parent_version: PositiveVersion | None = None
    expected_head_version: PositiveVersion | None = None
    locked_sections: Annotated[list[str], Field(max_length=100)] = []
    source_refs: Annotated[list[RuntimeIdentifier], Field(max_length=100)] = []
    derived_from: Annotated[list[ArtifactVersionRef], Field(max_length=100)] = []


class ArtifactPublishParams(ArtifactPrepareParams):
    approval_id: RuntimeIdentifier
    approval_digest: Digest


class ArtifactBytesPrepareParams(RuntimeProjectParams):
    command_id: RuntimeIdentifier
    request_id: RuntimeIdentifier
    content_base64: Annotated[str, Field(max_length=65536)]
    mime: Annotated[str, Field(min_length=1, max_length=128)]
    artifact_id: RuntimeIdentifier | None = None
    parent_version: PositiveVersion | None = None
    expected_head_version: PositiveVersion | None = None
    derived_from: Annotated[list[ArtifactVersionRef], Field(max_length=100)] = []


class ArtifactBytesPublishParams(ArtifactBytesPrepareParams):
    approval_id: RuntimeIdentifier
    approval_digest: Digest


class ArtifactSectionEdit(Params):
    anchor: RuntimeIdentifier
    expected_sha256: Digest
    replacement: Annotated[str, Field(max_length=65536)]


class ArtifactEditParams(RuntimeProjectParams):
    command_id: RuntimeIdentifier
    request_id: RuntimeIdentifier
    artifact_id: RuntimeIdentifier
    parent_version: PositiveVersion
    expected_head_version: PositiveVersion | None = None
    edits: Annotated[list[ArtifactSectionEdit], Field(min_length=1, max_length=100)]


class ArtifactEditPublishParams(ArtifactEditParams):
    approval_id: RuntimeIdentifier
    approval_digest: Digest


class ArtifactMergeParams(RuntimeProjectParams):
    command_id: RuntimeIdentifier
    request_id: RuntimeIdentifier
    artifact_id: RuntimeIdentifier
    branch_version: PositiveVersion
    current_head_version: PositiveVersion
    approved_anchors: Annotated[list[RuntimeIdentifier], Field(min_length=1, max_length=100)]


class ArtifactMergePublishParams(ArtifactMergeParams):
    approval_id: RuntimeIdentifier
    approval_digest: Digest


class ArtifactProposalResult(Result):
    request_id: str
    project_id: str
    artifact_id: str
    version: int
    sha256: Digest
    size: int
    mime: str
    parent_version: int | None
    expected_head_version: int | None
    action_digest: Digest
    approval_id: str
    approval_digest: Digest
    expires_at: float


class ArtifactPublishResult(Result):
    project_id: str
    artifact_id: str
    version: int
    sha256: Digest
    size: int
    mime: str
    parent_version: int | None
    disposition: Literal["canonical", "branch"]
    head_version: int | None
    validation_status: Literal["passed"]
    approval_status: Literal["approved"]


class ArtifactReadParams(RuntimeProjectParams):
    artifact_id: RuntimeIdentifier
    version: PositiveVersion | None = None
    offset: Annotated[StrictInt, Field(ge=0)] = 0
    limit: Annotated[StrictInt, Field(ge=1, le=65536)] = 65536


class ArtifactReadResult(Result):
    project_id: str
    artifact_id: str
    version: int
    sha256: Digest
    size: int
    mime: str
    offset: int
    data_base64: str
    next_offset: int
    eof: bool
    preview_mode: Literal["plain_text", "download_only"]


class ArtifactCommandParams(RuntimeSessionParams):
    command_id: RuntimeIdentifier


class ArtifactCancelledResult(Result):
    cancel_requested: bool
    effects_undone: Literal[False]


class ArtifactBlockedResult(Result):
    blocked: Literal[True]
    reason: Literal["budget_unavailable"]


class ArtifactBundleResult(Result):
    project_id: str
    outputs: list[ArtifactPublishResult]
    manifest: ArtifactPublishResult
    state: Literal["published"]
    publication_atomic: Literal[False]
    external_production: Literal["not_performed"]


class ArtifactResponseJSON(Result):
    project_id: str | None = None
    response_json: Annotated[str, Field(max_length=3145728)]


class ArtifactControlStatus(Result):
    command_id: str
    run_id: str
    status: Literal["accepted", "claimed", "completed", "cancelled", "failed", "blocked"]
    owner_live: bool
    expires_at: float | None
    result: ArtifactPublishResult | ArtifactCancelledResult | ArtifactBlockedResult | ArtifactBundleResult | ArtifactResponseJSON | None


method("runtime.project.create", params=RuntimeProjectCreateParams, result=RuntimeProjectResult)
method("runtime.project.get", params=RuntimeProjectParams, result=RuntimeProjectResult)
method("runtime.project.list", params=RuntimeSessionParams, result=RuntimeProjectListResult)
method("runtime.project.claim", params=RuntimeProjectRevisionParams, result=RuntimeProjectResult)
method("runtime.project.update", params=RuntimeProjectUpdateParams, result=RuntimeProjectResult)
method("runtime.project.grants.set", params=RuntimeProjectGrantsParams, result=RuntimeProjectResult)
method("runtime.artifact.prepare", params=ArtifactPrepareParams, result=ArtifactProposalResult,
       doc="Prepare one exact owned edit for approval; does not dispatch a model or publish bytes.")
method("runtime.artifact.publish", params=ArtifactPublishParams, result=ArtifactPublishResult,
       doc="Approve and publish exactly the prepared content under the same live bounded control claim.")
method("runtime.artifact.bytes.prepare", params=ArtifactBytesPrepareParams, result=ArtifactProposalResult,
       doc="Prepare bounded complete bytes using an allowlisted structural format validator; no active preview.")
method("runtime.artifact.bytes.publish", params=ArtifactBytesPublishParams, result=ArtifactPublishResult,
       doc="Approve and publish exact validated bytes; visual fidelity is not implied.")
method("runtime.artifact.get", params=ArtifactReadParams, result=ArtifactReadResult,
       doc="Read complete immutable bytes in bounded chunks; render only as plain text.")
method("runtime.artifact.status", params=ArtifactCommandParams, result=ArtifactControlStatus)
method("runtime.artifact.cancel", params=ArtifactCommandParams, result=ArtifactControlStatus)

method("runtime.artifact.edit.prepare", params=ArtifactEditParams, result=ArtifactProposalResult)
method("runtime.artifact.edit.publish", params=ArtifactEditPublishParams, result=ArtifactPublishResult)
method("runtime.artifact.merge.prepare", params=ArtifactMergeParams, result=ArtifactProposalResult)
method("runtime.artifact.merge.publish", params=ArtifactMergePublishParams, result=ArtifactPublishResult)


class ArtifactRecoveryParams(RuntimeProjectParams):
    effect_id: RuntimeIdentifier
    offset: Annotated[StrictInt, Field(ge=0)] = 0
    limit: Annotated[StrictInt, Field(ge=1, le=65536)] = 65536


class ArtifactRecoveryResult(ArtifactReadResult):
    publication_state: Literal["committed", "published_uncommitted"]


method("runtime.artifact.recovery.get", params=ArtifactRecoveryParams, result=ArtifactRecoveryResult,
       doc="Recover exact confirmed publication bytes without publishing a catalog version or moving a head.")
