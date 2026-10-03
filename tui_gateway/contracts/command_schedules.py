"""Additional command-schedule controls; all times are UTC Unix seconds."""
from typing import Annotated
from pydantic import Field, StrictInt
from .schedules import ScheduleGetParams, ScheduleRecordResult
from .runtime_v1 import RuntimeIdentifier
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
