"""Actual profile stores, live grants and owned user-control boundaries for BE07."""
from contextlib import contextmanager
import json
import sqlite3
from types import SimpleNamespace

import pytest

from agent.agent_identity import resolve_agent_context
from agent.identity_lifecycle import agent_runtime_scope
from agent.project_context import (
    ProjectControl, authorize_project, cas_project_metadata, claim_owned_project,
    create_owned_project, get_owned_project, project_access, project_control,
    set_project_grants, update_owned_project,
)
from hermes_cli import projects_db as pdb
from tools.capability_broker import CapabilityDenied


def config(projects):
    return {"agent_identity": {"schema_version": 1, "principal_id": "owner", "profile_id": "profile",
        "primary_agent_id": "primary", "active_agent_id": "primary",
        "agents": {"primary": {"policy_version": 1, "role": "primary", "memory_backend": "personal_mcp",
                                "project_grants": projects}},
        "child_policy": {"policy_version": 1, "role": "child", "memory_backend": "builtin",
                         "project_grants": projects + ["p_not_parent_granted"]}}}


def setup(home, *, project_name="Shared"):
    home.mkdir()
    with pdb.connect_closing(home / "projects.db") as conn:
        pid = pdb.create_project(conn, name=project_name, folders=[str(home / "folder")],
            owner_principal_id="owner", grants=[{"principal_id": "owner", "agent_id": "primary",
                                                "permissions": ["read", "write", "share"]}])
    raw = config([pid])
    (home / "config.yaml").write_text(json.dumps(raw))
    parent = resolve_agent_context(raw, session_id="session", profile_home=home)
    child = resolve_agent_context(raw, session_id="child_session", profile_home=home,
                                  parent_context=parent, is_child=True)
    return pid, parent, child


@contextmanager
def owner_control(context, monkeypatch):
    from tui_gateway import server
    from tui_gateway.transport import bind_transport, reset_transport
    agent = SimpleNamespace(runtime_context=context)
    peer = SimpleNamespace(write=lambda frame: True)
    sid = "project-control-session"
    monkeypatch.setitem(server._sessions, sid, {"agent": agent, "transport": peer})
    token = bind_transport(peer)
    rpc_token = server._current_rpc_method.set("runtime.project.create")
    try:
        with agent_runtime_scope(context):
            yield project_control(agent, sid), agent, peer
    finally:
        server._current_rpc_method.reset(rpc_token)
        reset_transport(token)


def test_profile_and_cross_agent_live_grants_cas_and_revocation(tmp_path, monkeypatch):
    from agent.runtime_context import bind_agent_context
    from tui_gateway import server
    a, a_context, a_child = setup(tmp_path / "a")
    b, b_context, _ = setup(tmp_path / "b")
    for project_id, context in ((a, a_context), (b, b_context), (a, a_context)):
        with agent_runtime_scope(context):
            record = authorize_project(context, project_id)
            assert record["name"] == "Shared" and record["project_id"] == project_id
            cwd = record["folders"][0]["path"]
            assert server._project_info_for_cwd(cwd)["id"] == project_id
            with bind_agent_context(None):
                assert server._project_info_for_cwd(cwd) is None
            with pytest.raises(CapabilityDenied, match="outside the immutable"):
                authorize_project(context, b if project_id == a else a)
            access = project_access(context)
            actor = {key: getattr(context.identity, key) for key in ("principal_id", "profile_id", "agent_id")}
            with pytest.raises(CapabilityDenied, match="actor differs"):
                access.assert_access(project_id, {**actor, "agent_id": "foreign"}, "read")
            with access.guard(project_id, actor, "read"):
                assert access.assert_access(project_id, actor, "read")["project_id"] == project_id
                # Context propagation must not lend the SQLite guard to another thread.
                from concurrent.futures import ThreadPoolExecutor
                from contextvars import copy_context
                copied = copy_context()
                with ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(copied.run, access.assert_access, project_id, actor, "read")
                    with pytest.raises(CapabilityDenied, match="cannot cross threads"):
                        future.result(timeout=5)
    assert a_child.policy.project_grants == frozenset({a})
    with agent_runtime_scope(a_child):
        with pytest.raises(CapabilityDenied, match="live project grant"):
            authorize_project(a_child, a)
    with owner_control(a_context, monkeypatch) as (control, _, _):
        original = get_owned_project(control, a)
        grants = original["grants"] + [{"principal_id": "owner", "agent_id": a_child.identity.agent_id,
                                       "permissions": ["read"]}]
        changed = set_project_grants(control, a, original["revision"], grants)
        with pytest.raises(CapabilityDenied, match="Project changed"):
            set_project_grants(control, a, original["revision"], grants)
    with agent_runtime_scope(a_child):
        access = project_access(a_child)
        actor = {key: getattr(a_child.identity, key) for key in ("principal_id", "profile_id", "agent_id")}
        assert access.assert_access(a, actor, "read")["revision"] == changed["revision"]
        with pytest.raises(CapabilityDenied, match="live project grant"):
            cas_project_metadata(a_child, a, changed["revision"], {"purpose": "unauthorized mutation"})
    with owner_control(a_context, monkeypatch) as (control, _, _):
        set_project_grants(control, a, changed["revision"], original["grants"])
    with agent_runtime_scope(a_child):
        with pytest.raises(CapabilityDenied, match="live project grant"):
            access.assert_access(a, actor, "read")  # existing access object cannot cache a revoked grant
        assert server._project_info_for_cwd(str(tmp_path / "a" / "folder")) is None
    with agent_runtime_scope(a_context):
        record = authorize_project(a_context, a)
        changed = cas_project_metadata(a_context, a, record["revision"], {
            "purpose": "durable shared project", "source_refs": [{"capture_id": "capture-1"}],
            "active_mission_refs": [{"session_id": "session", "run_id": "run-1"}]})
        with pdb.connect_closing() as conn:
            pdb.add_folder(conn, a, str(tmp_path / "another"))
        with pytest.raises(CapabilityDenied, match="Project changed"):
            cas_project_metadata(a_context, a, changed["revision"], {"purpose": "stale overwrite"})
        assert authorize_project(a_context, a)["purpose"] == "durable shared project"


