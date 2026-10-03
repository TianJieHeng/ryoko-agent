"""Local source-backed research and approval-ready, claim-scoped brief refreshes.

This adapter reads the existing BE07 stores only. Capture URLs are metadata; no
network fetch, session search, scheduler, model loop, or publication is implied.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import time

from agent.source_manifest import (
    MAX_RESEARCH_BYTES, MAX_SOURCES, EvidenceRange, ResearchResult, SourceManifest,
    SourceManifestError, SourceRequest, SourceResolution, SourceScope, bounded_tuple,
    digest, identifier, require,
)
from hermes_cli.artifact_store import ArtifactProposal
from hermes_cli.project_sources import source_authority
from hermes_state_runtime import RuntimeStoreError


def _artifact_source(context, db, request, actor, access):
    row = db.read_artifact_version(request.source_id, request.version, actor, access=access)
    return row, None


def _capture_source(context, db, request, actor, access):
    capture = db.get_capture(request.source_id, actor, access=access)
    require(capture["project_id"] == request.project_id,
            "source_scope_mismatch", "Capture is outside the requested project")
    original = capture["original_ref"]
    require(original["version"] == request.version,
            "source_version_mismatch", "Capture request must name the retained original byte version")
    row = db.read_artifact_version(original["artifact_id"], original["version"], actor, access=access)
    return row, capture["acquired_at"]


_RESOLVERS = {"project_artifact": _artifact_source, "capture_original": _capture_source}


def _check_evidence(db, request, actor, access, data):
    errors = []
    for span in request.evidence_ranges:
        exact = data[span.start:span.end]
        require(span.end <= len(data) and exact == span.quote.encode("utf-8")
                and hashlib.sha256(exact).hexdigest() == span.sha256,
                "evidence_range_mismatch", "Citation range does not match the exact original bytes")
        if span.anchor_id is not None:
            anchor = db.get_evidence_anchor(span.anchor_id, actor, access=access)
            reference = ({"artifact_id": request.source_id, "version": request.version}
                         if request.source_type == "project_artifact" else {"capture_id": request.source_id})
            require(anchor["project_id"] == request.project_id and anchor["source_ref"] == reference
                    and anchor["source_version"] == str(request.version)
                    and anchor["range_ref"] == {"unit": "byte", "start": span.start, "end": span.end},
                    "evidence_anchor_mismatch", "Citation anchor does not identify this exact source range")
            validity = anchor["effective_validity"]
            require(validity in {"current", "stale"},
                    "evidence_" + validity, "Citation anchor is not usable")
            if validity == "stale":
                errors.append("evidence_stale")
    return tuple(dict.fromkeys(errors))


def _covered_bytes(ranges):
    end, covered = 0, 0
    for span in sorted(ranges, key=lambda item: (item.start, item.end)):
        covered += max(0, span.end - max(end, span.start))
        end = max(end, span.end)
    return covered


def _unavailable(request, scope, retrieved, availability, code):
    return SourceResolution(SourceManifest(request.source_id, request.version, scope, retrieved,
        "unknown", request.evidence_ranges, (code,), request.source_type, request.sha256,
        request.authority, availability, fresh_until=request.fresh_until))


def _resolve(context, db, request, actor, access, remaining):
    from agent.result_artifacts import ArtifactConflict, read_project_artifact
    scope = SourceScope(**actor, project_id=request.project_id, policy_digest=context.identity.policy_digest)
    retrieved = time.time()
    try:
        with access.guard(request.project_id, actor, "read"):
            row, acquired = _RESOLVERS[request.source_type](context, db, request, actor, access)
            require(row["project_id"] == request.project_id,
                    "source_scope_mismatch", "Source is outside the requested project")
            descriptor = row["descriptor"]
            require(descriptor["version"] == request.version and descriptor["sha256"] == request.sha256,
                    "source_version_mismatch", "Source descriptor differs from its exact requested version and digest")
            require(descriptor["size"] <= remaining, "source_budget_exceeded", "Research source byte budget exceeded")
            data = read_project_artifact(context, db, request.project_id, descriptor["artifact_id"], request.version)
            evidence_errors = _check_evidence(db, request, actor, access, data)
            head = db.get_artifact_head(descriptor["artifact_id"], actor, access=access)
            head_version = head["version"] if head else None
            stale = (bool(evidence_errors) or row["derived_validity"] != "current"
                     or request.source_type == "project_artifact" and head_version != request.version
                     or request.fresh_until is not None and request.fresh_until <= retrieved)
            freshness = "stale" if stale else ("unspecified" if request.fresh_until is None else "within_declared_window")
            manifest = SourceManifest(request.source_id, request.version, scope, retrieved, freshness,
                request.evidence_ranges, evidence_errors, request.source_type, request.sha256, request.authority,
                "available", descriptor["artifact_id"], len(data), descriptor["mime"], head_version,
                acquired, request.fresh_until, _covered_bytes(request.evidence_ranges))
            return SourceResolution(manifest, data)
    except PermissionError:
        return _unavailable(request, scope, retrieved, "inaccessible", "source_access_denied")
    except FileNotFoundError:
        return _unavailable(request, scope, retrieved, "missing", "source_bytes_missing")
    except SourceManifestError as error:
        status = "inaccessible" if error.code == "source_scope_mismatch" else "invalid"
        return _unavailable(request, scope, retrieved, status, error.code)
    except RuntimeStoreError as error:
        status = "missing" if error.code in {"artifact_not_found", "source_not_found"} else "invalid"
        return _unavailable(request, scope, retrieved, status,
                            "source_not_found" if status == "missing" else "source_catalog_invalid")
    except ArtifactConflict:
        return _unavailable(request, scope, retrieved, "invalid", "source_bytes_invalid")
    except OSError:
        return _unavailable(request, scope, retrieved, "inaccessible", "source_storage_unavailable")


def resolve_sources(context, db, requests):
    """Resolve exact immutable sources and ranges; return full bytes plus safe metadata."""
    bounded_tuple(requests, SourceRequest, MAX_SOURCES, nonempty=True)
    require(len({request.source_id for request in requests}) == len(requests),
            "invalid_source", "Each source identity must be unique")
    actor, access = source_authority(context, db)
    remaining, resolved = MAX_RESEARCH_BYTES, []
    for request in requests:
        source = _resolve(context, db, request, actor, access, remaining)
        remaining -= len(source.content_bytes or b"")
        resolved.append(source)
    return ResearchResult(tuple(resolved))


@dataclass(frozen=True)
class ClaimCitation:
    source_id: str
    range_index: int
    source_version: int | None = None
    source_sha256: str | None = None
    request_digest: str | None = None

    def __post_init__(self):
        identifier(self.source_id)
        require(type(self.range_index) is int and 0 <= self.range_index < 32,
                "invalid_claim", "Claim citations need a bounded evidence range index")
        require((self.source_version is None) == (self.source_sha256 is None),
                "invalid_claim", "A claim evidence pin requires both version and digest")
        if self.source_version is not None:
            require(type(self.source_version) is int and 0 < self.source_version < 2**31,
                    "invalid_claim", "Claim source version must be bounded")
            digest(self.source_sha256)
        if self.request_digest is not None:
            require(self.source_version is not None, "invalid_claim", "Evidence manifest pins need an exact source version")
            digest(self.request_digest)


    @classmethod
    def from_record(cls, record):
        require(isinstance(record, dict) and {"source_id", "range_index"} <= set(record)
                and set(record) <= set(cls.__dataclass_fields__), "invalid_claim", "Invalid claim citation fields")
        return cls(**record)


@dataclass(frozen=True)
class ClaimDependency:
    claim_id: str
    section: str
    text: str
    kind: str
    section_sha256: str
    citations: tuple[ClaimCitation, ...]

    def __post_init__(self):
        identifier(self.claim_id)
        identifier(self.section)
        _claim_text(self.text)
        require(isinstance(self.kind, str) and self.kind in {"fact", "interpretation"}, "invalid_claim", "Facts and interpretations must be distinguished")
        digest(self.section_sha256)
        bounded_tuple(self.citations, ClaimCitation, 32, nonempty=True)
        require(len(set(self.citations)) == len(self.citations), "invalid_claim", "Duplicate claim citations are not allowed")


    @classmethod
    def from_record(cls, record):
        require(isinstance(record, dict) and set(record) == set(cls.__dataclass_fields__),
                "invalid_claim", "Invalid claim dependency fields")
        data = dict(record)
        citations = data["citations"]
        require(isinstance(citations, (tuple, list)) and 0 < len(citations) <= 32,
                "invalid_claim", "Claim citations exceed their bound")
        data["citations"] = tuple(ClaimCitation.from_record(item) for item in citations)
        return cls(**data)


def _claim_text(value):
    require(isinstance(value, str) and 0 < len(value.encode("utf-8")) <= 4096
            and not any(char in value for char in ("\n", "\r", "\0")),
            "invalid_claim", "Claims must be bounded exact single-line text spans")


@dataclass(frozen=True)
class ClaimUpdate:
    claim_id: str
    replacement: str

    def __post_init__(self):
        identifier(self.claim_id)
        _claim_text(self.replacement)


    @classmethod
    def from_record(cls, record):
        require(isinstance(record, dict) and set(record) == set(cls.__dataclass_fields__),
                "invalid_claim", "Invalid claim update fields")
        return cls(**record)


@dataclass(frozen=True)
class BriefRefresh:
    proposal: ArtifactProposal
    research: ResearchResult
    factual_changes: tuple[str, ...]
    interpretation_changes: tuple[str, ...]
    affected_claims: tuple[str, ...]
    unchanged_claims: tuple[str, ...]
    dependencies: tuple[ClaimDependency, ...]
    dependency_sources: tuple[SourceManifest, ...]

    def to_record(self):
        return {"artifact_proposal": self.proposal.public_record(),
                "source_manifest": self.research.to_record(),
                "factual_changes": list(self.factual_changes),
                "interpretation_changes": list(self.interpretation_changes),
                "affected_claims": list(self.affected_claims),
                "unchanged_claims": list(self.unchanged_claims),
                "claim_dependencies": [asdict(claim) for claim in self.dependencies],
                "dependency_sources": [source.to_record() for source in self.dependency_sources],
                "pending_claims": [claim for claim in self.affected_claims if claim in self.unchanged_claims],
                "claim_verification": "not_performed", "publication_status": "awaiting_approval"}


def _citation_bytes(manifests, citation):
    require(citation.source_id in manifests, "invalid_claim", "Claim refers to a source outside the bounded manifest")
    manifest = manifests[citation.source_id]
    require(citation.range_index < len(manifest.evidence_ranges), "invalid_claim", "Claim citation range is missing")
    span = manifest.evidence_ranges[citation.range_index]
    return manifest, span


def _historical_manifest(previous, citation):
    candidates = [source for source in previous if source.source_id == citation.source_id
                  and (citation.source_version is None or (source.version, source.sha256) ==
                       (citation.source_version, citation.source_sha256))
                  and (citation.request_digest is None or source.request_digest == citation.request_digest)]
    require(len(candidates) == 1, "invalid_claim", "Claim must select one exact historical source version")
    return candidates[0]


def _claim_changed(claim, previous, current):
    changed = False
    for citation in claim.citations:
        selected = _historical_manifest(previous, citation)
        old, old_span = _citation_bytes({citation.source_id: selected}, citation)
        new, new_span = _citation_bytes(current, citation)
        changed |= (old_span.quote, old_span.sha256, old.authority) != (
            new_span.quote, new_span.sha256, new.authority)
    return changed


def _target_edits(content, claims, updates, current, previous):
    from hermes_cli.artifact_store import _section, _sections, _sha
    sections, grouped, refreshed = _sections(content), {}, []
    for update in updates:
        require(update.claim_id in claims, "invalid_claim", "Update names an unknown claim")
        claim = claims[update.claim_id]
        original = _section(sections, claim.section)[2]
        require(_sha(original) == claim.section_sha256,
                "brief_section_changed", "User-edited target section requires review before refresh")
        require(original.count(claim.text) == 1 and claim.text != update.replacement,
                "invalid_claim", "A changed claim must identify one exact text span")
        for citation in claim.citations:
            manifest, _span = _citation_bytes(current, citation)
            require(manifest.availability == "available" and manifest.freshness != "stale",
                    "brief_source_unavailable", "Updated claims need available current source evidence")
        start = original.index(claim.text)
        grouped.setdefault(claim.section, []).append((start, start + len(claim.text), update.replacement))
    edits = []
    for section, replacements in grouped.items():
        original = _section(sections, section)[2]
        ordered = sorted(replacements)
        require(not any(left[1] > right[0] for left, right in zip(ordered, ordered[1:])),
                "invalid_claim", "Overlapping claim replacements require review")
        replacement = original
        for start, end, new in reversed(ordered):
            replacement = replacement[:start] + new + replacement[end:]
        # A claim edit cannot change Markdown section identity or boundaries.
        require(list(_sections(replacement)) == list(_sections(original)),
                "invalid_claim", "Claim updates cannot change section structure")
        target_claims = [claim for claim in claims.values() if claim.section == section]
        spans = []
        for claim in target_claims:
            require(original.count(claim.text) == 1, "invalid_claim", "Tracked claims must remain exact in an edited section")
            position = original.index(claim.text)
            spans.append((position, position + len(claim.text)))
            expected = next((update.replacement for update in updates if update.claim_id == claim.claim_id), claim.text)
            require(replacement.count(expected) == 1, "invalid_claim", "An edit would change or duplicate another tracked claim")
        spans.sort()
        require(not any(left[1] > right[0] for left, right in zip(spans, spans[1:])),
                "invalid_claim", "Overlapping tracked claims require review")
        edits.append({"anchor": section, "expected_sha256": _sha(original), "replacement": replacement})
    updated_text = {update.claim_id: update.replacement for update in updates}
    updated_hash = {edit["anchor"]: _sha(edit["replacement"]) for edit in edits}
    dependency_sources = {}
    for claim in claims.values():
        citations = []
        for citation in claim.citations:
            source = current[citation.source_id] if claim.claim_id in updated_text else _historical_manifest(previous, citation)
            key = source.request_digest
            dependency_sources[key] = source
            citations.append(ClaimCitation(citation.source_id, citation.range_index, source.version, source.sha256, source.request_digest))
        refreshed.append(ClaimDependency(claim.claim_id, claim.section, updated_text.get(claim.claim_id, claim.text),
            claim.kind, updated_hash.get(claim.section, claim.section_sha256), tuple(citations)))
    require(len(dependency_sources) <= MAX_SOURCES * 2, "invalid_claim", "Retained claim dependencies exceed the manual refresh bound")
    return edits, tuple(refreshed), tuple(dependency_sources.values())


def prepare_living_brief_refresh(run, *, project_id, artifact_id, parent_version, request_id,
                                 previous_sources, requests, claims, updates):
    """Prepare exact changed-claim edits; unchanged prose and locked sections survive.

    The caller supplies proposed factual/interpretive wording. We verify citation
    membership and dependency changes, never claim semantic truth from a quote.
    Publication retains the existing independent exact-approval requirement.
    """
    from agent.result_artifacts import read_project_artifact
    from hermes_cli.artifact_store import prepare_markdown_edit
    bounded_tuple(previous_sources, SourceManifest, MAX_SOURCES * 2, nonempty=True)
    bounded_tuple(claims, ClaimDependency, 100, nonempty=True)
    bounded_tuple(updates, ClaimUpdate, 100, nonempty=True)
    require(len({item.request_digest for item in previous_sources}) == len(previous_sources)
            and len({claim.claim_id for claim in claims}) == len(claims)
            and len({update.claim_id for update in updates}) == len(updates),
            "invalid_claim", "Source, claim, and update identities must be unique")
    require(len(json.dumps([source.to_record() for source in previous_sources], sort_keys=True,
                           separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()) <= 1024 * 1024,
            "source_manifest_limit", "Retained brief manifests exceed their bounded metadata budget")
    actor, _access = source_authority(run.context, run.db)
    for source in previous_sources:
        require(all(getattr(source.scope, key) == value for key, value in actor.items())
                and source.scope.policy_digest == run.context.identity.policy_digest,
                "source_scope_mismatch", "Prior manifest must belong to the same actor and policy")
    # Historical manifests are inputs, not trusted proof. Reopen their exact
    # original bytes before deciding that any dependent claim changed.
    historical_bytes = 0
    for source in previous_sources:
        historical = resolve_sources(run.context, run.db, (source.request(),))
        require(historical.sources[0].manifest.availability == "available",
                "brief_prior_source_unavailable", "Prior source evidence must remain readable and exact")
        historical_bytes += len(historical.sources[0].content_bytes)
        require(historical_bytes <= MAX_RESEARCH_BYTES * 2,
                "source_budget_exceeded", "Retained historical source bytes exceed the refresh budget")
    research = resolve_sources(run.context, run.db, requests)
    previous = previous_sources
    current = {source.manifest.source_id: source.manifest for source in research.sources}
    require({source.source_id for source in previous} == set(current), "invalid_claim", "Refresh requires the exact bounded source identity set")
    for old in previous:
        new = current[old.source_id]
        require((old.source_type, old.scope.project_id) == (new.source_type, new.scope.project_id),
                "source_scope_mismatch", "Refresh cannot substitute a different source type or project")
    affected = tuple(claim.claim_id for claim in claims if _claim_changed(claim, previous, current))
    require(all(update.claim_id in affected for update in updates),
            "unaffected_claim", "Only claims whose cited evidence changed may be refreshed")
    content = read_project_artifact(run.context, run.db, project_id, artifact_id, parent_version).decode("utf-8")
    by_id = {claim.claim_id: claim for claim in claims}
    edits, dependencies, dependency_sources = _target_edits(content, by_id, updates, current, previous)
    proposal = prepare_markdown_edit(run, project_id=project_id, artifact_id=artifact_id,
        parent_version=parent_version, expected_head_version=parent_version,
        request_id=request_id, edits=edits)
    changed_ids = {update.claim_id for update in updates}
    return BriefRefresh(proposal, research,
        tuple(update.claim_id for update in updates if by_id[update.claim_id].kind == "fact"),
        tuple(update.claim_id for update in updates if by_id[update.claim_id].kind == "interpretation"),
        affected, tuple(claim.claim_id for claim in claims if claim.claim_id not in changed_ids), dependencies, dependency_sources)
