"""Real profile/store/transport boundaries for the durable runtime RPC surface."""

import json
import os
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest


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
        agent = SimpleNamespace(runtime_context=context, _session_db=db, session_id=context.identity.session_id,
                                api_mode="chat_completions", provider="fixture")
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
    for session in sessions.values():
        server._release_active_session_slot(session)
    for agent in agents.values():
        agent._session_db.close()


def envelope(command_id="command-1", **changes):
    return {"command_id": command_id, "idempotency_key": command_id, "expected_revision": 0,
            "operation": "submit", "payload": {"text": "Private fixture request"}, **changes}


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
