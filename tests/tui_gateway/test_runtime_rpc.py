"""Real profile/store/transport boundaries for the durable runtime RPC surface."""

import json
import os
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
import httpx
import openai


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    from agent.agent_identity import resolve_agent_context
    from hermes_state import SessionDB
    from tui_gateway import server

    homes, agents, peers, sessions, dispatched = {}, {}, {}, {}, []
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    for label in ("a", "b"):
        home = tmp_path / label
        home.mkdir()
        config = {"agent_identity": {
            "schema_version": 1, "principal_id": "fixture-owner", "profile_id": f"profile-{label}",
            "primary_agent_id": "ryoko", "active_agent_id": "ryoko",
            "agents": {"ryoko": {"policy_version": 1, "role": "primary", "memory_backend": "personal_mcp"}},
        }}
        (home / "config.yaml").write_text(json.dumps(config))
        (home / ".env").write_text(f"PROFILE_ONLY_SECRET=fixture-{label}\n")
        context = resolve_agent_context(config, session_id="same-stored-id", profile_home=home)
        db = SessionDB(home / "state.db")
        db.create_session(context.identity.session_id, source="tui")
        db.claim_session_agent_identity(context.identity.session_id, context.identity.to_record())
        client = openai.OpenAI(api_key="private-fixture-key", base_url="https://private-fixture.invalid/v1",
            http_client=httpx.Client(transport=httpx.MockTransport(
                lambda request: httpx.Response(500, json={"error": "unexpected fixture request"}))))
        agent = SimpleNamespace(runtime_context=context, _session_db=db, session_id=context.identity.session_id,
                                api_mode="chat_completions", provider="openai", client=client)
        peer = SimpleNamespace(write=lambda _frame: True)
        homes[label], agents[label], peers[label] = home, agent, peer
        sessions[f"live-{label}"] = {"agent": agent, "profile_home": str(home), "transport": peer,
                                    "session_key": context.identity.session_id, "history": [],
                                    "history_lock": threading.RLock()}
    monkeypatch.setenv("HERMES_HOME", str(homes["a"]))
    monkeypatch.setattr(server, "_sessions", sessions)

    def record_worker(_rid, sid, session, _text, *, image_paths, runtime_command_receipt):
        """Provider boundary only; real RPC, admission, identity and SQLite remain in use."""
        from agent.secret_scope import get_secret
        from hermes_constants import get_hermes_home

        label = sid.removeprefix("live-")
        assert get_hermes_home() == homes[label]
        assert get_secret("PROFILE_ONLY_SECRET") == f"fixture-{label}"
        agent = session["agent"]
        db = agent._session_db
        assert db.try_acquire_session_turn_lease(agent.session_id, "fixture-worker", ttl_seconds=60)
        generation = db.get_session_turn_lease(agent.session_id)["generation"]
        assert db.claim_runtime_command(agent.session_id, runtime_command_receipt["command_id"],
                                        holder="fixture-worker", generation=generation)
        db.append_runtime_event(agent.session_id, "model.completed", {"provider_response": "private-fixture-output"},
                                holder="fixture-worker", generation=generation, run_id=runtime_command_receipt["run_id"],
                                operation_id="fixture-model-attempt")
        db.finish_runtime_command(agent.session_id, runtime_command_receipt["command_id"],
                                  holder="fixture-worker", generation=generation,
                                  result={"final_response": "private-fixture-output"})
        db.release_session_turn_lease(agent.session_id, "fixture-worker")
        dispatched.append(runtime_command_receipt["command_id"])
        with session["history_lock"]:
            session["running"] = False
        return True

    monkeypatch.setattr(server, "_run_prompt_submit", record_worker)

    def call(method, label="a", *, via=None, **params):
        defaults = {"session_id": f"live-{label}"}
        if method != "runtime.capabilities":
            defaults["schema_version"] = 1
        return server.dispatch({"jsonrpc": "2.0", "id": "fixture-rpc", "method": method,
                                "params": {**defaults, **params}}, transport=via or peers[label])

    yield SimpleNamespace(server=server, homes=homes, agents=agents, peers=peers, sessions=sessions,
                          dispatched=dispatched, call=call)
    from tui_gateway import prompt_admission
    if prompt_admission._HANDLE is not None:
        prompt_admission._HANDLE.cancel(wait=2)
        prompt_admission._HANDLE = None
    prompt_admission._STARTED = False
    prompt_admission._STOPPING = False
    for session in sessions.values():
        server._release_active_session_slot(session)
    for agent in agents.values():
        agent.client.close()
        agent._session_db.close()


