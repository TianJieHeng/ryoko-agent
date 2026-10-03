"""Exact human-reviewed delivery to a stable specialist, effective next session."""
from typing import Annotated, Literal

from pydantic import Field, StrictInt

from .base import Result
from .registry import method
from .runtime_results import Digest
from .runtime_v1 import RuntimeIdentifier
from .workflows import WorkflowProjectParams, WorkflowVersionParams, WorkflowRecord


class WorkflowDeliveryParams(WorkflowVersionParams):
    command_id: RuntimeIdentifier
    sha256: Digest
    specialist_id: RuntimeIdentifier
    expected_delivery_revision: Annotated[StrictInt, Field(ge=0)]
    action: Literal["deliver", "rollback"]


class WorkflowDeliveryCommitParams(WorkflowDeliveryParams):
    approval_id: RuntimeIdentifier
    approval_digest: Digest


class WorkflowDeliveryRecord(Result):
    delivery_id: str
    project_id: str
    workflow_id: str
    version: int
    sha256: Digest
    specialist_id: str
    delivery_revision: int
    action: Literal["deliver", "rollback"]
    approval_id: str
    approval_digest: Digest
    previous_delivery_id: str | None
    activation: Literal["next_session"]
    execution_authority: Literal[False]
    personal_memory_shared: Literal[False]
    recorded_at: float


class WorkflowDeliveryPrepareResult(Result):
    approval_id: str
    approval_digest: Digest
    expires_at: float
    scope_json: str
    workflow: WorkflowRecord
    current_delivery: WorkflowDeliveryRecord | None


class WorkflowDeliveryCommitResult(Result):
    delivery: WorkflowDeliveryRecord


class WorkflowDeliveryListParams(WorkflowProjectParams):
    specialist_id: RuntimeIdentifier


class WorkflowDeliveryListResult(Result):
    deliveries: list[WorkflowDeliveryRecord]
    complete: Literal[True]


method("runtime.workflow.delivery.prepare", params=WorkflowDeliveryParams, result=WorkflowDeliveryPrepareResult,
       doc="Review exact approved workflow bytes and a named stable specialist before installing advisory knowledge next session.")
method("runtime.workflow.delivery.commit", params=WorkflowDeliveryCommitParams, result=WorkflowDeliveryCommitResult,
       doc="Install the exact reviewed immutable specialist pin with compare-and-swap; rollback selects an earlier delivered approved version.")
method("runtime.workflow.delivery.list", params=WorkflowDeliveryListParams, result=WorkflowDeliveryListResult,
       doc="List current workflow pins for one owned specialist and project; installation grants no tool or personal-memory authority.")
