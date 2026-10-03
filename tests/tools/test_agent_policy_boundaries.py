"""Real identity dispatch boundaries and the BE05 certified-adapter floor.

Authority comes from real config parsing and real ContextVars. Only the external
RPC/transport is a harmless stub; registry, discovery, bridge and hook paths are real.
"""
import asyncio
import copy
import json
from types import SimpleNamespace

import pytest

from tests.tools.test_capability_broker import live_runtime  # noqa: F401

from agent.agent_identity import resolve_agent_context
from agent.runtime_context import bind_agent_context, current_agent_context
from hermes_constants import reset_hermes_home_override, set_hermes_home_override
from tools.agent_policy_gate import authorize_mcp, authorize_tool, bind_mcp_connection
from tests.tools.test_mcp_agent_trust_lifecycle import fixture as mcp_runtime  # noqa: F401


def config(active="primary", *, allowed=(), mcp=None):
    primary = {"policy_version": 1, "role": "primary", "memory_backend": "personal_mcp",
               "allowed_tools": list(allowed), "mcp_grants": mcp or {}}
    if mcp:
        primary["mcp_policies"] = {server: {
            "policy_version": 1, "transport": "streamable_http", "endpoint": "https://fixture.invalid/mcp",
            "tool_allowlist": [tool for tool in granted if not tool.startswith(("resources/", "prompts/"))],
            "read_scopes": {op: (True if op.endswith("/list") else
                                    ["fixture://allowed"] if op == "resources/read" else ["allowed"])
                            for op in granted if op.startswith(("resources/", "prompts/"))},
        } for server, granted in mcp.items()}
    specialist = {"policy_version": 1, "role": "specialist", "memory_backend": "builtin",
                  "allowed_tools": list(allowed), "mcp_grants": {}}
    return {"agent_identity": {"schema_version": 1, "principal_id": "owner", "profile_id": "profile",
            "primary_agent_id": "primary", "active_agent_id": active,
            "personal_mcp_servers": ["vault"], "agents": {"primary": primary, "specialist": specialist}}}


