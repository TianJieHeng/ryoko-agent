"""Versioned, deny-by-default agent identities resolved from trusted configuration.

Display names, prompts, model identifiers and environment variables are deliberately
not identity inputs. Policy version one is opt-in; absent/empty configuration keeps
the legacy runtime, while malformed enabled policy never falls back to it.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

if TYPE_CHECKING:
    from agent.runtime_context import AgentContext

SCHEMA_VERSION = 1
BINDING_REVISION = 1
ID_PATTERN = r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}"
_CHILD_PREFIX = "child_"
_RESERVED_IDS = frozenset({
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
})
_EXACT_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.:/-]{0,255}\Z")


class IdentityPolicyError(ValueError):
    """Invalid or stale identity policy; callers must fail before execution."""


def _schema(description: str, *, required: bool = False) -> dict:
    return {"description": description, "required": required, "config": True}


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode()
    ).hexdigest()


def _version(value: object, path: str) -> None:
    if type(value) is not int or value != SCHEMA_VERSION:
        raise IdentityPolicyError(
            f"{path} must be supported integer version {SCHEMA_VERSION}"
        )


def _identifier(value: object, path: str, *, configured_agent: bool = False) -> str:
    if (
        not isinstance(value, str)
        or re.fullmatch(ID_PATTERN, value) is None
        or value.lower() in _RESERVED_IDS
    ):
        raise IdentityPolicyError(
            f"{path} must be a path-safe identifier matching {ID_PATTERN}"
        )
    if configured_agent and value.startswith(_CHILD_PREFIX):
        raise IdentityPolicyError(f"{path} uses the reserved ephemeral-child namespace")
    return value


def _names(value: object, path: str) -> frozenset[str]:
    if not isinstance(value, (list, tuple, set, frozenset)):
        raise IdentityPolicyError(f"{path} must be a collection of exact names")
    result = []
    for item in value:
        if not isinstance(item, str) or _EXACT_NAME.fullmatch(item) is None:
            raise IdentityPolicyError(
                f"{path} contains an invalid name; wildcards are forbidden"
            )
        result.append(item)
    if len(result) != len(set(result)):
        raise IdentityPolicyError(f"{path} contains duplicate grants")
    return frozenset(result)


def _mapping(value: object, path: str) -> Mapping:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise IdentityPolicyError(f"{path} must be a mapping with string keys")
    return value


def _validate_fields(value: Mapping, cls: type, path: str) -> None:
    schema = {item.name: item for item in fields(cls) if item.metadata.get("config")}
    if value.keys() - schema.keys():
        raise IdentityPolicyError(f"{path} contains unknown fields")
    missing = [
        name
        for name, item in schema.items()
        if item.metadata.get("required") and name not in value
    ]
    if missing:
        raise IdentityPolicyError(
            f"{path} is missing required fields: {', '.join(missing)}"
        )


@dataclass(frozen=True)
class AgentPolicy:
    policy_version: int = field(
        metadata=_schema("Policy contract version; currently 1", required=True)
    )
    role: Literal["primary", "specialist", "child"] = field(
        metadata=_schema(
            "primary for the sole primary ID; specialist for configured peers; child for child_policy",
            required=True,
        )
    )
    memory_backend: Literal["personal_mcp", "builtin"] = field(
        metadata=_schema(
            "personal_mcp for primary only; builtin for every specialist and child",
            required=True,
        )
    )
    allowed_tools: frozenset[str] = field(
        default_factory=frozenset,
        metadata=_schema("Exact model tool names; empty denies every tool"),
    )
    mcp_grants: Mapping[str, frozenset[str]] = field(
        default_factory=dict,
        metadata=_schema(
            "Exact MCP server names mapped to exact tool-name lists; empty grants admit nothing"
        ),
    )
    mcp_policies: Mapping = field(
        default_factory=dict,
        metadata=_schema("Exact per-server HTTP endpoint, read, tool, credential and refresh contracts"),
    )
    recipient_plan: object | None = field(
        default=None,
        metadata=_schema("Versioned exact purpose/recipient egress plan; absent denies strict egress"),
    )
    secret_refs: frozenset[str] = field(
        default_factory=frozenset,
        repr=False,
        metadata=_schema("Exact credential reference names, never credential values"),
    )
    project_grants: frozenset[str] = field(
        default_factory=frozenset,
        metadata=_schema(
            "Exact project IDs; intersected with live project principal/agent permissions"
        ),
    )
    egress_purposes: frozenset[str] = field(
        default_factory=frozenset,
        metadata=_schema(
            "Reserved egress purpose list; must be empty until BE05 enforcement"
        ),
    )
    # Inventories are derived by the resolver, never accepted inside an agent policy.
    all_secret_refs: frozenset[str] = field(default_factory=frozenset, repr=False)
    personal_secret_refs: frozenset[str] = field(default_factory=frozenset, repr=False)
    personal_mcp_servers: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        _version(self.policy_version, "policy_version")
        if self.role not in ("primary", "specialist", "child"):
            raise IdentityPolicyError("role must be primary, specialist or child")
        expected_backend = "personal_mcp" if self.role == "primary" else "builtin"
        if self.memory_backend != expected_backend:
            raise IdentityPolicyError(
                "primary requires personal_mcp; all other roles require builtin"
            )
        for name in (
            "allowed_tools",
            "secret_refs",
            "project_grants",
            "egress_purposes",
            "all_secret_refs",
            "personal_secret_refs",
            "personal_mcp_servers",
        ):
            object.__setattr__(self, name, _names(getattr(self, name), name))
        grants = _mapping(self.mcp_grants, "mcp_grants")
        _names(list(grants), "mcp_grants")
        object.__setattr__(
            self,
            "mcp_grants",
            MappingProxyType({
                server: _names(tools, "mcp_grants tools")
                for server, tools in grants.items()
            }),
        )
        from tools.mcp_tool_policy import parse_mcp_policies
        object.__setattr__(self, "mcp_policies", parse_mcp_policies(self.mcp_policies))
        for server, grant in self.mcp_policies.items():
            operations = set(grant["tool_allowlist"]) | {op for op, targets in grant["read_scopes"].items() if targets}
            if not operations <= self.mcp_grants.get(server, frozenset()):
                raise IdentityPolicyError("MCP policy operations must also appear in mcp_grants")
            if grant["secret_ref"] is not None and grant["secret_ref"] not in self.secret_refs:
                raise IdentityPolicyError("MCP policy credential must be in secret_refs")
        if self.recipient_plan is not None:
            from tools.egress_policy import RecipientPlan
            object.__setattr__(self, "recipient_plan", RecipientPlan.from_record(self.recipient_plan))
        if self.role != "primary" and (
            self.secret_refs & self.personal_secret_refs
            or self.mcp_grants.keys() & self.personal_mcp_servers
            or self.mcp_policies.keys() & self.personal_mcp_servers
        ):
            raise IdentityPolicyError(
                "non-primary policy cannot grant personal MCP servers or credentials"
            )

    def allows_tool(self, name: str) -> bool:
        return name in self.allowed_tools

    def allows_mcp(self, server: str, tool: str | None = None) -> bool:
        grants = self.mcp_grants.get(server, frozenset())
        return bool(grants) if tool is None else tool in grants

    def allows_secret(self, name: str) -> bool:
        return name in self.secret_refs

    def to_record(self, *, redacted: bool = False) -> dict:
        result = {
            "policy_version": self.policy_version,
            "role": self.role,
            "memory_backend": self.memory_backend,
            "allowed_tools": sorted(self.allowed_tools),
            "mcp_grants": {
                key: sorted(value) for key, value in sorted(self.mcp_grants.items())
            },
            "secret_refs": {"redacted_count": len(self.secret_refs)}
            if redacted
            else sorted(self.secret_refs),
            "project_grants": sorted(self.project_grants),
            "egress_purposes": sorted(self.egress_purposes),
        }
        if self.mcp_policies:
            from tools.mcp_tool_policy import _record
            result["mcp_policies"] = _record(self.mcp_policies)
            if redacted:
                for grant in result["mcp_policies"].values():
                    grant["secret_ref"] = "[redacted]" if grant["secret_ref"] else None
        if self.recipient_plan is not None:
            result["recipient_plan"] = self.recipient_plan.to_record(redacted=redacted)
        return result

    @property
    def digest(self) -> str:
        return _digest(self.to_record())


@dataclass(frozen=True)
class AgentIdentityConfig:
    schema_version: int = field(
        metadata=_schema("Opt-in identity schema version; currently 1", required=True)
    )
    principal_id: str = field(
        metadata=_schema("Immutable configured owner identifier", required=True)
    )
    profile_id: str = field(
        metadata=_schema(
            "Immutable configured profile identifier, distinct from its home path",
            required=True,
        )
    )
    primary_agent_id: str = field(
        metadata=_schema("The sole configured primary identity", required=True)
    )
    active_agent_id: str = field(
        metadata=_schema(
            "Configured stable identity used for new top-level sessions", required=True
        )
    )
    agents: Mapping[str, AgentPolicy] = field(
        metadata=_schema(
            "Configured stable agent IDs mapped to AgentPolicy records", required=True
        )
    )
    child_policy: AgentPolicy | None = field(
        default=None,
        metadata=_schema(
            "Explicit child/builtin ceiling; absent or null denies child construction"
        ),
    )
    personal_mcp_servers: frozenset[str] = field(
        default_factory=frozenset,
        metadata=_schema(
            "Personal harness server inventory; grants are restricted to the primary"
        ),
    )
    personal_secret_refs: frozenset[str] = field(
        default_factory=frozenset,
        repr=False,
        metadata=_schema(
            "Personal harness credential reference inventory; restricted to the primary"
        ),
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "agents", MappingProxyType(dict(self.agents)))
        object.__setattr__(
            self, "personal_mcp_servers", frozenset(self.personal_mcp_servers)
        )
        object.__setattr__(
            self, "personal_secret_refs", frozenset(self.personal_secret_refs)
        )

    @property
    def digest(self) -> str:
        return _digest({
            "schema_version": self.schema_version,
            "principal_id": self.principal_id,
            "profile_id": self.profile_id,
            "primary_agent_id": self.primary_agent_id,
            "active_agent_id": self.active_agent_id,
            "agents": {
                key: value.to_record() for key, value in sorted(self.agents.items())
            },
            "child_policy": self.child_policy.to_record()
            if self.child_policy is not None
            else None,
            "personal_mcp_servers": sorted(self.personal_mcp_servers),
            "personal_secret_refs": sorted(self.personal_secret_refs),
        })


def _parse_policy(value: object, path: str) -> AgentPolicy:
    data = _mapping(value, path)
    _validate_fields(data, AgentPolicy, path)
    policy = AgentPolicy(**data)
    if policy.egress_purposes:
        raise IdentityPolicyError("egress_purposes must be empty; use the enforced recipient_plan")
    return policy


def parse_agent_identity_config(config: Mapping) -> AgentIdentityConfig | None:
    """Parse the optional root, validating every agent before any can execute."""
    config = _mapping(config, "config")
    if "agent_identity" not in config:
        return None
    raw = _mapping(config["agent_identity"], "agent_identity")
    if not raw:
        return None
    _validate_fields(raw, AgentIdentityConfig, "agent_identity")
    _version(raw["schema_version"], "agent_identity.schema_version")
    ids = {
        name: _identifier(
            raw[name],
            f"agent_identity.{name}",
            configured_agent=name in ("primary_agent_id", "active_agent_id"),
        )
        for name in (
            "principal_id",
            "profile_id",
            "primary_agent_id",
            "active_agent_id",
        )
    }
    agents = {}
    for name, policy in _mapping(raw["agents"], "agent_identity.agents").items():
        _identifier(name, "agent_identity.agents key", configured_agent=True)
        agents[name] = _parse_policy(policy, "agent_identity.agents policy")
    if ids["primary_agent_id"] not in agents or ids["active_agent_id"] not in agents:
        raise IdentityPolicyError(
            "primary_agent_id and active_agent_id must refer to configured agents"
        )
    for name, policy in agents.items():
        expected_role = "primary" if name == ids["primary_agent_id"] else "specialist"
        if policy.role != expected_role:
            raise IdentityPolicyError(
                "exactly primary_agent_id must have primary role; all other configured agents must be specialists"
            )
    child = (
        _parse_policy(raw["child_policy"], "agent_identity.child_policy")
        if raw.get("child_policy") is not None
        else None
    )
    if child is not None and child.role != "child":
        raise IdentityPolicyError("child_policy must have child role")
    personal_servers = _names(
        raw.get("personal_mcp_servers", []), "personal_mcp_servers"
    )
    personal_secrets = _names(
        raw.get("personal_secret_refs", []), "personal_secret_refs"
    )
    all_secrets = (
        personal_secrets
        | frozenset().union(*(policy.secret_refs for policy in agents.values()))
        | (child.secret_refs if child is not None else frozenset())
    )
    inventories = {
        "personal_mcp_servers": personal_servers,
        "personal_secret_refs": personal_secrets,
        "all_secret_refs": all_secrets,
    }
    agents = {key: replace(value, **inventories) for key, value in agents.items()}
    child = replace(child, **inventories) if child is not None else None
    return AgentIdentityConfig(
        SCHEMA_VERSION,
        **ids,
        agents=agents,
        child_policy=child,
        personal_mcp_servers=personal_servers,
        personal_secret_refs=personal_secrets,
    )


@dataclass(frozen=True)
class IdentityBinding:
    principal_id: str
    profile_id: str
    agent_id: str
    session_id: str
    lifecycle: Literal["stable", "ephemeral"]
    policy_digest: str
    config_digest: str
    profile_home_digest: str
    parent_agent_id: str | None = None
    binding_revision: int = BINDING_REVISION

    def __post_init__(self) -> None:
        _version(self.binding_revision, "binding_revision")
        for name in ("principal_id", "profile_id", "agent_id", "session_id"):
            _identifier(getattr(self, name), name)
        if self.parent_agent_id is not None:
            _identifier(self.parent_agent_id, "parent_agent_id")
        if self.lifecycle not in ("stable", "ephemeral"):
            raise IdentityPolicyError("lifecycle must be stable or ephemeral")
        if self.lifecycle == "ephemeral":
            if (
                self.parent_agent_id is None
                or re.fullmatch(r"child_[0-9a-f]{32}", self.agent_id) is None
            ):
                raise IdentityPolicyError(
                    "ephemeral identities require a parent and trusted child namespace"
                )
        elif self.parent_agent_id is not None or self.agent_id.startswith(
            _CHILD_PREFIX
        ):
            raise IdentityPolicyError("stable identity cannot use ephemeral provenance")
        for name in ("policy_digest", "config_digest", "profile_home_digest"):
            value = getattr(self, name)
            if (
                not isinstance(value, str)
                or re.fullmatch(r"[0-9a-f]{64}", value) is None
            ):
                raise IdentityPolicyError(f"{name} must be a SHA-256 digest")

    def to_record(self) -> dict:
        return {item.name: getattr(self, item.name) for item in fields(self)}

    @classmethod
    def from_record(cls, record: Mapping) -> IdentityBinding:
        record = _mapping(record, "stored identity binding")
        expected = {item.name for item in fields(cls)}
        if set(record) != expected:
            raise IdentityPolicyError(
                "stored identity binding is missing fields or contains unsupported fields; legacy sessions cannot resume under enabled identity policy"
            )
        return cls(**record)


def _child_policy(parent: AgentPolicy, ceiling: AgentPolicy) -> AgentPolicy:
    from tools.mcp_tool_policy import intersect_mcp_policies
    from tools.egress_policy import intersect_recipient_plans
    return replace(
        ceiling,
        allowed_tools=parent.allowed_tools & ceiling.allowed_tools,
        mcp_grants={
            server: tools & parent.mcp_grants.get(server, frozenset())
            for server, tools in ceiling.mcp_grants.items()
            if tools & parent.mcp_grants.get(server, frozenset())
        },
        mcp_policies=intersect_mcp_policies(parent.mcp_policies, ceiling.mcp_policies),
        recipient_plan=intersect_recipient_plans(parent.recipient_plan, ceiling.recipient_plan),
        secret_refs=parent.secret_refs & ceiling.secret_refs,
        project_grants=parent.project_grants & ceiling.project_grants,
        egress_purposes=parent.egress_purposes & ceiling.egress_purposes,
    )


def resolve_agent_context(
    config: Mapping,
    *,
    session_id: str,
    profile_home: str | Path,
    parent_context: AgentContext | None = None,
    is_child: bool = False,
    stored_binding: Mapping | None = None,
) -> AgentContext | None:
    """Trusted construction seam; an empty stored binding explicitly means legacy resume.

    ``None`` means a new session, not an existing session missing its binding. The
    composition root must pass ``{}`` for the latter to prevent silent promotion.
    """
    from agent.runtime_context import AgentContext, canonical_profile_home

    parsed = parse_agent_identity_config(config)
    if parsed is None:
        if stored_binding is not None or parent_context is not None:
            raise IdentityPolicyError(
                "identity-bound sessions cannot fall back to disabled identity policy"
            )
        return None
    home = canonical_profile_home(profile_home)
    _identifier(session_id, "session_id")
    if type(is_child) is not bool:
        raise IdentityPolicyError("is_child must be boolean")
    if parent_context is not None and not is_child:
        raise IdentityPolicyError(
            "a parent context requires explicit child construction"
        )
    stored = (
        IdentityBinding.from_record(stored_binding)
        if stored_binding is not None
        else None
    )
    if is_child:
        if parent_context is None or parsed.child_policy is None:
            raise IdentityPolicyError(
                "child construction requires a bound parent and explicit child_policy"
            )
        if (
            parent_context.profile_home != home
            or parent_context.config_digest != parsed.digest
            or parent_context.identity.principal_id != parsed.principal_id
            or parent_context.identity.profile_id != parsed.profile_id
        ):
            raise IdentityPolicyError(
                "child construction requires matching parent profile and configuration"
            )
        policy = _child_policy(parent_context.policy, parsed.child_policy)
        agent_id = (
            stored.agent_id if stored is not None else _CHILD_PREFIX + uuid4().hex
        )
        parent_id = parent_context.identity.agent_id
        lifecycle = "ephemeral"
    else:
        agent_id = parsed.active_agent_id
        policy = parsed.agents[agent_id]
        parent_id = None
        lifecycle = "stable"
    binding = IdentityBinding(
        principal_id=parsed.principal_id,
        profile_id=parsed.profile_id,
        agent_id=agent_id,
        session_id=session_id,
        lifecycle=lifecycle,
        policy_digest=policy.digest,
        config_digest=parsed.digest,
        profile_home_digest=_digest(home),
        parent_agent_id=parent_id,
    )
    if stored is not None and stored != binding:
        raise IdentityPolicyError(
            "stored identity binding does not match this session, home or current policy; start a new session"
        )
    return AgentContext(
        identity=binding, policy=policy, config_digest=parsed.digest, profile_home=home
    )


def resolve_owned_agent_context(config: Mapping, *, owner_binding: Mapping,
                                session_id: str, profile_home: str | Path) -> AgentContext:
    """Resolve a persisted stable job owner, never the profile's current default.

    This seam is for trusted durable records, not model arguments. Policy/home/
    principal changes revoke the record; changing only the default active agent
    cannot silently give its memory or credentials to an existing schedule.
    """
    from agent.runtime_context import AgentContext, canonical_profile_home

    owner = IdentityBinding.from_record(owner_binding)
    parsed = parse_agent_identity_config(config)
    home = canonical_profile_home(profile_home)
    _identifier(session_id, "session_id")
    if (parsed is None or owner.lifecycle != "stable"
            or owner.principal_id != parsed.principal_id or owner.profile_id != parsed.profile_id
            or owner.profile_home_digest != _digest(home) or owner.agent_id not in parsed.agents):
        raise IdentityPolicyError("Scheduled owner is unavailable or belongs to another profile")
    policy = parsed.agents[owner.agent_id]
    if policy.digest != owner.policy_digest:
        raise IdentityPolicyError("Scheduled owner policy changed; explicit reauthorization is required")
    binding = replace(owner, session_id=session_id, config_digest=parsed.digest)
    return AgentContext(identity=binding, policy=policy, config_digest=parsed.digest, profile_home=home)
