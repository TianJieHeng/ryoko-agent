"""BE05 authority uses real policy reloads, registry dispatch and SQLite fences."""
import contextvars
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest
from openai import OpenAI

from agent.agent_identity import resolve_agent_context
from agent.identity_lifecycle import agent_runtime_scope
from agent.runtime_commands import RuntimeFenceError, RuntimeRun, bind_runtime_run, reset_runtime_run
from hermes_state import SessionDB
from tools import capability_broker as broker
from tools.registry import registry


def config():
    return {"agent_identity": {"schema_version": 1, "principal_id": "fixture_owner",
        "profile_id": "fixture_profile", "primary_agent_id": "primary", "active_agent_id": "primary",
        "agents": {"primary": {"policy_version": 1, "role": "primary", "memory_backend": "personal_mcp",
                               "allowed_tools": ["todo_list", "execute_code", "opaque_tool", "tool_call", "tool_search", "tool_describe"]}}}}


@pytest.fixture
def live_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    raw = config()
    (tmp_path / "config.yaml").write_text(json.dumps(raw))
    context = resolve_agent_context(raw, session_id="session", profile_home=tmp_path)
    db = SessionDB(tmp_path / "state.db")
    db.create_session("session", source="cli")
    db.claim_session_agent_identity("session", context.identity.to_record())
    identity = context.identity
    actor = {"principal_id": identity.principal_id, "profile_id": identity.profile_id, "agent_id": identity.agent_id}
    command = {"schema_version": 1, "command_id": "command", "idempotency_key": "command",
               "expected_revision": None, "operation": "submit", "payload": {"text": "fixture"},
               "identity_binding": actor}
    receipt = db.submit_runtime_command("session", actor=actor, command=command)
    assert db.acquire_session_turn_lease("session", "owner", wait_seconds=0)
    generation = db.get_session_turn_lease("session")["generation"]
    assert db.claim_runtime_command("session", "command", holder="owner", generation=generation)
    client = OpenAI(api_key="fixture-key", base_url="https://fixture.invalid/v1",
                    http_client=httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(500))))
    agent = SimpleNamespace(api_mode="chat_completions", provider="openai", client=client, runtime_context=context)
    run = RuntimeRun(agent, db, "session", "command", receipt["run_id"], "owner", generation, context)
    import tools.todo_tool  # noqa: F401
    with agent_runtime_scope(context):
        token = bind_runtime_run(run)
        try:
            yield SimpleNamespace(run=run, db=db, home=tmp_path, raw=raw, context=context)
        finally:
            reset_runtime_run(token, run)
    client.close()
    db.close()


def test_capabilities_bind_exact_input_one_use_live_policy_and_real_owner_generation(live_runtime):
    runtime = live_runtime
    arguments = {"action": "read"}
    action = broker.tool_action("todo_list", arguments)
    capability = broker.issue_capability(action)
    arguments["action"] = "mutated_after_issue"
    assert json.loads(action.input_json) == {"action": "read"}
    with pytest.raises(broker.CapabilityDenied):
        broker.consume_capability(capability, broker.tool_action("todo_list", arguments))
    with pytest.raises(broker.CapabilityDenied):
        broker.consume_capability(replace(capability), action)
    broker.consume_capability(capability, action)
    with pytest.raises(broker.CapabilityDenied, match="consumed"):
        broker.consume_capability(capability, action)

    race = broker.issue_capability(action)
    def claim():
        try:
            broker.consume_capability(race, action)
            return "consumed"
        except broker.CapabilityDenied:
            return "denied"
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = [pool.submit(contextvars.copy_context().run, claim) for _ in range(2)]
        assert sorted(result.result() for result in claims) == ["consumed", "denied"]

    # The same process switches A -> B -> A; a ticket never follows its caller
    # into a different profile, even when the declared principal names match.
    other_home = runtime.home / "profile_b"
    other_home.mkdir()
    (other_home / "config.yaml").write_text(json.dumps(runtime.raw))
    other = resolve_agent_context(runtime.raw, session_id="session", profile_home=other_home)
    scoped = broker.issue_capability(action)
    with agent_runtime_scope(other):
        with pytest.raises(RuntimeFenceError, match="identity"):
            broker.consume_capability(scoped, action)
    broker.consume_capability(scoped, action)

    changed = broker.issue_capability(action)
    runtime.raw["agent_identity"]["agents"]["primary"]["allowed_tools"].remove("todo_list")
    (runtime.home / "config.yaml").write_text(json.dumps(runtime.raw))
    with pytest.raises(broker.CapabilityDenied, match="policy changed"):
        broker.consume_capability(changed, action)
    runtime.raw["agent_identity"]["agents"]["primary"]["allowed_tools"].append("todo_list")
    (runtime.home / "config.yaml").write_text(json.dumps(runtime.raw))
    stale = broker.issue_capability(action)
    copied = contextvars.copy_context()
    runtime.db.release_session_turn_lease("session", "owner")
    assert runtime.db.acquire_session_turn_lease("session", "successor", wait_seconds=0)
    with pytest.raises(RuntimeFenceError, match="generation"):
        copied.run(broker.consume_capability, stale, action)


