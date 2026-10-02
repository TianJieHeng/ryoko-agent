"""Agent-owned MCP contracts against real policy, registry, SQLite and SDK seams.

All remote calls are harmless in-memory sessions/transports. No server or credentials
are installed; fixture identifiers are not a personal harness deployment contract.
"""
import asyncio
import contextvars
import copy
import json
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from openai import OpenAI

from agent.agent_identity import resolve_agent_context, parse_agent_identity_config, IdentityPolicyError
from agent.identity_lifecycle import agent_runtime_scope
from agent.runtime_commands import RuntimeRun, bind_runtime_run, reset_runtime_run
from hermes_state import SessionDB
from tools import mcp_tool as core, mcp_tool_policy as policy
from tools.agent_policy_gate import bind_mcp_connection, authorize_mcp
from tools.mcp_tool_registration import _register_server_tools
from tools.mcp_tool_scope import _server_key, _resolve_server_key
from tools.registry import registry


def raw_config():
    grant = {"policy_version": 1, "transport": "streamable_http",
             "endpoint": "https://fixture.invalid/mcp", "tool_allowlist": ["read", "write"],
             "read_scopes": {"resources/read": ["fixture://allowed"], "prompts/get": ["allowed"]},
             "read_only_tools": ["read"], "sampling_limits": {"enabled": False},
             "schema_digests": {
                 "read": policy.schema_digest(SimpleNamespace(name="read", description="fixture",
                     inputSchema={"type": "object", "properties": {}}, annotations={"readOnlyHint": True})),
                 "write": policy.schema_digest(SimpleNamespace(name="write", description="fixture mutation",
                     inputSchema={"type": "object"}, annotations={"readOnlyHint": True})),
             }}
    common = {"policy_version": 1, "allowed_tools": ["mcp__public__read", "mcp__public__read_resource",
               "mcp__public__get_prompt", "mcp__public__write"], "mcp_grants": {"public": ["read", "write", "resources/read", "prompts/get"]},
              "mcp_policies": {"public": grant}, "recipient_plan": {"schema_version": 1,
              "envelope": "declared", "grants": [{"recipient_id": "public", "purpose": "mcp",
                  "endpoint": "https://fixture.invalid/mcp", "transport": "httpx"}]}}
    primary = {**copy.deepcopy(common), "role": "primary", "memory_backend": "personal_mcp"}
    specialist = {**copy.deepcopy(common), "role": "specialist", "memory_backend": "builtin"}
    child = {**copy.deepcopy(common), "role": "child", "memory_backend": "builtin"}
    return {"agent_identity": {"schema_version": 1, "principal_id": "fixture_owner", "profile_id": "fixture_profile",
              "primary_agent_id": "primary", "active_agent_id": "primary", "personal_mcp_servers": ["personal"],
              "personal_secret_refs": ["PERSONAL_TOKEN"], "agents": {"primary": primary, "specialist": specialist},
              "child_policy": child}, "mcp_servers": {"public": {"url": grant["endpoint"]}}}


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    raw = raw_config()
    (tmp_path / "config.yaml").write_text(json.dumps(raw))
    primary = resolve_agent_context(raw, session_id="primary_session", profile_home=tmp_path)
    child = resolve_agent_context(raw, session_id="child_session", profile_home=tmp_path,
                                  parent_context=primary, is_child=True)
    parsed = parse_agent_identity_config(raw)
    specialist_policy = parsed.agents["specialist"]
    specialist = replace(primary, policy=specialist_policy, identity=replace(primary.identity,
                          agent_id="specialist", session_id="specialist_session", policy_digest=specialist_policy.digest))
    maps = ("_servers", "_server_scope_keys", "_server_tool_scopes", "_server_connect_errors",
            "_server_connect_retry_after", "_server_connect_failures", "_lazy_server_configs",
            "_tool_read_only_hints", "_tool_operator_read_only", "_server_error_counts", "_server_breaker_opened_at",
            "_server_trust_levels", "_mcp_tool_server_names")
    for name in maps:
        monkeypatch.setattr(core, name, {})
    monkeypatch.setattr(core, "_server_connecting", set())
    monkeypatch.setattr(core, "_parallel_safe_servers", set())
    monkeypatch.setattr(registry, "_scoped_tools", {})
    monkeypatch.setattr(registry, "_catalog_metadata_cache", {})
    db = SessionDB(tmp_path / "state.db")
    client = OpenAI(api_key="harmless-fixture", base_url="https://fixture.invalid/v1",
                    http_client=httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(500))))
    runs = {}
    for ctx in (primary, specialist, child):
        sid = ctx.identity.session_id
        db.create_session(sid, source="cli")
        db.claim_session_agent_identity(sid, ctx.identity.to_record())
        actor = {"principal_id": ctx.identity.principal_id, "profile_id": ctx.identity.profile_id,
                 "agent_id": ctx.identity.agent_id}
        command = {"schema_version": 1, "command_id": sid, "idempotency_key": sid,
                   "expected_revision": None, "operation": "submit", "payload": {"text": "fixture"},
                   "identity_binding": actor}
        receipt = db.submit_runtime_command(sid, actor=actor, command=command)
        assert db.acquire_session_turn_lease(sid, "holder", wait_seconds=0)
        generation = db.get_session_turn_lease(sid)["generation"]
        assert db.claim_runtime_command(sid, sid, holder="holder", generation=generation)
        agent = SimpleNamespace(api_mode="chat_completions", provider="openai", client=client, runtime_context=ctx)
        runs[ctx.identity.agent_id] = RuntimeRun(agent, db, sid, sid, receipt["run_id"], "holder", generation, ctx)
    @contextmanager
    def scope(ctx=primary):
        run = runs[ctx.identity.agent_id]
        with agent_runtime_scope(ctx):
            token = bind_runtime_run(run)
            try:
                yield
            finally:
                reset_runtime_run(token, run)
    monkeypatch.setattr("tools.mcp_tool_loop._run_on_mcp_loop", lambda fn, **kwargs: asyncio.run(fn()))
    def approve(action):
        from tools.capability_broker import preview_action, resolve_approval
        preview = preview_action(action)
        resolve_approval(preview, preview.approval_digest, "once")
        return preview
    monkeypatch.setattr("tools.capability_approval.request_exact_approval", approve)
    yield SimpleNamespace(home=tmp_path, raw=raw, primary=primary, specialist=specialist,
                          child=child, scope=scope, db=db, runs=runs)
    client.close()
    db.close()