@pytest.fixture
def home(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    token = set_hermes_home_override(str(home))
    yield home
    reset_hermes_home_override(token)


def context(home, cfg, session="session"):
    (home / "config.yaml").write_text(json.dumps(cfg), encoding="utf-8")
    return resolve_agent_context(cfg, session_id=session, profile_home=home)


@pytest.fixture
def mcp_state(monkeypatch):
    from tools import mcp_tool as core
    maps = ("_servers", "_server_scope_keys", "_server_tool_scopes", "_server_connect_errors",
            "_server_connect_retry_after", "_server_connect_failures", "_lazy_server_configs",
            "_tool_read_only_hints", "_server_error_counts", "_server_breaker_opened_at",
            "_server_trust_levels", "_mcp_tool_server_names")
    for name in maps:
        monkeypatch.setattr(core, name, {})
    monkeypatch.setattr(core, "_server_connecting", set())
    monkeypatch.setattr(core, "_parallel_safe_servers", set())
    return core


def test_unknown_and_hidden_direct_dispatch_deny_before_hooks(home, monkeypatch):
    import model_tools
    from tools.registry import ToolRegistry
    ctx = context(home, config(allowed=["clarify"]))
    hits = []
    reg = ToolRegistry()
    reg.register(name="hidden", toolset="fixture", schema={"name": "hidden"},
                 handler=lambda args: hits.append(args) or '{}')
    monkeypatch.setattr("hermes_cli.plugins._dispatch_pre_tool_call_hooks",
                        lambda *a, **kw: pytest.fail("denied call must not reach hooks"))
    with bind_agent_context(ctx):
        assert json.loads(reg.dispatch("hidden", {}))["status"] == "denied"
        assert json.loads(model_tools.handle_function_call("hidden", {}))["status"] == "denied"
        assert json.loads(model_tools.handle_function_call("never_registered", {}))["status"] == "denied"
        assert reg.get_definitions({"hidden"}) == []
    assert hits == []


@pytest.mark.parametrize("name", ["terminal", "execute_code", "read_file", "write_file", "browser_navigate",
                                  "web_extract", "manage_connections", "plugin_fixture", "memory"])
def test_nonprimary_execution_requires_certified_boundary_even_if_granted(home, name):
    ctx = context(home, config("specialist", allowed=[name]))
    with bind_agent_context(ctx):
        if name in {"execute_code", "memory"}:
            import tools.code_execution_tool  # noqa: F401
            import tools.memory_tool  # noqa: F401
            from tools.registry import registry
            assert authorize_tool(name) is None
            # BE08 certifies the specialist's own built-in memory schema, just
            # as BE05 certifies execute_code. Neither is execution authority.
            arguments = ({"code": "print('unreachable')"} if name == "execute_code" else
                         {"action": "add", "target": "memory", "content": "must not be written"})
            before_paths = set(home.rglob("*"))
            denied = json.loads(registry.dispatch(name, arguments))
            assert "durable command" in denied["error"]
            assert set(home.rglob("*")) == before_paths
            # A built-in memory grant never creates a primary personal-MCP grant.
            assert json.loads(authorize_mcp("vault", "read"))["status"] == "denied"
        else:
            denied = json.loads(authorize_tool(name))
            assert denied["status"] == "unsupported"
            assert "BE05" in denied["message"]


def test_policy_bound_schema_cache_is_primary_specialist_primary(home, monkeypatch):
    import model_tools
    import tools.todo_tool  # noqa: F401
    name = "todo_list"
    monkeypatch.setattr(model_tools, "_select_tool_names", lambda *a, **k: {name})
    cfg = config(allowed=[name])
    cfg["agent_identity"]["agents"]["specialist"]["allowed_tools"] = []
    first = context(home, cfg)
    cfg2 = copy.deepcopy(cfg)
    cfg2["agent_identity"]["active_agent_id"] = "specialist"
    second = context(home, cfg2, "other")
    for ctx, expected in [(first, [name]), (second, []), (first, [name])]:
        with bind_agent_context(ctx):
            defs = model_tools.get_tool_definitions(quiet_mode=True, skip_tool_search_assembly=True)
            assert [d["function"]["name"] for d in defs] == expected


def test_strict_boot_defers_before_config_credentials_or_sdk(home, monkeypatch):
    from tools import mcp_tool_discovery as discovery
    context(home, config(mcp={"vault": ["read"]}))
    monkeypatch.setattr(discovery._config, "_load_mcp_config", lambda: pytest.fail("must not render credentials at boot"))
    monkeypatch.setattr("tools.mcp_tool._ensure_mcp_sdk", lambda: pytest.fail("must not start MCP at boot"))
    assert current_agent_context() is None
    assert discovery.discover_mcp_tools() == []
    assert discovery.register_mcp_servers({"vault": {"command": "false"}}) == []
    with pytest.raises(PermissionError, match="Trusted agent context"):
        asyncio.run(discovery._connect_server("vault", {"command": "false"}))


def test_missing_model_tool_grant_denies_raw_handler(home, monkeypatch):
    from tools import mcp_tool_handlers as handlers
    ctx = context(home, config(mcp={"vault": ["read"]}))
    monkeypatch.setattr(handlers, "_acquire_call_server", lambda *a: pytest.fail("must not acquire transport"))
    with bind_agent_context(ctx):
        assert json.loads(handlers._make_tool_handler("vault", "read", 1)({}))["status"] == "denied"


@pytest.mark.parametrize("factory, op, name, args", [
    ("_make_read_resource_handler", "resources/read", "read_resource", {"uri": "fixture://private"}),
    ("_make_list_resources_handler", "resources/list", "list_resources", {}),
    ("_make_get_prompt_handler", "prompts/get", "get_prompt", {"name": "private"}),
    ("_make_list_prompts_handler", "prompts/list", "list_prompts", {}),
])
def test_utility_operations_require_explicit_grant_before_transport(home, monkeypatch, factory, op, name, args):
    from tools import mcp_tool_handlers as handlers
    ctx = context(home, config(allowed=["mcp__vault__" + name], mcp={"vault": ["read"]}))
    monkeypatch.setattr(handlers, "_acquire_call_server", lambda *a: pytest.fail("must not acquire transport"))
    with bind_agent_context(ctx):
        assert json.loads(getattr(handlers, factory)("vault", 1)(args))["status"] == "denied"


def test_primary_call_and_utility_use_only_matching_owned_connection(mcp_runtime):
    from tools.mcp_tool_discovery import _get_connected_server_for_call
    from tools.mcp_tool_scope import _server_key
    from tools.registry import registry
    from tests.tools.test_mcp_agent_trust_lifecycle import server
    runtime = mcp_runtime
    with runtime.scope():
        live = server()
        handler = registry.get_entry("mcp__public__read").handler
        resource = registry.get_entry("mcp__public__read_resource").handler
        assert "error" not in json.loads(handler({"x": 1}))
        assert "error" not in json.loads(resource({"uri": "fixture://allowed"}))
        assert _get_connected_server_for_call("public") is live
    with runtime.scope(runtime.child):
        assert _get_connected_server_for_call("public") is None
        assert "error" in json.loads(handler({}))
    with runtime.scope():
        assert _get_connected_server_for_call("public") is live
        from tools.mcp_tool import _servers
        assert _servers[_server_key("public")] is live
    live.session.call_tool.assert_awaited_once_with("read", arguments={"x": 1})
    live.session.read_resource.assert_awaited_once_with("fixture://allowed")


def test_nonprimary_cannot_connect_or_wake_same_profile_personal_server(home, monkeypatch, mcp_state):
    from tools import mcp_tool_discovery as discovery, mcp_tool_handlers as handlers
    from tools.mcp_tool_scope import _server_key
    cfg = config(allowed=["mcp__vault__read"], mcp={"vault": ["read"]})
    primary = context(home, cfg)
    server = SimpleNamespace(name="vault", session=None, _is_recycled_stdio=lambda: True)
    with bind_agent_context(primary):
        bind_mcp_connection(server)
        discovery._adopt_server("vault", server)
    cfg["agent_identity"]["active_agent_id"] = "specialist"
    specialist = context(home, cfg, "specialist")
    monkeypatch.setattr(discovery._loop, "_signal_reconnect", lambda *a: pytest.fail("no wake"))
    monkeypatch.setattr(discovery._loop, "_running_loop", lambda: pytest.fail("no loop access"))
    with bind_agent_context(specialist):
        assert discovery._get_connected_server_for_call("vault") is None
        assert discovery._request_lazy_reconnect("vault", server) is False
        assert discovery._select_new_servers({"vault": {"command": "false"}}) == {}
        assert json.loads(handlers._make_tool_handler("vault", "read", 1)({}))["status"] in {"denied", "unsupported"}
        with pytest.raises(PermissionError):
            asyncio.run(discovery._connect_server("vault", {"command": "false"}))


def test_unowned_connection_cannot_be_adopted_by_strict_primary(home, mcp_state):
    from tools import mcp_tool_discovery as discovery
    ctx = context(home, config(mcp={"vault": ["read"]}))
    unowned = SimpleNamespace(name="vault", session=object())
    with bind_agent_context(ctx), pytest.raises(PermissionError, match="ownership"):
        discovery._adopt_server("vault", unowned)


def test_refresh_cannot_carry_disallowed_pinned_or_injected_schema(home, monkeypatch):
    import model_tools
    from tools import mcp_tool_agent as refresh
    ctx = context(home, config("specialist", allowed=["todo_list", "terminal"]))
    def schema(name):
        return {"type": "function", "function": {"name": name, "parameters": {}}}
    agent = SimpleNamespace(runtime_context=ctx, tools=[schema("terminal")], valid_tool_names={"terminal"})
    monkeypatch.setattr(model_tools, "get_tool_definitions", lambda **kw: [schema("terminal"), schema("todo_list")])
    refresh.refresh_agent_mcp_tools(agent, preserve_prefix=True)
    assert agent.valid_tool_names == {"todo_list"}
    refresh.restore_agent_tool_prefix(agent, [schema("terminal"), schema("todo_list")])
    assert agent.valid_tool_names == {"todo_list"}


def test_mcp_config_filters_before_interpolating_denied_credentials(home, monkeypatch):
    from tools import mcp_tool_config as mcp_config
    from agent.identity_lifecycle import agent_runtime_scope
    cfg = config(mcp={"vault": ["read"]})
    cfg["mcp_servers"] = {
        "vault": {"url": "https://example.invalid/permitted"},
        "foreign": {"url": "https://example.invalid/${FORBIDDEN_CREDENTIAL}"},
    }
    ctx = context(home, cfg)
    monkeypatch.setattr("hermes_cli.env_loader.load_hermes_dotenv", lambda: None)
    monkeypatch.setattr(mcp_config, "_portable_mcp_servers", lambda servers: None)
    original = mcp_config._interpolate_env_vars
    def guarded(value):
        assert "FORBIDDEN_CREDENTIAL" not in str(value)
        return original(value)
    monkeypatch.setattr(mcp_config, "_interpolate_env_vars", guarded)
    with agent_runtime_scope(ctx):
        assert set(mcp_config._load_mcp_config()) == {"vault"}


def test_nonprimary_discovery_does_not_hydrate_credentials(home, monkeypatch):
    from tools import mcp_tool_discovery as discovery
    ctx = context(home, config("specialist"))
    monkeypatch.setattr(discovery, "_owner_secret_scope", lambda: pytest.fail("must not hydrate secrets"))
    with bind_agent_context(ctx):
        assert discovery.discover_mcp_tools() == []


def test_hook_exception_frozen_mutation_and_duplicate_callback_do_not_bypass(live_runtime, monkeypatch):
    from agent import tool_executor as executor, relay_tools
    from dataclasses import FrozenInstanceError
    from tools.todo_tool import TodoStore, todo_tool
    ctx, agent = live_runtime.context, live_runtime.run.agent
    agent.session_id, agent._current_turn_id = "session", ""
    agent._tool_guardrails = SimpleNamespace(before_call=lambda *a: SimpleNamespace(allows_execution=True))
    store = TodoStore()
    calls, mutation_errors, duplicate_errors = [], [], []
    monkeypatch.setattr(executor, "_begin_tool_execution", lambda *a, **kw: None)
    monkeypatch.setattr(executor, "_run_with_activity_heartbeat", lambda agent, name, fn: fn())
    monkeypatch.setattr("hermes_cli.middleware.apply_tool_request_middleware",
                        lambda name, args, **kw: SimpleNamespace(payload=args, trace=[]))
    monkeypatch.setattr("hermes_cli.middleware.run_tool_execution_middleware",
                        lambda name, args, callback, **kw: callback(args))
    def broken_hook(*args, **kwargs):
        try:
            ctx.policy.allowed_tools = frozenset({"terminal"})
        except FrozenInstanceError:
            mutation_errors.append(True)
        raise RuntimeError("plugin failure must not skip immutable authority")
    monkeypatch.setattr("hermes_cli.plugins._dispatch_pre_tool_call_hooks", broken_hook)
    todos = [{"id": "1", "content": "approved local task", "status": "completed"}]
    def twice(name, args, callback, **kw):
        result = callback({"todos": todos})
        try:
            callback({"command": "must not execute"})
        except RuntimeError as exc:
            duplicate_errors.append(str(exc))
        return result, args
    monkeypatch.setattr(relay_tools, "execute", twice)
    def execute(args):
        calls.append((args, current_agent_context()))
        return todo_tool(todos=args["todos"], store=store)
    result = executor._run_agent_tool_execution_middleware(
        agent, function_name="todo_list", function_args={}, effective_task_id="task", tool_call_id="call",
        execute=execute)
    assert "error" not in json.loads(result.result)
    assert store.read() == todos and calls == [({"todos": todos}, ctx)]
    assert mutation_errors == [True] and len(duplicate_errors) == 1
    denied = executor._run_agent_tool_execution_middleware(
        agent, function_name="terminal", function_args={}, effective_task_id="task", tool_call_id="denied",
        execute=lambda args: pytest.fail("denied tool must not dispatch"))
    assert denied.blocked
    assert len(mutation_errors) == 1  # denied before plugins or relay callbacks


def test_nonpersonal_mcp_is_explicitly_unsupported_for_nonprimary(home):
    cfg = config("specialist")
    cfg["agent_identity"]["agents"]["specialist"]["mcp_grants"] = {"public": ["read"]}
    ctx = context(home, cfg)
    with bind_agent_context(ctx):
        assert json.loads(authorize_mcp("public", "read"))["status"] == "unsupported"


def test_lifecycle_reconnect_rechecks_connection_ownership_before_transport(home):
    from tools.mcp_tool_server_run import MCPServerRunMixin
    ctx = context(home, config(mcp={"vault": ["read"]}))
    calls = []
    class Server(MCPServerRunMixin):
        name = "vault"
        session = None
        async def _prepare_run(self, config):
            return True
        def _is_http(self):
            return True
        async def _run_http(self, config):
            calls.append("transport")
            self._agent_policy_owner = None  # ownership invalidated before reconnect
            return "reconnect"
        async def _on_clean_return(self, reason, budget):
            return True
        def _publish_error(self, error):
            calls.append(type(error).__name__)
        def _deregister_tools(self):
            calls.append("deregister")
    server = Server()
    with bind_agent_context(ctx):
        bind_mcp_connection(server)
        asyncio.run(server.run({"url": "https://fixture.invalid/mcp"}))
    assert calls == ["transport", "PermissionError", "deregister"]


def test_tool_call_bridge_cannot_resolve_hidden_unauthorized_tool(live_runtime):
    import model_tools
    # The bridge itself is explicitly granted. Its inner terminal target is not.
    assert authorize_tool("tool_call") is None
    result = model_tools.handle_function_call("tool_call", {"name": "terminal", "arguments": {"command": "false"}})
    assert "error" in json.loads(result)
