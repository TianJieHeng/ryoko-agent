"""Exact retained scheduled-draft review, with separate publication approval."""
from typing import Annotated, Literal
from pydantic import Field, StrictInt
from .schedules import ScheduleGetParams
from .runtime_v1 import RuntimeIdentifier
from .runtime_results import Digest
from .artifacts import ArtifactReadResult, ArtifactProposalResult, ArtifactPublishResult
from .registry import method


class ScheduleOutputParams(ScheduleGetParams):
    occurrence_id: RuntimeIdentifier
    output_index: Annotated[StrictInt, Field(ge=0, le=15)]


class ScheduleOutputReadParams(ScheduleOutputParams):
    offset: Annotated[StrictInt, Field(ge=0)] = 0
    limit: Annotated[StrictInt, Field(ge=1, le=65536)] = 65536


class ScheduleOutputReadResult(ArtifactReadResult):
    draft_only: Literal[True]
    occurrence_id: str
    occurrence_state: str
    output_index: int
    workflow_run_id: str


class ScheduleOutputPrepareParams(ScheduleOutputParams):
    command_id: RuntimeIdentifier
    expected_sha256: Digest


class ScheduleOutputPublishParams(ScheduleOutputPrepareParams):
    approval_id: RuntimeIdentifier
    approval_digest: Digest


method("runtime.schedule.output.get", params=ScheduleOutputReadParams, result=ScheduleOutputReadResult,
       doc="Read exact confirmed private scheduled output bytes without rerunning or publishing")
method("runtime.schedule.output.prepare", params=ScheduleOutputPrepareParams, result=ArtifactProposalResult,
       doc="Prepare a fresh human approval for exact retained scheduled draft bytes")
method("runtime.schedule.output.publish", params=ScheduleOutputPublishParams, result=ArtifactPublishResult,
       doc="Consume fresh exact human approval to publish retained scheduled bytes without rerunning")
