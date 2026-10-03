"""Bounded deterministic domain production under explicit artifact controls."""
from typing import Annotated, Literal
from pydantic import Field
from .base import Params, Result
from .registry import method
from .runtime_v1 import RuntimeIdentifier, RuntimeSessionParams
from .runtime_results import Digest
from .artifacts import ArtifactProposalResult, ArtifactBundleResult


class DomainPrepareParams(RuntimeSessionParams):
    command_id: RuntimeIdentifier
    job_json: Annotated[str, Field(min_length=2, max_length=65536)]


class DomainApproval(Params):
    approval_id: RuntimeIdentifier
    approval_digest: Digest


class DomainPublishParams(DomainPrepareParams):
    approvals: Annotated[list[DomainApproval], Field(min_length=2, max_length=17)]


class DomainPrepareResult(Result):
    proposals: list[ArtifactProposalResult]
    publication_atomic: Literal[False]


class DomainPublishResult(ArtifactBundleResult):
    pass


method("runtime.domain.prepare", params=DomainPrepareParams, result=DomainPrepareResult,
       doc="Transform exact authorized sources with a bounded local adapter and prepare complete outputs; no publication.")
method("runtime.domain.publish", params=DomainPublishParams, result=DomainPublishResult,
       doc="Approve each exact prepared output and publish its manifest last; bundle publication is not atomic.")
