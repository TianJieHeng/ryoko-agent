"""BE02 uses the real AIAgent loop and the existing SQLite writer lease."""
import contextvars
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agent.agent_identity import resolve_agent_context
from agent.identity_lifecycle import agent_runtime_scope
from agent.runtime_commands import (
    RuntimeCommandError, RuntimeFenceError, assert_runtime_dispatch, bind_runtime_run,
    bind_submitted_command, claim_turn_command, finish_turn_command, prepare_turn_command,
    read_command_state, reset_runtime_run, submit_command,
)
from agent.runtime_context import current_agent_context
from agent.turn_facade_lease import admit_durable_turn_lease
from hermes_state import SessionDB


def _config():
    return {"agent_identity": {
        "schema_version": 1, "principal_id": "owner", "profile_id": "fixture",
        "primary_agent_id": "primary", "active_agent_id": "primary",
        "agents": {"primary": {"policy_version": 1, "role": "primary",
            "memory_backend": "personal_mcp", "secret_refs": ["OPENAI_API_KEY"],
            "allowed_tools": ["runtime_fixture"]}},
        "child_policy": {"policy_version": 1, "role": "child", "memory_backend": "builtin",
                         "secret_refs": [], "allowed_tools": []},
    }}


def _envelope(command_id="command", operation="submit", text="hello"):
    return {"schema_version": 1, "command_id": command_id, "idempotency_key": command_id,
            "expected_revision": None, "operation": operation,
            "payload": {"text": text} if operation != "cancel" else {"reason": "stop"}}


def _response(tool=False):
    calls = [SimpleNamespace(id="tool_1", type="function",
                             function=SimpleNamespace(name="runtime_fixture", arguments='{}'))] if tool else None
    return SimpleNamespace(choices=[SimpleNamespace(finish_reason="tool_calls" if tool else "stop",
        message=SimpleNamespace(content=None if tool else "recorded answer", reasoning_content=None,
                                reasoning=None, tool_calls=calls))], model="fixture/model", usage=None)


