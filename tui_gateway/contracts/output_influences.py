"""Versioned fresh-input references, not inferred causal explanations."""
from typing import Annotated, Literal
from pydantic import Field, StrictInt
from .base import Result
from .runtime_v1 import RuntimeIdentifier, RuntimeSessionParams
from .runtime_results import Digest
from .registry import method


class OutputContextParams(RuntimeSessionParams):
    run_id: RuntimeIdentifier


class OutputReference(Result):
    record_id: str
    version: int
    source_ref: str
    namespace_id: str
    deletion_state: str
    scope: str


class OutputDirective(Result):
    control_id: str
    record_id: str
    version: int
    action: Literal["ignore", "correct", "expire"]
    scope: Literal["response", "project", "general"]
    replacement: str | None


class OutputContextResult(Result):
    run_id: str
    backend: Literal["builtin", "personal_mcp"]
    namespace_id: str | None
    project_id: str | None
    references: list[OutputReference]
    context_packet_sha256: Digest
    immutable_prefix_sha256: Digest
    coverage: Literal["fresh_memory_context_only"]
    controls: list[OutputDirective]
    degraded: bool
    state: Literal["supplied_to_provider_call"]
    context_sha256: Digest
    latest: bool
    causal_explanation: Literal[False]
    historical_context_enumerated: Literal[False]


class OutputContextSummary(Result):
    run_id: str
    context_sha256: Digest


class OutputContextsResult(Result):
    outputs: list[OutputContextSummary]
    backend: Literal["builtin", "personal_mcp"]
    complete: Literal[False]
    unavailable_reason: str | None


class OutputControlParams(OutputContextParams):
    control_id: RuntimeIdentifier
    context_sha256: Digest
    record_id: RuntimeIdentifier
    expected_version: Annotated[StrictInt, Field(ge=1)]
    namespace_id: RuntimeIdentifier
    action: Literal["ignore", "correct", "remove"]
    scope: Literal["response", "project", "general"]
    project_id: RuntimeIdentifier | None = None
    content: Annotated[str, Field(min_length=1, max_length=4096)] | None = None


class OutputControlGetParams(RuntimeSessionParams):
    control_id: RuntimeIdentifier


class OutputControlResult(Result):
    control_id: str
    run_id: str
    action: Literal["ignore", "correct", "remove"]
    scope: Literal["response", "project", "general"]
    project_id: str | None
    status: Literal["queued_next_turn", "mutation_pending", "memory_acknowledged", "version_conflict",
                    "context_supplied", "stale_not_applied", "expired"]
    acknowledged_version: int | None
    applied_run_id: str | None
    current_output_changed: Literal[False]
    current_run_application: Literal["not_applied", "late_not_applied"]
    deletion_semantics: Literal["none", "tombstone_not_physical_erasure"]


method("runtime.memory.output.list", params=RuntimeSessionParams, result=OutputContextsResult)
method("runtime.memory.output.get", params=OutputContextParams, result=OutputContextResult)
method("runtime.memory.output.control", params=OutputControlParams, result=OutputControlResult)
method("runtime.memory.output.control.get", params=OutputControlGetParams, result=OutputControlResult)