def envelope(command_id="command-1", **changes):
    return {"command_id": command_id, "idempotency_key": command_id, "expected_revision": 0,
            "operation": "submit", "payload": {"text": "Private fixture request"}, **changes}


def test_capability_inspection_uses_frozen_view_and_redacts_provider_authority(runtime, monkeypatch):
    from agent.tool_view import ToolView

    agent = runtime.agents["a"]
    view = ToolView("fixture-catalog", agent.runtime_context.policy.digest, (), (), (), (), {})
    agent.tool_view = view

    def forbidden_refresh(*args, **kwargs):
        raise AssertionError("Capability inspection must not rebuild tool schemas")

    monkeypatch.setattr("model_tools.get_tool_definitions", forbidden_refresh)
    first = runtime.call("runtime.capabilities")["result"]
    second = runtime.call("runtime.capabilities")["result"]
    assert first["tool_view"] == second["tool_view"] == view.to_record()
    assert agent.tool_view is view
    assert first["provider"]["declaration_scope"] == "adapter"
    assert first["provider"]["model_capabilities"] == "unverified"
    assert first["provider"]["execution_owner"] == "hermes"
    assert first["provider"]["cancellation"] != "provider_acknowledgment"
    assert "private-fixture" not in json.dumps(first)
    agent.tool_view = ToolView("foreign", "other-policy", ("private-foreign-tool",), (), (), (), {})
    foreign = runtime.call("runtime.capabilities")["result"]
    assert foreign["tool_view"] is None and "private-foreign-tool" not in json.dumps(foreign)
    agent.tool_view = view
    known_client = agent.client
    try:
        agent.client = object()
        unknown = runtime.call("runtime.capabilities")["result"]
        assert not unknown["provider"]["durable_execution"]
        assert not any(item["executes"] for item in unknown["operations"])
        denied = runtime.call("runtime.command", **envelope("opaque"))
        assert denied["error"]["data"]["code"] == "runtime_transport_unsupported"
    finally:
        agent.client = known_client


def test_real_two_profile_transport_boundaries_and_strict_identity(runtime):
    from hermes_constants import get_hermes_home

    before = dict(os.environ)
    for label in ("a", "b", "a"):
        caps = runtime.call("runtime.capabilities", label)["result"]
        assert caps["strict_identity_required"] and caps["durable_replay"]
        assert caps["max_events"] == 200
        assert not next(op for op in caps["operations"] if op["operation"] == "approval")["accepts_commands"]
        snapshot = runtime.call("runtime.snapshot", label)["result"]
        assert snapshot["revision"] == 0
        assert snapshot["session_id"] == runtime.agents[label].session_id
        other = "b" if label == "a" else "a"
        for method, params in (("runtime.capabilities", {}), ("runtime.snapshot", {}),
                               ("runtime.events.since", {}), ("runtime.command", envelope())):
            assert runtime.call(method, label, via=runtime.peers[other], **params)["error"]["code"] == 4001
    assert dict(os.environ) == before
    assert get_hermes_home() == runtime.homes["a"]
    for forged in ({"principal_id": "other"}, {"agent_id": "other"},
                   {"identity_binding": {"agent_id": "other"}}, {"profile": "b"}):
        assert runtime.call("runtime.command", **envelope(**forged))["error"]["code"] == 4000
    agent = runtime.agents["a"]
    context = agent.runtime_context
    original_sid = agent.session_id
    agent._session_db.end_session(original_sid, "compression")
    agent._session_db.create_session("compressed-tip", source="tui", parent_session_id=original_sid,
                                     model_config={"agent_identity": context.identity.to_record()})
    agent.session_id = "compressed-tip"
    assert runtime.call("runtime.snapshot")["result"]["session_id"] == original_sid
    assert runtime.call("runtime.command", **envelope())["result"]["status"] == "accepted"
    agent.api_mode = "codex_app_server"
    unsupported = runtime.call("runtime.capabilities")["result"]
    assert not any(item["executes"] or item["accepts_commands"] for item in unsupported["operations"])
    assert "result" in runtime.call("runtime.snapshot")
    assert runtime.call("runtime.command", **envelope("unsupported", expected_revision=None))["error"]["data"]["code"] == "runtime_transport_unsupported"
    agent.api_mode = "chat_completions"
    agent.runtime_context = None
    assert runtime.call("runtime.command", **envelope())["error"]["data"]["code"] == "identity_required"
    agent.runtime_context = context
    agent._session_db.patch_session_model_config(agent.session_id, {"agent_identity": None})
    for method, params in (("runtime.snapshot", {}), ("runtime.command", envelope())):
        assert runtime.call(method, **params)["error"]["data"]["code"] == "identity_mismatch"


