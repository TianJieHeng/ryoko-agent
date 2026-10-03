"""Named conversation targets preserve stable identity, ownership and startup pins."""
import json
from types import SimpleNamespace

import pytest

from agent.identity_lifecycle import agent_runtime_scope
from tests.tui_gateway.test_agent_configuration_rpc import construct
from tests.tui_gateway.test_artifact_rpc import denied, result
from tests.tui_gateway.test_runtime_conversations_rpc import config_for, runtime  # noqa: F401


def configure_specialists(rt):
    for label in ("a", "b"):
        config = config_for(label)
        config["agent_identity"]["agents"].update({name: {
            "policy_version": 1, "role": "specialist", "memory_backend": "builtin",
            "allowed_tools": ["memory", "todo_list"], "project_grants": [],
        } for name in ("researcher", "writer")})
        (rt.homes[label] / "config.yaml").write_text(json.dumps(config), encoding="utf-8")


def primary_controls(rt):
    conversation = result(rt.call("create", idempotency_key="primary-controls", title="Owner"))["conversation"]
    bound = result(rt.call("bind", conversation_id=conversation["conversation_id"]))
    agent = SimpleNamespace()
    with rt.server._profile_build_scope(str(rt.homes["a"])):
        construct(agent, session_id=conversation["conversation_id"], session_db=rt.stores["a"])
    rt.server._sessions[bound["session_id"]]["agent"] = agent

    def call(method, **params):
        return rt.server.dispatch({"jsonrpc": "2.0", "id": "configuration", "method": "runtime.agent." + method,
            "params": {"schema_version": 1, "session_id": bound["session_id"], **params}}, transport=rt.peer)

    return call


def enrolled(rt, sid):
    with rt.stores["a"]._runtime_read() as conn:
        row = conn.execute("SELECT * FROM agent_configuration_sessions WHERE session_id=?", (sid,)).fetchone()
        assert row is not None, "Conversation creation must atomically enroll the selected configuration"
        return dict(row)


def build_at_provider_boundary(rt, bound, monkeypatch):
    """Exercise the real resume kwargs, AIAgent and identity constructor without provider use."""
    import agent.agent_init as initialization

    class ReachedProvider(Exception):
        pass

    observed = {}

    def witness(agent, *_args):
        observed.update(context=agent.runtime_context, prompt=agent.ephemeral_system_prompt or "")
        raise ReachedProvider()

    with monkeypatch.context() as patch:
        patch.setattr(initialization, "_finalize_routing", witness)
        patch.setattr(rt.server, "_resolve_agent_model_runtime", lambda *_args: (
            "fixture/model", {"provider": "custom", "base_url": "https://fixture.invalid/v1", "api_mode": "chat_completions"}))
        session = rt.server._sessions[bound["session_id"]]
        kwargs = rt.server._deferred_build_agent_kwargs(session, rt.stores["a"])
        with rt.server._profile_build_scope(str(rt.homes["a"])):
            with pytest.raises(ReachedProvider):
                rt.server._make_agent(bound["session_id"], session["session_key"], **kwargs)
    return observed


