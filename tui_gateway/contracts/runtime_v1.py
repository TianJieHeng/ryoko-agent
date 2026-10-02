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


class RuntimeCapabilities(Result):
    schema_versions: list[Literal[1]]
    operations: list[RuntimeOperationCapability]
    strict_identity_required: bool
    durable_replay: bool
    max_events: int
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
    status: Literal["pending", "outcome_uncertain"]


class MissionSnapshot(Result):
    schema_version: Literal[1]
    session_id: str
    revision: int
    state: RuntimeSnapshotState
    outstanding_requests: list[RuntimeOutstandingRequest]
    artifacts: list[RuntimeArtifactReference]
    unresolved_effects: list[RuntimeUnresolvedEffect]
    last_cursor: str
    compatibility_status: Literal["native", "legacy"]


class RuntimeEventPayload(Result):
    """Safe correlation metadata only. Raw model/tool outputs stay off this wire."""

    command_id: str | None = None
    operation: RuntimeOperation | None = None
    checkpoint_id: str | None = None
    included_seq: int | None = None
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
