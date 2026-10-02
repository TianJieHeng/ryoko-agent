"""Strict recall scopes candidates before hydration and never trusts lineage as ACL."""
from contextlib import contextmanager
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
from openai import OpenAI

from agent.agent_identity import resolve_agent_context
from agent.identity_lifecycle import agent_runtime_scope
from agent.runtime_commands import RuntimeRun, bind_runtime_run, reset_runtime_run
from hermes_state import SessionDB
from tools.session_search_tool import session_search


def config():
    tools = ["memory", "session_search", "delegate_task"]
    return {"agent_identity": {"schema_version": 1, "principal_id": "owner", "profile_id": "profile",
        "primary_agent_id": "primary", "active_agent_id": "specialist",
        "agents": {"primary": {"policy_version": 1, "role": "primary", "memory_backend": "personal_mcp",
                                "allowed_tools": tools},
                   "specialist": {"policy_version": 1, "role": "specialist", "memory_backend": "builtin",
                                  "allowed_tools": tools}},
        "child_policy": {"policy_version": 1, "role": "child", "memory_backend": "builtin",
                         "allowed_tools": tools}}}


def setup(home):
    home.mkdir()
    raw = config()
    (home / "config.yaml").write_text(json.dumps(raw))
    parent = resolve_agent_context(raw, session_id="current", profile_home=home)
    child = resolve_agent_context(raw, session_id="child", profile_home=home, parent_context=parent, is_child=True)
    db = SessionDB(home / "state.db")
    return raw, parent, child, db


def seed(db, context, sid, text, *, parent=None, title=None, binding=None):
    db.create_session(sid, source="cli", parent_session_id=parent)
    db.claim_session_agent_identity(sid, binding or replace(context.identity, session_id=sid).to_record())
    if title:
        db._execute_write(lambda conn: conn.execute("UPDATE sessions SET title = ? WHERE id = ?", (title, sid)))
    return db.append_message(sid, "user", text)


@contextmanager
def execution(db, context, agent=None):
    sid = context.identity.session_id
    if db.get_session(sid) is None:
        seed(db, context, sid, "live turn")
    actor = {key: getattr(context.identity, key) for key in ("principal_id", "profile_id", "agent_id")}
    receipt = db.submit_runtime_command(sid, actor=actor, command={
        "schema_version": 1, "command_id": "search-call", "idempotency_key": "search-call",
        "expected_revision": None, "operation": "submit", "payload": {"text": "recall"}, "identity_binding": actor})
    assert db.acquire_session_turn_lease(sid, "holder", wait_seconds=0)
    generation = db.get_session_turn_lease(sid)["generation"]
    db.claim_runtime_command(sid, "search-call", holder="holder", generation=generation)
    client = None
    if agent is None:
        client = OpenAI(api_key="fixture", base_url="https://fixture.invalid/v1",
                        http_client=httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(500))))
        agent = SimpleNamespace(runtime_context=context, api_mode="chat_completions", provider="openai", client=client)
    run = RuntimeRun(agent, db, sid, "search-call", receipt["run_id"], "holder", generation, context)
    with agent_runtime_scope(context):
        token = bind_runtime_run(run)
        try:
            yield agent
        finally:
            reset_runtime_run(token, run)
            if client is not None:
                client.close()