def test_physical_attempt_replay_is_typed_and_never_exports_raw_provider_data(runtime):
    receipt = runtime.call("runtime.command", **envelope())["result"]
    agent = runtime.agents["a"]
    db = agent._session_db
    assert db.try_acquire_session_turn_lease(agent.session_id, "attempt-fixture", ttl_seconds=60)
    generation = db.get_session_turn_lease(agent.session_id)["generation"]
    attempt = {"attempt_id": "physical-1", "reason": "throttled", "provider_account_ref": "opaque-account-ref",
               "reservation_id": "physical-1", "remote_acceptance": "rejected", "logical_request_id": "logical-1"}
    for payload in ({"physical_attempt": attempt, "raw_response": "private-fixture-provider-data"},
                    {"physical_attempt": {**attempt, "api_key": "private-fixture-secret"}}):
        db.append_runtime_event(agent.session_id, "model.failed", payload, holder="attempt-fixture",
                                generation=generation, run_id=receipt["run_id"], operation_id="physical-1")
    db.release_session_turn_lease(agent.session_id, "attempt-fixture")
    replay = runtime.call("runtime.events.since")["result"]
    events = [event for event in replay["events"] if event["type"] == "model.failed"]
    assert events[0]["payload"]["physical_attempt"] == attempt
    assert events[1]["payload"] == {}
    assert "private-fixture" not in json.dumps(replay)


def test_command_receipt_is_idempotent_restart_replay_and_protocol_freshness(runtime):
    from hermes_state import SessionDB

    first_snapshot = runtime.call("runtime.snapshot")["result"]
    response = runtime.call("runtime.command", **envelope())
    assert "error" not in response, response
    first = response["result"]
    assert first["status"] == "accepted"
    assert runtime.call("runtime.command", **envelope())["result"] == first
    assert runtime.dispatched == [first["command_id"]]
    initial_migration = runtime.call("runtime.events.since", cursor=first_snapshot["last_cursor"])["result"]
    assert initial_migration["status"] == "snapshot_required"
    replay = runtime.call("runtime.events.since")["result"]
    accepted = [event for event in replay["events"] if event["type"] == "command.accepted"]
    assert len(accepted) == 1
    assert accepted[0]["run_id"] == first["run_id"]
    assert "Private fixture request" not in json.dumps(replay)
    assert "private-fixture-output" not in json.dumps(replay)
    model_events = [event for event in replay["events"] if event["type"] == "model.completed"]
    assert len(model_events) == 1 and model_events[0]["payload"] == {}
    assert runtime.call("runtime.snapshot", "b")["result"]["revision"] == 0
    assert runtime.call("runtime.command", **envelope(payload={"text": "Changed intent"}))["error"]["data"]["code"] == "idempotency_conflict"
    assert runtime.call("runtime.command", **envelope("command-2"))["error"]["data"]["code"] == "revision_conflict"
    agent = runtime.agents["a"]
    agent._session_db.close()
    agent._session_db = SessionDB(runtime.homes["a"] / "state.db")
    after = runtime.call("runtime.events.since")["result"]
    assert after == replay
    assert runtime.call("runtime.command", **envelope())["result"] == first
    assert runtime.dispatched == [first["command_id"]]
    for cursor in ("expired-process-epoch:999999999", first_snapshot["last_cursor"].split(":")[0] + ":999999999"):
        expired = runtime.call("runtime.events.since", cursor=cursor)["result"]
        assert expired["status"] == "snapshot_required"
        assert expired["snapshot"]["last_cursor"] == expired["last_cursor"]
        assert expired["snapshot"]["revision"] >= first["durable_revision"]
    for change in ({"schema_version": 2}, {"schema_version": True}, {"schema_version": "1"}):
        assert runtime.call("runtime.snapshot", **change)["error"]["data"]["code"] == "unsupported_schema"
    for limit in (0, 201, True, "2"):
        assert "error" in runtime.call("runtime.events.since", limit=limit)
    denied = runtime.call("runtime.command", **envelope("approval-1", expected_revision=None,
                           operation="approval", payload={"approval_id": "unknown", "decision": "approve"}))["result"]
    assert denied["status"] == "rejected"
    assert denied["conflict"]["code"] == "operation_not_supported"


