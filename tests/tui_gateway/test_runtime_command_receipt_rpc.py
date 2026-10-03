"""Receipt recovery observes durable commands without reviving their execution."""

import json
import os
from types import SimpleNamespace

import pytest

from tests.tui_gateway.test_runtime_rpc import envelope, runtime  # noqa: F401


def _accept(agent, command_id):
    identity = agent.runtime_context.identity
    actor = {key: getattr(identity, key) for key in ("principal_id", "agent_id", "profile_id")}
    return agent._session_db.submit_runtime_command(agent.session_id, actor, {
        "schema_version": 1, **envelope(command_id), "identity_binding": actor,
    })


def _forbidden_write(*_args, **_kwargs):
    raise AssertionError("Receipt recovery may not mutate state, enqueue, or execute")


@pytest.mark.parametrize("status", ["accepted", "claimed", "completed", "failed", "blocked", "cancelled"])
def test_receipt_recovery_preserves_recorded_state_without_writes_or_execution(runtime, monkeypatch, status):
    from hermes_state import SessionDB
    from tui_gateway.contracts.runtime_v1 import RuntimeCommandReceiptResult

    agent = runtime.agents["a"]
    db, sid = agent._session_db, agent.session_id
    with monkeypatch.context() as readonly:
        readonly.setattr(db, "_execute_write", _forbidden_write)
        missing = runtime.call("runtime.command.receipt", command_id="never-submitted")["result"]
        assert not missing["found"] and missing["receipt"] is None and missing["status"] is None
        assert missing["durable_revision"] == 0
    receipt = _accept(agent, "old-command")
    if status != "accepted":
        assert db.try_acquire_session_turn_lease(sid, "private-worker", ttl_seconds=60)
        generation = db.get_session_turn_lease(sid)["generation"]
        assert db.claim_runtime_command(sid, receipt["command_id"], holder="private-worker", generation=generation)
        if status != "claimed":
            db.finish_runtime_command(sid, receipt["command_id"], holder="private-worker", generation=generation,
                                      status=status, result={"final_response": "private-fixture-output"})
        db.release_session_turn_lease(sid, "private-worker")

    db.close()
    agent._session_db = db = SessionDB(runtime.homes["a"] / "state.db")
    before = db.read_runtime_command(sid, receipt["command_id"])
    snapshot = db.read_runtime_snapshot(sid)

    with monkeypatch.context() as readonly:
        readonly.setattr(db, "_execute_write", _forbidden_write)
        readonly.setattr(runtime.server, "_submit_runtime_prompt", _forbidden_write)
        readonly.setattr(runtime.server, "_run_prompt_submit", _forbidden_write)
        readonly.setattr("agent.runtime_commands.submit_command", _forbidden_write)
        for _ in range(2):
            response = runtime.call("runtime.command.receipt", command_id=receipt["command_id"])
            assert "result" in response, response
            result = response["result"]
            RuntimeCommandReceiptResult.model_validate(result)
            assert result["found"] and result["receipt"] == receipt
            assert result["command_id"] == receipt["command_id"] and result["status"] == status
            assert result["durable_revision"] == snapshot["revision"]
            assert "Private fixture request" not in json.dumps(result)
            assert "private-" not in json.dumps(result)
        missing = runtime.call("runtime.command.receipt", command_id="never-submitted")["result"]
        assert not missing["found"] and missing["receipt"] is None and missing["status"] is None
        assert missing["durable_revision"] == snapshot["revision"]
    assert db.read_runtime_command(sid, receipt["command_id"]) == before
    assert db.read_runtime_snapshot(sid) == snapshot
    assert not runtime.dispatched
    with db._read_ctx() as conn:
        assert conn.execute("SELECT COUNT(*) FROM runtime_admission_queue").fetchone()[0] == 0
    if status == "accepted":
        # Another real handle commits between the receipt and revision reads.
        # The response must describe the pre-claim snapshot consistently.
        writer = SessionDB(runtime.homes["a"] / "state.db")
        try:
            assert writer.try_acquire_session_turn_lease(sid, "concurrent-worker", ttl_seconds=60)
            generation = writer.get_session_turn_lease(sid)["generation"]
            read_command = db._runtime_command_on_conn

            def read_then_claim(conn, session_id, command_id):
                row = read_command(conn, session_id, command_id)
                assert writer.claim_runtime_command(sid, command_id, holder="concurrent-worker", generation=generation)
                return row

            with monkeypatch.context() as interleaved:
                interleaved.setattr(db, "_runtime_command_on_conn", read_then_claim)
                result = runtime.call("runtime.command.receipt", command_id=receipt["command_id"])["result"]
            assert result["status"] == "accepted" and result["durable_revision"] == snapshot["revision"]
            current = runtime.call("runtime.command.receipt", command_id=receipt["command_id"])["result"]
            assert current["status"] == "claimed" and current["durable_revision"] > result["durable_revision"]
            writer.release_session_turn_lease(sid, "concurrent-worker")
        finally:
            writer.close()


def test_receipt_lookup_requires_current_transport_and_identity_and_keeps_profiles_isolated(runtime):
    from hermes_constants import get_hermes_home

    receipts = {label: _accept(agent, "same-command") for label, agent in runtime.agents.items()}
    environment = dict(os.environ)
    for label in ("a", "b", "a"):
        result = runtime.call("runtime.command.receipt", label, command_id="same-command")["result"]
        assert result["receipt"] == receipts[label]
        other = "b" if label == "a" else "a"
        assert result["receipt"]["run_id"] != receipts[other]["run_id"]
        denied = runtime.call("runtime.command.receipt", label, via=runtime.peers[other], command_id="same-command")
        assert denied["error"]["code"] == 4001
    assert dict(os.environ) == environment and get_hermes_home() == runtime.homes["a"]
    for forged in ({"principal_id": "other"}, {"agent_id": "other"}, {"profile": "b"},
                   {"payload": {"text": "private-forged-prompt"}}, {"idempotency_key": "retry"}):
        denied = runtime.call("runtime.command.receipt", command_id="same-command", **forged)
        assert denied["error"]["code"] == 4000
        assert "private-forged-prompt" not in json.dumps(denied)
    for version in (2, True, "1"):
        assert runtime.call("runtime.command.receipt", command_id="same-command", schema_version=version)["error"]["code"] == 4000

    agent = runtime.agents["a"]
    context = agent.runtime_context
    agent._session_db.end_session(agent.session_id, "compression")
    agent._session_db.create_session("compressed-tip", source="tui", parent_session_id=agent.session_id,
                                     model_config={"agent_identity": context.identity.to_record()})
    agent.session_id = "compressed-tip"
    runtime.sessions["live-a"]["transport"] = replacement = SimpleNamespace(write=lambda _frame: True)
    assert runtime.call("runtime.command.receipt", command_id="same-command")["error"]["code"] == 4001
    result = runtime.call("runtime.command.receipt", via=replacement, command_id="same-command")["result"]
    assert result["receipt"] == receipts["a"]
    agent.runtime_context = None
    assert runtime.call("runtime.command.receipt", via=replacement, command_id="same-command")["error"]["data"]["code"] == "identity_required"
    agent.runtime_context = context
    agent._session_db.patch_session_model_config(agent.session_id, {"agent_identity": None})
    assert runtime.call("runtime.command.receipt", via=replacement, command_id="same-command")["error"]["data"]["code"] == "identity_mismatch"
