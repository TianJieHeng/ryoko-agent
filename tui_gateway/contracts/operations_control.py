"""Owned local operator plans; no filesystem, credentials or arbitrary repairs."""
from typing import Annotated, Literal

from pydantic import Field, StrictInt

from .base import Result
from .registry import method
from .runtime_v1 import RuntimeIdentifier, RuntimeSessionParams
from .runtime_results import Digest


class OperationsRecord(Result):
    record_json: Annotated[str, Field(min_length=2, max_length=262144)]


class OperationsAuditParams(RuntimeSessionParams):
    cursor: Annotated[str, Field(max_length=2048)] | None = None
    limit: Annotated[StrictInt, Field(ge=1, le=100)] = 50


class OperationsRepairParams(RuntimeSessionParams):
    action: Literal["reconcile-effect", "retry-delivery", "revoke-lease", "rebuild-index", "restore-checkpoint"]
    target_id: RuntimeIdentifier


class OperationsApplyParams(RuntimeSessionParams):
    plan_json: Annotated[str, Field(min_length=2, max_length=262144)]
    authorization_digest: Digest


class OperationsDeletionParams(RuntimeSessionParams):
    memory_record_id: RuntimeIdentifier | None = None


_SPECS = {
    "runtime.operations.inspect": RuntimeSessionParams,
    "runtime.operations.audit": OperationsAuditParams,
    "runtime.operations.retention": RuntimeSessionParams,
    "runtime.operations.checkpoint": RuntimeSessionParams,
    "runtime.operations.repair.prepare": OperationsRepairParams,
    "runtime.operations.repair.apply": OperationsApplyParams,
    "runtime.operations.deletion.prepare": OperationsDeletionParams,
    "runtime.operations.deletion.apply": OperationsApplyParams,
}
for name, params in _SPECS.items():
    method(name, params=params, result=OperationsRecord)
