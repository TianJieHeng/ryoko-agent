"""Real owned MCP dispatch and ledger accounting at the physical HTTP boundary."""
import asyncio
from dataclasses import replace
import json
import time

import httpx
import pytest

from agent.budget_account import BudgetRuntime, actor_for, parse_budget_policy
from tests.agent.test_budget_runtime import policy as budget_policy
from tests.agent.test_memory_mcp_provider import configured, live_server
from tests.tools import test_mcp_agent_trust_lifecycle as mcp_tests
from tests.tools.test_mcp_agent_trust_lifecycle import fixture as mcp_fixture  # noqa: F401
from tools.mcp_tool_budget import wrap_transport

CONTRACT = {"billing": "unmetered", "request_timeout_ms": 2000,
            "max_request_bytes": 32768, "max_response_bytes": 32768}


@pytest.fixture
def harness(tmp_path, monkeypatch, request):
    def config():
        raw = configured()
        raw["agent_identity"]["agents"]["primary"]["mcp_policies"]["public"]["budget"] = CONTRACT
        raw["runtime_budget"] = budget_policy()
        if "protocol_server" in request.fixturenames:
            endpoint, _ = request.getfixturevalue("protocol_server")
            primary = raw["agent_identity"]["agents"]["primary"]
            primary["mcp_policies"]["public"]["endpoint"] = endpoint
            primary["recipient_plan"]["grants"][0]["endpoint"] = endpoint
            raw["mcp_servers"]["public"]["url"] = endpoint
        return raw
    (tmp_path / ".env").write_text("TEST_MEMORY_TOKEN=synthetic-test-only\n")
    monkeypatch.setattr(mcp_tests, "raw_config", config)
    value = request.getfixturevalue("mcp_fixture")
    run = value.runs["primary"]
    policy = parse_budget_policy(value.raw)
    actor = actor_for(value.primary)
    account = value.db.create_budget_account(run.session_id, actor, run.run_id, policy.limits,
        deadline=time.time() + 120, holder=run.holder, generation=run.generation, policy_snapshot=policy.record)
    budget = BudgetRuntime(value.db, account["account_id"], account["root_id"], policy, actor,
                              run.holder, run.generation, account["deadline"], run.agent)
    value.runs["primary"] = replace(run, budget=budget)
    return value


def rows(harness):
    with harness.db._runtime_read() as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM budget_reservations")]


