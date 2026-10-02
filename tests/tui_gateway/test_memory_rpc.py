"""BE08 real owned RPC, routed manager and isolated SQLite memory boundaries."""
import base64
import hashlib
import json
import threading
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.platforms("linux")


@pytest.fixture
def memory(tmp_path, monkeypatch):
    from agent.agent_identity import parse_agent_identity_config, resolve_agent_context
    from agent.identity_lifecycle import agent_runtime_scope
    from agent.memory_router import initialize_routed_memory
    from hermes_cli import projects_db
    from hermes_state import SessionDB
    from tui_gateway import server

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    homes, configs, projects, agents, peers, sessions = {}, {}, {}, {}, {}, {}
    for home_id in ("home", "other"):
        home = tmp_path / home_id
        home.mkdir(mode=0o700)
        with projects_db.connect_closing(home / "projects.db") as conn:
            project_id = projects_db.create_project(conn, name="Scoped facts", owner_principal_id="owner", grants=[
                {"principal_id": "owner", "agent_id": "a", "permissions": ["read", "write"]},
                {"principal_id": "owner", "agent_id": "b", "permissions": ["read"]}])
        with projects_db.connect_closing(home / "projects.db") as conn:
            alternate_id = projects_db.create_project(conn, name="Alternate facts", owner_principal_id="owner", grants=[
                {"principal_id": "owner", "agent_id": "a", "permissions": ["read", "write"]}])
        projects[home_id + "-alternate"] = alternate_id
        config = {"agent_identity": {"schema_version": 1, "principal_id": "owner", "profile_id": home_id,
            "primary_agent_id": "primary", "active_agent_id": "primary", "agents": {
                "primary": {"policy_version": 1, "role": "primary", "memory_backend": "personal_mcp"},
                **{actor: {"policy_version": 1, "role": "specialist", "memory_backend": "builtin",
                           "allowed_tools": ["memory"], "project_grants": [project_id, alternate_id]} for actor in ("a", "b")}}}}
        (home / "config.yaml").write_text(json.dumps(config))
        homes[home_id], configs[home_id], projects[home_id] = home, config, project_id
        primary = resolve_agent_context(config, session_id=home_id + "-primary", profile_home=home)
        policies = parse_agent_identity_config(config).agents
        for actor in ("primary", "a", "b"):
            label = actor if home_id == "home" else home_id + "-" + actor
            policy = policies[actor]
            context = replace(primary, policy=policy, identity=replace(primary.identity,
                agent_id=actor, session_id=label, policy_digest=policy.digest))
            db = SessionDB(home / (label + "-state.db"))
            db.create_session(label, source="tui")
            db.claim_session_agent_identity(label, context.identity.to_record())
            agent = SimpleNamespace(runtime_context=context, _session_db=db, session_id=label,
                                    disabled_toolsets=[], _emit_startup_warning=lambda _message: None)
            peer = SimpleNamespace(write=lambda _frame: True)
            sessions[label] = {"agent": agent, "profile_home": str(home), "transport": peer,
                              "session_key": label, "history": [], "history_lock": threading.RLock()}
            agents[label], peers[label] = agent, peer
    monkeypatch.setenv("HERMES_HOME", str(homes["home"]))
    monkeypatch.setattr(server, "_sessions", sessions)

    def initialize(label):
        agent = agents[label]
        home_id = "other" if label.startswith("other-") else "home"
        with agent_runtime_scope(agent.runtime_context):
            initialize_routed_memory(agent, configs[home_id])
        return agent

    def call(method, label="a", *, via=None, **params):
        if not hasattr(agents[label], "_memory_manager"):
            initialize(label)
        return server.dispatch({"jsonrpc": "2.0", "id": "memory-test", "method": method,
            "params": {"schema_version": 1, "session_id": label, **params}},
            transport=peers[label] if via is None else via)

    yield SimpleNamespace(call=call, initialize=initialize, agents=agents, peers=peers,
                          homes=homes, configs=configs, projects=projects)
    for agent in agents.values():
        agent._session_db.close()


def result(response):
    assert response is not None and "error" not in response, response
    return response["result"]


def denied(response, code=None):
    assert response is not None and "error" in response, response
    if code is not None:
        assert response["error"].get("data", {}).get("code") == code, response
    return response["error"]


def write(memory, label="a", **params):
    params.setdefault("record_id", "fixture-record")
    return result(memory.call("runtime.memory.record.write", label, **params))["outcome"]


