"""Owned local research and exact, independently approved living-brief bundles."""
from typing import Annotated

from pydantic import Field

from .artifacts import PositiveVersion, RuntimeProjectParams
from .base import Params, Result
from .registry import method
from .runtime_results import Digest
from .runtime_v1 import RuntimeIdentifier, RuntimeSessionParams

ResearchRequestJson = Annotated[str, Field(min_length=2, max_length=2 * 1024 * 1024)]


class ResearchResolveParams(RuntimeSessionParams):
    request_json: ResearchRequestJson


class ResearchResponse(Result):
    project_id: RuntimeIdentifier | None = None
    response_json: Annotated[str, Field(min_length=2, max_length=3 * 1024 * 1024)]


class BriefManifestRef(Params):
    artifact_id: RuntimeIdentifier
    version: PositiveVersion
    sha256: Digest


class BriefPrepareParams(RuntimeProjectParams):
    command_id: RuntimeIdentifier
    request_id: RuntimeIdentifier
    artifact_id: RuntimeIdentifier
    parent_version: PositiveVersion
    request_json: ResearchRequestJson
    manifest_ref: BriefManifestRef | None = None


class BriefPublishParams(BriefPrepareParams):
    brief_approval_id: RuntimeIdentifier
    brief_approval_digest: Digest
    manifest_approval_id: RuntimeIdentifier
    manifest_approval_digest: Digest


method("runtime.research.resolve", params=ResearchResolveParams, result=ResearchResponse,
       doc="Read exact granted local source originals and return bounded provenance, citation and freshness metadata.")
method("runtime.brief.prepare", params=BriefPrepareParams, result=ResearchResponse,
       doc="Prepare exact changed-claim edits and an immutable JSON dependency sidecar, each requiring approval.")
method("runtime.brief.publish", params=BriefPublishParams, result=ResearchResponse,
       doc="Revalidate source grants and evidence, publish the exact approved brief first and its dependency manifest last; not atomic.")