@pytest.fixture
def protocol_server():
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    from tests.agent.test_memory_mcp_provider import _SCHEMA
    seen = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_HEAD(self):
            seen.append(({"method": "HEAD"}, self.headers.get("Authorization")))
            self.send_error(405)

        def do_GET(self):
            seen.append(({"method": "GET"}, self.headers.get("Authorization")))
            self.send_error(405)

        def do_POST(self):
            row = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append((row, self.headers.get("Authorization")))
            method = row["method"]
            if method == "notifications/initialized":
                self.send_response(202)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if method == "initialize":
                result = {"protocolVersion": row["params"]["protocolVersion"],
                          "capabilities": {"tools": {}},
                          "serverInfo": {"name": "fixture", "version": "1"}}
            elif method == "tools/list":
                result = {"tools": [{"name": "read", "description": "fixture",
                                    "inputSchema": _SCHEMA, "annotations": {"readOnlyHint": True}}]}
            elif method == "tools/call":
                result = {"content": [{"type": "text", "text": "local protocol response"}], "isError": False}
            else:
                result = {}
            data = json.dumps({"jsonrpc": "2.0", "id": row["id"], "result": result}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/mcp", seen
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_real_sdk_negotiation_discovery_and_read_charge_every_http_post(harness, protocol_server):
    from tools import mcp_tool as core
    from tools.agent_policy_gate import bind_mcp_connection
    endpoint, seen = protocol_server
    with harness.scope():
        server = core.MCPServerTask("public")
        bind_mcp_connection(server)
        from tools.mcp_tool_config import _load_mcp_config
        server._config = _load_mcp_config()["public"]
        async def read_and_stop():
            response = await server.session.call_tool("read", {"query": "fixture", "token_budget": 20,
                                                             "include_links": False})
            assert response.content[0].text == "local protocol response"
            return "shutdown"
        server._wait_for_lifecycle_event = read_and_stop
        async def drive():
            await server._preflight_content_type(endpoint, headers=server._config["headers"])
            return await server._run_http(server._config)
        asyncio.run(drive())
        assert [row["method"] for row, _ in seen] == [
            "initialize", "notifications/initialized", "tools/list", "tools/call"]
        assert all(token == "Bearer synthetic-test-only" for _, token in seen)
        reservations = rows(harness)
        assert len(reservations) == len(seen)
        assert all(row["settlement_state"] == "settled" for row in reservations)


def test_read_recall_uses_real_handler_and_each_post_has_one_ledger_charge(harness):
    from agent.memory_mcp_provider import MCPContextPackProvider
    seen = []
    with harness.scope():
        def respond(request):
            seen.append(json.loads(request.content))
            return httpx.Response(200, json={"context": "Budgeted fixture", "complete": True,
                "usage": "confirm_first", "relevance_band": "strong"})
        transports = []
        server = live_server(prepare=lambda value: transports.append(
            wrap_transport(httpx, httpx.MockTransport(respond), value, CONTRACT)))
        transport = transports[0]
        async def call_tool(name, arguments):
            from types import SimpleNamespace
            async with httpx.AsyncClient(transport=transport) as client:
                response = await client.post(server._config["url"], json={"jsonrpc": "2.0", "id": 1,
                    "method": "tools/call", "params": {"name": name, "arguments": arguments}})
            return SimpleNamespace(content=[], isError=False, structuredContent=response.json())
        server.session.call_tool.side_effect = call_tool
        provider = MCPContextPackProvider(harness.primary, harness.raw["memory"]["personal_mcp"])
        from agent.budget_account import budget_tool_scope
        with budget_tool_scope(harness.runs["primary"], "mcp__public__read"):
            assert "Budgeted fixture" in provider.prefetch("What matters?")
        assert provider.health()["status"] == "ready"
        assert len(seen) == 1 and seen[0]["params"]["arguments"]["include_links"] is False
        reserved = rows(harness)
        assert len(reserved) == 1 and reserved[0]["settlement_state"] == "settled"
        used = json.loads(reserved[0]["consumed_json"])
        assert used["attempts"] == 1 and used["wall_ms"] > 0 and used["tokens"] == 0
        assert not any(json.loads(reserved[0]["held_json"]).values())


@pytest.mark.parametrize("failure", ["write", "unknown_method", "oversize", "compressed", "timeout"])
def test_unsupported_requests_send_nothing_and_uncertain_reads_retain_capacity(harness, failure):
    from tools.mcp_tool_policy import MCPUnsupported
    seen = []
    with harness.scope():
        async def respond(request):
            seen.append(request)
            if failure == "timeout":
                await asyncio.Event().wait()
            if failure == "compressed":
                return httpx.Response(200, stream=httpx.ByteStream(b"untrusted"), headers={"Content-Encoding": "gzip"})
            return httpx.Response(200, content=b"x" * (CONTRACT["max_response_bytes"] + 1))
        transports = []
        server = live_server(prepare=lambda value: transports.append(
            wrap_transport(httpx, httpx.MockTransport(respond), value, CONTRACT)))
        transport = transports[0]
        async def perform():
            async with httpx.AsyncClient(transport=transport) as client:
                body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                        "params": {"name": "write" if failure == "write" else "read", "arguments": {}}}
                if failure == "unknown_method":
                    body.pop("method")
                await client.post(server._config["url"], json=body)
        with pytest.raises((PermissionError, TimeoutError, MCPUnsupported)):
            asyncio.run(perform())
        reserved = rows(harness)
        if failure in {"write", "unknown_method"}:
            assert not seen and not reserved
        else:
            assert len(seen) == len(reserved) == 1
            assert reserved[0]["settlement_state"] == "unknown"
            assert json.loads(reserved[0]["held_json"])["executor_slots"] == 1
            assert harness.runs["primary"].budget.blocked_reason is not None