def test_controls_and_checkpoints_preserve_safe_outcomes_and_retention(runtime):
    from agent.runtime_commands import RuntimeRun

    idle = runtime.call("runtime.command", **envelope("idle-cancel", operation="cancel", payload={}))["result"]
    assert idle["status"] == "rejected" and idle["conflict"]["code"] == "no_active_run"
    agent = runtime.agents["a"]
    db, sid = agent._session_db, agent.session_id
    identity = agent.runtime_context.identity
    actor = {key: getattr(identity, key) for key in ("principal_id", "agent_id", "profile_id")}
    command = {"schema_version": 1, **envelope("active-run"), "identity_binding": actor}
    receipt = db.submit_runtime_command(sid, actor, command)
    old_cursor = db.read_runtime_snapshot(sid)["last_cursor"]
    assert db.try_acquire_session_turn_lease(sid, "active-worker", ttl_seconds=60)
    generation = db.get_session_turn_lease(sid)["generation"]
    assert db.claim_runtime_command(sid, "active-run", holder="active-worker", generation=generation)
    agent._active_runtime_run = RuntimeRun(agent, db, sid, "active-run", receipt["run_id"],
                                          "active-worker", generation, agent.runtime_context)
    applied = []
    agent.steer = lambda text: applied.append(("steer", text)) or True
    agent.interrupt = lambda reason, *, hard_cancel: applied.append(("cancel", reason, hard_cancel)) or True
    steer = envelope("steer-1", operation="steer", expected_revision=None, payload={"text": "A private correction"})
    cancel = envelope("cancel-1", operation="cancel", expected_revision=None, payload={"reason": "A private reason"})
    for value in (steer, cancel):
        first = runtime.call("runtime.command", **value)["result"]
        assert first["status"] == "accepted"
        assert runtime.call("runtime.command", **value)["result"] == first
    assert applied == [("steer", "A private correction"), ("cancel", "A private reason", True)]
    replay = runtime.call("runtime.events.since", cursor=old_cursor, limit=2)["result"]
    assert len(replay["events"]) == 2 and replay["has_more"]
    all_events = runtime.call("runtime.events.since")["result"]["events"]
    outcomes = [event["payload"]["control_outcome"] for event in all_events if "control_outcome" in event["payload"]]
    assert outcomes == ["steer_queued", "cancel_requested"]
    assert "private correction" not in json.dumps(all_events) and "private reason" not in json.dumps(all_events)
    checkpoint = {"schema_version": 1, "config_version": "fixture-config", "policy_version": "fixture-policy",
                  "runtime_version": "v1", "prompt_projection_version": "v1",
                  "outstanding_requests": [{"request_id": "request-1", "kind": "approval", "status": "pending"}],
                  "artifacts": [{"artifact_id": "artifact-1", "version": "v1"}],
                  "unresolved_effects": [{"effect_id": "effect-1", "status": "outcome_uncertain"}]}
    revision = db.read_runtime_snapshot(sid)["revision"]
    db.publish_runtime_checkpoint(sid, checkpoint, holder="active-worker", generation=generation,
                                  expected_revision=revision, included_seq=revision)
    snapshot = runtime.call("runtime.snapshot")["result"]
    for field in ("outstanding_requests", "artifacts", "unresolved_effects"):
        assert snapshot[field] == checkpoint[field]
    db.prune_runtime_events(sid, through_seq=revision, holder="active-worker", generation=generation)
    expired = runtime.call("runtime.events.since", cursor=old_cursor)["result"]
    assert expired["status"] == "snapshot_required"
    assert expired["snapshot"] == snapshot
    assert expired["last_cursor"] == snapshot["last_cursor"]
    db.release_session_turn_lease(sid, "active-worker", generation=generation)


def test_accepted_retry_keeps_receipt_without_dispatching_a_closing_session(runtime):
    from agent.identity_lifecycle import agent_runtime_scope
    from agent.runtime_commands import submit_command

    agent = runtime.agents["a"]
    with agent_runtime_scope(agent.runtime_context):
        receipt = submit_command(agent, {"schema_version": 1, **envelope()})
    session = runtime.sessions["live-a"]
    session["_closing"] = True
    assert runtime.call("runtime.command", **envelope())["result"] == receipt
    assert runtime.dispatched == []
    session.pop("_closing")
    assert runtime.call("runtime.command", **envelope())["result"] == receipt
    assert runtime.dispatched == [receipt["command_id"]]
    assert runtime.call("runtime.command", **envelope())["result"] == receipt
    assert runtime.dispatched == [receipt["command_id"]]