def test_legacy_migration_and_explicit_owned_bootstrap_preserve_identity(tmp_path, monkeypatch):
    home = tmp_path / "legacy"
    home.mkdir()
    with sqlite3.connect(home / "projects.db") as conn:
        conn.executescript("""CREATE TABLE projects(id TEXT PRIMARY KEY,slug TEXT UNIQUE,name TEXT,
            description TEXT,created_at INTEGER,archived INTEGER DEFAULT 0);
            CREATE TABLE project_folders(project_id TEXT,path TEXT,label TEXT,is_primary INTEGER,
            added_at INTEGER,PRIMARY KEY(project_id,path));
            INSERT INTO projects VALUES('p_existing','old-project','Existing','original',1,0);
            INSERT INTO project_folders VALUES('p_existing','/work/existing','kept',1,1);""")
    raw = config([])
    (home / "config.yaml").write_text(json.dumps(raw))
    context = resolve_agent_context(raw, session_id="session", profile_home=home)
    with owner_control(context, monkeypatch) as (control, agent, _):
        with pdb.connect_closing() as conn:
            legacy = pdb.project_record(conn, "p_existing")
        adopted = claim_owned_project(control, "p_existing", legacy["revision"])
        assert adopted["id"] == legacy["id"] and adopted["slug"] == legacy["slug"]
        assert adopted["folders"] == legacy["folders"] and adopted["description"] == "original"
        assert adopted["grants"] == [] and adopted["owner_principal_id"] == "owner"
        with pytest.raises(CapabilityDenied, match="unowned legacy"):
            claim_owned_project(control, "p_existing", adopted["revision"])
        fresh = create_owned_project(control, name="New Project", purpose="Created through owned control")
        assert fresh["grants"] == [] and context.policy.project_grants == frozenset()
        with pytest.raises(CapabilityDenied, match="outside the immutable"):
            authorize_project(context, fresh["project_id"])
        changed = update_owned_project(control, fresh["project_id"], fresh["revision"], {"purpose": "New purpose"})
        assert changed["purpose"] == "New purpose"
        with pytest.raises(CapabilityDenied, match="Owned transport"):
            ProjectControl(object(), agent, "project-control-session", None)
        from agent.runtime_commands import _RUN
        run_token = _RUN.set(object())
        try:
            with pytest.raises(CapabilityDenied, match="explicit user project RPC"):
                create_owned_project(control, name="model worker cannot bootstrap")
        finally:
            _RUN.reset(run_token)
        from tui_gateway import server
        server._sessions["project-control-session"]["transport"] = object()
        with pytest.raises(CapabilityDenied, match="no longer current"):
            update_owned_project(control, fresh["project_id"], changed["revision"], {"purpose": "stale transport"})


def test_strict_tools_and_old_broad_rpc_routes_cannot_bootstrap_project_grants(tmp_path, monkeypatch):
    project_id, context, _ = setup(tmp_path / "strict")
    from tools import project_tools
    from tui_gateway import server
    monkeypatch.setenv("HERMES_HOME", context.profile_home)
    with agent_runtime_scope(context):
        listed = json.loads(project_tools.project_list())
        assert [row["id"] for row in listed["projects"]] == [project_id]
        for result in (project_tools.project_create("model-created"), project_tools.project_switch("Shared")):
            assert json.loads(result)["error"] == "project_mutation_unsupported"
        for method, params in (("projects.list", {}), ("projects.create", {"name": "bypass"}),
                               ("projects.tree", {}), ("projects.discover_repos", {})):
            response = server._methods[method]("strict-project", params)
            assert response["error"]["data"]["code"] == "project_control_required"
        with pdb.connect_closing() as conn:
            assert len(pdb.list_projects(conn)) == 1
