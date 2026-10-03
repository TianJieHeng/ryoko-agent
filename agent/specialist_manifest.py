"""Configured specialists own stable memory; delegation only narrows their policy."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
import json

from agent.agent_identity import IdentityBinding, parse_agent_identity_config, _child_policy, _digest
from agent.delegation_contract import canonical, digest, require, text
from agent.individual_memory_scope import IndividualMemoryScope
from agent.runtime_context import AgentContext


@dataclass(frozen=True)
class SpecialistManifest:
    snapshot: str

    @classmethod
    def resolve(cls, config, agent_id, *, parent, session_id):
        parsed = parse_agent_identity_config(config)
        require(parsed is not None and parent.config_digest == parsed.digest,
                "specialist_policy_changed", "Current parent policy is required")
        raw = (config.get("delegation") or {}).get("specialists", {}).get(agent_id)
        require(isinstance(raw, dict) and set(raw) == {"responsibility", "methods_ref", "limits", "output_contract"},
                "specialist_unconfigured", "Specialist requires an exact configured manifest")
        policy = parsed.agents.get(agent_id)
        require(policy is not None and policy.role == "specialist", "specialist_unconfigured", "Named identity must be a configured specialist")
        text(raw["responsibility"], "responsibility", 4096)
        require(isinstance(raw["methods_ref"], dict) and set(raw["methods_ref"]) == {"id", "version", "sha256"},
                "invalid_specialist", "Methods require an immutable version/digest reference")
        from agent.delegation_contract import sha, DelegationLimits
        text(raw["methods_ref"]["id"], "methods id")
        sha(raw["methods_ref"]["sha256"])
        require(type(raw["methods_ref"]["version"]) is int and raw["methods_ref"]["version"] > 0,
                "invalid_specialist", "Exact methods version required")
        limits = DelegationLimits(**raw["limits"])
        require(isinstance(raw["output_contract"], dict), "invalid_specialist", "Specialist output schema required")
        from tools.delegation_output_schema import coerce_output_schema
        schema, error = coerce_output_schema(raw["output_contract"])
        require(error is None, "invalid_specialist", error or "Invalid output schema")
        narrowed = _child_policy(parent.policy, policy)
        # The stable memory namespace remains unchanged when per-task grants narrow.
        binding = IdentityBinding(parsed.principal_id, parsed.profile_id, agent_id, session_id,
            "stable", narrowed.digest, parsed.digest, _digest(parent.profile_home))
        context = AgentContext(binding, narrowed, parsed.digest, parent.profile_home,
                               configuration_session_id=parent.identity.session_id)
        manifest = cls(canonical({"agent_id": agent_id, "responsibility": raw["responsibility"],
            "methods_ref": raw["methods_ref"], "limits": limits.to_record(), "output_contract": schema,
            "grants": narrowed.to_record(), "builtin_memory_namespace": IndividualMemoryScope.from_context(context).namespace_id}))
        return manifest, context

    def to_record(self):
        return json.loads(self.snapshot)

    @property
    def sha256(self):
        return digest(self.to_record())


_CONSTRUCTION = ContextVar("trusted_specialist_construction", default=None)


@contextmanager
def specialist_construction(manifest, context, parent):
    """Host-owned constructor scope, never populated by model-supplied identity bytes."""
    token = _CONSTRUCTION.set((manifest, context, parent))
    try:
        yield
    finally:
        _CONSTRUCTION.reset(token)


def construction_context(config, *, parent, session_id, stored):
    selected = _CONSTRUCTION.get()
    if selected is None:
        return None
    manifest, expected, owner = selected
    require(owner == parent and session_id == expected.identity.session_id,
            "specialist_identity_mismatch", "Specialist construction lost its parent/session binding")
    current, context = SpecialistManifest.resolve(config, expected.identity.agent_id, parent=owner, session_id=session_id)
    require(current == manifest and expected == context and (stored is None or stored == context.identity.to_record()),
            "specialist_policy_changed", "Specialist handoff or stored identity changed")
    return context


def specialist_methods_text(manifest, context, *, parent, db):
    """Resolve exact methods through both live ACLs before adding any source text."""
    from agent.identity_lifecycle import agent_runtime_scope
    from agent.project_context import project_access
    from agent.result_artifacts import read_project_artifact, artifact_actor
    from agent.mission_runtime import assert_mission_project_scope
    from agent.runtime_commands import assert_runtime_dispatch
    import hashlib
    record = manifest.to_record()
    ref = record["methods_ref"]
    content = None
    for owner in (parent, context):
        with agent_runtime_scope(owner):
            row = db.read_artifact_version(ref["id"], ref["version"], artifact_actor(owner), access=project_access(owner))
            require(row["derived_validity"] == "current", "specialist_methods_changed", "Specialist methods are stale")
            if owner is parent:
                assert_mission_project_scope(assert_runtime_dispatch(), row["project_id"])
            content = read_project_artifact(owner, db, row["project_id"], ref["id"], ref["version"])
            require(len(content) <= 32768 and hashlib.sha256(content).hexdigest() == ref["sha256"],
                    "specialist_methods_changed", "Exact bounded specialist methods required")
            require(row["descriptor"]["mime"] in {"text/plain", "text/markdown"},
                    "specialist_methods_unsupported", "Specialist methods must be text")
    return "\nSpecialist responsibility: " + record["responsibility"] + "\nVersioned methods:\n" + content.decode("utf-8")
