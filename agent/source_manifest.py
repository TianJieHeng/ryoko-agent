"""Bounded research records for exact local sources, not execution authority.

A verified byte range establishes citation membership only. It neither validates
an arbitrary claim nor promotes a caller-declared source class to approved truth.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
import math
import re

MAX_SOURCES = 32
MAX_RANGES = 32
MAX_SOURCE_BYTES = 8 * 1024 * 1024
MAX_RESEARCH_BYTES = 16 * 1024 * 1024
MAX_MANIFEST_BYTES = 512 * 1024
MAX_EVIDENCE_BYTES = 64 * 1024
SOURCE_TYPES = frozenset({"project_artifact", "capture_original"})
AUTHORITIES = frozenset({"authoritative_spec", "casual_note", "source_claim", "user_statement"})
_HEX = re.compile(r"[0-9a-f]{64}\Z")


class SourceManifestError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def require(condition, code, message):
    if not condition:
        raise SourceManifestError(code, message)


def identifier(value):
    require(isinstance(value, str) and 0 < len(value) <= 256 and value == value.strip()
            and "*" not in value and not any(ord(char) < 32 for char in value),
            "invalid_source", "Source references must be exact bounded identifiers")
    return value


def digest(value):
    require(isinstance(value, str) and _HEX.fullmatch(value) is not None,
            "invalid_source", "Source digests must be lowercase SHA-256")
    return value


def timestamp(value):
    require(type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 253402300799,
            "invalid_source", "Source times must be finite UTC timestamps")
    return value


def bounded_tuple(value, kind, maximum, *, nonempty=False):
    require(isinstance(value, tuple) and (bool(value) or not nonempty) and len(value) <= maximum
            and all(isinstance(item, kind) for item in value),
            "invalid_source", "Research records require bounded typed tuples")


@dataclass(frozen=True)
class EvidenceRange:
    """UTF-8 original-byte offsets, end exclusive; quote and digest must both match."""
    start: int
    end: int
    sha256: str
    quote: str
    anchor_id: str | None = None

    def __post_init__(self):
        require(type(self.start) is int and type(self.end) is int
                and 0 <= self.start < self.end <= MAX_SOURCE_BYTES,
                "invalid_source", "Evidence requires a nonempty bounded original-byte range")
        digest(self.sha256)
        require(isinstance(self.quote, str) and 0 < len(self.quote.encode("utf-8")) <= 8192
                and "\0" not in self.quote, "invalid_source", "Evidence quotes must be bounded UTF-8 text")
        if self.anchor_id is not None:
            identifier(self.anchor_id)

    @classmethod
    def from_record(cls, record):
        require(isinstance(record, dict) and set(record) <= {"start", "end", "sha256", "quote", "anchor_id"}
                and {"start", "end", "sha256", "quote"} <= set(record),
                "invalid_source", "Invalid evidence range fields")
        return cls(**record)


@dataclass(frozen=True)
class SourceRequest:
    source_id: str
    source_type: str
    project_id: str
    version: int
    sha256: str
    authority: str = "source_claim"
    evidence_ranges: tuple[EvidenceRange, ...] = ()
    fresh_until: float | None = None

    def __post_init__(self):
        identifier(self.source_id)
        identifier(self.project_id)
        require(isinstance(self.source_type, str) and self.source_type in SOURCE_TYPES, "unsupported_source", "Only retained local artifact and capture originals are supported")
        require(type(self.version) is int and 0 < self.version < 2**31,
                "invalid_source", "An exact immutable source byte version is required")
        digest(self.sha256)
        require(isinstance(self.authority, str) and self.authority in AUTHORITIES, "invalid_source", "Unknown declared authority class")
        bounded_tuple(self.evidence_ranges, EvidenceRange, MAX_RANGES)
        require(sum(len(span.quote.encode("utf-8")) for span in self.evidence_ranges) <= MAX_EVIDENCE_BYTES,
                "invalid_source", "Evidence quotations exceed the source manifest bound")
        require(len(set(self.evidence_ranges)) == len(self.evidence_ranges), "invalid_source", "Duplicate evidence ranges are not allowed")
        if self.fresh_until is not None:
            timestamp(self.fresh_until)

    def to_record(self):
        return asdict(self)

    @classmethod
    def from_record(cls, record):
        require(isinstance(record, dict) and set(record) <= {
            "source_id", "source_type", "project_id", "version", "sha256", "authority", "evidence_ranges", "fresh_until"}
            and {"source_id", "source_type", "project_id", "version", "sha256"} <= set(record),
            "invalid_source", "Invalid source request fields")
        data = dict(record)
        ranges = data.get("evidence_ranges", [])
        require(isinstance(ranges, (list, tuple)) and len(ranges) <= MAX_RANGES,
                "invalid_source", "Evidence range count exceeds its bound")
        data["evidence_ranges"] = tuple(EvidenceRange.from_record(item) for item in ranges)
        return cls(**data)


@dataclass(frozen=True)
class SourceScope:
    principal_id: str
    profile_id: str
    agent_id: str
    project_id: str
    policy_digest: str

    def __post_init__(self):
        for value in (self.principal_id, self.profile_id, self.agent_id, self.project_id):
            identifier(value)
        digest(self.policy_digest)


@dataclass(frozen=True)
class SourceManifest:
    source_id: str
    version: int
    scope: SourceScope
    retrieved_at: float
    freshness: str
    evidence_ranges: tuple[EvidenceRange, ...]
    errors: tuple[str, ...]
    source_type: str
    sha256: str
    authority: str
    availability: str
    artifact_id: str | None = None
    size: int | None = None
    mime: str | None = None
    head_version: int | None = None
    acquired_at: float | None = None
    fresh_until: float | None = None
    covered_bytes: int = 0

    def __post_init__(self):
        require(isinstance(self.scope, SourceScope), "invalid_source", "Source scope must be typed")
        SourceRequest(self.source_id, self.source_type, self.scope.project_id, self.version,
                      self.sha256, self.authority, self.evidence_ranges, self.fresh_until)
        timestamp(self.retrieved_at)
        require(isinstance(self.freshness, str) and isinstance(self.availability, str) and self.freshness in {"within_declared_window", "unspecified", "stale", "unknown"}
                and self.availability in {"available", "missing", "inaccessible", "invalid"},
                "invalid_source", "Unknown source resolution status")
        bounded_tuple(self.errors, str, MAX_RANGES + 4)
        for error in self.errors:
            identifier(error)
        if self.artifact_id is not None:
            identifier(self.artifact_id)
        for value in (self.size, self.covered_bytes):
            require(value is None or type(value) is int and 0 <= value <= MAX_SOURCE_BYTES,
                    "invalid_source", "Source coverage exceeds its byte bound")
        require(self.size is None or self.covered_bytes <= self.size,
                "invalid_source", "Evidence coverage exceeds the source size")
        if self.mime is not None:
            identifier(self.mime)
        if self.head_version is not None:
            require(type(self.head_version) is int and 0 < self.head_version < 2**31,
                    "invalid_source", "Head version must be a bounded integer")
        if self.acquired_at is not None:
            timestamp(self.acquired_at)
        require(self.availability != "available" or (self.artifact_id is not None and self.size is not None
                and self.mime is not None and self.freshness != "unknown"),
                "invalid_source", "Available sources require complete byte metadata")

    @property
    def request_digest(self):
        return hashlib.sha256(json.dumps(self.request().to_record(), sort_keys=True, separators=(",", ":"),
                                         ensure_ascii=True, allow_nan=False).encode()).hexdigest()

    def to_record(self):
        return {**asdict(self), "request_digest": self.request_digest, "authority_basis": "caller_declared", "execution_authority": False,
                "citation_validation": "exact_original_bytes" if self.availability == "available" else "not_validated",
                "claim_verification": "not_performed"}

    @classmethod
    def from_record(cls, record):
        require(isinstance(record, dict), "invalid_source", "Source manifest must be an object")
        data = dict(record)
        request_digest = data.pop("request_digest", None)
        expected = {"authority_basis": "caller_declared", "execution_authority": False,
                    "claim_verification": "not_performed"}
        for key, value in expected.items():
            require(data.pop(key, value) == value, "invalid_source", "Source labels cannot assert execution or claim authority")
        validation = data.pop("citation_validation", "not_validated")
        require(validation == ("exact_original_bytes" if data.get("availability") == "available" else "not_validated"),
                "invalid_source", "Citation validation must match source availability")
        require(set(data) == set(cls.__dataclass_fields__), "invalid_source", "Invalid source manifest fields")
        require(isinstance(data["scope"], dict) and set(data["scope"]) == set(SourceScope.__dataclass_fields__),
                "invalid_source", "Invalid source scope fields")
        data["scope"] = SourceScope(**data["scope"])
        require(isinstance(data["evidence_ranges"], (tuple, list)) and len(data["evidence_ranges"]) <= MAX_RANGES,
                "invalid_source", "Evidence range count exceeds its bound")
        data["evidence_ranges"] = tuple(EvidenceRange.from_record(item) for item in data["evidence_ranges"])
        require(isinstance(data["errors"], (tuple, list)) and len(data["errors"]) <= MAX_RANGES + 4,
                "invalid_source", "Source error count exceeds its bound")
        data["errors"] = tuple(data["errors"])
        result = cls(**data)
        require(request_digest is None or request_digest == result.request_digest,
                "invalid_source", "Source request digest differs from its exact evidence manifest")
        return result

    def request(self):
        return SourceRequest(self.source_id, self.source_type, self.scope.project_id, self.version,
                             self.sha256, self.authority, self.evidence_ranges, self.fresh_until)


@dataclass(frozen=True)
class SourceResolution:
    manifest: SourceManifest
    content_bytes: bytes | None = field(default=None, repr=False)

    def __post_init__(self):
        require(isinstance(self.manifest, SourceManifest), "invalid_source", "A typed source manifest is required")
        if self.content_bytes is not None:
            require(isinstance(self.content_bytes, bytes) and len(self.content_bytes) <= MAX_SOURCE_BYTES
                    and self.manifest.availability == "available"
                    and len(self.content_bytes) == self.manifest.size
                    and hashlib.sha256(self.content_bytes).hexdigest() == self.manifest.sha256,
                    "invalid_source", "Resolution bytes must match the complete manifest")
            for span in self.manifest.evidence_ranges:
                exact = self.content_bytes[span.start:span.end]
                require(span.end <= len(self.content_bytes) and exact == span.quote.encode("utf-8")
                        and hashlib.sha256(exact).hexdigest() == span.sha256,
                        "evidence_range_mismatch", "Resolution citations must match exact original bytes")
        else:
            require(self.manifest.availability != "available", "invalid_source", "Available sources require complete bytes")


@dataclass(frozen=True)
class ResearchResult:
    sources: tuple[SourceResolution, ...]

    def __post_init__(self):
        bounded_tuple(self.sources, SourceResolution, MAX_SOURCES, nonempty=True)
        require(sum(len(source.content_bytes or b"") for source in self.sources) <= MAX_RESEARCH_BYTES,
                "source_budget_exceeded", "Complete research bytes exceed the batch bound")
        require(len({source.manifest.source_id for source in self.sources}) == len(self.sources),
                "invalid_source", "Source identities must be unique within a manifest")
        self.to_bytes()

    def to_record(self):
        manifests = [source.manifest for source in self.sources]
        groups = {}
        for source in manifests:
            if source.availability == "available":
                groups.setdefault((source.sha256, source.authority), []).append(source.source_id)
        coverage = {status: sum(source.availability == status for source in manifests)
                    for status in ("available", "missing", "inaccessible", "invalid")}
        coverage.update(requested=len(manifests), stale=sum(source.freshness == "stale" for source in manifests),
                        cited=sum(bool(source.evidence_ranges) and source.availability == "available" for source in manifests))
        return {"sources": [source.to_record() for source in manifests], "coverage": coverage,
                "complete": coverage["available"] == len(manifests), "search_complete": False,
                "validator_manifest": {"status": "passed" if coverage["available"] == len(manifests) and not coverage["stale"] else "partial",
                    "checks": ["live_project_grants", "immutable_version_sha256", "complete_original_bytes", "exact_citation_ranges"],
                    "claim_verification": "not_performed", "external_connected_sources": 0,
                    "live_two_source_acceptance": "pending_P08"},
                "duplicate_groups": [{"sha256": key[0], "authority": key[1], "source_ids": ids}
                                     for key, ids in groups.items() if len(ids) > 1],
                "consolidation_performed": False}

    def to_bytes(self):
        data = (json.dumps(self.to_record(), sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                           allow_nan=False) + "\n").encode("utf-8")
        require(len(data) <= MAX_MANIFEST_BYTES, "source_manifest_limit", "Research manifest exceeds its complete export bound")
        return data
