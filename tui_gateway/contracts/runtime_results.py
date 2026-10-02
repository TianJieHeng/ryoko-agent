"""Owned immutable results and delivery receipts, independent of execution status."""
from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictInt

from .base import Payload, Result
from .registry import method
from .runtime_v1 import RuntimeIdentifier, RuntimeSessionParams

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class RuntimeResultGetParams(RuntimeSessionParams):
    command_id: RuntimeIdentifier
    offset: Annotated[StrictInt, Field(ge=0)] = 0
    limit: Annotated[StrictInt, Field(ge=1, le=65536)] = 65536


class RuntimeResultChunk(Result):
    command_id: str
    artifact_id: str
    version: Annotated[int, Field(ge=1)]
    sha256: Digest
    size: Annotated[int, Field(ge=0)]
    mime: str
    offset: int
    data_base64: str
    next_offset: int
    eof: bool
    publication_state: Literal["committed", "published_uncommitted"]
    delivery_id: str | None


class RuntimeDeliveryParams(RuntimeSessionParams):
    delivery_id: RuntimeIdentifier


class RuntimeDeliveryAckParams(RuntimeDeliveryParams):
    attempt_token: RuntimeIdentifier
    sha256: Digest
    text_received: StrictBool = False
    artifact_received: StrictBool = False


class RuntimeDeliveryDestination(Result):
    kind: Literal["local_runtime"]
    session_id: str
    principal_id: str
    profile_id: str
    agent_id: str


class RuntimeDeliveryComponents(Result):
    text: Literal["not_sent", "client_received"]
    artifact: Literal["not_sent", "client_received"]


class RuntimeDeliveryReceipt(Result):
    delivery_id: str
    artifact_id: str
    version: int
    sha256: Digest
    destination: RuntimeDeliveryDestination
    state: Literal["pending", "attempting", "awaiting_ack", "partial", "delivered", "failed", "outcome_unknown", "dead_letter"]
    acknowledgment_level: Literal["none", "transport_accepted", "client_received"]
    components: RuntimeDeliveryComponents
    platform_ids: list[str]
    attempt_count: int
    max_attempts: int
    next_attempt_at: float | None
    deadline_at: float
    retention_until: float
    last_error: str | None
    result_available: bool


class RuntimeResultAvailablePayload(Payload):
    delivery_id: str
    command_id: str
    artifact_id: str
    version: Annotated[int, Field(ge=1)]
    sha256: Digest
    size: Annotated[int, Field(ge=0)]
    mime: str
    attempt_token: str


method("runtime.result.get", params=RuntimeResultGetParams, result=RuntimeResultChunk,
       doc="Read bounded digest-checked immutable result bytes without executing or delivering work.")
method("runtime.delivery.status", params=RuntimeDeliveryParams, result=RuntimeDeliveryReceipt,
       doc="Read delivery truth without creating a delivery attempt.")
method("runtime.delivery.retry", params=RuntimeDeliveryParams, result=RuntimeDeliveryReceipt,
       doc="Explicitly retry only a result notification on the owned local transport; never rerun inference.")
method("runtime.delivery.ack", params=RuntimeDeliveryAckParams, result=RuntimeDeliveryReceipt,
       doc="Record exact attempt/digest-bound client component receipt, not human read confirmation.")
