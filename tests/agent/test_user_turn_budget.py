"""User input starts a task budget; retries and autonomous work cannot renew it."""
import time

import pytest

from agent.admission import AdmissionQueue
from agent.budget_account import actor_for, admission_deadline
from agent.runtime_commands import bind_submitted_command
from hermes_state_runtime import RuntimeStoreError
from tests.agent.test_budget_runtime import policy, factory  # noqa: F401


def command(cid, **extra):
    return {"schema_version": 1, "command_id": cid, "idempotency_key": cid,
            "expected_revision": None, "operation": "submit", "payload": {"text": "hello again"}, **extra}


def test_new_user_message_after_idle_preserves_history_and_original_budget(factory, monkeypatch):
    make, db, _ = factory
    turn_policy = policy()
    turn_policy["deadline_seconds"] = 3600
    turn_policy["limits"]["wall_ms"] = 3600000
    agent, calls = make(budget=turn_policy)
    first = agent.run_conversation("first message")
    actor = actor_for(agent.runtime_context)
    root = db.get_budget_account(first["runtime_budget"]["root_id"], actor)
    later = root["deadline"] + 301
    monkeypatch.setattr(time, "time", lambda: later)
    queue = AdmissionQueue(db)
    with pytest.raises(RuntimeStoreError, match="queue expired"):
        queue.submit(agent, command("automatic"), deadline=admission_deadline(agent))
    envelope = command("user-second", new_user_turn=True)
    receipt = queue.submit(agent, envelope, deadline=admission_deadline(agent, new_user_turn=True))
    reserved = queue.reserve_next("test-worker", {agent.session_id})
    assert reserved["command_id"] == receipt["command_id"]
    with bind_submitted_command(agent, receipt):
        second = agent.run_conversation("hello again")
    assert second["completed"] and len(calls) == 2
    assert second["runtime_budget"]["root_id"] != root["root_id"]
    fresh = db.get_budget_account(second["runtime_budget"]["account_id"], actor)
    assert fresh["deadline"] == later + turn_policy["deadline_seconds"]
    old = db.get_budget_account(root["account_id"], actor)
    assert old["expired"]
    for key in ("deadline", "consumed", "reserved", "limits", "parent_id", "root_id"):
        assert old[key] == root[key]
    assert queue.submit(agent, envelope, deadline=admission_deadline(agent, new_user_turn=True)) == receipt
    assert len(calls) == 2
    # Existing transcript stays in the same session, with both user turns.
    text = [m.get("content") for m in db.get_messages(agent.session_id) if m["role"] == "user"]
    assert "first message" in text and "hello again" in text
