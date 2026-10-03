"""Owned durable schedules and local retained-source obligations."""
from typing import Annotated, Literal
from pydantic import Field, StrictInt
from .base import Params, Result
from .runtime_v1 import RuntimeIdentifier, RuntimeSessionParams
from .registry import method


class ScheduleRecordResult(Result):
    record_json: Annotated[str, Field(max_length=131072)]


class ScheduleProjectParams(RuntimeSessionParams):
    project_id: RuntimeIdentifier


class ScheduleGetParams(ScheduleProjectParams):
    schedule_id: RuntimeIdentifier


class ScheduleCreateParams(RuntimeSessionParams):
    command_id: RuntimeIdentifier
    definition_json: Annotated[str, Field(min_length=2, max_length=131072)]
    expected_revision: Annotated[StrictInt, Field(ge=1)] | None = None


class ScheduleImportParams(ScheduleCreateParams):
    import_json: Annotated[str, Field(min_length=2, max_length=4096)]


class ScheduleUpdateParams(ScheduleGetParams):
    command_id: RuntimeIdentifier
    expected_revision: Annotated[StrictInt, Field(ge=1)]
    state: Literal["active", "paused", "revoked"]


class ScheduleGrantParams(ScheduleGetParams):
    command_id: RuntimeIdentifier
    expected_revision: Annotated[StrictInt, Field(ge=1)]
    expires_at: float
    max_age_seconds: Annotated[StrictInt, Field(ge=1, le=3600)]
    max_fires: Annotated[StrictInt, Field(ge=1, le=100)]


class ScheduleReconcileParams(ScheduleGetParams):
    command_id: RuntimeIdentifier
    occurrence: RuntimeIdentifier
    evidence_ref_json: Annotated[str, Field(min_length=2, max_length=2048)]


class InboxPrepareParams(ScheduleProjectParams):
    command_id: RuntimeIdentifier
    source_ref_json: Annotated[str, Field(min_length=2, max_length=2048)]
    selection_json: Annotated[str, Field(min_length=2, max_length=8192)]


class CommitmentGetParams(ScheduleProjectParams):
    commitment_id: RuntimeIdentifier


class CommitmentCandidateParams(ScheduleProjectParams):
    candidate_id: RuntimeIdentifier


class CommitmentDue(Params):
    at: str
    timezone: str
    kind: Literal["due", "check"]


class CommitmentAcceptParams(CommitmentCandidateParams):
    command_id: RuntimeIdentifier
    expected_revision: Annotated[StrictInt, Field(ge=1)]
    owner: Annotated[str, Field(min_length=1, max_length=320)]
    outcome: Annotated[str, Field(min_length=1, max_length=8000)]
    due_or_check_at: CommitmentDue | None = None


class CommitmentUpdateParams(CommitmentGetParams):
    command_id: RuntimeIdentifier
    expected_revision: Annotated[StrictInt, Field(ge=1)]
    state: Literal["ready", "waiting", "done", "cancelled", "superseded"]
    evidence_ref_json: Annotated[str, Field(min_length=2, max_length=2048)]
    superseded_by: RuntimeIdentifier | None = None
    due_or_check_at: CommitmentDue | None = None


class CalendarPreviewParams(ScheduleProjectParams):
    availability_ref_json: Annotated[str, Field(min_length=2, max_length=2048)]
    timezone: Annotated[str, Field(min_length=1, max_length=128)]
    participants: Annotated[list[str], Field(min_length=1, max_length=50)]
    start_at: str
    end_at: str
    duration_minutes: Annotated[StrictInt, Field(ge=5, le=480)] = 30


class CorrespondenceGetParams(ScheduleProjectParams):
    correspondence_id: RuntimeIdentifier


class CorrespondenceDraftParams(CorrespondenceGetParams):
    command_id: RuntimeIdentifier
    recipients: Annotated[list[str], Field(min_length=1, max_length=50)]
    content: Annotated[str, Field(min_length=1, max_length=16000)]
    source_refs_json: Annotated[str, Field(min_length=2, max_length=16384)]


class CorrespondenceReceiptParams(CorrespondenceGetParams):
    command_id: RuntimeIdentifier
    effect_id: RuntimeIdentifier


_SPECS = {
    "runtime.schedule.create": (ScheduleCreateParams, "Create a paused immutable owner-bound local schedule"),
    "runtime.schedule.import": (ScheduleImportParams, "Import a declared paused reconciled foreign schedule with deterministic identity"),
    "runtime.schedule.update": (ScheduleUpdateParams, "Pause, resume or revoke an exact schedule revision"),
    "runtime.schedule.grant": (ScheduleGrantParams, "Grant bounded fresh local review authority separate from observation"),
    "runtime.schedule.reconcile": (ScheduleReconcileParams, "Retain manual evidence for an unknown occurrence without replaying it"),
    "runtime.schedule.get": (ScheduleGetParams, "Read owned schedule health, occurrences and retained notification intents"),
    "runtime.schedule.list": (ScheduleProjectParams, "List owned project schedules"),
    "runtime.inbox.prepare": (InboxPrepareParams, "Classify selected immutable imported inbox messages as nonbinding candidates"),
    "runtime.commitment.candidate": (CommitmentCandidateParams, "Read an imported nonbinding commitment candidate"),
    "runtime.commitment.accept": (CommitmentAcceptParams, "Human-accept a sourced obligation"),
    "runtime.commitment.update": (CommitmentUpdateParams, "Record sourced waiting or terminal commitment evidence"),
    "runtime.commitment.get": (CommitmentGetParams, "Read an accepted obligation"),
    "runtime.commitment.list": (ScheduleProjectParams, "Read authoritative accepted obligations"),
    "runtime.commitment.review": (ScheduleProjectParams, "Review currently active accepted and waiting obligations without reopening them"),
    "runtime.correspondence.draft": (CorrespondenceDraftParams, "Prepare an exact sourced draft and flag possible new promises without sending"),
    "runtime.correspondence.receipt": (CorrespondenceReceiptParams, "Associate existing exact confirmed send evidence; never sends a message"),
    "runtime.correspondence.get": (CorrespondenceGetParams, "Read draft versus sent proof and exact correspondence recipients"),
    "runtime.calendar.preview": (CalendarPreviewParams, "Preview supplied availability; no live calendar certification or invitation"),
}
for name, (params, doc) in _SPECS.items():
    method(name, params=params, result=ScheduleRecordResult, doc=doc)
