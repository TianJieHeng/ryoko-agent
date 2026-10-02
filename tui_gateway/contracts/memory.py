"""Owner-bound memory controls; primary harness capabilities remain explicit."""
from typing import Literal

from .base import Result
from .registry import method
from .runtime_v1 import RuntimeSessionParams


class MemoryCapabilities(Result):
    backend: Literal["builtin", "personal_mcp"]
    recall: bool
    write: bool
    supersede: bool
    delete: bool
    export: bool
    session_ingest: bool


class MemoryHealth(Result):
    backend: Literal["builtin", "personal_mcp"]
    status: Literal["ready", "unconfigured", "degraded", "disabled"]
    reason_code: str | None
    supported_operations: list[str]


class MemoryStatusResult(Result):
    capabilities: MemoryCapabilities
    health: MemoryHealth


method("runtime.memory.status", params=RuntimeSessionParams, result=MemoryStatusResult,
       doc="Inspect the owned agent's single routed memory backend without recall or fallback.")


from typing import Annotated
from pydantic import Field, StrictInt, model_validator
from .runtime_v1 import RuntimeIdentifier
from .runtime_results import Digest


class MemoryRecord(Result):
    record_id: str
    version: int
    revision: int
    supersedes_version: int | None
    owner_agent_id: str
    owner_principal_id: str
    owner_profile_id: str
    namespace_id: str
    target: Literal["memory", "user"]
    kind: Literal["stated_fact", "inference", "preference", "decision", "procedure_reference"]
    content: str | None
    source_ref: str
    author: str
    created_at: float
    updated_at: float
    valid_from: float
    valid_to: float | None
    confidence: float | None
    validity: Literal["valid", "uncertain", "invalid", "superseded"]
    scope: str
    deletion_state: Literal["present", "deleted"]
    deleted_at: float | None
    superseded_by_version: int | None = None


class MemoryRecordParams(RuntimeSessionParams):
    record_id: RuntimeIdentifier
    version: Annotated[StrictInt, Field(ge=1)] | None = None


class MemoryRecordResult(Result):
    record: MemoryRecord


class MemoryWriteParams(RuntimeSessionParams):
    content: Annotated[str, Field(min_length=1, max_length=65536)]
    record_id: RuntimeIdentifier
    expected_version: Annotated[StrictInt, Field(ge=0)] = 0
    target: Literal["memory", "user"] = "memory"
    kind: Literal["stated_fact", "inference", "preference", "decision", "procedure_reference"] = "stated_fact"
    source_ref: Annotated[str, Field(min_length=1, max_length=4096)] | None = None
    author: RuntimeIdentifier | None = None
    valid_from: float | None = None
    valid_to: float | None = None
    confidence: Annotated[float, Field(ge=0, le=1)] | None = None
    validity: Literal["valid", "uncertain", "invalid"] = "valid"
    scope: Annotated[str, Field(min_length=1, max_length=264)] = "individual"


class MemoryDeleteParams(RuntimeSessionParams):
    record_id: RuntimeIdentifier
    expected_version: Annotated[StrictInt, Field(ge=1)]


class MemoryWriteSuccess(Result):
    success: Literal[True]
    record: MemoryRecord
    acknowledged_version: int
    revision: int


class MemoryWriteConflict(Result):
    success: Literal[False]
    code: Literal["version_conflict"]
    conflict_id: str
    record_id: str
    expected_version: int
    current_version: int


class MemoryMutationResult(Result):
    outcome: MemoryWriteSuccess | MemoryWriteConflict


class MemorySnapshotParams(RuntimeSessionParams):
    include_deleted: bool = False
    project_id: RuntimeIdentifier | None = None
    expected_revision: Annotated[StrictInt, Field(ge=0)] | None = None

    @model_validator(mode="after")
    def continuation_has_revision(self):
        if getattr(self, "offset", 0) > 0 and self.expected_revision is None:
            raise ValueError("Continuation requires the exact prior snapshot revision")
        return self


class MemoryListParams(MemorySnapshotParams):
    offset: Annotated[StrictInt, Field(ge=0)] = 0
    limit: Annotated[StrictInt, Field(ge=1, le=100)] = 100


class MemoryListResult(Result):
    revision: int
    records: list[MemoryRecord]
    offset: int
    next_offset: int
    total: int
    has_more: bool


class MemoryExportParams(MemorySnapshotParams):
    offset: Annotated[StrictInt, Field(ge=0)] = 0
    limit: Annotated[StrictInt, Field(ge=1, le=65536)] = 65536


class MemoryExportResult(Result):
    revision: int
    sha256: Digest
    size: int
    format: Literal["json"]
    deletion_semantics: Literal["tombstones_not_physical_erasure"]
    offset: int
    data_base64: str
    next_offset: int
    eof: bool


method("runtime.memory.record.get", params=MemoryRecordParams, result=MemoryRecordResult)
method("runtime.memory.records.list", params=MemoryListParams, result=MemoryListResult)
method("runtime.memory.record.write", params=MemoryWriteParams, result=MemoryMutationResult,
       doc="Compare-and-swap one owner-bound built-in record; never falls back from personal MCP.")
method("runtime.memory.record.delete", params=MemoryDeleteParams, result=MemoryMutationResult,
       doc="Tombstone one exact built-in record version; this is not physical erasure of backups.")
method("runtime.memory.export", params=MemoryExportParams, result=MemoryExportResult,
       doc="Read a revision-bound local structured export in bounded chunks; no remote sharing.")


class MemoryScopeParams(RuntimeSessionParams):
    project_id: RuntimeIdentifier | None


class MemoryScopeResult(Result):
    project_id: str | None
    scope_key: str


method("runtime.memory.scope.set", params=MemoryScopeParams, result=MemoryScopeResult,
       doc="Select an explicitly granted project for fresh built-in memory context; never rewrites the frozen prefix.")