def server(description="fixture"):
    instance = core.MCPServerTask("public")
    bind_mcp_connection(instance)
    instance._config = {"url": "https://fixture.invalid/mcp"}
    instance.session = SimpleNamespace(
        call_tool=AsyncMock(return_value=SimpleNamespace(content=[], isError=False, structuredContent=None)),
        read_resource=AsyncMock(return_value=SimpleNamespace(contents=[])),
        get_prompt=AsyncMock(return_value=SimpleNamespace(messages=[], description="fixture")))
    instance._tools = [SimpleNamespace(name="read", description=description,
                         inputSchema={"type": "object", "properties": {}}, annotations={"readOnlyHint": True})]
    from tools.mcp_tool_discovery import _adopt_server
    _adopt_server("public", instance)
    instance._registered_tool_names = _register_server_tools("public", instance, instance._config)
    return instance


def test_real_same_profile_primary_specialist_child_registry_and_pools(fixture):
    owned = []
    for ctx in (fixture.primary, fixture.specialist, fixture.child):
        with fixture.scope(ctx):
            assert registry.get_entry("mcp__public__read") is None
            live = server()
            key = _server_key("public")
            owned.append((ctx, live, key, registry.get_entry("mcp__public__read").handler))
            assert json.loads(registry.dispatch("mcp__public__read", {})) == {"result": ""}
            live.session.call_tool.assert_awaited_once()
    assert len({item[2] for item in owned}) == 3
    for ctx, live, key, handler in (owned[0], owned[1], owned[0]):
        with fixture.scope(ctx):
            assert _resolve_server_key("public") == key
            assert core._servers[key] is live
            assert registry.get_entry("mcp__public__read").handler is handler
            assert authorize_mcp("public", "read", connection=owned[2][1]) is not None