def test_selected_identity_scopes_lists_receipts_titles_and_homes(runtime):
    rt = runtime
    configure_specialists(rt)
    primary = result(rt.call("create", idempotency_key="same-key", title="A new thought"))["conversation"]
    specialist = result(rt.call("create", agent_id="researcher", idempotency_key="same-key", title="A new thought"))["conversation"]
    writer = result(rt.call("create", agent_id="writer", idempotency_key="same-key", title="A new thought"))["conversation"]
    assert {primary["agent_id"], specialist["agent_id"], writer["agent_id"]} == {"ryoko", "researcher", "writer"}
    assert len({row["conversation_id"] for row in (primary, specialist, writer)}) == 3
    for selected, expected in ((None, primary), ("researcher", specialist), ("writer", writer)):
        params = {} if selected is None else {"agent_id": selected}
        assert result(rt.call("list", **params))["conversations"] == [expected]
        receipt = result(rt.call("operation.get", idempotency_key="same-key", **params))
        assert receipt["found"] and receipt["conversation"] == expected
    assert not result(rt.call("operation.get", agent_id="researcher", idempotency_key="missing"))["found"]
    sid = specialist["conversation_id"]
    original = rt.stores["a"].get_session_model_config_value(sid, "agent_identity")
    assert original["agent_id"] == "researcher" and original["lifecycle"] == "stable"
    rt.stores["a"].append_message(sid, "assistant", content="Researcher's private conversation")
    renamed = result(rt.call("rename", conversation_id=sid, idempotency_key="rename-specialist",
                             expected_revision=specialist["revision"], title="ryoko"))["conversation"]
    assert renamed["agent_id"] == "researcher" and renamed["title"] == "ryoko"
    assert rt.stores["a"].get_session_model_config_value(sid, "agent_identity") == original
    assert result(rt.call("operation.get", agent_id="researcher", idempotency_key="rename-specialist"))["conversation"] == renamed
    assert not result(rt.call("operation.get", idempotency_key="rename-specialist"))["found"]
    for label, expected in (("a", [renamed]), ("b", []), ("a", [renamed])):
        assert result(rt.call("list", label, agent_id="researcher"))["conversations"] == expected
    denied(rt.call("history", "b", conversation_id=sid), "session_not_found")
    denied(rt.call("bind", "b", conversation_id=sid), "session_not_found")
    assert result(rt.call("history", conversation_id=sid))["messages"][0]["text"] == "Researcher's private conversation"
    denied(rt.call("create", agent_id="child_" + "a" * 32, idempotency_key="ephemeral"))
    denied(rt.call("create", agent_id="unknown", idempotency_key="unknown"))
    assert denied(rt.call("create", agent_id="researcher", idempotency_key="spoof", principal_id="owner-b"))["code"] == 4000
    assert denied(rt.call("history", conversation_id=sid, agent_id="ryoko"))["code"] == 4000
    denied(rt.call("list", agent_id="researcher", via=SimpleNamespace(write=lambda frame: True)), "runtime_transport_unsupported")
    result(rt.call("create", agent_id="researcher", idempotency_key="second-research", title="Another research thread"))
    page = result(rt.call("list", agent_id="researcher", limit=1))
    assert page["has_more"] and page["next_cursor"]
    denied(rt.call("list", agent_id="writer", cursor=page["next_cursor"]), "invalid_cursor")
    denied(rt.call("list", "b", agent_id="researcher", cursor=page["next_cursor"]), "invalid_cursor")
    # Matching configured names and even owner/profile IDs cannot authorize a
    # store handle from a different home.
    (rt.homes["b"] / "config.yaml").write_bytes((rt.homes["a"] / "config.yaml").read_bytes())
    own_b = rt.stores["b"]
    rt.stores["b"] = rt.stores["a"]
    try:
        denied(rt.call("history", "b", conversation_id=sid))
    finally:
        rt.stores["b"] = own_b
    assert result(rt.call("history", conversation_id=sid))["messages"][0]["text"] == "Researcher's private conversation"


def test_create_pins_revision_before_bind_and_archived_agent_keeps_owned_history(runtime, monkeypatch):
    from agent.individual_memory_scope import IndividualMemoryScope
    from tools.capability_broker import CapabilityDenied
    rt = runtime
    configure_specialists(rt)
    control = primary_controls(rt)
    target = result(control("get", agent_id="researcher"))["agent"]
    copied = result(control("create", copy_from_agent_id="researcher",
                            config={**target["config"], "name": "ryoko"}))["agent"]
    assert copied["role"] == "specialist" and copied["builtin_memory_namespace"] != target["builtin_memory_namespace"]
    copied_conversation = result(rt.call("create", agent_id=copied["agent_id"], idempotency_key="copied-specialist"))["conversation"]
    assert copied_conversation["agent_id"] == copied["agent_id"]
    copied_observed = build_at_provider_boundary(rt,
        result(rt.call("bind", conversation_id=copied_conversation["conversation_id"])), monkeypatch)
    assert copied_observed["context"].identity.agent_id == copied["agent_id"]
    assert copied_observed["context"].policy.memory_backend == "builtin"
    with agent_runtime_scope(copied_observed["context"]):
        assert IndividualMemoryScope.from_context(copied_observed["context"]).namespace_id == copied["builtin_memory_namespace"]
    first = result(control("update", agent_id="researcher", expected_revision=target["revision"],
        config={**target["config"], "instructions": "First reviewed startup instructions"}))["agent"]
    old = result(rt.call("create", agent_id="researcher", idempotency_key="before-edit"))["conversation"]
    old_enrollment = enrolled(rt, old["conversation_id"])
    assert old_enrollment["selected_agent_id"] == "researcher"
    old_binding = rt.stores["a"].get_session_model_config_value(old["conversation_id"], "agent_identity")
    second = result(control("update", agent_id="researcher", expected_revision=first["revision"],
        config={**first["config"], "name": "ryoko", "instructions": "Next session instructions"}))["agent"]
    assert second["role"] == "specialist" and second["memory_backend"] == "builtin"
    assert second["builtin_memory_namespace"] == first["builtin_memory_namespace"]
    assert denied(control("update", agent_id="researcher", expected_revision=second["revision"],
        config={**second["config"], "role": "primary"}))["code"] == 4000
    replay = result(rt.call("create", agent_id="researcher", idempotency_key="before-edit"))
    assert replay["conversation"] == old and not replay["created"]
    assert enrolled(rt, old["conversation_id"])["snapshot_id"] == old_enrollment["snapshot_id"]
    assert rt.stores["a"].get_session_model_config_value(old["conversation_id"], "agent_identity") == old_binding
    bound_old = result(rt.call("bind", conversation_id=old["conversation_id"]))
    assert bound_old["conversation"]["agent_id"] == "researcher" and bound_old["readiness"] == "building"
    observed_old = build_at_provider_boundary(rt, bound_old, monkeypatch)
    context = observed_old["context"]
    assert context.identity.to_record() == old_binding and context.policy.role == "specialist"
    assert context.policy.memory_backend == "builtin" and "memory" in context.policy.allowed_tools
    assert "First reviewed startup instructions" in observed_old["prompt"] and "Next session instructions" not in observed_old["prompt"]
    with agent_runtime_scope(context):
        assert IndividualMemoryScope.from_context(context).namespace_id == first["builtin_memory_namespace"]
    fresh = result(rt.call("create", agent_id="researcher", idempotency_key="after-edit"))["conversation"]
    assert enrolled(rt, fresh["conversation_id"])["snapshot_id"] != old_enrollment["snapshot_id"]
    fresh_bound = result(rt.call("bind", conversation_id=fresh["conversation_id"]))
    observed_fresh = build_at_provider_boundary(rt, fresh_bound, monkeypatch)
    assert "Next session instructions" in observed_fresh["prompt"]
    assert "memory" in observed_fresh["context"].policy.allowed_tools
    assert observed_fresh["context"].identity.agent_id == "researcher"
    narrowed = result(control("update", agent_id="researcher", expected_revision=second["revision"],
        config={**second["config"], "memory_allowed": False}))["agent"]
    with pytest.raises(CapabilityDenied, match="narrowed or archived"):
        build_at_provider_boundary(rt, bound_old, monkeypatch)
    rt.stores["a"].append_message(old["conversation_id"], "user", content="Keep this old discussion")
    archived = result(control("archive", agent_id="researcher", expected_revision=narrowed["revision"]))["agent"]
    assert archived["archived"]
    denied(rt.call("create", agent_id="researcher", idempotency_key="after-archive"), "agent_unavailable")
    assert result(rt.call("history", conversation_id=old["conversation_id"]))["messages"][0]["text"] == "Keep this old discussion"
    assert result(rt.call("operation.get", agent_id="researcher", idempotency_key="before-edit"))["conversation"] == old
    assert {row["conversation_id"] for row in result(rt.call("list", agent_id="researcher"))["conversations"]} == {
        old["conversation_id"], fresh["conversation_id"]}
    # Historical text remains readable; archived runtime dispatch is revoked.
    with pytest.raises(CapabilityDenied, match="narrowed or archived"):
        build_at_provider_boundary(rt, result(rt.call("bind", conversation_id=old["conversation_id"])), monkeypatch)


