"""Agent-authority checks at schema, dispatch and MCP admission boundaries.

The opt-in BE01 policy is an application boundary, not an OS sandbox. Until BE05
certifies an executor and agent-keyed MCP pools, nonprimary agents can only use
explicitly granted in-memory/session-control tools. Legacy profiles are unchanged.
"""
from __future__ import annotations

import json
from typing import Any

_CURRENT = object()
# These handlers do not execute generated code, expose the filesystem or send
# arbitrary network requests. Bridge dispatch separately authorizes its target.
_SESSION_ONLY_TOOLS = frozenset({"todo_list", "clarify", "tool_search", "tool_describe", "tool_call"})
_SESSION_HANDLER_MODULES = frozenset({"tools.todo_tool", "tools.clarify_tool", "tools.tool_search"})


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
    if ctx.policy.role != "primary":
        if name not in _SESSION_ONLY_TOOLS:
            return _denied("This execution route requires BE05-certified agent isolation.", unsupported=True)
        if entry is not None and getattr(entry.handler, "__module__", "") not in _SESSION_HANDLER_MODULES:
            return _denied("Plugin execution requires BE05-certified agent isolation.", unsupported=True)
    # Handler metadata records the raw MCP name. Never reverse a lossy normalized
    # registry name: different raw names can normalize to the same spelling.
    if entry is not None and entry.toolset.startswith("mcp-"):
        target = getattr(entry.handler, "_agent_mcp_target", None)
        if not isinstance(target, tuple) or len(target) != 2:
            return _denied("MCP tool provenance is unavailable; rediscovery is required.")
        return authorize_mcp(*target, context=ctx)
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
    if ctx.policy.role != "primary":
        return _denied("Nonprimary MCP connections require BE05-certified agent-owned pools.", unsupported=True)
    if connection is not None and vars(connection).get("_agent_policy_owner") != _owner(ctx):
        return _denied("MCP connection ownership does not match this agent policy; restart the connection in its owning scope.")
    return None


def bind_mcp_connection(connection) -> None:
    """Called only before a fresh connection starts, never to adopt an existing one."""
    ctx = _context()
    connection._agent_policy_owner = None if ctx is None else _owner(ctx)


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
    if ctx.policy.role != "primary":
        return _denied("Nonprimary MCP discovery requires BE05-certified agent-owned pools.", unsupported=True)
    return None