def test_direct_utility_targets_and_raw_handlers_are_authorized(fixture):
    with fixture.scope():
        live = server()
        resource = registry.get_entry("mcp__public__read_resource").handler
        assert "error" not in json.loads(resource({"uri": "fixture://allowed"}))
        assert "error" in json.loads(resource({"uri": "fixture://private"}))
        live.session.read_resource.assert_awaited_once_with("fixture://allowed")
        prompt = registry.get_entry("mcp__public__get_prompt").handler
        assert "error" not in json.loads(prompt({"name": "allowed"}))
        assert "error" in json.loads(prompt({"name": "private"}))
        live.session.get_prompt.assert_awaited_once()


def test_personal_server_never_visible_or_callable_to_specialist_or_child(fixture):
    for ctx in (fixture.specialist, fixture.child):
        with fixture.scope(ctx):
            assert authorize_mcp("personal", "read") is not None
            from tools.mcp_tool_handlers import _make_read_resource_handler, _make_tool_handler
            assert json.loads(_make_tool_handler("personal", "read", 1)({}))["status"] == "denied"
            assert "error" in json.loads(_make_read_resource_handler("personal", 1)({"uri": "fixture://allowed"}))
            from tools.mcp_tool_discovery import _connect_server
            with pytest.raises(PermissionError):
                asyncio.run(_connect_server("personal", {"url": "https://fixture.invalid/mcp"}))


def test_refresh_rejects_changed_schema_and_cannot_reconnect_around_revocation(fixture):
    with fixture.scope():
        live = server()
        old = registry.get_entry("mcp__public__read").handler
        changed = copy.copy(live._tools[0])
        changed.description = "new instructions and parameters"
        with pytest.raises(PermissionError, match="reauthorization"):
            policy.validate_schemas(live, [changed])
        assert registry.get_entry("mcp__public__read") is None
        assert "error" in json.loads(old({}))
        assert authorize_mcp("public", connection=live) is not None
        live.session.call_tool.assert_not_awaited()


def test_live_config_revocation_denies_old_discovery_and_transport(fixture):
    with fixture.scope():
        live = server()
        fixture.raw["agent_identity"]["agents"]["primary"]["mcp_grants"] = {}
        fixture.raw["agent_identity"]["agents"]["primary"]["mcp_policies"] = {}
        (fixture.home / "config.yaml").write_text(json.dumps(fixture.raw))
        assert authorize_mcp("public", connection=live) is not None
        from tools.mcp_tool_discovery import discover_mcp_tools
        assert discover_mcp_tools() == []
        with pytest.raises(PermissionError, match="stale|revoked"):
            policy.require_transport("public", live._config, connection=live)


def test_endpoint_changed_namespace_hides_previous_pool(fixture):
    with fixture.scope():
        live = server()
        old_key = _server_key("public")
        fixture.raw["mcp_servers"]["public"]["url"] = "https://other.invalid/mcp"
        (fixture.home / "config.yaml").write_text(json.dumps(fixture.raw))
        assert _server_key("public") != old_key
        assert registry.get_entry("mcp__public__read") is None
        assert authorize_mcp("public", connection=live) is not None


def test_shared_profile_lazy_cache_is_not_authority(fixture):
    from tools.mcp_tool_registration import _register_from_cache_sync
    from tools.mcp_tool_discovery import _register_lazy_from_cache
    with fixture.scope():
        assert _register_from_cache_sync("public", {}, {"tools": []}) == []
        cfg = {"public": {"url": "https://fixture.invalid/mcp", "lazy": True}}
        assert _register_lazy_from_cache(cfg) == (cfg, 0, 0)


@pytest.mark.parametrize("config", [{"command": "false"}, {"url": "https://fixture.invalid/mcp", "transport": "sse"},
    {"url": "https://fixture.invalid/mcp", "auth": "oauth"}, {"url": "https://fixture.invalid/mcp", "ssl_verify": False}])
