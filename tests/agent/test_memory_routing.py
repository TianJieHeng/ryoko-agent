"""Real identity/store/MCP lifecycle boundaries; all transport payloads are synthetic."""
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from agent.agent_identity import parse_agent_identity_config, resolve_agent_context
from agent.agent_init import _init_memory
from agent.identity_lifecycle import agent_runtime_scope
from gateway.platforms.api_server_memory_sessions import ApiServerMemorySessions
from tests.agent.test_agent_identity import config_for

pytestmark = pytest.mark.platforms("linux")


@pytest.fixture
def owners(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    raw = config_for()
    raw["memory"] = {"provider": "must-never-load"}
    (tmp_path / "config.yaml").write_text(json.dumps(raw))
    primary = resolve_agent_context(raw, session_id="primary-session", profile_home=tmp_path)
    policy = parse_agent_identity_config(raw).agents["specialist"]
    specialist = replace(primary, policy=policy, identity=replace(primary.identity,
        agent_id="specialist", session_id="specialist-session", policy_digest=policy.digest))
    child = resolve_agent_context(raw, session_id="child-session", profile_home=tmp_path,
                                  parent_context=primary, is_child=True)
    return SimpleNamespace(raw=raw, primary=primary, specialist=specialist, child=child, home=tmp_path)


def build(ctx, raw, *, skip=False, manager=None):
    notices = []
    agent = SimpleNamespace(runtime_context=ctx, enabled_toolsets=["memory"], disabled_toolsets=[],
                            session_id=ctx.identity.session_id, _emit_startup_warning=notices.append)
    with agent_runtime_scope(ctx):
        _init_memory(agent, raw, skip, "cli", memory_manager=manager)
    agent.notices = notices
    return agent


def test_primary_degraded_never_constructs_legacy_store_or_loads_plugin(owners, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("A primary must never initialize a built-in store or arbitrary plugin")
    monkeypatch.setattr("tools.memory_tool.MemoryStore", forbidden)
    monkeypatch.setattr("plugins.memory.load_memory_provider", forbidden)
    agent = build(owners.primary, owners.raw)
    assert agent._memory_store is None and agent.notices
    with agent_runtime_scope(owners.primary):
        manager = agent._memory_manager
        assert manager.health()["status"] == "unconfigured"
        assert "authorized active context" in manager.prefetch_all("What did I decide?")
        assert not manager.capability_manifest()["write"]
        manager.sync_all("PRIVATE", "PRIVATE", messages=[{"content": "PRIVATE"}])
        manager.on_session_end([{"content": "PRIVATE"}])
        manager.on_pre_compress([{"content": "PRIVATE"}])
        assert manager._sync_executor is None and manager.providers == []
    assert not (owners.home / "memories").exists()
    assert not (owners.home / "individual-memory").exists()


def test_specialist_child_and_primary_alternation_resume_and_frozen_prefix(owners):
    specialist = build(owners.specialist, owners.raw)
    child = build(owners.child, owners.raw)
    with agent_runtime_scope(owners.specialist):
        first = specialist._memory_store.write_record("SPECIALIST_ONLY")
        assert first["success"]
        frozen = specialist._memory_store.format_for_system_prompt("memory")
        fresh = specialist._memory_manager.fresh_context("relevant")
        assert fresh["records"][0]["content"] == "SPECIALIST_ONLY"
    with agent_runtime_scope(owners.child):
        assert child._memory_store.recall() == []
        child._memory_store.write_record("CHILD_ONLY")
        with pytest.raises((PermissionError, ValueError)):
            specialist._memory_manager.health()
        with pytest.raises((PermissionError, ValueError)):
            specialist._memory_manager.shutdown_all()
    resumed = build(owners.child, owners.raw)
    with agent_runtime_scope(owners.child):
        assert resumed._memory_store.recall()[0]["content"] == "CHILD_ONLY"
        assert resumed._memory_manager.fresh_context("child")["records"][0]["content"] == "CHILD_ONLY"
    with agent_runtime_scope(owners.specialist):
        assert specialist._memory_store.format_for_system_prompt("memory") == frozen
        assert specialist._memory_store.recall()[0]["content"] == "SPECIALIST_ONLY"
    primary = build(owners.primary, owners.raw)
    assert primary._memory_store is None


def test_disabled_routing_no_provider_payload_and_manager_reuse_rejects_owner(owners, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Disabled memory must not construct its provider")
    monkeypatch.setattr("agent.memory_mcp_provider.MCPContextPackProvider", forbidden)
    disabled = build(owners.primary, owners.raw, skip=True)
    with agent_runtime_scope(owners.primary):
        manager = disabled._memory_manager
        assert manager.health()["status"] == "disabled"
        assert manager.prefetch_all("PRIVATE") == ""
        manager.initialize_all("PRIVATE", messages=["PRIVATE"])
        manager.on_turn_start(1, "PRIVATE")
        manager.sync_all("PRIVATE", "PRIVATE")
        manager.on_memory_write("add", "memory", "PRIVATE")
        manager.on_pre_compress([{"content": "PRIVATE"}])
        assert manager._sync_executor is None
        with pytest.raises(PermissionError):
            manager.add_provider(SimpleNamespace(name="builtin"))
    with pytest.raises(PermissionError):
        build(owners.specialist, owners.raw, manager=manager)


def test_pool_cache_key_and_teardown_owning_identity(owners):
    pool = ApiServerMemorySessions(max_size=8, idle_ttl_secs=3600)
    primary = build(owners.primary, owners.raw)
    specialist = build(owners.specialist, owners.raw)
    specialist.session_id = primary.session_id  # host collision cannot merge identities
    with agent_runtime_scope(owners.primary):
        pool.checkin(primary)
    with agent_runtime_scope(owners.specialist):
        assert pool.checkout(primary.session_id) is None
        pool.checkin(specialist)
        assert pool.checkout(primary.session_id) is specialist._memory_manager
        with pytest.raises(PermissionError):
            pool.checkin(primary)
    with agent_runtime_scope(owners.primary):
        assert pool.checkout(primary.session_id) is primary._memory_manager
        pool.checkin(primary)
    with agent_runtime_scope(owners.specialist):
        pool.close_all()  # explicitly reinstalls the saved primary identity at teardown
    assert primary._memory_manager._closed


def test_cache_reuse_and_hooks_reject_changed_memory_contract(owners):
    primary = build(owners.primary, owners.raw)
    changed = {**owners.raw, "memory": {"provider": "different"}}
    (owners.home / "config.yaml").write_text(json.dumps(changed))
    with agent_runtime_scope(owners.primary):
        with pytest.raises(PermissionError):
            primary._memory_manager.prefetch_all("PRIVATE")
        with pytest.raises(PermissionError):
            primary._memory_manager.sync_all("PRIVATE", "PRIVATE")
        assert not primary._memory_manager.matches(owners.primary, changed)


def test_unbound_api_checkout_requires_durable_exact_session_owner(owners):
    from hermes_state import SessionDB
    db = SessionDB(owners.home / "state.db")
    try:
        agent = build(owners.primary, owners.raw)
        db.create_session(agent.session_id, source="api_server")
        db.claim_session_agent_identity(agent.session_id, owners.primary.identity.to_record())
        pool = ApiServerMemorySessions(max_size=8, idle_ttl_secs=3600)
        pool.checkin(agent)
        assert pool.checkout(agent.session_id) is None
        assert pool.checkout("invented", session_db=db) is None
        assert pool.checkout(agent.session_id, session_db=db) is agent._memory_manager
    finally:
        db.close()


def test_fresh_corrections_redeliver_until_durable_ack_and_exclude_projects(owners):
    agent = build(owners.specialist, owners.raw)
    with agent_runtime_scope(owners.specialist):
        manager, store = agent._memory_manager, agent._memory_store
        store.write_record("first")
        packet = manager.fresh_context("related")
        assert packet["scope_key"] == "individual"
        assert manager.fresh_context("related") == packet
        assert not manager.acknowledge_fresh_context("invented")
        assert manager.acknowledge_fresh_context(packet["cursor"])
        assert manager.fresh_context("related")["records"] == []
        row = packet["records"][0]
        store.write_record("corrected", record_id=row["record_id"], expected_version=1)
        correction = manager.fresh_context("related")
        assert correction["records"][0]["version"] == 2
        assert correction["invalidation_refs"] == [row["record_id"]]
        assert "source_ref" in correction["records"][0]


def test_explicit_project_scope_switches_keep_independent_cursors(owners, monkeypatch, tmp_path):
    from pathlib import Path
    from hermes_cli import projects_db as pdb
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    with agent_runtime_scope(owners.specialist), pdb.connect_closing() as db:
        projects = [pdb.create_project(db, name=name, owner_principal_id="owner", grants=[
            {"principal_id": "owner", "agent_id": "specialist", "permissions": ["read", "write"]}])
            for name in ("One", "Two")]
    owners.raw["agent_identity"]["agents"]["specialist"]["project_grants"] = projects
    (owners.home / "config.yaml").write_text(json.dumps(owners.raw))
    raw = owners.raw
    primary = resolve_agent_context(raw, session_id="primary-session", profile_home=owners.home)
    policy = parse_agent_identity_config(raw).agents["specialist"]
    ctx = replace(primary, policy=policy, identity=replace(primary.identity, agent_id="specialist",
        session_id="specialist-session", policy_digest=policy.digest))
    agent = build(ctx, raw)
    with agent_runtime_scope(ctx):
        store, manager = agent._memory_store, agent._memory_manager
        rows = [store.write_record(f"Only project {i}", scope="project:" + project)["record"]
                for i, project in enumerate(projects)]
        assert manager.fresh_context("context")["records"] == []
        a_scope = manager.set_project_scope(projects[0])
        first = manager.fresh_context("context")
        assert a_scope == {"project_id": projects[0], "scope_key": "project:" + projects[0]}
        assert [r["content"] for r in first["records"]] == ["Only project 0"]
        assert manager.acknowledge_fresh_context(first["cursor"])
        manager.set_project_scope(projects[1])
        second = manager.fresh_context("context")
        assert [r["content"] for r in second["records"]] == ["Only project 1"]
        assert not manager.acknowledge_fresh_context(first["cursor"])
        assert manager.acknowledge_fresh_context(second["cursor"])
        store.write_record("Project0 correction", record_id=rows[0]["record_id"], expected_version=1,
                           scope="project:" + projects[0])
        assert manager.fresh_context("context")["records"] == []
        manager.set_project_scope(projects[0])
        corrected = manager.fresh_context("context")
        assert [r["content"] for r in corrected["records"]] == ["Project0 correction"]
        assert corrected["scope_key"] == "project:" + projects[0]
        with pdb.connect_closing() as db:
            db.execute("DELETE FROM project_grants WHERE project_id=?", (projects[0],))
            db.commit()
        with pytest.raises(PermissionError):
            manager.fresh_context("context")
        manager.set_project_scope(None)
        reset = manager.fresh_context("context")
        assert reset["records"] == []
        assert "Memory applicability changed explicitly to individual" in reset["context_text"]
    primary_agent = build(primary, raw)
    with agent_runtime_scope(primary), pytest.raises(PermissionError):
        primary_agent._memory_manager.set_project_scope(projects[0])