def test_exact_approval_has_no_broad_cache_and_rejects_action_expiry_and_ui_absence(live_runtime, monkeypatch):
    action = broker.tool_action("todo_list", {"action": "read", "target": "first"})
    preview = broker.preview_action(action)
    with pytest.raises(broker.CapabilityDenied):
        broker.resolve_approval(preview, "different digest", "once")
    broker.resolve_approval(preview, preview.approval_digest, "once")
    with pytest.raises(broker.CapabilityDenied):
        broker.issue_capability(broker.tool_action("todo_list", {"action": "read", "target": "second"}), approval=preview)
    capability = broker.issue_capability(action, approval=preview)
    assert capability.approval_digest == preview.approval_digest
    with pytest.raises(broker.CapabilityDenied):
        broker.issue_capability(action, approval=preview)
    broker.consume_capability(capability, action)

    expired = broker.preview_action(action, ttl_seconds=1)
    original_time = broker.time.time
    monkeypatch.setattr(broker.time, "time", lambda: expired.expires_at + 1)
    with pytest.raises(broker.CapabilityDenied):
        broker.resolve_approval(expired, expired.approval_digest, "once")
    monkeypatch.setattr(broker.time, "time", original_time)
    from tools import approval
    from tools.capability_approval import request_exact_approval
    monkeypatch.setattr(approval, "_presence", lambda: (None, False, False, False))
    monkeypatch.setattr(approval, "_yolo_active", lambda: True)
    with pytest.raises(broker.CapabilityDenied) as failure:
        request_exact_approval(action)
    assert failure.value.code == "approval_surface_unavailable" and failure.value.pending

    shown = []
    def human(command, description, **kwargs):
        shown.append((command, description, kwargs))
        assert kwargs["allow_session"] is False and kwargs["allow_permanent"] is False
        return "once"
    monkeypatch.setattr(approval, "_presence", lambda: (human, True, False, False))
    first = request_exact_approval(action)
    broker.issue_capability(action, approval=first)
    second = request_exact_approval(action)
    broker.issue_capability(action, approval=second)
    assert first.approval_id != second.approval_id and len(shown) == 2
    assert "first" in shown[0][0] and action.digest in shown[0][1]


def test_real_registry_consumes_one_forwarded_ticket_and_refuses_uncertified_handlers(live_runtime, monkeypatch):
    arguments = {"action": "read"}
    capability = broker.prepare_tool_capability("todo_list", arguments)
    with broker.dispatch_capability(capability, "todo_list", arguments):
        first = registry.dispatch("todo_list", arguments)
        second = registry.dispatch("todo_list", arguments)
    assert "capability" not in str(first)
    assert "cannot invoke its handler twice" in str(second)
    capability = broker.prepare_tool_capability("todo_list", arguments)
    with broker.dispatch_capability(capability, "todo_list", arguments):
        changed = registry.dispatch("todo_list", {"action": "write", "todos": []})
    assert "scope" in str(changed)
    called = []
    monkeypatch.setattr(registry, "_tools", dict(registry._tools))
    registry.register(name="opaque_tool", toolset="fixture", schema={"name": "opaque_tool"},
                      handler=lambda *_args, **_kwargs: called.append(True) or "unexpected")
    assert "unsupported" in registry.dispatch("opaque_tool", {"actor": "primary", "approved": True})
    assert called == []
