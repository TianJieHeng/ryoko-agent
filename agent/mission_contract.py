"""Finite BE09 mission contracts. Data is intent/evidence, never capability authority."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
import math
import re

from hermes_state_runtime import RuntimeStoreError

MISSION_STATES = frozenset({"ready", "working", "waiting_for_user", "waiting_for_source", "ready_to_review",
    "completed", "partially_completed", "paused", "cancelled", "failed"})
CRITERION_KINDS = frozenset({"existence", "markdown_sections", "text_exact", "json_schema",
    "linked_consistency", "test_execution", "user_acceptance"})
MAX_MISSION_ITEMS = 100
MAX_MISSION_BYTES = 131072


class MissionContractError(RuntimeStoreError):
    pass


def require(condition, message, code="invalid_mission"):
    if not condition:
        raise MissionContractError(code, message)


def bounded_json(value, maximum=MAX_MISSION_BYTES):
    def walk(item, depth=0):
        require(depth <= 16, "Mission JSON exceeds depth bound")
        if isinstance(item, dict):
            require(all(isinstance(key, str) for key in item), "Object keys must be strings")
            for child in item.values():
                walk(child, depth + 1)
        elif isinstance(item, list):
            require(len(item) <= 1000, "Nested list exceeds bound")
            for child in item:
                walk(child, depth + 1)
        else:
            require(item is None or type(item) in (str, int, float, bool), "Expected finite JSON")
    try:
        walk(value)
        result = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    except (ValueError, TypeError, RecursionError) as exc:
        raise MissionContractError("invalid_mission", "Expected finite JSON") from exc
    require(len(result.encode()) <= maximum, "Mission metadata exceeds bound", "mission_capacity")
    return result


def text(value, maximum=4096, *, optional=False):
    if optional and value is None:
        return None
    require(isinstance(value, str) and len(value.encode()) <= maximum, "Text exceeds bound")
    return value


def identifier(value):
    text(value, 256)
    require(bool(value) and value.strip() == value and not any(ord(c) < 32 for c in value), "Invalid identifier")
    return value


def digest(value):
    require(isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None, "Expected SHA-256 digest")
    return value


def timestamp(value, optional=True):
    if value is None and optional:
        return None
    require(type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 253402300799,
            "Expected finite UTC timestamp")
    return float(value)


def items(value, maximum=MAX_MISSION_ITEMS):
    require(isinstance(value, list) and len(value) <= maximum, "Mission list exceeds bound", "mission_capacity")
    return value


def artifact_ref(value):
    require(isinstance(value, dict) and set(value) == {"artifact_id", "version", "digest"}, "Expected exact version and digest")
    identifier(value["artifact_id"])
    require(type(value["version"]) is int and 0 < value["version"] < 2**31, "Expected positive artifact version")
    digest(value["digest"])
    return dict(value)


@dataclass(frozen=True)
class MissionCriterion:
    criterion_id: str
    kind: str
    description: str = ""
    artifact_refs: list = field(default_factory=list)
    parameters: dict = field(default_factory=dict)
    required: bool = True

    @classmethod
    def from_dict(cls, value):
        require(isinstance(value, dict) and set(value) <= set(cls.__dataclass_fields__), "Unknown criterion fields")
        require({"criterion_id", "kind"} <= set(value), "Criterion ID and kind required")
        result = cls(**value)
        identifier(result.criterion_id)
        require(result.kind in CRITERION_KINDS and type(result.required) is bool, "Invalid criterion kind/required")
        text(result.description)
        for ref in items(result.artifact_refs, 16):
            artifact_ref(ref)
        require(isinstance(result.parameters, dict), "Criterion parameters must be an object")
        bounded_json(result.parameters, 16384)
        return result

    def to_dict(self):
        return asdict(self)


def criterion_digest(value):
    row = value.to_dict() if isinstance(value, MissionCriterion) else MissionCriterion.from_dict(value).to_dict()
    return hashlib.sha256(bounded_json(row).encode()).hexdigest()


@dataclass(frozen=True)
class VerificationReceipt:
    criterion_id: str
    criterion_digest: str
    artifact_refs: list
    verifier: str
    evidence_ref: str
    result: str
    observed_at: float
    details: dict = field(default_factory=dict)
    receipt_id: str | None = None

    @classmethod
    def from_dict(cls, value):
        require(isinstance(value, dict) and set(value) <= set(cls.__dataclass_fields__), "Unknown receipt fields")
        require({"criterion_id", "criterion_digest", "artifact_refs", "verifier", "evidence_ref", "result", "observed_at"} <= set(value),
                "Receipt missing exact evidence bindings")
        row = cls(**value)
        identifier(row.criterion_id)
        digest(row.criterion_digest)
        identifier(row.verifier)
        text(row.evidence_ref, 1024)
        require(bool(row.evidence_ref), "Evidence reference required")
        require(row.result in {"pass", "fail", "blocked", "unsupported"}, "Invalid verification result")
        timestamp(row.observed_at, optional=False)
        for ref in items(row.artifact_refs, 16):
            artifact_ref(ref)
        require(isinstance(row.details, dict) and set(row.details) <= {"reason_codes", "inputs_digest", "dependencies", "checks"},
                "Only typed verification metadata is retained")
        for key in ("reason_codes", "checks"):
            for entry in items(row.details.get(key, []), 100):
                text(entry, 512)
        if "inputs_digest" in row.details:
            digest(row.details["inputs_digest"])
        for dep in items(row.details.get("dependencies", []), 64):
            require(isinstance(dep, dict) and set(dep) <= {"kind", "reference", "version", "digest", "status"}
                    and dep.get("kind") in {"artifact", "evidence"}, "Invalid verification dependency")
            identifier(dep.get("reference"))
            if "digest" in dep:
                digest(dep["digest"])
        bounded_json(row.details, 16384)
        if row.receipt_id is not None:
            identifier(row.receipt_id)
        return row

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class MissionContract:
    outcome: str
    project_id: str | None = None
    deliverables: list = field(default_factory=list)
    acceptance: list = field(default_factory=list)
    scope_ref: str | None = None
    budget_ref: str | None = None
    deadline: float | None = None
    dependencies: list = field(default_factory=list)
    plan_steps: list = field(default_factory=list)
    policy: str = "reviewed"
    risk: str = "unknown"
    uncertainty: str = "unknown"
    max_turns: int = 20
    no_progress_limit: int = 2
    legacy_contract: dict = field(default_factory=dict)
    subgoals: list = field(default_factory=list)
    gates: list = field(default_factory=list)

    @classmethod
    def from_dict(cls, value):
        require(isinstance(value, dict) and set(value) <= set(cls.__dataclass_fields__), "Unknown mission contract fields")
        require("outcome" in value, "Requested outcome is required")
        row = cls(**value)
        text(row.outcome, 16384)
        require(bool(row.outcome.strip()), "Requested outcome is empty")
        for key in ("project_id", "scope_ref", "budget_ref"):
            if getattr(row, key) is not None:
                identifier(getattr(row, key))
        timestamp(row.deadline)
        require(row.policy in {"direct", "reviewed"}, "Unknown mission execution policy")
        require(row.risk in {"unknown", "low", "consequential"} and row.uncertainty in {"unknown", "low", "high"}, "Unknown mission risk or uncertainty")
        require(row.policy != "direct" or row.risk == row.uncertainty == "low", "Direct execution requires declared low risk and uncertainty")
        require(type(row.max_turns) is int and 1 <= row.max_turns <= 100, "Turn ceiling must be finite")
        require(type(row.no_progress_limit) is int and 1 <= row.no_progress_limit <= 5, "No-progress ceiling must be finite")
        seen = set()
        artifact_refs = set()
        for value in items(row.acceptance, 32):
            criterion = MissionCriterion.from_dict(value)
            require(criterion.criterion_id not in seen, "Duplicate criterion ID")
            seen.add(criterion.criterion_id)
            artifact_refs.update((ref["artifact_id"], ref["version"], ref["digest"]) for ref in criterion.artifact_refs)
        deliverable_ids = set()
        for deliverable in items(row.deliverables):
            require(isinstance(deliverable, dict) and set(deliverable) <= {"deliverable_id", "description", "artifact_ref", "required"},
                    "Invalid deliverable")
            identifier(deliverable.get("deliverable_id"))
            require(deliverable["deliverable_id"] not in deliverable_ids, "Duplicate deliverable ID")
            deliverable_ids.add(deliverable["deliverable_id"])
            text(deliverable.get("description", ""))
            require(type(deliverable.get("required", True)) is bool, "Invalid deliverable requirement")
            if deliverable.get("artifact_ref") is not None:
                ref = artifact_ref(deliverable["artifact_ref"])
                artifact_refs.add((ref["artifact_id"], ref["version"], ref["digest"]))
        require(len(artifact_refs) <= MAX_MISSION_ITEMS, "Distinct mission artifacts exceed retention bound", "mission_capacity")
        for dependency in items(row.dependencies, 64):
            require(isinstance(dependency, dict) and set(dependency) <= {"dependency_id", "kind", "reference", "version", "digest", "status"},
                    "Invalid dependency")
            identifier(dependency.get("dependency_id"))
            require(dependency.get("kind") in {"artifact", "evidence", "input", "mission"}, "Invalid dependency kind")
            identifier(dependency.get("reference"))
            if dependency.get("digest") is not None:
                digest(dependency["digest"])
        steps = {}
        for step in items(row.plan_steps):
            require(isinstance(step, dict) and set(step) <= {"step_id", "description", "status", "depends_on", "input_digests", "target_refs", "approval_ids", "checkpoint"},
                    "Invalid plan step")
            identifier(step.get("step_id"))
            require(step["step_id"] not in steps, "Duplicate plan step ID")
            require(type(step.get("checkpoint", False)) is bool, "Invalid checkpoint declaration")
            steps[step["step_id"]] = step
            text(step.get("description", ""))
            require(step.get("status", "pending") in {"pending", "working", "completed", "blocked", "skipped"}, "Invalid plan step status")
            for key in ("depends_on", "target_refs", "approval_ids"):
                for ref in items(step.get(key, [])):
                    identifier(ref)
            for ref in items(step.get("input_digests", [])):
                digest(ref)
        visited, visiting = set(), set()
        def visit(step_id):
            require(step_id in steps, "Plan dependency names an unknown step")
            require(step_id not in visiting, "Plan dependencies must be acyclic")
            if step_id in visited:
                return
            visiting.add(step_id)
            for parent in steps[step_id].get("depends_on", []):
                visit(parent)
            visiting.remove(step_id)
            visited.add(step_id)
        for step_id in steps:
            visit(step_id)
        require(isinstance(row.legacy_contract, dict) and set(row.legacy_contract) <= {"outcome", "verification", "constraints", "boundaries", "stop_when"},
                "Invalid legacy contract projection")
        for value in row.legacy_contract.values():
            text(value)
        for value in items(row.subgoals):
            text(value)
        # Legacy gates remain historical intent; this does not authorize shell execution.
        items(row.gates)
        bounded_json(asdict(row))
        return row

    def to_dict(self):
        value = asdict(self)
        value["acceptance"] = [MissionCriterion.from_dict(row).to_dict() for row in value["acceptance"]]
        return value