def test_uncertified_transports_fail_before_spawn_or_credentials(fixture, monkeypatch, config):
    from tools.mcp_tool_discovery import _connect_server
    monkeypatch.setattr(core, "MCPServerTask", lambda *a: pytest.fail("must fail before transport allocation"))
    with fixture.scope(), pytest.raises(PermissionError, match="unsupported"):
        asyncio.run(_connect_server("public", config))


def test_credential_free_endpoint_rejects_ambient_or_live_headers(fixture):
    with fixture.scope():
        with pytest.raises(PermissionError, match="bearer"):
            policy.require_http_inputs("public", {"url": "https://fixture.invalid/mcp"},
                                       "https://fixture.invalid/mcp", {"Authorization": "Bearer personal-fixture"})
        auth = policy.require_http_inputs("public", {"url": "https://fixture.invalid/mcp"},
                                          "https://fixture.invalid/mcp", {})
        with pytest.raises(PermissionError):
            auth.check_url("https://other.invalid/mcp")


def test_strict_sampling_and_sdk_mrtr_deny_without_auxiliary_call(fixture, monkeypatch):
    from tools.mcp_tool_sampling import SamplingHandler
    core._ensure_mcp_sdk()
    monkeypatch.setattr("agent.auxiliary_client.call_llm", lambda **kw: pytest.fail("no certified sampling adapter"))
    with fixture.scope():
        captured = contextvars.copy_context()
        handler = SamplingHandler("public", {}, call_context=lambda: captured, policy_owned=True)
        # The modern SDK dispatch table and legacy callbacks both reach this handler.
        from mcp import ClientSession
        from mcp.types import CreateMessageRequest, CreateMessageRequestParams, SamplingMessage, TextContent
        params = CreateMessageRequestParams(messages=[SamplingMessage(role="user", content=TextContent(type="text", text="x"))], maxTokens=10)
        session = ClientSession(object(), object(), sampling_callback=handler)
        direct = asyncio.run(handler(None, params))
        modern = asyncio.run(session.dispatch_input_request(None, CreateMessageRequest(params=params)))
        assert "unsupported" in direct.message
        assert "unsupported" in modern.message


def test_bounded_mode_direct_call_cannot_bypass_existing_tool_budget(fixture, monkeypatch):
    with fixture.scope():
        live = server()
        monkeypatch.setattr("agent.budget_account.current_budget", lambda: object())
        result = json.loads(registry.get_entry("mcp__public__read").handler({}))
        assert "certified BE03" in result["error"]
        live.session.call_tool.assert_not_awaited()


def test_schema_and_recipient_defaults_preserve_existing_policy_digest():
    from agent.agent_identity import AgentPolicy
    basic = AgentPolicy(1, "primary", "personal_mcp")
    assert "mcp_policies" not in basic.to_record()
    assert "recipient_plan" not in basic.to_record()
    assert basic.digest == AgentPolicy(1, "primary", "personal_mcp", mcp_policies={}, recipient_plan=None).digest


def test_invalid_or_unenforced_sampling_limits_are_not_accepted():
    cfg = raw_config()
    cfg["agent_identity"]["agents"]["primary"]["mcp_policies"]["public"]["sampling_limits"] = {"enabled": True, "max_tokens": 1}
    with pytest.raises(IdentityPolicyError, match="sampling/MRTR"):
        parse_agent_identity_config(cfg)


def test_real_mcp_mutation_requires_semantic_adapter_before_any_approval(fixture, monkeypatch):
    from tools import capability_broker as broker, capability_approval, approval
    # Restore the real UI request function, overridden only for ordinary fixture calls.
    import importlib
    importlib.reload(capability_approval)
    with fixture.scope():
        live = server()
        live._tools.append(SimpleNamespace(name="write", description="fixture mutation",
                           inputSchema={"type": "object"}, annotations={"readOnlyHint": True}))
        live._registered_tool_names = _register_server_tools("public", live, live._config)
        raw = registry.get_entry("mcp__public__write").handler
        monkeypatch.setattr(approval, "_presence", lambda: (None, False, False, False))
        denied = json.loads(raw({"target": "first"}))
        assert denied["status"] == "denied"
        assert denied["error"] == "effect_adapter_unsupported"
        live.session.call_tool.assert_not_awaited()
        shown = []
        def human(command, description, **kw):
            shown.append(command)
            return "once"
        monkeypatch.setattr(approval, "_presence", lambda: (human, True, False, False))
        denied = json.loads(registry.dispatch("mcp__public__write", {"target": "first"}))
        assert denied["error"] == "effect_adapter_unsupported"
        assert shown == []
        live.session.call_tool.assert_not_awaited()
        with pytest.raises(broker.CapabilityDenied, match="no durable semantic"):
            broker.prepare_tool_capability("mcp__public__write", {"target": "first"})
        # A copied old handler cannot borrow a freshly registered schema's authority.
        _register_server_tools("public", live, live._config)
        assert json.loads(raw({"target": "first"}))["error"] == "handler_changed"


