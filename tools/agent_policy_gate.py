"""Agent-authority checks at schema, dispatch and MCP admission boundaries.

The identity policy admits only granted, certified adapters. Concrete execution,
recipient and MCP boundaries enforce their declared scope again at dispatch;
schema exposure is never execution authority. Legacy profiles are unchanged.
"""
from __future__ import annotations

import json
from typing import Any

_CURRENT = object()
# These handlers do not execute generated code, expose the filesystem or send
# arbitrary network requests. Bridge dispatch separately authorizes its target.
_SESSION_ONLY_TOOLS = frozenset({"todo_list", "clarify", "tool_search", "tool_describe", "tool_call"})


def _denied(reason: str, *, unsupported: bool = False) -> str:
    return json.dumps({
        "error": "agent_policy_unsupported" if unsupported else "agent_policy_denied",
        "message": reason,
        "status": "unsupported" if unsupported else "denied",
    })


def _context(context=_CURRENT):
    if context is _CURRENT:
        from agent.runtime_context import current_agent_context
        return current_agent_context()
    return context


def missing_context_denial() -> str | None:
    """A configured strict profile must never silently fall back to legacy scope."""
    from agent.identity_lifecycle import strict_identity_enabled
    if strict_identity_enabled():
        return _denied("Trusted agent context is required; agent-owned tool discovery and execution are deferred.")
    return None


def authorize_tool(name: str, *, context=_CURRENT, entry=None) -> str | None:
    ctx = _context(context)
    if ctx is None:
        return missing_context_denial()
    if not ctx.policy.allows_tool(name):
        return _denied("The current agent has no grant for this tool.")
    if entry is None:
        from tools.registry import registry
        entry = registry.get_entry(name)
    # Discovery cannot certify execution. Only concrete host-owned adapters
    # with a declared boundary are admitted, even for the primary identity.
    if entry is not None and entry.toolset.startswith("mcp-"):
        target = getattr(entry.handler, "_agent_mcp_target", None)
        if not isinstance(target, tuple) or len(target) != 2:
            return _denied("MCP tool provenance is unavailable; rediscovery is required.")
        return authorize_mcp(*target, context=ctx)
    module = getattr(getattr(entry, "handler", None), "__module__", "")
    certified = {
        "todo_list": "tools.todo_tool", "clarify": "tools.clarify_tool",
        "tool_search": "tools.tool_search", "tool_describe": "tools.tool_search",
        "tool_call": "tools.tool_search", "execute_code": "tools.code_execution_tool",
        "delegate_task": "tools.delegate_tool",
        "memory": "tools.memory_tool", "session_search": "tools.session_search_tool",
    }
    expected = certified.get(name)
    if expected is None or (entry is not None and module != expected):
        return _denied("This execution route has no BE05-certified isolation/egress contract.", unsupported=True)
    if entry is None and name not in _SESSION_ONLY_TOOLS:
        return _denied("Certified handler provenance is unavailable.", unsupported=True)
    if name in ("memory", "session_search"):
        if ctx.policy.memory_backend != "builtin" or ctx.policy.role == "primary":
            return _denied("Local memory and session recall require an individual built-in backend.", unsupported=True)
    return None


def filter_tool_definitions(definitions, *, context=_CURRENT) -> list:
    ctx = _context(context)
    return [definition for definition in definitions
            if authorize_tool((definition.get("function") or {}).get("name", ""), context=ctx) is None]


def _owner(ctx) -> tuple:
    from hermes_constants import hermes_home_key
    identity = ctx.identity
    return (hermes_home_key(), identity.principal_id, identity.profile_id, identity.agent_id,
            ctx.policy.digest, ctx.config_digest)


def authorize_mcp(server: str, tool: str | None = None, *, connection=None, context=_CURRENT) -> str | None:
    ctx = _context(context)
    if ctx is None:
        if connection is not None and vars(connection).get("_agent_policy_owner") is not None:
            return _denied("A policy-owned MCP connection requires its trusted agent context.")
        return missing_context_denial()
    if not ctx.policy.allows_mcp(server, tool):
        return _denied("The current agent has no grant for this MCP operation.")
    if ctx.policy.role != "primary" and server in ctx.policy.personal_mcp_servers:
        return _denied("Personal MCP access is restricted to the configured primary identity.")
    if not ctx.policy.mcp_policies.get(server):
        return _denied("MCP connections require BE05-certified agent-owned transport grants.", unsupported=True)
    try:
        from tools.mcp_tool_policy import require_operation
        require_operation(server, tool, connection=connection)
    except (ValueError, PermissionError) as exc:
        from tools.mcp_tool_policy import MCPUnsupported
        return _denied(str(exc), unsupported=isinstance(exc, MCPUnsupported))
    if connection is not None and vars(connection).get("_agent_policy_owner") != _owner(ctx):
        return _denied("MCP connection ownership does not match this agent policy.")
    return None


def bind_mcp_connection(connection) -> None:
    """Called only before a fresh connection starts, never to adopt an existing one."""
    ctx = _context()
    connection._agent_policy_owner = None if ctx is None else _owner(ctx)
    from tools.mcp_tool_policy import registry_scope
    connection._agent_registry_scope = registry_scope(ctx)
    connection._agent_schema_digests = None
    connection._agent_refresh_blocked = None


def require_mcp(server: str, tool: str | None = None, *, connection=None) -> None:
    denial = authorize_mcp(server, tool, connection=connection)
    if denial is not None:
        raise PermissionError(json.loads(denial)["message"])


def filter_mcp_servers(servers: dict) -> dict:
    return {name: config for name, config in servers.items() if authorize_mcp(name) is None}


def mcp_discovery_denial() -> str | None:
    ctx = _context()
    if ctx is None:
        return missing_context_denial()
    try:
        from tools.mcp_tool_policy import live_context, require_budget_support
        live_context()
        require_budget_support()
    except (ValueError, PermissionError) as exc:
        from tools.mcp_tool_policy import MCPUnsupported
        return _denied(str(exc), unsupported=isinstance(exc, MCPUnsupported))
    if not ctx.policy.mcp_policies:
        return _denied("MCP discovery requires BE05-certified agent-owned transport grants.", unsupported=True)
    return None


def assert_runtime_dispatch() -> None:
    """Dispatch-only fence. Schema discovery never needs an executing turn."""
    from agent.runtime_commands import assert_runtime_dispatch as assert_owner
    assert_owner()
