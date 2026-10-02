"""BE04 session views exercise real toolset, registry, bridge and identity scopes."""
import copy
import json
from dataclasses import FrozenInstanceError

import pytest

from agent.agent_identity import resolve_agent_context
from agent.identity_lifecycle import agent_runtime_scope
from hermes_constants import set_hermes_home_override, reset_hermes_home_override


BRIDGES = ["tool_search", "tool_describe", "tool_call"]


def policy(active="primary"):
    return {"tools": {"tool_search": {"enabled": "on", "defer": ["todo_list"]}},
            "agent_identity": {
                "schema_version": 1, "principal_id": "owner", "profile_id": "same_profile",
                "primary_agent_id": "primary", "active_agent_id": active,
                "personal_mcp_servers": ["private_vault"],
                "agents": {
                    "primary": {"policy_version": 1, "role": "primary", "memory_backend": "personal_mcp",
                                "allowed_tools": BRIDGES + ["todo_list", "read_window_below", "mcp__private_vault__read"],
                                "mcp_grants": {"private_vault": ["read"]}},
                    "specialist": {"policy_version": 1, "role": "specialist", "memory_backend": "builtin",
                                   "allowed_tools": BRIDGES + ["clarify"]}}}}


@pytest.fixture
def home(tmp_path, monkeypatch):
    root = tmp_path / "profile"
    root.mkdir()
    (root / "config.yaml").write_text(json.dumps(policy()))
    (root / ".env").write_text("")
    token = set_hermes_home_override(str(root))
    monkeypatch.setenv("HERMES_HOME", str(root))
    yield root
    reset_hermes_home_override(token)


def ctx(home, active="primary", cfg=None):
    cfg = copy.deepcopy(cfg or policy())
    cfg["agent_identity"]["active_agent_id"] = active
    return resolve_agent_context(cfg, session_id="session_" + active, profile_home=home)


def test_same_profile_views_search_reopen_and_dispatch_share_grants(home):
    import model_tools
    from tools.registry import registry
    from tools.tool_search import dispatch_tool_search, dispatch_tool_describe
    primary, specialist = ctx(home), ctx(home, "specialist")
    before = None
    for binding, allowed in [(primary, "todo_list"), (specialist, "clarify"), (primary, "todo_list")]:
        with agent_runtime_scope(binding):
            defs, view = model_tools.get_tool_definitions_with_view(["todo", "clarify"], quiet_mode=True)
            raw = model_tools.get_tool_definitions(["todo", "clarify"], quiet_mode=True,
                                                   skip_tool_search_assembly=True)
            assert allowed in view.discoverable_tool_ids
            denied = "clarify" if allowed == "todo_list" else "todo_list"
            assert denied not in view.authorized_tool_ids
            assert denied not in json.dumps(view.to_record())
            assert view.selected_tool_ids == tuple(sorted(td["function"]["name"] for td in defs))
            assert json.loads(registry.dispatch(denied, {}))["status"] == "denied"
            assert json.loads(model_tools.handle_function_call(denied, {}))["status"] == "denied"
            # Stale/foreign schemas passed straight to a bridge cannot expose a grant.
            foreign = [{"type": "function", "function": {"name": denied, "description": "private", "parameters": {}}}]
            search = json.loads(dispatch_tool_search({"queries": [denied]}, current_tool_defs=raw + foreign))
            assert denied not in search["tools"]
            described = json.loads(dispatch_tool_describe({"names": [denied]}, current_tool_defs=raw + foreign))
            assert denied not in described["tools"]
            if binding is primary:
                found = json.loads(model_tools.handle_function_call("tool_search", {"queries": ["todo_list"]},
                                   enabled_toolsets=["todo", "clarify"]))
                assert "todo_list" in found["tools"]
                reopened = json.loads(model_tools.handle_function_call("tool_describe", {"names": ["todo_list"]},
                                      enabled_toolsets=["todo", "clarify"]))
                assert "todo_list" in reopened["tools"]
                serialized = json.dumps(defs, sort_keys=True)
                if before is None:
                    before = serialized, view
                else:
                    assert serialized == before[0]
                    assert view == before[1]
    assert primary.identity.profile_id == specialist.identity.profile_id
    assert primary.identity.agent_id != specialist.identity.agent_id


