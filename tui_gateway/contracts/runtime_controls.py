"""Owner/profile pause, explicit dispatch semantics and read-only receipt recovery."""
from typing import Annotated, Literal
from pydantic import Field, StrictInt
from .base import Result
from .registry import method
from .runtime_v1 import RuntimeIdentifier, RuntimeSessionParams


class RuntimeControlGetParams(RuntimeSessionParams):
    operation_id: RuntimeIdentifier | None = None


class RuntimeControlParams(RuntimeSessionParams):
    operation_id: RuntimeIdentifier
    expected_revision: Annotated[StrictInt, Field(ge=0)]


class RuntimeControlState(Result):
    revision: int
    paused: bool
    updated_at: float | None
    scope: Literal["owner_profile"]
    admission_blocked: bool
    scheduled_dispatch_blocked: bool
    in_flight_dispatch: Literal["blocked_at_next_boundary", "allowed_at_checked_boundary"]
    accepted_commands: int
    claimed_commands: int
    accepted_work_retained: Literal[True]
    already_dispatched_may_complete: Literal[True]
    provider_cancelled: Literal[False]
    remote_effects_undone: Literal[False]


class RuntimeControlOperation(Result):
    operation_id: str
    digest: str
    revision: int
    paused: bool
    committed_at: float
    status: Literal["committed"]


class RuntimeControlResult(Result):
    control: RuntimeControlState
    operation: RuntimeControlOperation | None
    dispatch_performed: Literal[False]


method("runtime.control.get", params=RuntimeControlGetParams, result=RuntimeControlResult,
       doc="Read owner/profile pause state and an optional immutable operation receipt; never replay a control.")
for operation in ("pause", "resume"):
    method("runtime.control." + operation, params=RuntimeControlParams, result=RuntimeControlResult,
           doc="CAS and idempotently set owner/profile admission and dispatch state. Existing work retains ownership; no rollback or provider stop is implied.")
