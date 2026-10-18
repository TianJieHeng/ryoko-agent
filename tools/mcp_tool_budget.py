"""Finite per-request accounting for explicitly unmetered, read-only MCP services.

The SDK still owns protocol negotiation and parsing. This transport owns every
physical POST and its complete bounded response, including discovery and retries.
Unsolicited GET streams are declined locally; no background work borrows a future
turn's budget. Paid services and sampling need their own verified charge adapter.
"""
from __future__ import annotations

import asyncio
from collections.abc import Mapping
from contextvars import copy_context
import json
import math
import time


def parse_contract(raw):
    from agent.agent_identity import IdentityPolicyError
    fields = {"billing", "request_timeout_ms", "max_request_bytes", "max_response_bytes"}
    if not isinstance(raw, Mapping) or set(raw) != fields or raw["billing"] != "unmetered":
        raise IdentityPolicyError("MCP budget requires an explicitly unmetered service contract")
    limits = {"request_timeout_ms": 60000, "max_request_bytes": 1048576, "max_response_bytes": 1048576}
    if any(type(raw[key]) is not int or not 1 <= raw[key] <= maximum for key, maximum in limits.items()):
        raise IdentityPolicyError("MCP budget request bounds are invalid")
    return dict(raw)


def require_support(server=None):
    from agent.identity_lifecycle import identity_config
    from agent.budget_account import parse_budget_policy, current_budget
    from agent.runtime_commands import _RUN
    from tools.mcp_tool_policy import MCPUnsupported, policy_for
    configured = parse_budget_policy(identity_config())
    run = _RUN.get()
    budget = current_budget() if run is not None else None
    if configured is None and budget is None:
        return None
    if run is None or budget is None:
        raise MCPUnsupported("Budgeted MCP discovery requires an admitted durable turn")
    if server is None:
        grants = run.context.policy.mcp_policies.values()
        supported = any(grant.get("budget") is not None for grant in grants)
    else:
        grant = policy_for(server)
        supported = grant is not None and grant.get("budget") is not None
    if not supported:
        raise MCPUnsupported("Budgeted MCP requires an explicit unmetered read contract")
    if configured != budget.policy:
        raise MCPUnsupported("MCP budget policy changed; admit a new durable turn")
    budget.check()
    return budget


def _request_operation(server, request, limit):
    from tools.mcp_tool_policy import MCPUnsupported, require_operation, _READ_OPS
    if request.method != "POST":
        raise MCPUnsupported("Budgeted MCP permits finite POST operations only")
    body = request.content
    if not isinstance(body, bytes) or len(body) > limit:
        raise MCPUnsupported("MCP request exceeds its declared byte bound")
    try:
        row = json.loads(body)
    except (ValueError, UnicodeError):
        raise MCPUnsupported("MCP request must be a bounded JSON-RPC object") from None
    if not isinstance(row, dict) or row.get("jsonrpc") != "2.0" or not isinstance(row.get("params", {}), dict):
        raise MCPUnsupported("MCP request must be a bounded JSON-RPC object")
    method, params = row.get("method"), row.get("params", {})
    if not isinstance(method, str):
        raise MCPUnsupported("MCP operation is not supported by the bounded read adapter")
    if method in {"initialize", "server/discover", "notifications/initialized", "tools/list", "ping"}:
        return
    if method == "tools/call":
        if not isinstance(params.get("name"), str) or not isinstance(params.get("arguments", {}), dict):
            raise MCPUnsupported("MCP tool call requires an exact name and argument object")
        require_operation(server.name, params.get("name"), params.get("arguments", {}),
                          connection=server, require_run=True)
        return
    if method not in _READ_OPS:
        raise MCPUnsupported("MCP operation is not supported by the bounded read adapter")
    require_operation(server.name, method, params, connection=server, require_run=True)


def wrap_transport(httpx, inner, server, contract):
    """Use the SDK's HTTPX types; never mutate a legacy client's shared pool."""
    contract = parse_contract(contract)
    server._agent_budget_transport = True

    class BudgetTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            if request.method == "GET":
                # A request-correlated SSE POST is bounded below. A separate
                # notification stream has no finite caller-owned work allocation.
                return httpx.Response(405, request=request)
            context = getattr(server, "_pending_call_context", None)
            context = context.copy() if context is not None else copy_context()
            return await asyncio.create_task(self._request(request), context=context)

        async def _request(self, request):
            from agent.runtime_commands import assert_runtime_dispatch
            from tools.capability_broker import require_live_policy
            from tools.mcp_tool_policy import MCPUnsupported, require_transport
            run = assert_runtime_dispatch()
            require_live_policy()
            require_transport(server.name, server._config, connection=server)
            if str(request.url) != server._config["url"]:
                raise MCPUnsupported("MCP request changed its exact destination")
            _request_operation(server, request, contract["max_request_bytes"])
            budget = require_support(server.name)
            if budget is None:
                raise MCPUnsupported("MCP budget transport lost its admitted budget owner")
            maximum = min(contract["request_timeout_ms"], budget.policy.record["request_timeout_ms"],
                          math.floor((budget.deadline - time.time()) * 1000))
            if maximum <= 0:
                budget.block("MCP request deadline elapsed")
            operation = budget.reserve({"attempts": 1, "executor_slots": 1, "wall_ms": maximum})
            try:
                budget.dispatched(operation)
            except BaseException:
                budget.db.release_budget_reservation(budget.account_id, budget.actor, operation, **budget.fence)
                raise
            started, complete, response = time.monotonic(), False, None
            try:
                async with asyncio.timeout(maximum / 1000):
                    response = await inner.handle_async_request(request)
                    if response.headers.get("content-encoding", "identity").lower() != "identity":
                        raise MCPUnsupported("MCP response compression is outside the bounded read contract")
                    data = bytearray()
                    async for chunk in response.stream:
                        if len(data) + len(chunk) > contract["max_response_bytes"]:
                            raise MCPUnsupported("MCP response exceeds its declared byte bound")
                        data.extend(chunk)
                    await response.aclose()
                    complete = True
                    if assert_runtime_dispatch() is not run:
                        raise MCPUnsupported("MCP response lost its durable owner")
                    require_live_policy()
                    return httpx.Response(response.status_code, headers=response.headers,
                                          content=bytes(data), request=request, extensions=response.extensions)
            except BaseException:
                if not complete:
                    budget.blocked_reason = "MCP request completion is unknown; further dispatch blocked"
                raise
            finally:
                try:
                    if response is not None:
                        await response.aclose()
                finally:
                    budget.settle(operation, {"attempts": 1, "wall_ms": math.ceil((time.monotonic() - started) * 1000)},
                                  unknown=not complete, slots_released=complete)

        async def aclose(self):
            await inner.aclose()

    return BudgetTransport()