def test_missing_bridge_grants_never_hide_an_authorized_tool(home):
    import model_tools
    cfg = policy()
    cfg["agent_identity"]["agents"]["primary"]["allowed_tools"] = ["todo_list"]
    with agent_runtime_scope(ctx(home, cfg=cfg)):
        defs, view = model_tools.get_tool_definitions_with_view(["todo"], quiet_mode=True)
    assert [td["function"]["name"] for td in defs] == ["todo_list"]
    assert view.selected_tool_ids == view.discoverable_tool_ids == ("todo_list",)


def test_surface_selection_uses_session_toolsets_without_desktop_env(home, monkeypatch):
    import model_tools
    monkeypatch.delenv("HERMES_DESKTOP", raising=False)
    with agent_runtime_scope(ctx(home)):
        cli, cli_view = model_tools.get_tool_definitions_with_view(["todo"], quiet_mode=True)
        gui, gui_view = model_tools.get_tool_definitions_with_view(["todo", "desktop_ui"], quiet_mode=True)
        cli_again, again_view = model_tools.get_tool_definitions_with_view(["todo"], quiet_mode=True)
    assert "read_window_below" not in cli_view.discoverable_tool_ids
    assert cli_view.unavailable_reasons["read_window_below"] == "not_selected_for_session"
    assert "read_window_below" in gui_view.discoverable_tool_ids
    assert cli == cli_again and cli_view == again_view
    assert "read_window_below" not in {td["function"]["name"] for td in cli}
    assert "read_window_below" in {td["function"]["name"] for td in gui}


def test_catalog_and_view_are_immutable_and_inspection_never_refreshes(home, monkeypatch):
    import model_tools
    from tools.registry import registry
    with agent_runtime_scope(ctx(home)):
        first = registry.catalog_metadata()
        assert registry.catalog_metadata() is first
        defs, view = model_tools.get_tool_definitions_with_view(["todo"], quiet_mode=True)
        with pytest.raises(FrozenInstanceError):
            first[0].name = "changed"
        with pytest.raises(TypeError):
            view.unavailable_reasons["hidden"] = "changed"
        record = view.to_record()
        record["selected_tool_ids"].clear()
        monkeypatch.setattr(registry, "get_definitions", lambda *a, **k: pytest.fail("inspection reprobed"))
        monkeypatch.setattr(registry, "catalog_metadata", lambda: pytest.fail("inspection rebuilt"))
        assert view.to_record()["selected_tool_ids"]
        assert view.with_selection([]).selected_tool_ids == ()
        assert view.discoverable_tool_ids == ("todo_list",)


def test_private_unavailable_source_never_leaks_or_probes_for_specialist(home, monkeypatch):
    import model_tools
    from hermes_platform import declaration
    from tools.registry import registry
    from tools.tool_search_catalog import hidden_declared_sources, build_catalog_listing_with_form
    from tools.tool_search import dispatch_tool_search
    name = "mcp__private_vault__read"
    probes = []
    def handler(args):
        return '{}'
    handler._agent_mcp_target = ("private_vault", "read")
    def unavailable():
        probes.append("probe")
        return False
    registry.register(name=name, toolset="mcp-private_vault", schema={"name": name, "description": "private label"},
                      handler=handler, check_fn=unavailable)
    declaration.register("private_vault", declaration.Declaration("private_vault", None))
    monkeypatch.setattr("tools.mcp_liveness.unavailable_details", lambda name: (None, None, "private app absent"))
    try:
        for binding, visible in [(ctx(home), True), (ctx(home, "specialist"), False), (ctx(home), True)]:
            probes.clear()
            with agent_runtime_scope(binding):
                defs, view = model_tools.get_tool_definitions_with_view(["todo", "clarify", "mcp-private_vault"], quiet_mode=True)
                rows = hidden_declared_sources()
                listing, _ = build_catalog_listing_with_form([])
                search = dispatch_tool_search({"queries": ["unmatched"]}, current_tool_defs=[])
                all_public = json.dumps([defs, view.to_record(), rows, listing, search])
                assert ("private_vault" in all_public) is visible
                if visible:
                    assert probes and view.unavailable_reasons[name] == "service_unavailable"
                else:
                    assert not probes
                    assert json.loads(registry.dispatch(name, {}))["status"] == "denied"
                    assert view.unavailable_reason(name) == view.unavailable_reason("never_installed")
    finally:
        registry.deregister(name)
        declaration.unregister("private_vault")


