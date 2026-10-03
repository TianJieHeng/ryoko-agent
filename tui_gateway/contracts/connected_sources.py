"""Exact read-only connected-source refresh; publication is a separate approval."""
from typing import Annotated, Literal

from pydantic import Field, StrictInt

from .artifacts import ArtifactProposalResult, ArtifactReadResult, RuntimeProjectParams
from .base import Params, Result
from .registry import method
from .runtime_results import Digest
from .runtime_v1 import RuntimeIdentifier


class GmailThreadSelection(Params):
    kind: Literal["gmail_thread"]
    account_id: RuntimeIdentifier
    mailbox: Annotated[str, Field(min_length=3, max_length=320)]
    thread_id: RuntimeIdentifier


class CalendarAvailabilitySelection(Params):
    kind: Literal["calendar_availability"]
    account_id: RuntimeIdentifier
    calendar_ids: Annotated[list[RuntimeIdentifier], Field(min_length=1, max_length=20)]
    timezone: Annotated[str, Field(min_length=1, max_length=128)]
    start_at: Annotated[str, Field(min_length=1, max_length=64)]
    end_at: Annotated[str, Field(min_length=1, max_length=64)]


class ConnectedSourcePrepareParams(RuntimeProjectParams):
    command_id: RuntimeIdentifier
    request_id: RuntimeIdentifier
    selection: GmailThreadSelection | CalendarAvailabilitySelection


class ConnectedSourcePublishParams(RuntimeProjectParams):
    command_id: RuntimeIdentifier
    preparation_id: RuntimeIdentifier
    original_approval_id: RuntimeIdentifier
    original_approval_digest: Digest
    projection_approval_id: RuntimeIdentifier | None = None
    projection_approval_digest: Digest | None = None


class ConnectedSourcePreviewParams(RuntimeProjectParams):
    command_id: RuntimeIdentifier
    preparation_id: RuntimeIdentifier
    part: Literal["original", "projection"]
    offset: Annotated[StrictInt, Field(ge=0)] = 0
    limit: Annotated[StrictInt, Field(ge=1, le=65536)] = 65536


class ConnectedSourcePreviewResult(ArtifactReadResult):
    preparation_id: RuntimeIdentifier
    part: Literal["original", "projection"]
    approval_id: RuntimeIdentifier
    approval_digest: Digest


class ConnectedSourceResult(Result):
    project_id: RuntimeIdentifier
    state: Literal["awaiting_approval", "published", "partial", "unavailable"]
    preparation_id: RuntimeIdentifier | None = None
    source_kind: Literal["gmail_thread", "calendar_availability"]
    account_id: RuntimeIdentifier
    observed_at: float | None = None
    fresh_until: float | None = None
    coverage: Literal["complete", "partial", "unavailable"]
    errors: list[RuntimeIdentifier]
    original: ArtifactProposalResult | None = None
    projection: ArtifactProposalResult | None = None
    # Exact artifact refs, provenance and domain-specific coverage are JSON data,
    # never a tool selector or execution authority.
    record_json: Annotated[str, Field(min_length=2, max_length=65536)]


method("runtime.sources.prepare", params=ConnectedSourcePrepareParams, result=ConnectedSourceResult,
       doc="Read one exact selected Gmail thread or bounded calendar availability through an account-pinned connector; prepare immutable original and separate domain projection.")
method("runtime.sources.publish", params=ConnectedSourcePublishParams, result=ConnectedSourceResult,
       doc="Publish the exact previously fetched source bytes with both artifact approvals; never refetch or execute source content.")
method("runtime.sources.preview", params=ConnectedSourcePreviewParams, result=ConnectedSourcePreviewResult,
       doc="Read bounded inert chunks of exact prepared original or projection bytes under the original live owner and approval binding; never refetch or publish.")