def test_prompt_resource_notifications_make_refresh_explicitly_unsupported(fixture):
    core._ensure_mcp_sdk()
    from mcp.types import PromptListChangedNotification, ResourceListChangedNotification
    for notice in (PromptListChangedNotification(), ResourceListChangedNotification()):
        with fixture.scope():
            live = server()
            asyncio.run(live._make_message_handler()(notice))
            assert "refresh unsupported" in live._agent_refresh_blocked
            assert registry.get_entry("mcp__public__read") is None
            assert authorize_mcp("public", connection=live) is not None


def test_strict_same_code_pin_cannot_restore_untrusted_schema_bytes(fixture):
    from tools.mcp_tool_agent import restore_agent_tool_prefix, tool_pin_version
    with fixture.scope():
        live = server()
        entry = registry.get_entry("mcp__public__read")
        fresh = {"type": "function", "function": dict(entry.schema)}
        agent = SimpleNamespace(runtime_context=fixture.primary, tools=[fresh],
                                valid_tool_names={entry.name}, enabled_toolsets=None, disabled_toolsets=None)
        stale = copy.deepcopy(fresh)
        stale["function"]["description"] = "untrusted old policy text"
        restore_agent_tool_prefix(agent, {"version": tool_pin_version(), "tools": [stale]})
        assert agent.tools == [fresh]
        assert agent.tools[0]["function"]["description"] == "fixture"


def test_owned_http_client_blocks_cross_endpoint_redirect_before_second_send(fixture, monkeypatch):
    from contextlib import asynccontextmanager
    received = []
    async def respond(request):
        received.append(str(request.url))
        return httpx.Response(307, headers={"location": "https://outside.invalid/mcp"})
    @asynccontextmanager
    async def sdk_transport(url, http_client, terminate_on_close=True):
        assert terminate_on_close is False
        await http_client.post(url, json={"method": "tools/list"})
        yield (None, None)
    core._ensure_mcp_sdk()
    monkeypatch.setattr(core, "sdk_httpx", lambda: httpx)
    monkeypatch.setattr(core, "streamable_http_client", sdk_transport)
    monkeypatch.setattr(core, "_MCP_NEW_HTTP", True)
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **kw: httpx.MockTransport(respond))
    monkeypatch.setenv("HTTPS_PROXY", "http://unapproved-proxy.invalid")
    with fixture.scope():
        live = server()
        live._agent_recipient_authorization = policy.require_http_inputs(
            "public", live._config, live._config["url"], {}, connection=live)
        async def invoke():
            async with live._streamable_http_transport(live._config["url"], {}, 1, True, None, None, True, set()):
                pytest.fail("the redirect must be blocked")
        with pytest.raises(PermissionError):
            asyncio.run(invoke())
    assert received == ["https://fixture.invalid/mcp"]