def test_denied_catalog_changes_do_not_change_visible_version(home):
    import model_tools
    from tools.registry import registry
    with agent_runtime_scope(ctx(home, "specialist")):
        _, before = model_tools.get_tool_definitions_with_view(["clarify"], quiet_mode=True)
        registry.register(name="sibling_private_tool", toolset="sibling_private_tool", schema={"name": "sibling_private_tool"},
                          handler=lambda args: '{}')
        try:
            _, after = model_tools.get_tool_definitions_with_view(["clarify"], quiet_mode=True)
        finally:
            registry.deregister("sibling_private_tool")
    assert before == after


def test_strict_service_probe_cache_never_crosses_identity_grants(home):
    from tools.registry import _check_fn_cached
    from agent.runtime_context import current_agent_context
    seen = []
    def probe():
        identity = current_agent_context().identity.agent_id
        seen.append(identity)
        return identity == "primary"
    results = []
    for binding in (ctx(home), ctx(home, "specialist"), ctx(home)):
        with agent_runtime_scope(binding):
            results.append(_check_fn_cached(probe))
    assert results == [True, False, True]
    assert seen == ["primary", "specialist", "primary"]


def test_legacy_schema_and_view_memo_share_bounded_cache(tmp_path, monkeypatch):
    import model_tools
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    token = set_hermes_home_override(str(tmp_path))
    model_tools._clear_tool_defs_cache()
    try:
        first, view = model_tools.get_tool_definitions_with_view(["todo"], quiet_mode=True)
        monkeypatch.setattr(model_tools, "_compute_tool_definitions", lambda *a, **k: pytest.fail("cache missed"))
        second, cached_view = model_tools.get_tool_definitions_with_view(["todo"], quiet_mode=True)
        assert first == second and first is not second
        assert view is cached_view
        assert model_tools.get_tool_definitions(["todo"], quiet_mode=True) == first
        first[0]["function"]["parameters"]["properties"]["poison"] = {"type": "string"}
        assert "poison" not in second[0]["function"]["parameters"]["properties"]
        assert model_tools.get_tool_definitions_with_view(["todo"], quiet_mode=True)[0] == second
        first.clear()
        assert model_tools.get_tool_definitions_with_view(["todo"], quiet_mode=True)[0] == second
    finally:
        model_tools._clear_tool_defs_cache()
        reset_hermes_home_override(token)


def test_reopen_checks_target_grants_before_registry_classification(home):
    from tools.tool_search import resolve_underlying_call
    with agent_runtime_scope(ctx(home, "specialist")):
        denied = resolve_underlying_call({"calls": [{"name": "todo_list", "arguments": {}}]})
        unknown = resolve_underlying_call({"calls": [{"name": "never_installed", "arguments": {}}]})
        assert denied == unknown
        assert denied[0] is None and "grant" in denied[2]


def test_prefix_restore_updates_view_selection_without_reprobe(home, monkeypatch):
    import model_tools
    from tools.mcp_tool_agent import restore_agent_tool_prefix, refresh_agent_mcp_tools
    from types import SimpleNamespace
    binding = ctx(home)
    with agent_runtime_scope(binding):
        defs, view = model_tools.get_tool_definitions_with_view(["todo", "desktop_ui"], quiet_mode=True)
    agent = SimpleNamespace(runtime_context=binding, tools=defs, tool_view=view,
                            valid_tool_names=set(view.selected_tool_ids), enabled_toolsets=["todo", "desktop_ui"],
                            disabled_toolsets=[], _tool_snapshot_generation=0)
    # An existing refresh is the sole place discovery may be recaptured.
    refresh_agent_mcp_tools(agent, enabled_override=["todo"], content_aware=True)
    assert "read_window_below" not in agent.tool_view.selected_tool_ids
    assert set(agent.tool_view.selected_tool_ids) == agent.valid_tool_names
    frozen = agent.tool_view
    monkeypatch.setattr(model_tools, "get_tool_definitions_with_view", lambda *a, **kw: pytest.fail("restore reprobed"))
    restore_agent_tool_prefix(agent, list(reversed(agent.tools)))
    assert agent.tool_view.discoverable_tool_ids == frozen.discoverable_tool_ids
    assert set(agent.tool_view.selected_tool_ids) == agent.valid_tool_names