@pytest.fixture
def real_agent(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "config.yaml").write_text(json.dumps(_config()))
    (tmp_path / ".env").write_text("OPENAI_API_KEY=fixture-provider-key\n")
    tool = {"type": "function", "function": {"name": "runtime_fixture", "description": "test",
            "parameters": {"type": "object", "properties": {}}}}
    monkeypatch.setattr("model_tools.get_tool_definitions", lambda *a, **k: [tool])
    monkeypatch.setattr("model_tools.check_toolset_requirements", lambda *a, **k: {})
    monkeypatch.setattr("agent.process_bootstrap.OpenAI", MagicMock())
    from run_agent import AIAgent
    db = SessionDB(tmp_path / "state.db")
    agent = AIAgent(model="gpt-4.1-mini", provider="openai", api_key="fixture-provider-key", base_url="https://fixture.invalid/v1",
        session_id="session", session_db=db, quiet_mode=True, skip_context_files=True,
        skip_memory=True, max_iterations=4)
    agent.client = MagicMock()
    agent._cached_system_prompt = "Fixture system prompt."
    agent._use_prompt_caching = False
    agent._disable_streaming = True
    agent.tool_delay = 0
    agent.save_trajectories = False
    agent.compression_enabled = False
    monkeypatch.setattr(agent, "_create_request_openai_client", lambda **kwargs: agent.client)
    monkeypatch.setattr(agent, "_close_request_openai_client", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(agent, "_cleanup_task_resources", lambda *a, **k: None)
    monkeypatch.setattr(agent, "_save_trajectory", lambda *a, **k: None)
    yield agent
    agent.close()
    db.close()


def _submit(agent, envelope):
    with agent_runtime_scope(agent.runtime_context):
        return submit_command(agent, envelope)


def _run_submitted(agent, receipt, text="hello"):
    with bind_submitted_command(agent, receipt):
        return agent.run_conversation(text)


def test_existing_loop_records_provider_tool_outcomes_and_duplicate_is_read_only(real_agent, monkeypatch):
    agent = real_agent
    agent.client.chat.completions.create.side_effect = [_response(True), _response()]
    tool_calls = []
    monkeypatch.setattr("model_tools.handle_function_call", lambda *a, **k: tool_calls.append(a[0]) or '{"ok":true}')
    envelope = _envelope()
    receipt = _submit(agent, envelope)
    result = _run_submitted(agent, receipt)
    assert result["final_response"] == "recorded answer"
    assert agent.client.chat.completions.create.call_count == 2
    assert tool_calls == ["runtime_fixture"]
    recorded = agent._session_db.read_runtime_command("session", "command")
    assert recorded["status"] == "completed"
    assert recorded["result"]["final_response"] == "recorded answer"
    page = agent._session_db.replay_runtime_events("session")
    assert {"model.completed", "tool.completed", "checkpoint.published"} <= {event["type"] for event in page["events"]}
    assert _submit(agent, envelope) == receipt
    assert _run_submitted(agent, receipt)["final_response"] == result["final_response"]
    assert agent.client.chat.completions.create.call_count == 2
    assert tool_calls == ["runtime_fixture"]
    assert agent._session_db.get_session_turn_lease("session") is None
    assert current_agent_context() is None


def test_distinct_normal_turns_and_consumed_submission_do_not_reuse_task_id(real_agent):
    agent = real_agent
    agent.client.chat.completions.create.side_effect = [_response(), _response(), _response()]
    receipt = _submit(agent, _envelope())
    with bind_submitted_command(agent, receipt):
        agent.run_conversation("first", task_id="stable-session-task")
        agent.run_conversation("second", task_id="stable-session-task")
    agent.run_conversation("third", task_id="stable-session-task")
    events = agent._session_db.replay_runtime_events("session")["events"]
    ids = [event["payload"]["command_id"] for event in events if event["type"] == "command.accepted"]
    assert len(ids) == len(set(ids)) == 3
    assert agent.client.chat.completions.create.call_count == 3


def test_claimed_crash_stays_uncertain_and_two_holder_transfer_blocks_dispatch(real_agent):
    agent = real_agent
    receipt = _submit(agent, _envelope())
    with agent_runtime_scope(agent.runtime_context):
        admission = admit_durable_turn_lease(agent, session_id="session", relay_turn_id="first",
            task_context={"session_id": "session", "platform": "cli"}, conversation_history=[])
        run = claim_turn_command(agent, read_command_state(agent, "command"), admission.lease)
        token = bind_runtime_run(run)
        copied = contextvars.copy_context()
        assert assert_runtime_dispatch(agent) is run
        reset_runtime_run(token, run)
        old_generation = admission.lease.generation
        admission.lease.release()
        assert agent._session_db.acquire_session_turn_lease("session", "successor", wait_seconds=0)
        assert agent._session_db.get_session_turn_lease("session")["generation"] > old_generation
        with pytest.raises(RuntimeFenceError):
            copied.run(assert_runtime_dispatch, agent)
        from tools.registry import registry
        ran = []
        registry.register(name="runtime_fixture", toolset="test", schema={"name": "runtime_fixture"},
                          handler=lambda *_a, **_k: ran.append(True) or "ok")
        denied = copied.run(registry.dispatch, "runtime_fixture", {})
        assert "error" in denied and ran == []
        agent._session_db.release_session_turn_lease("session", "successor")
    pending = _run_submitted(agent, receipt)
    assert pending["runtime_status"] == "outcome_uncertain"
    assert agent.client.chat.completions.create.call_count == 0


def test_journal_failure_and_oversized_input_never_invoke_provider(real_agent, monkeypatch):
    agent = real_agent
    with pytest.raises(RuntimeCommandError):
        agent.run_conversation("x" * 65537)
    def failed_accept(*args, **kwargs):
        raise OSError("journal unavailable")
    monkeypatch.setattr(agent._session_db, "submit_runtime_command", failed_accept)
    with pytest.raises(OSError, match="journal unavailable"):
        agent.run_conversation("hi")
    assert agent.client.chat.completions.create.call_count == 0
    assert agent._session_db.get_session_turn_lease("session") is None


def test_failed_outcome_journal_blocks_retry_and_exception_cleanup(real_agent, monkeypatch):
    agent = real_agent
    agent.client.chat.completions.create.return_value = _response()
    append = agent._session_db.append_runtime_event
    def fail_output(sid, event_type, payload, **kwargs):
        if event_type == "model.completed":
            raise OSError("disk full after output")
        return append(sid, event_type, payload, **kwargs)
    monkeypatch.setattr(agent._session_db, "append_runtime_event", fail_output)
    receipt = _submit(agent, _envelope())
    result = _run_submitted(agent, receipt)
    assert agent.client.chat.completions.create.call_count == 1
    record = agent._session_db.read_runtime_command("session", "command")
    assert record["result"]["outcome_uncertain"] is True
    assert agent._session_db.get_session_turn_lease("session") is None
    assert getattr(agent, "_active_runtime_run", None) is None
    assert current_agent_context() is None


def test_control_receipts_do_not_claim_provider_cancellation_or_run_completion(real_agent):
    agent = real_agent
    receipt = _submit(agent, _envelope())
    with agent_runtime_scope(agent.runtime_context):
        admission = admit_durable_turn_lease(agent, session_id="session", relay_turn_id="control",
            task_context={"session_id": "session", "platform": "cli"}, conversation_history=[])
        run = claim_turn_command(agent, read_command_state(agent, "command"), admission.lease)
        token = bind_runtime_run(run)
        try:
            steer = submit_command(agent, _envelope("steer", "steer"))
            cancel = submit_command(agent, _envelope("cancel", "cancel"))
            assert steer["status"] == cancel["status"] == "accepted"
            assert read_command_state(agent, "steer")["result"]["applied"] is False
            assert read_command_state(agent, "cancel")["result"]["provider_cancelled"] is False
            assert agent._session_db.read_runtime_snapshot("session")["state"]["status"] == "claimed"
            finish_turn_command(run, {"interrupted": True, "final_response": ""})
        finally:
            reset_runtime_run(token, run)
            admission.lease.release()


@pytest.mark.parametrize("mode", ["codex_app_server", "unknown_plugin_agent"])
def test_unfenced_transport_rejected_before_acceptance(real_agent, mode):
    real_agent.api_mode = mode
    with pytest.raises(RuntimeCommandError, match="runtime_transport_unsupported"):
        real_agent.run_conversation("hello")
    assert real_agent._session_db.read_runtime_snapshot("session")["revision"] == 0
    assert real_agent.client.chat.completions.create.call_count == 0


def test_unwind_after_base_exception_records_failure_and_cleans_scope(real_agent, monkeypatch):
    def cancelled(*args, **kwargs):
        raise KeyboardInterrupt()
    monkeypatch.setattr("agent.conversation_loop.run_conversation", cancelled)
    receipt = _submit(real_agent, _envelope())
    with pytest.raises(KeyboardInterrupt):
        _run_submitted(real_agent, receipt)
    assert real_agent._session_db.read_runtime_command("session", "command")["status"] == "failed"
    assert real_agent._session_db.get_session_turn_lease("session") is None
    assert getattr(real_agent, "_active_runtime_run", None) is None
    assert current_agent_context() is None


def test_omitted_large_result_is_explicitly_unavailable_without_rerun(real_agent):
    agent = real_agent
    response = _response()
    import random
    response.choices[0].message.content = "".join(random.Random(0).choices("abcdefghijklmnopqrstuvwxyz0123456789 ", k=250000))
    agent.client.chat.completions.create.return_value = response
    receipt = _submit(agent, _envelope())
    assert len(_run_submitted(agent, receipt)["final_response"]) == 250000
    replay = _run_submitted(agent, receipt)
    assert replay["runtime_status"] == "recorded_output_unavailable"
    assert replay["completed"] is False
    assert agent.client.chat.completions.create.call_count == 1


def test_tui_worker_explicitly_binds_receipt_without_context_inheritance(real_agent, monkeypatch):
    """The normal TUI raw dispatcher thread does not inherit ContextVars."""
    import threading
    from contextlib import nullcontext
    from tui_gateway import server
    agent = real_agent
    agent.client.chat.completions.create.return_value = _response()
    receipt = _submit(agent, _envelope("tui-command"))
    session = {"agent": agent, "session_key": "session", "history": [],
               "history_lock": threading.Lock(), "running": True, "profile_home": agent.runtime_context.profile_home,
               "attached_images": [], "transport": None}
    monkeypatch.setattr(server, "_ensure_session_db_row", lambda session: True)
    monkeypatch.setattr(server, "_ensure_active_session_slot", lambda *a: None)
    monkeypatch.setattr(server, "_routing_provenance_db", lambda *a: nullcontext(None))
    monkeypatch.setattr(server, "_record_turn_marker", lambda *a, **k: "session")
    monkeypatch.setattr(server, "_prepare_turn_input", lambda *a: ("hello", "hello", 80, None))
    def invoke(_sid, _session, state, *_args):
        assert current_agent_context() is None
        state.result = agent.run_conversation("hello")
    monkeypatch.setattr(server, "_invoke_agent", invoke)
    monkeypatch.setattr(server, "_complete_turn_payload", lambda session, state, *a:
                        ({}, state.result["final_response"], "complete"))
    for name in ("_emit", "_absorb_turn_result", "_goal_followup_after_turn", "_after_complete_turn",
                 "_publish_session_control_snapshot", "_finish_turn", "_release_hosted_room_turn_slot",
                 "_clear_inflight_turn", "_retire_turn_marker", "_emit_settled_session_info", "_run_post_turn_followups"):
        monkeypatch.setattr(server, name, lambda *a, **k: None)
    started, failures = [], []
    def raw_worker(target, **kwargs):
        def work():
            try:
                target()
            except BaseException as exc:
                failures.append(exc)
        thread = threading.Thread(target=work)
        started.append(thread)
        thread.start()
        return thread
    monkeypatch.setattr(server, "_start_session_work", raw_worker)
    assert server._run_prompt_submit("rid", "runtime-ui", session, "hello", runtime_command_receipt=receipt)
    started[0].join(timeout=15)
    assert not started[0].is_alive() and not failures
    record = agent._session_db.read_runtime_command("session", "tui-command")
    assert record["status"] == "completed"
    events = agent._session_db.replay_runtime_events("session")["events"]
    assert sum(event["type"] == "command.accepted" for event in events) == 1
    assert agent.client.chat.completions.create.call_count == 1


def test_recovered_control_cannot_target_a_successor_run(real_agent, monkeypatch):
    agent = real_agent
    with agent_runtime_scope(agent.runtime_context):
        first = prepare_turn_command(agent, "first")
        admission = admit_durable_turn_lease(agent, session_id="session", relay_turn_id="first",
            task_context={"session_id": "session", "platform": "cli"}, conversation_history=[])
        first_run = claim_turn_command(agent, first, admission.lease)
        from agent.runtime_commands import _envelope as trusted_envelope
        db, sid, actor, command = trusted_envelope(agent, _envelope("late-steer", "steer"))
        receipt = db.submit_runtime_command(sid, actor=actor, command=command)
        finish_turn_command(first_run, {"final_response": "done"})
        admission.lease.release()
        second = prepare_turn_command(agent, "second")
        admission = admit_durable_turn_lease(agent, session_id="session", relay_turn_id="second",
            task_context={"session_id": "session", "platform": "cli"}, conversation_history=[])
        successor = claim_turn_command(agent, second, admission.lease)
        token = bind_runtime_run(successor)
        monkeypatch.setattr(agent, "steer", lambda text: pytest.fail("old control must not target successor"))
        try:
            assert submit_command(agent, _envelope("late-steer", "steer")) == receipt
            assert read_command_state(agent, "late-steer")["status"] == "blocked"
        finally:
            reset_runtime_run(token, successor)
            admission.lease.release()


def test_reconstructed_compression_tip_continues_original_durable_runtime(real_agent, monkeypatch):
    from run_agent import AIAgent
    original = real_agent
    original.client.chat.completions.create.return_value = _response()
    original.run_conversation("before compression")
    db = original._session_db
    before = db.read_runtime_snapshot("session")
    db.end_session("session", "compression")
    db.create_session("physical-tip", source="cli", parent_session_id="session",
                      model_config={"agent_identity": original.runtime_context.identity.to_record()})
    db.append_message("physical-tip", "assistant", "Compacted earlier conversation")
    reopened = SessionDB(db.db_path)
    restored = AIAgent(model="gpt-4.1-mini", provider="openai", api_key="fixture-provider-key",
        base_url="https://fixture.invalid/v1", session_id="physical-tip", session_db=reopened,
        quiet_mode=True, skip_context_files=True, skip_memory=True, max_iterations=4)
    restored.client = MagicMock()
    restored.client.chat.completions.create.return_value = _response()
    restored._cached_system_prompt = "Fixture system prompt."
    restored._disable_streaming = True
    restored.compression_enabled = False
    restored.save_trajectories = False
    monkeypatch.setattr(restored, "_create_request_openai_client", lambda **kwargs: restored.client)
    monkeypatch.setattr(restored, "_cleanup_task_resources", lambda *a, **k: None)
    monkeypatch.setattr(restored, "_save_trajectory", lambda *a, **k: None)
    try:
        assert restored.runtime_context.identity == original.runtime_context.identity
        result = restored.run_conversation("after restart", conversation_history=
            reopened.get_messages_as_conversation("physical-tip", include_row_ids=True))
        assert result["final_response"] == "recorded answer"
        assert restored.client.chat.completions.create.call_count == 1
        snapshot = reopened.read_runtime_snapshot("physical-tip")
        assert snapshot["session_id"] == "session" and snapshot["revision"] > before["revision"]
        assert snapshot["state"]["status"] == "completed"
        assert reopened.get_session_turn_lease("physical-tip") is None
    finally:
        restored.close()
        reopened.close()
