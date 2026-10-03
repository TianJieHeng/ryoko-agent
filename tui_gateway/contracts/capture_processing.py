"""Explicit local text indexing, bounded lexical discovery and reviewed capture batches."""
from typing import Annotated, Literal

from pydantic import Field, StrictInt

from .artifacts import ArtifactVersionRef, RuntimeProjectParams
from .base import Params, Result
from .project_sources import CaptureParams, CaptureRecord
from .registry import method
from .runtime_results import Digest
from .runtime_v1 import RuntimeIdentifier


class CaptureProcessParams(CaptureParams):
    expected_extraction_sequence: Annotated[StrictInt, Field(ge=0, le=32)]
    source: Literal["original", "latest_extraction"] = "original"


class CaptureProcessingState(Result):
    status: Literal["not_indexed", "indexed", "metadata_only", "stale"]
    method: Literal["utf8_identity", "supplied_text", "none"]
    source_ref: ArtifactVersionRef | None
    extraction_sequence: int
    indexed_characters: int
    truncated: bool
    failure_code: str | None
    indexed_at: float | None


class CaptureFilingHistory(Result):
    revision: int
    filed_project_id: str | None
    created_at: float


class CaptureConsolidationHistory(Result):
    revision: int
    consolidated_into: str | None
    created_at: float


class CaptureInspectResult(Result):
    capture: CaptureRecord
    processing: CaptureProcessingState
    consolidated_into: str | None
    filing_history: list[CaptureFilingHistory]
    consolidation_history: list[CaptureConsolidationHistory]


class CaptureSearchParams(RuntimeProjectParams):
    query: Annotated[str, Field(min_length=1, max_length=512)]
    limit: Annotated[StrictInt, Field(ge=1, le=50)] = 20
    scan_limit: Annotated[StrictInt, Field(ge=1, le=500)] = 100


class CaptureSearchMatch(Result):
    capture: CaptureRecord
    processing: CaptureProcessingState
    consolidated_into: str | None
    score: float
    matched_terms: list[str]
    excerpt: str


class CaptureSearchResult(Result):
    matches: list[CaptureSearchMatch]
    search_mode: Literal["lexical_fuzzy"]
    scanned: int
    truncated: bool
    complete: Literal[False]
    limitations: list[str]


class CaptureBatchItem(Params):
    capture_id: RuntimeIdentifier
    expected_revision: Annotated[StrictInt, Field(ge=0, le=128)]
    filed_project_id: RuntimeIdentifier | None
    consolidated_into: RuntimeIdentifier | None


class CaptureBatchParams(RuntimeProjectParams):
    batch_id: RuntimeIdentifier
    items: Annotated[list[CaptureBatchItem], Field(min_length=1, max_length=25)]


class CaptureBatchCommitParams(CaptureBatchParams):
    preview_digest: Digest


class CaptureBatchPreviewItem(Result):
    capture_id: str
    expected_revision: int
    previous_filed_project_id: str | None
    filed_project_id: str | None
    previous_consolidated_into: str | None
    consolidated_into: str | None
    original_sha256: Digest


class CaptureBatchPreviewResult(Result):
    batch_id: str
    project_id: str
    preview_digest: Digest
    items: list[CaptureBatchPreviewItem]
    originals_preserved: Literal[True]
    scope: Literal["capture_metadata_only"]


class CaptureBatchCommittedItem(Result):
    capture_id: str
    revision: int
    filed_project_id: str | None
    consolidated_into: str | None


class CaptureBatchCommitResult(Result):
    batch_id: str
    project_id: str
    preview_digest: Digest
    items: list[CaptureBatchCommittedItem]
    originals_preserved: Literal[True]
    replayed: bool
    scope: Literal["capture_metadata_only"]


method("runtime.capture.process", params=CaptureProcessParams, result=CaptureInspectResult,
       doc="Explicit bounded UTF-8 extraction/indexing of an existing original or supplied extraction; no fetch, OCR, models or embeddings.")
method("runtime.capture.inspect", params=CaptureParams, result=CaptureInspectResult,
       doc="Inspect original, processing status and append-only filing/consolidation histories.")
method("runtime.capture.search", params=CaptureSearchParams, result=CaptureSearchResult,
       doc="Bounded exact-project lexical/fuzzy retrieval with live grants; never semantic or exhaustive.")
method("runtime.capture.batch.preview", params=CaptureBatchParams, result=CaptureBatchPreviewResult,
       doc="Review exact scoped filing/consolidation CAS changes without mutation or original deletion.")
method("runtime.capture.batch.commit", params=CaptureBatchCommitParams, result=CaptureBatchCommitResult,
       doc="Commit the exact reviewed batch in SessionDB with idempotency and live source/destination grants; no cross-store artifact move.")