def test_hosted_connector_search_and_describe_filter_exact_tool_grants(home):
    from tools.tool_search import dispatch_tool_search, dispatch_tool_describe
    from types import SimpleNamespace
    cfg = policy()
    cfg["agent_identity"]["agents"]["primary"]["allowed_tools"] += ["manage_connections", "connectors__example__allowed"]
    definitions = [{"type": "function", "function": {"name": "manage_connections", "parameters": {}}}]
    def search(groups):
        return SimpleNamespace(failure=None, payload={
            "schemas": {"denied": {"connector": "example", "description": "private denied"},
                        "allowed": {"connector": "example", "description": "allowed"}},
            "results": [{"use_case": groups[0]["use_case"], "tools": ["denied", "allowed"]}]})
    described = []
    def describe(names):
        described.extend(names)
        return SimpleNamespace(failure=None, payload={"tools": {name: {"description": "own"} for name in names}})
    with agent_runtime_scope(ctx(home, cfg=cfg)):
        found = json.loads(dispatch_tool_search({"queries": ["example"]}, current_tool_defs=definitions,
                                               connector_search=search))
        # Hosted names use the canonical connector formatter, never caller aliases.
        assert set(found["tools"]) == {"connectors__example__allowed"}
        dispatch_tool_describe({"names": ["connectors__example__denied"]}, current_tool_defs=definitions,
                               connector_describe=describe)
    assert described == []


def test_real_owned_dispatch_and_reopen_keep_same_profile_personal_mcp_primary_only(home):
    """No live MCP: the real registry/bridge/lease invokes one inert test handler."""
    import httpx
    import model_tools
    from openai import OpenAI
    from types import SimpleNamespace
    from agent.runtime_commands import (prepare_turn_command, claim_turn_command, bind_runtime_run,
                                        reset_runtime_run, finish_turn_command)
    from agent.turn_facade_lease import DurableTurnLease
    from hermes_state import SessionDB
    from tools.registry import registry
    name = "mcp__private_vault__read"
    calls = []
    def handler(args):
        calls.append(dict(args))
        return json.dumps({"fixture": "read"})
    handler._agent_mcp_target = ("private_vault", "read")
    registry.register(name=name, toolset="mcp-private_vault", schema={"name": name,
                      "parameters": {"type": "object", "properties": {}}}, handler=handler)
    primary, specialist = ctx(home), ctx(home, "specialist")
    client = OpenAI(api_key="inert-fixture", max_retries=0, base_url="https://fixture.invalid/v1",
                    http_client=httpx.Client(transport=httpx.MockTransport(
                        lambda request: pytest.fail("no network call expected"))))
    db = SessionDB(home / "state.db")
    sid = primary.identity.session_id
    db.create_session(sid, source="test")
    db.claim_session_agent_identity(sid, primary.identity.to_record())
    agent = SimpleNamespace(runtime_context=primary, session_id=sid, _session_db=db, client=client,
                            api_mode="chat_completions", provider="openai", _current_turn_id="")
    try:
        with agent_runtime_scope(primary):
            command = prepare_turn_command(agent, "exercise owned tool bridge")
            assert db.acquire_session_turn_lease(sid, "fixture", wait_seconds=0)
            lease = DurableTurnLease(agent, db, sid, "fixture")
            lease.generation = db.get_session_turn_lease(sid)["generation"]
            run = claim_turn_command(agent, command, lease)
            token = bind_runtime_run(run)
            try:
                for binding, allowed in [(primary, True), (specialist, False), (primary, True)]:
                    with agent_runtime_scope(binding):
                        direct = json.loads(registry.dispatch(name, {}))
                        reopened = json.loads(model_tools.handle_function_call(
                            "tool_call", {"calls": [{"name": name, "arguments": {}}]},
                            enabled_toolsets=["mcp-private_vault"]))
                        if allowed:
                            assert direct == reopened == {"fixture": "read"}
                        else:
                            assert direct["status"] == "denied"
                            assert "error" in reopened
                finish_turn_command(run, {"final_response": "checked"})
            finally:
                reset_runtime_run(token, run)
                lease.release()
    finally:
        registry.deregister(name)
        client.close()
        db.close()
    assert calls == [{}, {}, {}, {}]