def test_granted_credential_rotation_creates_distinct_pool_without_leaking_old_secret(fixture):
    from agent.secret_scope import set_secret_scope, reset_secret_scope
    from agent.runtime_context import bind_agent_context
    from hermes_constants import set_hermes_home_override, reset_hermes_home_override
    raw = copy.deepcopy(fixture.raw)
    owner = raw["agent_identity"]["agents"]["primary"]
    owner["secret_refs"] = ["MCP_FIXTURE_TOKEN"]
    owner["mcp_policies"]["public"]["secret_ref"] = "MCP_FIXTURE_TOKEN"
    (fixture.home / "config.yaml").write_text(json.dumps(raw))
    context = resolve_agent_context(raw, session_id="credential_session", profile_home=fixture.home)
    home_token = set_hermes_home_override(str(fixture.home))
    try:
        scopes = []
        for credential in ("fixture-one", "fixture-two", "fixture-one"):
            token = set_secret_scope({"MCP_FIXTURE_TOKEN": credential}, profile_home=str(fixture.home))
            try:
                with bind_agent_context(context):
                    scopes.append(policy.registry_scope())
                    policy.require_http_inputs("public", {"url": "https://fixture.invalid/mcp"},
                        "https://fixture.invalid/mcp", {"Authorization": "Bearer " + credential})
                    with pytest.raises(PermissionError, match="bearer"):
                        policy.require_http_inputs("public", {"url": "https://fixture.invalid/mcp"},
                            "https://fixture.invalid/mcp", {"Authorization": "Bearer forbidden-personal-fixture"})
            finally:
                reset_secret_scope(token)
        assert scopes[0] == scopes[2] and scopes[0] != scopes[1]
        assert all("fixture-one" not in key and "fixture-two" not in key for key in scopes)
    finally:
        reset_hermes_home_override(home_token)


def test_output_schema_change_also_requires_reauthorization(fixture):
    with fixture.scope():
        live = server()
        changed = copy.copy(live._tools[0])
        changed.outputSchema = {"type": "object", "properties": {"credential": {"type": "string"}}}
        with pytest.raises(PermissionError, match="reauthorization"):
            policy.validate_schemas(live, [changed])


def test_budgeted_startup_cannot_discover_before_a_run_or_charge_adapter(fixture, monkeypatch):
    from tests.agent.test_budget_runtime import policy as budget_policy
    from tools.mcp_tool_discovery import discover_mcp_tools, _connect_server
    from tools.agent_policy_gate import mcp_discovery_denial
    fixture.raw["runtime_budget"] = budget_policy()
    (fixture.home / "config.yaml").write_text(json.dumps(fixture.raw))
    # Identity construction has a policy but no admitted run or installed budget.
    with agent_runtime_scope(fixture.primary):
        monkeypatch.setattr("tools.mcp_tool_config._load_mcp_config", lambda: pytest.fail("no metadata/credential work"))
        assert "BE03" in mcp_discovery_denial()
        assert discover_mcp_tools() == []
        with pytest.raises(PermissionError):
            asyncio.run(_connect_server("public", {"url": "https://fixture.invalid/mcp"}))


def test_agent_teardown_retires_only_its_pool_without_remote_cleanup(fixture, monkeypatch):
    from tools.mcp_tool_lifecycle import close_agent_mcp_connections
    monkeypatch.setattr(core, "_mcp_loop", None)
    monkeypatch.setattr("tools.mcp_tool_loop._stop_mcp_loop", lambda **kw: None)
    with fixture.scope():
        primary = server()
        primary_key = _server_key("public")
    with fixture.scope(fixture.child):
        child = server()
        child_key = _server_key("public")
        close_agent_mcp_connections()
        assert child_key not in core._servers
        assert registry.get_entry("mcp__public__read") is None
        assert child._agent_remote_cleanup_status == "unconfirmed"
    with fixture.scope():
        assert core._servers[primary_key] is primary
        assert registry.get_entry("mcp__public__read") is not None
        assert authorize_mcp("public", connection=primary) is None


def test_missing_operator_schema_pin_never_becomes_callable_even_on_new_connection(fixture):
    raw = copy.deepcopy(fixture.raw)
    raw["agent_identity"]["agents"]["primary"]["mcp_policies"]["public"]["schema_digests"] = {}
    (fixture.home / "config.yaml").write_text(json.dumps(raw))
    context = resolve_agent_context(raw, session_id="unpinned_session", profile_home=fixture.home)
    with agent_runtime_scope(context):
        for _ in range(2):  # a replacement connection/process cannot create a TOFU authority
            with pytest.raises(PermissionError, match="pin missing"):
                server()
            assert registry.get_entry("mcp__public__read") is None
