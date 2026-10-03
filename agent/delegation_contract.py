"""Immutable BE13 handoffs; UI labels and textual context never grant authority."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import re


class DelegationError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def require(condition, code, message):
    if not condition:
        raise DelegationError(code, message)


def canonical(value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    require(len(encoded.encode()) <= 128 * 1024, "handoff_too_large", "Handoff exceeds its finite inline limit")
    return encoded


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def text(value, name, maximum=256):
    require(isinstance(value, str) and 0 < len(value.encode()) <= maximum,
            "invalid_handoff", f"{name} must be a bounded nonempty string")
    return value


def sha(value):
    require(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None,
            "invalid_handoff", "Exact SHA-256 required")
    return value


@dataclass(frozen=True)
class DelegationLimits:
    max_depth: int = 1
    max_total_children: int = 1
    max_concurrent_children: int = 1

    def __post_init__(self):
        for name, maximum in (("max_depth", 16), ("max_total_children", 256), ("max_concurrent_children", 32)):
            require(type(getattr(self, name)) is int and 1 <= getattr(self, name) <= maximum,
                    "invalid_delegation_limits", f"{name} must be a finite positive integer <= {maximum}")
        require(self.max_concurrent_children <= self.max_total_children,
                "invalid_delegation_limits", "Concurrency cannot exceed aggregate fan-out")

    def to_record(self):
        return vars(self).copy()


@dataclass(frozen=True, init=False)
class ImmutableHandoff:
    """Only canonical bytes are retained; nested input mutations cannot widen grants."""
    snapshot: str

    def __init__(self, record):
        expected = {"schema_version", "child_id", "parent_run_id", "parent_session_id", "root_run_id",
                    "objective", "constraints", "deadline", "artifacts", "evidence", "budget",
                    "parent_identity", "child_identity", "grants", "workspace", "executor", "depth",
                    "specialist", "output_contract"}
        require(isinstance(record, dict) and set(record) == expected and type(record["schema_version"]) is int
                and record["schema_version"] == 1, "invalid_handoff", "Complete versioned handoff required")
        for key in ("child_id", "parent_run_id", "parent_session_id", "root_run_id"):
            text(record[key], key)
        text(record["objective"], "objective", 65536)
        require(isinstance(record["constraints"], list) and len(record["constraints"]) <= 32,
                "invalid_handoff", "Bounded constraints required")
        for value in record["constraints"]:
            text(value, "constraint", 4096)
        require(type(record["deadline"]) in (float, int) and math.isfinite(record["deadline"])
                and record["deadline"] > 0, "invalid_handoff", "Finite deadline required")
        require(type(record["depth"]) is int and 1 <= record["depth"] <= 16,
                "invalid_handoff", "Explicit delegation depth required")
        from agent.agent_identity import IdentityBinding, AgentPolicy
        parent, child = (IdentityBinding.from_record(record[key]) for key in ("parent_identity", "child_identity"))
        require(parent.principal_id == child.principal_id and parent.profile_id == child.profile_id
                and parent.profile_home_digest == child.profile_home_digest,
                "handoff_identity_mismatch", "Delegation cannot change principal/profile/data locality")
        policy = AgentPolicy(**record["grants"])
        require(policy.digest == child.policy_digest and policy.role in {"specialist", "child"}
                and policy.memory_backend == "builtin", "handoff_grant_mismatch", "Individual child grants required")
        require(isinstance(record["budget"], dict) and set(record["budget"]) ==
                {"account_id", "root_id", "reservation_id", "policy_digest"}, "invalid_handoff", "Exact reserved budget required")
        for key, value in record["budget"].items():
            text(value, key)
        sha(record["budget"]["policy_digest"])
        require(isinstance(record["workspace"], dict) and set(record["workspace"]) == {"root", "manifest_digest"},
                "invalid_handoff", "Exact staged workspace required")
        text(record["workspace"]["root"], "workspace root", 4096)
        sha(record["workspace"]["manifest_digest"])
        require(isinstance(record["executor"], dict) and set(record["executor"]) ==
                {"executor_id", "generation", "capability_digest", "location"}, "invalid_handoff", "Exact executor required")
        text(record["executor"]["executor_id"], "executor_id")
        sha(record["executor"]["capability_digest"])
        require(record["executor"]["location"] == "local" and type(record["executor"]["generation"]) is int
                and record["executor"]["generation"] > 0, "executor_unsupported", "Only bound local execution is supported")
        for key in ("artifacts", "evidence"):
            require(isinstance(record[key], list) and len(record[key]) <= 64,
                    "invalid_handoff", "Bounded exact source/artifact references required")
            for ref in record[key]:
                require(isinstance(ref, dict) and set(ref) == {"id", "version", "sha256"},
                        "invalid_handoff", "References require ID, version and digest")
                text(ref["id"], "reference id")
                require(type(ref["version"]) is int and ref["version"] > 0,
                        "invalid_handoff", "Positive exact version required")
                sha(ref["sha256"])
        require(record["specialist"] is None or isinstance(record["specialist"], dict),
                "invalid_handoff", "Specialist reference must be exact")
        require(record["output_contract"] is None or isinstance(record["output_contract"], dict),
                "invalid_handoff", "Output contract must be a JSON schema")
        object.__setattr__(self, "snapshot", canonical(record))

    def to_record(self):
        return json.loads(self.snapshot)

    @property
    def sha256(self):
        return hashlib.sha256(self.snapshot.encode()).hexdigest()
