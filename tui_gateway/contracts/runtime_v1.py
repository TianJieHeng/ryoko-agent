"""Versioned durable runtime projections, separate from ephemeral token streaming.

The live session supplies authenticated identity; no client-provided principal,
agent, profile, provider configuration, or conversation history is accepted.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import ConfigDict, Field, StrictInt, field_validator, model_validator

from .base import Params, Result
from .registry import method

RuntimeIdentifier = Annotated[str, Field(min_length=1, max_length=256)]
RuntimeOperation = Literal["submit", "steer", "cancel", "approval"]
RuntimeStatus = Literal["idle", "accepted", "claimed", "completed", "failed", "blocked", "cancelled"]


class RuntimeCapabilitiesParams(Params):
    model_config = ConfigDict(extra="forbid", strict=True)
    session_id: RuntimeIdentifier


class RuntimeSessionParams(RuntimeCapabilitiesParams):
    schema_version: Literal[1]

    @field_validator("schema_version", mode="before")
    @classmethod
    def version_is_integer(cls, value):
        if type(value) is not int:
            raise ValueError("schema_version must be an integer")
        return value


class RuntimeTextPayload(Params):
    model_config = ConfigDict(extra="forbid", strict=True)
    text: Annotated[str, Field(min_length=1, max_length=65536)]

    @field_validator("text")
    @classmethod
    def text_is_nonblank(cls, value):
        if not value.strip():
            raise ValueError("text must not be blank")
        return value


class RuntimeCancelPayload(Params):
    model_config = ConfigDict(extra="forbid", strict=True)
    reason: Annotated[str, Field(max_length=1024)] = ""


class RuntimeApprovalPayload(Params):
    model_config = ConfigDict(extra="forbid", strict=True)
    approval_id: RuntimeIdentifier
    decision: Literal["approve", "deny"]


class RuntimeCommandParams(RuntimeSessionParams):
    """Operation selects the payload: text for submit/steer, reason for cancel,
    approval_id/decision for approval. Accepted is a durable receipt, not proof
    of execution; consult capabilities and replay for execution status.
    """

    command_id: RuntimeIdentifier
    idempotency_key: RuntimeIdentifier
    expected_revision: Annotated[StrictInt, Field(ge=0)] | None
    operation: RuntimeOperation
    payload: RuntimeTextPayload | RuntimeCancelPayload | RuntimeApprovalPayload

    @model_validator(mode="before")
    @classmethod
    def payload_matches_operation(cls, values):
        if isinstance(values, dict):
            operation = values.get("operation")
            payload_type = {
                "submit": RuntimeTextPayload,
                "steer": RuntimeTextPayload,
                "cancel": RuntimeCancelPayload,
                "approval": RuntimeApprovalPayload,
            }.get(operation) if isinstance(operation, str) else None
            if payload_type is not None:
                values = {**values, "payload": payload_type.model_validate(values.get("payload"))}
        return values


class RuntimeOperationCapability(Result):
    operation: RuntimeOperation
    accepts_commands: bool
    executes: bool
    effects_enabled: bool
    reason: str | None = None


class RuntimeAdmissionLimits(Result):
    scope: Literal["profile_store"] = "profile_store"
    max_active: int
    max_queued: int
    max_per_principal: int
    max_payload_bytes: int
    max_queue_bytes: int
    max_database_bytes: int
    ttl_seconds: float
    interactive_boost_seconds: float
    launch_lease_seconds: float


class RuntimeProviderCapabilities(Result):
    """Adapter declarations; model support and live cancellation remain separate."""

    schema_version: Literal[1]
    api_mode: str
    adapter: str
    declaration_scope: Literal["adapter"]
    streaming: Literal["supported", "unsupported", "unknown"]
    parallel_tools: Literal["supported", "unsupported", "unknown"]
    media_inputs: list[str]
    model_capabilities: Literal["unverified"]
    usage: Literal["final_response", "provider_reported", "unknown"]
    cancellation: Literal["local_only", "provider_acknowledgment", "unknown"]
    cache_semantics: str
    opaque_state_version: int | None
    execution_owner: Literal["hermes", "provider", "unknown"]
    durable_execution: bool
    bounded_budget: Literal["conditional_openai_text", "unsupported"]


class RuntimeToolView(Result):
    """Frozen authorized metadata only; inspection cannot refresh the prompt."""

    catalog_version: str
    session_policy_version: str
    installed_tool_ids: list[str]
    authorized_tool_ids: list[str]
    discoverable_tool_ids: list[str]
    selected_tool_ids: list[str]
    unavailable_reasons: dict[str, str]


class RuntimePhysicalAttempt(Result):
    """Opaque account correlation only, never credentials, endpoint or payload."""

    attempt_id: RuntimeIdentifier
    reason: Literal["initial", "auth_failure", "quota_exhausted", "throttled", "overloaded",
                    "context_overflow", "unsupported_capability", "ambiguous_transport", "request_rejected"]
    provider_account_ref: RuntimeIdentifier
    reservation_id: RuntimeIdentifier
    remote_acceptance: Literal["unknown", "rejected", "accepted"]
    logical_request_id: RuntimeIdentifier


class RuntimeCapabilities(Result):
    schema_versions: list[Literal[1]]
    operations: list[RuntimeOperationCapability]
    strict_identity_required: bool
    durable_replay: bool
    max_events: int
    admission: RuntimeAdmissionLimits | None = None
    provider: RuntimeProviderCapabilities | None = None
    tool_view: RuntimeToolView | None = None
    cursor_policy: Literal["snapshot_required_on_expired_or_unknown_cursor"]


class RuntimeConflict(Result):
    code: str
    message: str


class CommandReceipt(Result):
    schema_version: Literal[1]
    command_id: str
    status: Literal["accepted", "rejected", "duplicate"]
    durable_revision: int
    run_id: str | None
    conflict: RuntimeConflict | None = None


class RuntimeSnapshotState(Result):
    status: RuntimeStatus
    run_id: str | None
    last_command_id: str | None
    last_operation: RuntimeOperation | None


class RuntimeOutstandingRequest(Result):
    request_id: str
    kind: Literal["approval", "input"]
    status: Literal["pending"]


class RuntimeArtifactReference(Result):
    artifact_id: str
    version: str


class RuntimeUnresolvedEffect(Result):
    effect_id: str
    status: Literal["prepared", "dispatched", "outcome_unknown", "reconciliation_required"]


class RuntimeUnresolvedInvocation(Result):
    operation_id: str
    status: Literal["pending", "outcome_uncertain"]


class RuntimeReferenceCounts(Result):
    outstanding_requests: int = 0
    artifacts: int = 0
    unresolved_effects: int = 0
    unresolved_invocations: int = 0


class RuntimeAdmissionJob(Result):
    command_id: str
    state: Literal["queued", "running", "finished", "expired", "cancelled", "rejected"]
    enqueued_at: float
    expires_at: float
    reason: str | None


class RuntimeAdmissionSnapshot(Result):
    draining: bool
    jobs: list[RuntimeAdmissionJob]


class RuntimeCancellation(Result):
    request_id: str | None
    requested_at: float | None
    local_state: Literal["running", "requested", "stopped"]
    upstream_ack: bool | None
    pending_effect_ids: list[str]
    pending_handles: list[str]
    partial_result_available: bool
    remote_effects_undone: Literal[False]


class MissionSnapshot(Result):
    schema_version: Literal[1]
    session_id: str
    revision: int
    state: RuntimeSnapshotState
    outstanding_requests: list[RuntimeOutstandingRequest]
    artifacts: list[RuntimeArtifactReference]
    unresolved_effects: list[RuntimeUnresolvedEffect]
    unresolved_invocations: list[RuntimeUnresolvedInvocation] = Field(default_factory=list)
    reference_counts: RuntimeReferenceCounts = Field(default_factory=RuntimeReferenceCounts)
    reference_limit: int = 100
    references_truncated: bool = False
    last_cursor: str
    compatibility_status: Literal["native", "legacy"]
    admission: RuntimeAdmissionSnapshot | None = None


class RuntimeEventPayload(Result):
    """Safe correlation metadata only. Raw model/tool outputs stay off this wire."""

    command_id: str | None = None
    operation: RuntimeOperation | Literal["artifact"] | None = None
    effect_state: Literal["prepared", "dispatched", "confirmed", "failed", "outcome_unknown", "reconciliation_required"] | None = None
    operation_type: Literal["artifact_publish", "project_artifact_publish", "mission_test_execution", "unsupported"] | None = None
    approval_status: Literal["pending", "approved", "denied", "consumed", "invalidated"] | None = None
    expires_at: float | None = None
    invalidation_reason: str | None = None
    mission_revision: int | None = None
    mission_state: Literal["ready", "working", "waiting_for_user", "waiting_for_source", "ready_to_review",
                           "completed", "partially_completed", "paused", "cancelled", "failed"] | None = None
    checkpoint_id: str | None = None
    included_seq: int | None = None
    cancellation: RuntimeCancellation | None = None
    physical_attempt: RuntimePhysicalAttempt | None = None
    admission_state: Literal["expired", "cancelled", "rejected"] | None = None
    control_outcome: Literal["steer_queued", "steer_not_queued", "cancel_requested", "cancel_not_requested"] | None = None


class RuntimeEventEnvelope(Result):
    schema_version: Literal[1]
    event_id: str
    session_id: str
    seq: int
    cursor: str
    generation: int
    mission_id: str | None
    run_id: str | None
    operation_id: str | None
    effect_id: str | None
    delivery_id: str | None
    approval_id: str | None
    occurred_at: float
    type: Literal[
        "command.accepted", "command.claimed", "command.completed", "command.failed",
        "command.blocked", "command.cancelled", "checkpoint.published", "runtime.output",
        "runtime.state", "approval.requested", "approval.resolved", "effect.recorded",
        "model.started", "model.completed", "model.failed", "tool.started", "tool.completed", "tool.failed",
    ]
    payload: RuntimeEventPayload


class RuntimeEventsSinceParams(RuntimeSessionParams):
    cursor: Annotated[str, Field(min_length=1, max_length=256)] | None = None
    limit: Annotated[StrictInt, Field(ge=1, le=200)] = 100


class RuntimeEventsSinceResult(Result):
    status: Literal["ok", "snapshot_required"]
    events: list[RuntimeEventEnvelope]
    snapshot: MissionSnapshot | None
    last_cursor: str
    has_more: bool


method("runtime.capabilities", params=RuntimeCapabilitiesParams, result=RuntimeCapabilities,
       doc="Negotiate the owned session's durable runtime API and executable operations.")
method("runtime.command", params=RuntimeCommandParams, result=CommandReceipt,
       doc="Accept one idempotent command. Retries return the original durable receipt.")
method("runtime.snapshot", params=RuntimeSessionParams, result=MissionSnapshot,
       doc="Read a consistent durable mission projection and its restart-stable cursor.")
method("runtime.events.since", params=RuntimeEventsSinceParams, result=RuntimeEventsSinceResult,
       doc="Read bounded durable transitions, or an explicit snapshot_required with a consistent snapshot.")