def test_busy_command_is_durably_queued_and_existing_consumer_launches_it(runtime):
    from tui_gateway import prompt_admission
    session = runtime.sessions["live-a"]
    session["running"] = True
    first = runtime.call("runtime.command", **envelope("queued"))["result"]
    assert first["status"] == "accepted" and runtime.dispatched == []
    assert runtime.call("runtime.command", **envelope("queued"))["result"] == first
    snapshot = runtime.call("runtime.snapshot")["result"]
    assert snapshot["admission"]["jobs"][0]["state"] == "queued"
    # Losing transport does not cancel, and a status read does not execute.
    old_transport = session["transport"]
    session["transport"] = runtime.server._detached_ws_transport
    session["running"] = False
    prompt_admission.pump(runtime.server)
    assert runtime.dispatched == []
    session["transport"] = old_transport
    prompt_admission.pump(runtime.server)
    assert runtime.dispatched == ["queued"]
    assert runtime.call("runtime.command", **envelope("queued"))["result"] == first
    assert runtime.dispatched == ["queued"]
    assert runtime.call("runtime.snapshot")["result"]["admission"]["jobs"][0]["state"] == "finished"


def test_queued_cancel_is_visible_and_startup_expires_unattached_command(runtime, monkeypatch):
    from tui_gateway import prompt_admission
    session = runtime.sessions["live-a"]
    session["running"] = True
    runtime.call("runtime.command", **envelope("cancel-target"))
    result = runtime.call("runtime.command", **envelope("cancel-queue", expected_revision=None,
        operation="cancel", payload={"reason": "stop"}))["result"]
    assert result["status"] == "accepted"
    events = runtime.call("runtime.events.since")["result"]["events"]
    assert any(e["payload"].get("admission_state") == "cancelled" for e in events)
    cancel_events = [e for e in events if e["payload"].get("cancellation")]
    assert cancel_events[-1]["payload"]["cancellation"]["upstream_ack"] is None
    runtime.call("runtime.command", **envelope("expire-after-restart", expected_revision=None))
    db = runtime.agents["a"]._session_db
    db._execute_write(lambda conn: conn.execute("UPDATE runtime_admission_queue SET expires_at=0 "
        "WHERE command_id='expire-after-restart'"))
    monkeypatch.setattr(runtime.server, "_sessions", {})
    monkeypatch.setattr(runtime.server, "_get_db", lambda: db)
    callbacks = []
    def schedule(callback, interval):
        callbacks.append(callback)
        return SimpleNamespace(cancelled=False, cancel=lambda **kw: None)
    monkeypatch.setattr("agent.periodic_scheduler.schedule", schedule)
    prompt_admission.start(runtime.server)
    assert len(callbacks) == 1
    callbacks[0]()  # lifecycle maintenance, with no new submit or attached session
    assert db.read_runtime_command("same-stored-id", "expire-after-restart")["result"]["admission_state"] == "expired"
    assert runtime.dispatched == []


def test_real_shutdown_fences_launch_and_leaves_terminal_queue_receipts(runtime, monkeypatch):
    from tui_gateway import prompt_admission
    runtime.sessions["live-a"]["running"] = True
    runtime.call("runtime.command", **envelope("shutdown-queued"))
    # Exercise the actual server lifecycle, isolating unrelated plugin/process
    # teardown after the queue's own shutdown boundary.
    for name in ("_flush_sessions_before_exit", "_release_gateway_wake_owner", "_stop_turns_before_exit"):
        monkeypatch.setattr(runtime.server, name, lambda: None)
    monkeypatch.setattr(runtime.server, "_close_session_by_id", lambda *a, **k: None)
    runtime.server._shutdown_sessions()
    runtime.sessions["live-a"]["running"] = False
    prompt_admission.pump(runtime.server)
    assert runtime.dispatched == []
    record = runtime.agents["a"]._session_db.read_runtime_command("same-stored-id", "shutdown-queued")
    assert record["status"] == "cancelled"
    assert record["result"]["outcome"] == "shutdown_before_launch"
    denied = runtime.call("runtime.command", **envelope("after-shutdown", expected_revision=None))["result"]
    assert denied["status"] == "rejected" and denied["conflict"]["code"] == "admission_draining"