def test_every_recall_shape_filters_owner_before_hydration_and_profile_switch(tmp_path, monkeypatch):
    from tools.session_search_scope import OwnedSessionSearch
    fixtures = [setup(tmp_path / name) for name in ("a", "b")]
    for _raw, context, child, db in fixtures:
        own = seed(db, context, "own", "needle authorized " + context.profile_home, title="Owned title")
        foreign = seed(db, child, "foreign", "needle CHILD_PRIVATE", parent="own", title="Foreign private title")
        seed(db, context, "foreign_principal", "needle PRINCIPAL_PRIVATE", binding={
            **context.identity.to_record(), "session_id": "foreign_principal", "principal_id": "someone_else"})
        seed(db, context, "foreign_profile", "needle PROFILE_PRIVATE", binding={
            **context.identity.to_record(), "session_id": "foreign_profile", "profile_id": "another_profile"})
        seed(db, context, "foreign_specialist", "needle SPECIALIST_PRIVATE", binding={
            **context.identity.to_record(), "session_id": "foreign_specialist", "agent_id": "another_specialist"})
        db.create_session("legacy", source="cli")
        db.append_message("legacy", "user", "needle LEGACY_PRIVATE")
        # A foreign corpus larger than the discovery scan limit cannot starve the
        # authorized hit. This catches filtering after FTS selection/LIMIT.
        for _ in range(305):
            db.append_message("foreign", "user", "needle CHILD_PRIVATE")
        with agent_runtime_scope(context):
            view = OwnedSessionSearch(db, context)
            assert view.get_message_storage_state(foreign) is None
            assert view.get_session("foreign") is None
            assert view.resolve_session_by_title("Foreign private title") is None
            for args in ({}, {"query": "needle", "sort": "newest"}, {"query": "Owned title"},
                         {"query": "Foreign private title"}, {"session_id": "foreign"},
                         {"session_id": "own", "around_message_id": foreign},
                         {"session_id": "foreign", "around_message_id": own}):
                result = session_search(db=db, **args)
                assert all(secret not in result for secret in (
                    "CHILD_PRIVATE", "PRINCIPAL_PRIVATE", "PROFILE_PRIVATE", "LEGACY_PRIVATE", "SPECIALIST_PRIVATE"))
            found = json.loads(session_search(db=db, query="needle", sort="newest"))
            assert [row["session_id"] for row in found["results"]] == ["own"]
            assert json.loads(session_search(db=db, profile="other"))["error"] == "session_profile_denied"
            assert json.loads(session_search(db=db, session_id="other/own"))["error"] == "session_profile_denied"
            # CJK and corrupt/rebuilding-index fallback share the same SQL ACL.
            db.append_message("own", "assistant", "工程 authorized")
            db.append_message("foreign", "assistant", "工程 CHILD_PRIVATE")
            assert "CHILD_PRIVATE" not in session_search(db=db, query="工程")
            db._fts_stale = True
            fallback = json.loads(session_search(db=db, query="needle"))
            assert [row["session_id"] for row in fallback["results"]] == ["own"]
        with agent_runtime_scope(child):
            assert "authorized" not in session_search(db=db, session_id="own")
            record = json.loads(session_search(db=db, session_id="foreign"))
            assert "CHILD_PRIVATE" in json.dumps(record)
            assert "authorized" not in session_search(db=db, session_id="foreign", around_message_id=own)
            assert OwnedSessionSearch(db, child).get_session("foreign")["parent_session_id"] is None
    for fixture in (fixtures[0], fixtures[1], fixtures[0]):
        _raw, context, _child, db = fixture
        with agent_runtime_scope(context):
            result = session_search(db=db, session_id="own")
            assert context.profile_home in result
            other = fixtures[1][3] if fixture is fixtures[0] else fixtures[0][3]
            assert json.loads(session_search(db=other, session_id="own"))["error"] == "session_profile_denied"
    for *_unused, db in fixtures:
        db.close()
    monkeypatch.setenv("HERMES_HOME", fixtures[0][1].profile_home)
    assert json.loads(session_search())["error"] == "identity_required"


def test_real_registry_dispatch_and_inline_recall_respect_backend_and_live_policy(tmp_path):
    from agent.inline_tool_executors import INLINE_TOOL_EXECUTORS, InlineToolContext
    from tools.registry import registry
    from tools.agent_policy_gate import authorize_tool
    from tools.capability_broker import tool_action
    raw, context, _child, db = setup(tmp_path / "strict")
    seed(db, context, "own", "authorized history")
    with execution(db, context) as agent:
        assert authorize_tool("session_search") is None
        assert tool_action("session_search", {"session_id": "own"}).operation_class == "session_read"
        result = registry.dispatch("session_search", {"session_id": "own"}, db=db)
        assert "authorized history" in result
        agent.session_id = "current"
        agent._get_session_db_for_recall = lambda: db
        assert "authorized history" in INLINE_TOOL_EXECUTORS["session_search"](
            agent, {"session_id": "own"}, InlineToolContext("current"))
        raw["agent_identity"]["agents"]["specialist"]["allowed_tools"].remove("session_search")
        (Path(context.profile_home) / "config.yaml").write_text(json.dumps(raw))
        assert "authorized history" not in registry.dispatch("session_search", {"session_id": "own"}, db=db)
    raw["agent_identity"]["active_agent_id"] = "primary"
    (Path(context.profile_home) / "config.yaml").write_text(json.dumps(raw))
    primary = resolve_agent_context(raw, session_id="primary", profile_home=context.profile_home)
    with agent_runtime_scope(primary):
        assert authorize_tool("session_search") is not None
        assert json.loads(session_search(db=db, session_id="own"))["error"] == "memory_backend_unsupported"
    db.close()