def test_failed_create_rolls_back_configuration_enrollment_and_receipt_together(runtime, monkeypatch):
    from hermes_state_runtime import RuntimeStoreError
    rt = runtime
    configure_specialists(rt)
    db = rt.stores["a"]

    def counts():
        with db._runtime_read() as conn:
            return tuple(conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0] for table in (
                "sessions", "runtime_conversations", "agent_configuration_sessions", "runtime_conversation_operations"))

    before = counts()
    with monkeypatch.context() as patch:
        def fail_receipt(*_args, **_kwargs):
            raise RuntimeStoreError("fixture_commit_failure", "Simulated transaction failure before receipt")
        patch.setattr(db, "_conversation_record_operation", fail_receipt)
        denied(rt.call("create", agent_id="researcher", idempotency_key="retry-after-rollback"), "fixture_commit_failure")
    assert counts() == before
    assert not result(rt.call("operation.get", agent_id="researcher", idempotency_key="retry-after-rollback"))["found"]
    created = result(rt.call("create", agent_id="researcher", idempotency_key="retry-after-rollback"))["conversation"]
    assert created["agent_id"] == "researcher"
    assert enrolled(rt, created["conversation_id"])["selected_agent_id"] == "researcher"
    assert counts() == tuple(number + 1 for number in before)
    control = primary_controls(rt)
    target = result(control("get", agent_id="researcher"))["agent"]
    before_race = counts()
    writer = db._execute_write
    intervened = False
    def change_target_before_transaction(callback, *args, **kwargs):
        nonlocal intervened
        if not intervened and rt.server._current_rpc_method.get() == "runtime.conversation.create":
            intervened = True
            result(control("update", agent_id="researcher", expected_revision=target["revision"],
                           config={**target["config"], "memory_allowed": False}))
        return writer(callback, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(db, "_execute_write", change_target_before_transaction)
        denied(rt.call("create", agent_id="researcher", idempotency_key="race-target", title="Raced target"), "revision_conflict")
    assert intervened and counts() == before_race
    assert not result(rt.call("operation.get", agent_id="researcher", idempotency_key="race-target"))["found"]
    retried = result(rt.call("create", agent_id="researcher", idempotency_key="race-target", title="Raced target"))["conversation"]
    record = enrolled(rt, retried["conversation_id"])
    with db._runtime_read() as conn:
        records = json.loads(conn.execute("SELECT records_json FROM agent_configuration_snapshots WHERE snapshot_id=?",
                                         (record["snapshot_id"],)).fetchone()[0])
    assert records["researcher"]["config"]["memory_allowed"] is False
