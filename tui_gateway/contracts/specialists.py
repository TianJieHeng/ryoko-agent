"""Configured, owned, single-specialist handoff controls."""
from typing import Annotated, Literal

from pydantic import ConfigDict, Field, StrictInt

from .base import Params, Result
from .registry import method
from .runtime_v1 import CommandReceipt, RuntimeIdentifier, RuntimeSessionParams
from .runtime_results import Digest


class SpecialistReference(Params):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: RuntimeIdentifier
    version: Annotated[StrictInt, Field(ge=1)]
    sha256: Digest


class SpecialistLimits(Result):
    max_depth: int
    max_total_children: int
    max_concurrent_children: int


class SpecialistGrants(Result):
    allowed_tools: list[str]
    project_grants: list[str]
    mcp_grants: dict[str, list[str]]
    memory_backend: Literal["builtin"]
    personal_memory_access: Literal[False] = False


class SpecialistDescriptor(Result):
    agent_id: RuntimeIdentifier
    responsibility: str
    manifest_sha256: Digest
    methods_ref: SpecialistReference
    limits: SpecialistLimits
    grants: SpecialistGrants
    builtin_memory_namespace: str
    output_contract_json: str


class SpecialistProjectParams(RuntimeSessionParams):
    project_id: RuntimeIdentifier


class SpecialistUnavailable(Result):
    agent_id: RuntimeIdentifier
    code: str


class SpecialistCatalog(Result):
    specialists: list[SpecialistDescriptor]
    unavailable: list[SpecialistUnavailable]
    teams_enabled: Literal[False] = False
    execution: Literal["local_single_child"] = "local_single_child"


class SpecialistTask(Params):
    model_config = ConfigDict(extra="forbid", strict=True)
    project_id: RuntimeIdentifier
    specialist_id: RuntimeIdentifier
    objective: Annotated[str, Field(min_length=1, max_length=16384)]
    artifacts: Annotated[list[SpecialistReference], Field(max_length=32)] = Field(default_factory=list)
    evidence: Annotated[list[SpecialistReference], Field(max_length=32)] = Field(default_factory=list)
    constraints: Annotated[list[Annotated[str, Field(min_length=1, max_length=2048)]], Field(max_length=16)] = Field(default_factory=list)


class SpecialistPreviewParams(RuntimeSessionParams, SpecialistTask):
    pass


class SpecialistSelection(SpecialistTask):
    manifest_sha256: Digest
    config_digest: Digest
    parent_policy_digest: Digest
    mission_id: RuntimeIdentifier | None
    mission_revision: Annotated[StrictInt, Field(ge=1)] | None
    expires_at: Annotated[float, Field(gt=0, allow_inf_nan=False)]


class SpecialistPreview(Result):
    specialist: SpecialistDescriptor
    selection: SpecialistSelection
    preview_sha256: Digest
    runtime_revision: int


class SpecialistHandoffParams(RuntimeSessionParams):
    command_id: RuntimeIdentifier
    idempotency_key: RuntimeIdentifier
    expected_revision: Annotated[StrictInt, Field(ge=0)] | None
    selection: SpecialistSelection
    preview_sha256: Digest


class SpecialistStatusParams(RuntimeSessionParams):
    command_id: RuntimeIdentifier


class SpecialistCompletion(Result):
    specialist_id: RuntimeIdentifier
    manifest_sha256: Digest
    project_id: RuntimeIdentifier
    child_id: RuntimeIdentifier | None
    handoff_sha256: Digest | None
    state: Literal["completed", "failed", "blocked", "cancelled", "unknown"]
    summary: Annotated[str, Field(max_length=32768)]
    summary_truncated: bool
    schema_valid: bool | None
    parent_review_required: Literal[True] = True
    execution_resumed: Literal[False] = False


class SpecialistStatus(Result):
    command_id: RuntimeIdentifier
    run_id: RuntimeIdentifier
    specialist_id: RuntimeIdentifier
    manifest_sha256: Digest
    project_id: RuntimeIdentifier
    status: Literal["accepted", "claimed", "completed", "failed", "blocked", "cancelled"]
    outcome: Literal["pending", "running", "completed", "failed", "blocked", "cancelled", "unknown"]
    completion: SpecialistCompletion | None
    execution_resumed: Literal[False] = False


method("runtime.specialist.catalog", params=SpecialistProjectParams, result=SpecialistCatalog)
method("runtime.specialist.preview", params=SpecialistPreviewParams, result=SpecialistPreview)
method("runtime.specialist.handoff", params=SpecialistHandoffParams, result=CommandReceipt)
method("runtime.specialist.status", params=SpecialistStatusParams, result=SpecialistStatus)
