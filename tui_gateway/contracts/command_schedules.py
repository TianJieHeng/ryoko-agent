"""Additional command-schedule controls; all times are UTC Unix seconds."""
from typing import Annotated, Literal
from pydantic import Field, StrictInt
from .schedules import ScheduleGetParams, ScheduleRecordResult
from .runtime_v1 import RuntimeIdentifier, RuntimeSessionParams
from .base import Result
from .registry import method


class ScheduleRunNowParams(ScheduleGetParams):
    command_id: RuntimeIdentifier
    expected_revision: Annotated[StrictInt, Field(ge=1)]


class ScheduleCutoverParams(ScheduleRunNowParams):
    source_id: RuntimeIdentifier
    retirement_receipt: Annotated[str, Field(min_length=1, max_length=2048)]
    unresolved_occurrences: Annotated[list[RuntimeIdentifier], Field(max_length=100)]


COMMAND_SCHEDULE_SPECS = {
    "runtime.schedule.run_now": (ScheduleRunNowParams, "Admit one idempotent occurrence through the ordinary durable command queue"),
    "runtime.schedule.cutover": (ScheduleCutoverParams, "Retain a trusted legacy-retirement attestation; does not independently verify foreign execution"),
}
for _name, (_params, _doc) in COMMAND_SCHEDULE_SPECS.items():
    method(_name, params=_params, result=ScheduleRecordResult, doc=_doc)


class ScheduleSchedulerStatusResult(Result):
    schema_version: Literal[1]
    authority: Literal["runtime_cron"]
    enabled: bool
    surface: Literal["stdio", "other"]
    state: Literal["disabled", "awaiting_maintenance", "waiting", "ticking", "healthy", "lock_busy",
                   "standby_other_owner", "paused", "draining", "retired", "error", "stopping", "unsupported_surface"]
    maintenance_started: bool
    maintenance_live: bool
    recurring_admission_ready: bool
    tick_lock_held: bool
    other_gateway_owner_live: bool
    poll_interval_seconds: Annotated[StrictInt, Field(ge=1, le=300)] | None
    last_maintenance_at: Annotated[float, Field(ge=0)] | None
    last_tick_started_at: Annotated[float, Field(ge=0)] | None
    last_tick_completed_at: Annotated[float, Field(ge=0)] | None
    last_tick_succeeded_at: Annotated[float, Field(ge=0)] | None
    last_error_code: Literal["invalid_config", "tick_failed"] | None
    execution_requires_live_owned_session: Literal[True]
    scheduler_pause_cancels_running: Literal[False]
    dispatch_performed: Literal[False]


method("runtime.schedule.scheduler.status", params=RuntimeSessionParams, result=ScheduleSchedulerStatusResult,
       doc="Read owned profile stdio scheduler lifecycle and actual locked-tick evidence; never activate scheduling or dispatch work")