def test_unconfigured_primary_is_explicit_and_never_builds_builtin(memory, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Primary routing must not construct a built-in store or plugin")
    monkeypatch.setattr("tools.individual_memory_store.create_individual_memory_store", forbidden)
    monkeypatch.setattr("tools.memory_tool.MemoryStore", forbidden)
    monkeypatch.setattr("plugins.memory.load_memory_provider", forbidden)
    status = result(memory.call("runtime.memory.status", "primary"))
    assert status["health"]["status"] == "unconfigured"
    assert status["capabilities"]["backend"] == "personal_mcp"
    assert not any(value for key, value in status["capabilities"].items() if key != "backend")
    for method, params in (("runtime.memory.records.list", {}), ("runtime.memory.export", {}),
                           ("runtime.memory.record.get", {"record_id": "secret"}),
                           ("runtime.memory.record.write", {"record_id": "new", "content": "no fallback"}),
                           ("runtime.memory.record.delete", {"record_id": "secret", "expected_version": 1})):
        denied(memory.call(method, "primary", **params), "memory_operation_unsupported")
    assert not (memory.homes["home"] / "individual-memory").exists()
    # Gateway profile setup may create the empty standard directory.
    assert not list((memory.homes["home"] / "memories").rglob("*"))


def test_specialist_same_profile_and_cross_profile_alternation_isolated(memory):
    for actor, content in (("a", "A-only fact"), ("b", "B-only fact"), ("other-a", "Other profile fact")):
        outcome = write(memory, actor, record_id="same-id", content=content)
        assert outcome["success"] and outcome["record"]["owner_agent_id"] == actor.removeprefix("other-")
    namespaces = set()
    for actor, content in (("a", "A-only fact"), ("b", "B-only fact"), ("a", "A-only fact"), ("other-a", "Other profile fact")):
        record = result(memory.call("runtime.memory.record.get", actor, record_id="same-id"))["record"]
        assert record["content"] == content
        namespaces.add(record["namespace_id"])
        listing = result(memory.call("runtime.memory.records.list", actor))
        assert [row["content"] for row in listing["records"]] == [content]
    assert len(namespaces) == 3


def test_cas_conflicts_version_history_and_delete_tombstones(memory):
    first = write(memory, record_id="fact", content="Old fact", kind="inference", confidence=0.4)
    assert first["acknowledged_version"] == 1
    second = write(memory, record_id="fact", expected_version=1, content="Corrected fact", kind="stated_fact")
    assert second["success"] and second["record"]["supersedes_version"] == 1
    conflict = write(memory, record_id="fact", expected_version=1, content="Stale fact")
    assert not conflict["success"] and conflict["code"] == "version_conflict"
    assert conflict["expected_version"] == 1 and conflict["current_version"] == 2
    history = result(memory.call("runtime.memory.record.get", record_id="fact", version=1))["record"]
    assert history["content"] == "Old fact" and history["validity"] == "superseded"
    assert history["superseded_by_version"] == 2
    stale = result(memory.call("runtime.memory.record.delete", record_id="fact", expected_version=1))["outcome"]
    assert stale["code"] == "version_conflict"
    deleted = result(memory.call("runtime.memory.record.delete", record_id="fact", expected_version=2))["outcome"]
    assert deleted["success"] and deleted["acknowledged_version"] == 3
    assert deleted["record"]["deletion_state"] == "deleted" and deleted["record"]["content"] is None
    assert result(memory.call("runtime.memory.records.list"))["records"] == []
    assert result(memory.call("runtime.memory.records.list", include_deleted=True))["records"][0]["content"] is None
    assert result(memory.call("runtime.memory.record.get", record_id="fact", version=1))["record"]["content"] is None
    denied(memory.call("runtime.memory.record.write", record_id="fact", expected_version=3, content="Resurrect"), "record_deleted")


def test_full_revision_bound_json_export_has_complete_digest_checked_bytes(memory):
    write(memory, record_id="one", content="Café 🦊 remembered exactly", source_ref="conversation:fixture")
    write(memory, record_id="two", content="Second fact")
    caps = result(memory.call("runtime.memory.status"))["capabilities"]
    assert caps["backend"] == "builtin" and caps["export"]
    first = result(memory.call("runtime.memory.export", limit=41))
    data = bytearray(base64.b64decode(first["data_base64"], validate=True))
    part = first
    while not part["eof"]:
        part = result(memory.call("runtime.memory.export", offset=part["next_offset"], limit=41,
                                  expected_revision=first["revision"]))
        assert (part["revision"], part["sha256"], part["size"]) == (first["revision"], first["sha256"], first["size"])
        data.extend(base64.b64decode(part["data_base64"], validate=True))
    assert len(data) == first["size"] and hashlib.sha256(data).hexdigest() == first["sha256"]
    snapshot = json.loads(data)
    assert snapshot["revision"] == first["revision"]
    assert {row["content"] for row in snapshot["records"]} == {"Café 🦊 remembered exactly", "Second fact"}
    assert first["format"] == "json" and first["deletion_semantics"] == "tombstones_not_physical_erasure"
    assert denied(memory.call("runtime.memory.export", destination="https://outside.invalid"))["code"] == 4000
    assert denied(memory.call("runtime.memory.export", path="/tmp/export.json"))["code"] == 4000
    write(memory, record_id="three", content="Mutation between chunks")
    denied(memory.call("runtime.memory.export", offset=first["next_offset"], expected_revision=first["revision"]), "revision_conflict")


def test_list_continuation_requires_revision_and_detects_changes(memory):
    for index in range(3):
        write(memory, record_id="record-" + str(index), content="Fact " + str(index))
    first = result(memory.call("runtime.memory.records.list", limit=1))
    assert first["has_more"] and first["total"] == 3
    assert denied(memory.call("runtime.memory.records.list", offset=1))["code"] == 4000
    next_page = result(memory.call("runtime.memory.records.list", offset=1, limit=1, expected_revision=first["revision"]))
    assert next_page["records"][0]["record_id"] != first["records"][0]["record_id"]
    write(memory, record_id="new", content="New fact")
    denied(memory.call("runtime.memory.records.list", offset=2, expected_revision=first["revision"]), "revision_conflict")


@pytest.mark.parametrize("extra", [{"agent_id": "b"}, {"profile": "other"}, {"owner_agent_id": "b"}, {"expected_version": True}])
def test_forged_memory_write_dto_rejected(memory, extra):
    assert denied(memory.call("runtime.memory.record.write", record_id="forged", content="Do not store", **extra))["code"] == 4000
    assert result(memory.call("runtime.memory.records.list"))["records"] == []


def test_foreign_transport_cannot_read_write_or_export(memory):
    write(memory, content="Private", record_id="private")
    for method, params in (("runtime.memory.status", {}), ("runtime.memory.records.list", {}),
                           ("runtime.memory.export", {}), ("runtime.memory.record.get", {"record_id": "private"}),
                           ("runtime.memory.record.write", {"record_id": "unauthorized", "content": "unauthorized"}),
                           ("runtime.memory.record.delete", {"record_id": "private", "expected_version": 1})):
        assert denied(memory.call(method, via=memory.peers["b"], **params))["code"] == 4001


def test_manager_and_store_cross_owner_swap_fail_closed(memory):
    write(memory, "a", content="A secret", record_id="record")
    write(memory, "b", content="B secret", record_id="record")
    a, b = memory.agents["a"], memory.agents["b"]
    original = a._memory_manager
    a._memory_manager = b._memory_manager
    denied(memory.call("runtime.memory.status"))
    denied(memory.call("runtime.memory.record.get", record_id="record"))
    a._memory_manager = original
    original.store = b._memory_manager.store
    denied(memory.call("runtime.memory.record.get", record_id="record"))
    denied(memory.call("runtime.memory.record.write", record_id="injected", content="Injected"))


def test_live_policy_revocation_denies_existing_manager(memory):
    write(memory, content="Owned", record_id="record")
    config = memory.configs["home"]
    config["agent_identity"]["agents"]["a"]["policy_version"] += 1
    (memory.homes["home"] / "config.yaml").write_text(json.dumps(config))
    for method, params in (("runtime.memory.status", {}), ("runtime.memory.record.get", {"record_id": "record"}),
                           ("runtime.memory.record.write", {"record_id": "denied", "content": "Denied"}), ("runtime.memory.export", {})):
        denied(memory.call(method, **params))


def test_project_scoped_records_require_explicit_applicability_and_live_grants(memory):
    project = memory.projects["home"]
    write(memory, record_id="individual", content="Individual fact")
    write(memory, record_id="project", content="Project fact", scope="project:" + project)
    assert {row["record_id"] for row in result(memory.call("runtime.memory.records.list"))["records"]} == {"individual"}
    assert {row["record_id"] for row in result(memory.call("runtime.memory.records.list", project_id=project))["records"]} == {"individual", "project"}
    denied(memory.call("runtime.memory.record.write", "b", record_id="readonly", content="Read-only actor", scope="project:" + project), "memory_owner_denied")
    denied(memory.call("runtime.memory.record.write", record_id="foreign", content="Foreign project", scope="project:ungranted"), "memory_owner_denied")
    from hermes_cli import projects_db
    with projects_db.connect_closing(memory.homes["home"] / "projects.db") as conn:
        projects_db._replace_grants_locked(conn, project, [])
        conn.commit()
    denied(memory.call("runtime.memory.record.get", record_id="project"), "memory_owner_denied")
    denied(memory.call("runtime.memory.records.list", project_id=project), "memory_owner_denied")
    assert result(memory.call("runtime.memory.record.get", record_id="individual"))["record"]["content"] == "Individual fact"


def test_record_creation_requires_stable_id_and_retries_conflict_without_duplication(memory):
    assert denied(memory.call("runtime.memory.record.write", content="Missing identity"))["code"] == 4000
    first = write(memory, record_id="retry-stable", content="Exactly once", expected_version=0)
    again = write(memory, record_id="retry-stable", content="Exactly once", expected_version=0)
    assert first["success"] and not again["success"] and again["code"] == "version_conflict"
    listing = result(memory.call("runtime.memory.records.list"))
    assert listing["total"] == 1 and listing["revision"] == first["revision"]
    assert denied(memory.call("runtime.memory.export", offset=1))["code"] == 4000


def test_scope_rpc_selects_only_applicable_fresh_context_with_independent_cursors(memory):
    from agent.identity_lifecycle import agent_runtime_scope

    a_project, b_project = memory.projects["home"], memory.projects["home-alternate"]
    write(memory, record_id="individual", content="Individual fact")
    write(memory, record_id="project-a", content="A project fact", scope="project:" + a_project)
    write(memory, record_id="project-b", content="B project fact", scope="project:" + b_project)
    agent = memory.agents["a"]
    manager = agent._memory_manager
    with agent_runtime_scope(agent.runtime_context):
        frozen = manager.store.format_for_system_prompt("memory")
    selected = result(memory.call("runtime.memory.scope.set", project_id=a_project))
    assert selected == {"project_id": a_project, "scope_key": "project:" + a_project}
    with agent_runtime_scope(agent.runtime_context):
        a_first = manager.fresh_context("facts")
        assert {row["record_id"] for row in a_first["records"]} == {"individual", "project-a"}
        assert manager.acknowledge_fresh_context(a_first["cursor"])
    assert result(memory.call("runtime.memory.scope.set", project_id=b_project))["project_id"] == b_project
    with agent_runtime_scope(agent.runtime_context):
        b_first = manager.fresh_context("facts")
        assert {row["record_id"] for row in b_first["records"]} == {"individual", "project-b"}
        assert a_first["cursor"] != b_first["cursor"]
        assert not manager.acknowledge_fresh_context(a_first["cursor"])
        assert manager.fresh_context("facts")["records"] == b_first["records"]
    result(memory.call("runtime.memory.scope.set", project_id=a_project))
    with agent_runtime_scope(agent.runtime_context):
        assert not manager.acknowledge_fresh_context(b_first["cursor"])
        assert manager.fresh_context("facts")["records"] == []
        assert manager.store.format_for_system_prompt("memory") == frozen
    result(memory.call("runtime.memory.scope.set", project_id=b_project))
    with agent_runtime_scope(agent.runtime_context):
        assert manager.fresh_context("facts")["records"] == b_first["records"]
        assert manager.acknowledge_fresh_context(b_first["cursor"])
        assert manager.fresh_context("facts")["records"] == []
    assert result(memory.call("runtime.memory.scope.set", project_id=None)) == {
        "project_id": None, "scope_key": "individual"}
    with agent_runtime_scope(agent.runtime_context):
        assert {row["record_id"] for row in manager.fresh_context("facts")["records"]} == {"individual"}
        assert manager.store.format_for_system_prompt("memory") == frozen
    denied(memory.call("runtime.memory.scope.set", "primary", project_id=a_project), "memory_scope_denied")
    denied(memory.call("runtime.memory.scope.set", project_id=memory.projects["other"]), "memory_scope_denied")
    assert denied(memory.call("runtime.memory.scope.set", via=memory.peers["b"], project_id=a_project))["code"] == 4001