def test_finalization_captures_legacy_injections_without_registering_them(tmp_path, monkeypatch):
    import model_tools
    from agent.tool_view import finalize_tool_view
    from tools.registry import registry
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    token = set_hermes_home_override(str(tmp_path))
    model_tools._clear_tool_defs_cache()
    try:
        defs, initial = model_tools.get_tool_definitions_with_view(["todo"], quiet_mode=True)
        injected = {"type": "function", "function": {"name": "fixture_lcm_recall", "description": "session history",
                    "parameters": {"type": "object", "properties": {}}}}
        final = finalize_tool_view(initial, defs + [injected])
        assert registry.get_entry("fixture_lcm_recall") is None
        assert "fixture_lcm_recall" in final.installed_tool_ids
        assert "fixture_lcm_recall" in final.authorized_tool_ids
        assert "fixture_lcm_recall" in final.discoverable_tool_ids
        assert set(final.selected_tool_ids) == {td["function"]["name"] for td in defs + [injected]}
        assert final.catalog_version != initial.catalog_version
        assert finalize_tool_view(final, defs + [injected]) == final
        assert finalize_tool_view(final, defs) == initial
        assert "fixture_lcm_recall" not in initial.with_selection(defs + [injected]).selected_tool_ids
    finally:
        model_tools._clear_tool_defs_cache()
        reset_hermes_home_override(token)


def test_finalization_rechecks_injected_grants_and_rejects_foreign_view(home):
    import model_tools
    from agent.tool_view import finalize_tool_view
    cfg = policy()
    cfg["agent_identity"]["agents"]["primary"]["allowed_tools"].append("fixture_private_recall")
    injection = {"type": "function", "function": {"name": "fixture_private_recall", "description": "own schema",
                 "parameters": {"type": "object", "properties": {}}}}
    foreign = {"type": "function", "function": {"name": "foreign_private_recall", "description": "must not leak",
               "parameters": {"type": "object", "properties": {}}}}
    with agent_runtime_scope(ctx(home, cfg=cfg)):
        defs, view = model_tools.get_tool_definitions_with_view(["todo"], quiet_mode=True)
        final = finalize_tool_view(view, defs + [injection, foreign])
        assert "fixture_private_recall" in final.authorized_tool_ids
        assert "foreign_private_recall" not in json.dumps(final.to_record())
        assert final.catalog_version == finalize_tool_view(view, defs + [injection]).catalog_version
    with agent_runtime_scope(ctx(home, "specialist", cfg=cfg)):
        specialist_defs, specialist_view = model_tools.get_tool_definitions_with_view(["clarify"], quiet_mode=True)
        denied = finalize_tool_view(specialist_view, specialist_defs + [injection, foreign])
        assert "private_recall" not in json.dumps(denied.to_record())
        with pytest.raises(ValueError, match="active agent policy"):
            finalize_tool_view(final, specialist_defs)


def test_approved_refresh_captures_injected_tools_and_removes_stale_metadata(tmp_path, monkeypatch):
    import model_tools
    from tools.mcp_tool_agent import refresh_agent_mcp_tools, restore_agent_tool_prefix
    from types import SimpleNamespace
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    token = set_hermes_home_override(str(tmp_path))
    model_tools._clear_tool_defs_cache()
    schema = {"name": "fixture_engine_lookup", "description": "approved context engine tool",
              "parameters": {"type": "object", "properties": {}}}
    engine = SimpleNamespace(get_tool_schemas=lambda: [schema])
    try:
        defs, view = model_tools.get_tool_definitions_with_view(["todo", "context_engine"], quiet_mode=True)
        agent = SimpleNamespace(tools=defs, tool_view=view, valid_tool_names=set(view.selected_tool_ids),
                                context_compressor=engine, _context_engine_tool_names=set(),
                                enabled_toolsets=["todo", "context_engine"], disabled_toolsets=[])
        refresh_agent_mcp_tools(agent, content_aware=True)
        assert "fixture_engine_lookup" in agent.tool_view.authorized_tool_ids
        assert set(agent.tool_view.selected_tool_ids) == agent.valid_tool_names
        before = agent.tool_view
        restore_agent_tool_prefix(agent, list(reversed(agent.tools)))
        assert agent.tool_view.authorized_tool_ids == before.authorized_tool_ids
        engine.get_tool_schemas = lambda: []
        refresh_agent_mcp_tools(agent, content_aware=True)
        assert "fixture_engine_lookup" not in json.dumps(agent.tool_view.to_record())
        assert set(agent.tool_view.selected_tool_ids) == agent.valid_tool_names
    finally:
        model_tools._clear_tool_defs_cache()
        reset_hermes_home_override(token)
