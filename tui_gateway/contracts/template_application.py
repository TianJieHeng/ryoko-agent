"""Exact canonical template preview and accepted artifact publication."""
from typing import Annotated, Literal
from pydantic import Field
from .base import Params, Result
from .artifacts import (RuntimeProjectParams, PositiveVersion, ArtifactVersionRef,
                        ArtifactProposalResult, ArtifactPublishResult)
from .runtime_v1 import RuntimeIdentifier
from .runtime_results import Digest
from .registry import method


class CanonicalTemplateRef(Params):
    store: Literal["artifact_templates"] = "artifact_templates"
    template_id: RuntimeIdentifier
    version: PositiveVersion
    sha256: Digest


class TemplatePreviewParams(RuntimeProjectParams):
    template_ref: CanonicalTemplateRef
    slot_values: dict[str, Annotated[str, Field(max_length=65536)]]
    locked_sections: Annotated[list[RuntimeIdentifier], Field(max_length=64)] = []


class TemplateLock(Result):
    anchor: str
    sha256: Digest


class TemplatePreviewResult(Result):
    template_ref: CanonicalTemplateRef
    project_id: str
    content: str
    sha256: Digest
    size: int
    mime: Literal["text/markdown"]
    locked_sections: list[TemplateLock]
    advisory_style_keys: list[str]
    applied_style_keys: list[str]
    assets_mode: Literal["lineage_only"]
    baseline_copied: Literal[False]
    assets: list[ArtifactVersionRef]
    exclusions_checked: Literal[True]
    publication_state: Literal["preview_only"]
    preview_mode: Literal["plain_text"]


class TemplatePrepareParams(TemplatePreviewParams):
    command_id: RuntimeIdentifier
    request_id: RuntimeIdentifier


class TemplatePrepareResult(Result):
    proposal: ArtifactProposalResult
    preview: TemplatePreviewResult


class TemplatePublishParams(TemplatePrepareParams):
    approval_id: RuntimeIdentifier
    approval_digest: Digest


method("runtime.template.preview", params=TemplatePreviewParams, result=TemplatePreviewResult)
method("runtime.template.prepare", params=TemplatePrepareParams, result=TemplatePrepareResult)
method("runtime.template.publish", params=TemplatePublishParams, result=ArtifactPublishResult)
