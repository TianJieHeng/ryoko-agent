"""Trusted MCP grant, connection ownership and refresh fences.

Server metadata never supplies authority. Strict transports are deliberately limited to
caller-owned Streamable HTTP; stdio, OAuth, SSE and SDK-owned sampling remain unsupported.
The namespace is a registry key only, never a filesystem workspace or a sandbox claim.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from urllib.parse import urlsplit

class MCPUnsupported(PermissionError):
    """The installed enforcement/budget adapter cannot safely perform this route."""


_READ_OPS = frozenset({"resources/read", "resources/list", "prompts/get", "prompts/list"})


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=True).encode()).hexdigest()


def _freeze(value):
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _record(value):
    if isinstance(value, Mapping):
        return {key: _record(item) for key, item in value.items()}
    if isinstance(value, (tuple, frozenset)):
        return [_record(item) for item in value]
    return value


def parse_mcp_policies(raw):
    """Parse explicit contracts, retaining no implicit tools, scopes or credentials."""
    from agent.agent_identity import IdentityPolicyError, _names
    if not isinstance(raw, Mapping):
        raise IdentityPolicyError("mcp_policies must be a mapping")
    _names(list(raw), "mcp_policies")
    result = {}
    fields = {"policy_version", "transport", "endpoint", "tool_allowlist", "read_scopes",
              "secret_ref", "schema_digests", "read_only_tools", "sampling_limits"}
    for server, supplied in raw.items():
        if not isinstance(supplied, Mapping) or set(supplied) - fields:
            raise IdentityPolicyError("MCP policy contains unknown fields")
        if type(supplied.get("policy_version")) is not int or supplied["policy_version"] < 1:
            raise IdentityPolicyError("MCP policy_version must be a positive integer")
        if supplied.get("transport") != "streamable_http":
            raise IdentityPolicyError("MCP transport unsupported: only owned streamable_http is certified")
        endpoint = supplied.get("endpoint")
        parsed = urlsplit(endpoint) if isinstance(endpoint, str) else None
        if (parsed is None or parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise IdentityPolicyError("MCP endpoint must be an exact credential-free HTTP URL")
        tools = sorted(_names(supplied.get("tool_allowlist", []), "MCP tool_allowlist"))
        read_only = sorted(_names(supplied.get("read_only_tools", []), "MCP read_only_tools"))
        if not set(read_only) <= set(tools):
            raise IdentityPolicyError("MCP read_only_tools must be explicitly granted tools")
        scopes = supplied.get("read_scopes", {})
        if not isinstance(scopes, Mapping) or set(scopes) - _READ_OPS:
            raise IdentityPolicyError("MCP read_scopes contains unsupported operations")
        reads = {}
        for operation, targets in scopes.items():
            if operation.endswith("/list"):
                if type(targets) is not bool:
                    raise IdentityPolicyError("MCP list scope must be an explicit boolean")
                reads[operation] = targets
            else:
                if (not isinstance(targets, (list, tuple))
                        or any(not isinstance(target, str) or not target or "*" in target
                               or len(target) > 2048 for target in targets)
                        or len(set(targets)) != len(targets)):
                    raise IdentityPolicyError("MCP read targets must be distinct exact names or URIs")
                reads[operation] = sorted(targets)
        secret = supplied.get("secret_ref")
        if secret is not None:
            _names([secret], "MCP secret_ref")
        schemas = supplied.get("schema_digests", {})
        if not isinstance(schemas, Mapping) or not set(schemas) <= set(tools):
            raise IdentityPolicyError("MCP schema_digests must name granted tools")
        if any(not isinstance(value, str) or len(value) != 64
               or any(c not in "0123456789abcdef" for c in value) for value in schemas.values()):
            raise IdentityPolicyError("MCP schema digests must be SHA-256 hex strings")
        # The current BE03 adapter does not certify an MCP charge or SDK-owned MRTR loop.
        # Accept only an explicit disabled record rather than an inert limits object.
        sampling = supplied.get("sampling_limits", {"enabled": False})
        if not isinstance(sampling, Mapping) or dict(sampling) != {"enabled": False}:
            raise IdentityPolicyError("MCP sampling/MRTR limits unsupported by the certified budget adapter")
        result[server] = {"policy_version": supplied["policy_version"],
                          "transport": "streamable_http", "endpoint": endpoint,
                          "tool_allowlist": tools, "read_scopes": reads,
                          "secret_ref": secret, "schema_digests": dict(schemas),
                          "read_only_tools": read_only, "sampling_limits": {"enabled": False}}
    return _freeze(result)


def intersect_mcp_policies(parent, ceiling):
    """A child can inherit only an identical bounded transport contract, with narrowed scopes."""
    result = {}
    for server, cap in ceiling.items():
        inherited = parent.get(server)
        if inherited is None:
            continue
        if any(cap[key] != inherited[key] for key in
               ("transport", "endpoint", "secret_ref", "policy_version", "sampling_limits")):
            continue
        record = _record(cap)
        record["tool_allowlist"] = sorted(set(cap["tool_allowlist"]) & set(inherited["tool_allowlist"]))
        record["read_only_tools"] = sorted(set(cap["read_only_tools"]) & set(inherited["read_only_tools"]))
        record["schema_digests"] = {name: digest for name, digest in cap["schema_digests"].items()
                                     if inherited["schema_digests"].get(name) == digest
                                     and name in record["tool_allowlist"]}
        record["read_scopes"] = {
            op: (bool(target and inherited["read_scopes"].get(op)) if op.endswith("/list") else
                 sorted(set(target) & set(inherited["read_scopes"].get(op, ()))))
            for op, target in cap["read_scopes"].items()}
        result[server] = record
    return result


def live_context():
    from agent.runtime_context import current_agent_context
    ctx = current_agent_context()
    if ctx is None:
        return None
    from agent.agent_identity import parse_agent_identity_config
    from agent.identity_lifecycle import identity_config
    configured = parse_agent_identity_config(identity_config())
    if configured is None or configured.digest != ctx.config_digest:
        raise PermissionError("MCP policy is stale or revoked; a new authorized session is required")
    return ctx


def registry_scope(context=None):
    """Agent/policy/config/credential-owned overlay; no same-profile pool adoption."""
    from agent.runtime_context import current_agent_context
    ctx = context if context is not None else current_agent_context()
    if ctx is None:
        return None
    from agent.identity_lifecycle import identity_config
    from agent.secret_scope import current_secret_scope
    scoped = current_secret_scope() if ctx.policy.secret_refs else {}
    secrets = {name: (scoped or {}).get(name) for name in sorted(ctx.policy.secret_refs)}
    material = [ctx.identity.principal_id, ctx.identity.profile_id, ctx.identity.agent_id,
                ctx.policy.digest, ctx.config_digest, secrets,
                identity_config().get("mcp_servers", {})]
    return str(Path(ctx.profile_home) / ".agent-mcp" / _digest(material))


def policy_for(server, context=None):
    ctx = context if context is not None else live_context()
    return None if ctx is None else ctx.policy.mcp_policies.get(server)


def require_budget_support():
    """Startup metadata is potentially chargeable too, before a run is installed."""
    from agent.identity_lifecycle import identity_config
    from agent.budget_account import parse_budget_policy
    if parse_budget_policy(identity_config()) is not None:
        raise MCPUnsupported("MCP discovery and execution unsupported by the certified BE03 budget adapter")


def require_transport(server, config, *, connection=None):
    """Validate before credentials, subprocesses, DNS, OAuth or SDK work."""
    ctx = live_context()
    if ctx is None:
        return
    require_budget_support()
    grant = policy_for(server, ctx)
    if grant is None:
        raise PermissionError("MCP transport unsupported without an explicit agent-owned MCP policy")
    if ("url" not in config or config.get("transport", "http") not in {"http", "streamable_http"}
            or config.get("auth") or config.get("oauth") or config.get("identity_header")
            or config.get("client_cert") or config.get("client_key")
            or config.get("ssl_verify", True) is not True):
        raise PermissionError("MCP transport unsupported: stdio, SSE, OAuth and ambient client identities are not certified")
    if config.get("url") != grant["endpoint"]:
        raise PermissionError("MCP endpoint does not match the exact agent grant")
    if connection is not None and getattr(connection, "_agent_registry_scope", None) != registry_scope(ctx):
        raise PermissionError("MCP connection credentials or endpoint policy changed; reconnect in a new owning scope")


def require_http_inputs(server, config, url, headers, *, connection=None):
    require_transport(server, config, connection=connection)
    ctx = live_context()
    if ctx is None:
        return None
    grant = policy_for(server, ctx)
    if url != grant["endpoint"]:
        raise PermissionError("MCP live endpoint override is outside the exact agent grant")
    from agent.secret_scope import get_secret
    secret = get_secret(grant["secret_ref"]) if grant["secret_ref"] else None
    expected = {"authorization": "Bearer " + secret} if secret else {}
    if grant["secret_ref"] and not secret:
        raise PermissionError("MCP endpoint-bound credential is unavailable")
    supplied = {key.lower(): value for key, value in headers.items()}
    if supplied != expected:
        raise PermissionError("MCP headers must use only the explicitly endpoint-bound bearer credential")
    from tools.egress_policy import prepare_recipient
    return prepare_recipient("mcp", url, recipient_id=server, require_run=False)


def require_operation(server, operation=None, args=None, *, connection=None, require_run=False):
    ctx = live_context()
    if ctx is None:
        return
    require_budget_support()
    if ctx.policy.role != "primary" and server in ctx.policy.personal_mcp_servers:
        raise PermissionError("Personal MCP is restricted to the configured primary identity")
    grant = policy_for(server, ctx)
    if grant is None:
        raise PermissionError("MCP transport unsupported without an explicit agent-owned MCP policy")
    if operation is not None:
        if operation in _READ_OPS:
            target = grant["read_scopes"].get(operation)
            if not target:
                raise PermissionError("MCP read operation has no explicit scope")
            if args is not None and not operation.endswith("/list"):
                field = "uri" if operation == "resources/read" else "name"
                if args.get(field) not in target:
                    raise PermissionError("MCP read target is outside the exact agent grant")
        elif operation not in grant["tool_allowlist"]:
            raise PermissionError("MCP tool is outside the exact agent grant")
    if connection is not None:
        if getattr(connection, "_agent_registry_scope", None) != registry_scope(ctx):
            raise PermissionError("MCP connection ownership, credentials or endpoint changed")
        if getattr(connection, "_agent_refresh_blocked", None):
            raise PermissionError(connection._agent_refresh_blocked)
        if operation is not None and operation not in _READ_OPS:
            expected = grant["schema_digests"].get(operation)
            if not expected or (getattr(connection, "_agent_schema_digests", None) or {}).get(operation) != expected:
                raise PermissionError("MCP tool requires its exact operator schema digest before invocation")
    if require_run:
        from agent.budget_account import current_budget
        if current_budget() is not None:
            raise PermissionError("MCP execution unsupported by the certified BE03 bounded tool adapter")
        from agent.runtime_commands import assert_runtime_dispatch
        assert_runtime_dispatch()


def schema_digest(tool):
    from tools.mcp_tool_common import mcp_field
    dump = getattr(tool, "model_dump", None)
    if callable(dump):
        record = dump(mode="json", by_alias=True, exclude_none=True)
        if isinstance(record, Mapping):
            return _digest(dict(record))
    # Harmless fixtures/older SDKs lack model_dump; retain every contract field,
    # including output schemas and untrusted annotations rather than only inputs.
    record = {"name": tool.name, "description": getattr(tool, "description", "") or ""}
    for name, snake, camel in (("inputSchema", "input_schema", "inputSchema"),
                               ("outputSchema", "output_schema", "outputSchema"),
                               ("annotations", "annotations", "annotations"),
                               ("title", "title", "title"), ("_meta", "meta", "_meta")):
        value = mcp_field(tool, snake, camel)
        if value is not None:
            if callable(getattr(value, "model_dump", None)):
                value = value.model_dump(mode="json", by_alias=True, exclude_none=True)
            elif hasattr(value, "__dict__"):
                value = vars(value)
            record[name] = value
    return _digest(record)


def validate_schemas(server, tools):
    """Reauthorization requires trusted policy change; a server notification cannot grant it."""
    if getattr(server, "_agent_policy_owner", None) is None:
        return
    require_operation(server.name, connection=server)
    grant = policy_for(server.name)
    granted = {tool.name: schema_digest(tool) for tool in tools if tool.name in grant["tool_allowlist"]}
    pinned = grant["schema_digests"]
    prior = getattr(server, "_agent_schema_digests", None)
    if any(pinned.get(name) != digest for name, digest in granted.items()) or (
            prior is not None and any(name in prior and prior[name] != digest for name, digest in granted.items())):
        invalidate_refresh(server, "MCP schema pin missing or changed; explicit policy reauthorization and a new context are required")
        raise PermissionError(server._agent_refresh_blocked)
    # Retain removed schema identities too, so remove/re-add cannot reset the fence.
    server._agent_schema_digests = {**(prior or {}), **granted}


def invalidate_refresh(server, reason):
    server._agent_refresh_blocked = reason
    server._deregister_tools()


def operator_read_only(server, tool):
    """Only operator policy can establish retry safety; remote hints cannot."""
    ctx = live_context()
    if ctx is not None:
        grant = policy_for(server, ctx)
        return bool(grant and tool in grant["read_only_tools"])
    from tools import mcp_tool as core
    from tools.mcp_tool_scope import _resolve_server_key
    return tool in getattr(core, "_tool_operator_read_only", {}).get(_resolve_server_key(server), ())
