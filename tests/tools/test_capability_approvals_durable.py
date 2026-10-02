"""Exact approvals survive lost processes without becoming replay authority."""
import contextvars
import json
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from hermes_state import SessionDB
from hermes_state_runtime import RuntimeStoreError
from tests.tools.test_capability_broker import live_runtime  # noqa: F401
from tools import capability_broker as broker


def _actor(runtime):
    identity = runtime.context.identity
    return {key: getattr(identity, key) for key in ("principal_id", "profile_id", "agent_id")}


def _record(runtime, preview):
    return runtime.db.get_effect_approval(preview.approval_id, _actor(runtime))


def _action():
    return broker.tool_action("todo_list", {"action": "read", "target": "private fixture target"})


def test_preview_projection_and_decision_survive_reopen_without_local_authority(live_runtime):
    runtime = live_runtime
    action = _action()
    preview = broker.preview_action(action)
    # Deserialized/copied previews are values, not process-local grants. The
    # authenticated runtime and its durable record are rechecked for each step.
    with SessionDB(runtime.home / "state.db") as reopened:
        pending = reopened.get_effect_approval(preview.approval_id, _actor(runtime))
        assert pending["status"] == "pending"
        assert action.input_json not in json.dumps(pending)
        assert "private fixture target" not in json.dumps(pending)
        assert pending["binding"]["action_digest"] == action.digest
        assert reopened.list_effect_approvals("session", _actor(runtime)) == [pending]
    broker.resolve_approval(replace(preview), preview.approval_digest, "once")
    with SessionDB(runtime.home / "state.db") as reopened:
        assert reopened.get_effect_approval(preview.approval_id, _actor(runtime))["status"] == "approved"
    capability = broker.issue_capability(action, approval=replace(preview))
    consumed = _record(runtime, preview)
    assert consumed["status"] == "consumed"
    assert consumed["consumer_id"] == capability.capability_id
    assert capability.expires_at <= preview.expires_at
    broker.consume_capability(capability, action)
    with pytest.raises(broker.CapabilityDenied):
        broker.resolve_approval(preview, preview.approval_digest, "once")
    with pytest.raises(broker.CapabilityDenied):
        broker.issue_capability(action, approval=preview)
    assert _record(runtime, preview) == consumed


@pytest.mark.parametrize("phase", ["resolve", "consume"])
def test_process_crash_after_durable_decision_or_consumption_does_not_reset_status(live_runtime, phase):
    runtime = live_runtime
    action, preview = _action(), broker.preview_action(_action())
    if phase == "consume":
        broker.resolve_approval(preview, preview.approval_digest, "once")
    # This separate trusted store consumer terminates without closing SQLite or
    # returning a dispatch ticket. No provider or unsafe dummy adapter is used.
    script = '''
import json, os, sys
from pathlib import Path
from agent.agent_identity import resolve_agent_context
from hermes_state import SessionDB
home, approval_id, phase = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
context = resolve_agent_context(json.loads((home / "config.yaml").read_text()), session_id="session", profile_home=home)
actor = {key: getattr(context.identity, key) for key in ("principal_id", "profile_id", "agent_id")}
db = SessionDB(home / "state.db")
record = db.get_effect_approval(approval_id, actor)
if phase == "resolve":
    db.resolve_effect_approval(approval_id, actor, holder=record["binding"]["holder"],
        generation=record["binding"]["generation"], approval_digest=record["approval_digest"], choice="once")
else:
    db.consume_effect_approval(approval_id, actor, consumer_id="crashed-consumer", **record["binding"])
os._exit(73)
'''
    result = subprocess.run([sys.executable, "-c", script, str(runtime.home), preview.approval_id, phase],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 73, result.stderr
    with SessionDB(runtime.home / "state.db") as restarted:
        record = restarted.get_effect_approval(preview.approval_id, _actor(runtime))
        assert record["status"] == ("approved" if phase == "resolve" else "consumed")
    if phase == "resolve":
        capability = broker.issue_capability(action, approval=replace(preview))
        assert _record(runtime, preview)["consumer_id"] == capability.capability_id
    else:
        with pytest.raises(broker.CapabilityDenied):
            broker.issue_capability(action, approval=replace(preview))
        assert _record(runtime, preview)["consumer_id"] == "crashed-consumer"


def test_independent_sqlite_connections_cannot_consume_an_approval_twice(live_runtime):
    runtime, action = live_runtime, _action()
    preview = broker.preview_action(action)
    broker.resolve_approval(preview, preview.approval_digest, "once")
    record = _record(runtime, preview)
    ready = threading.Barrier(2)
    def consume(consumer_id):
        with SessionDB(runtime.home / "state.db") as db:
            ready.wait(timeout=10)
            try:
                return db.consume_effect_approval(preview.approval_id, _actor(runtime),
                    consumer_id=consumer_id, **record["binding"])["status"]
            except RuntimeStoreError as exc:
                return exc.code
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(consume, ["consumer-one", "consumer-two"]))
    assert sorted(results) == ["approval_mismatch", "consumed"]
    with pytest.raises(broker.CapabilityDenied):
        broker.issue_capability(action, approval=preview)


def test_competing_broker_issuers_mint_only_one_ticket(live_runtime):
    action = _action()
    preview = broker.preview_action(action)
    broker.resolve_approval(preview, preview.approval_digest, "once")
    ready = threading.Barrier(2)
    def consume():
        ready.wait(timeout=10)
        try:
            return broker.issue_capability(action, approval=replace(preview)).capability_id
        except broker.CapabilityDenied:
            return None
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = [workers.submit(contextvars.copy_context().run, consume) for _ in range(2)]
        issued = [future.result(timeout=15) for future in results]
    assert sum(item is not None for item in issued) == 1
    assert _record(live_runtime, preview)["consumer_id"] in issued


@pytest.mark.parametrize("changed", ["action", "digest", "expiry", "approval_id", "actor", "generation", "policy"])
def test_forged_or_changed_projection_never_matches_durable_approval(live_runtime, changed):
    action = _action()
    preview = broker.preview_action(action)
    broker.resolve_approval(preview, preview.approval_digest, "once")
    if changed == "action":
        action = broker.tool_action("todo_list", {"action": "read", "target": "changed target"})
        forged = replace(preview, action=action)
    elif changed == "digest":
        forged = replace(preview, approval_digest="f" * 64)
    elif changed == "expiry":
        forged = replace(preview, expires_at=preview.expires_at + 1)
    elif changed == "approval_id":
        forged = replace(preview, approval_id="never-persisted")
    else:
        fields = {"actor": {"principal_id": "other-actor"}, "generation": {"generation": 999},
                  "policy": {"policy_version": 999}}[changed]
        forged = replace(preview, authority=replace(preview.authority, **fields))
    with pytest.raises(broker.CapabilityDenied):
        broker.issue_capability(action, approval=forged)
    assert _record(live_runtime, preview)["status"] == "approved"


@pytest.mark.parametrize("field", ["target_ref", "input_revision", "artifact_revision", "policy_version"])
def test_durable_consumption_rejects_changed_semantic_binding(live_runtime, field):
    preview = broker.preview_action(_action())
    broker.resolve_approval(preview, preview.approval_digest, "once")
    record = _record(live_runtime, preview)
    binding = {**record["binding"], field: "changed"}
    with pytest.raises(RuntimeStoreError, match="exact unconsumed"):
        live_runtime.db.consume_effect_approval(preview.approval_id, _actor(live_runtime),
                                               consumer_id="wrong-scope", **binding)
    assert _record(live_runtime, preview)["status"] == "approved"


@pytest.mark.parametrize("choice", [None, "timeout", "cancelled", "session", "always", "deny"])
def test_ui_absence_or_non_exact_answer_never_becomes_durable_authority(live_runtime, monkeypatch, choice):
    from tools import approval
    from tools.capability_approval import request_exact_approval
    callback = lambda *_a, **_kw: choice
    monkeypatch.setattr(approval, "_presence", lambda: (callback, True, False, False))
    monkeypatch.setattr("tools.approval_prompt._present_with_selected_transport", lambda **_kw: {"selected": False})
    with pytest.raises(broker.CapabilityDenied) as failure:
        request_exact_approval(_action())
    pending = choice in (None, "timeout", "cancelled")
    assert failure.value.pending == pending
    records = live_runtime.db.list_effect_approvals("session", _actor(live_runtime))
    assert len(records) == 1
    assert records[0]["status"] == ("pending" if pending else "denied")


def test_ui_unavailable_records_pending_request_and_real_once_records_approval(live_runtime, monkeypatch):
    from tools import approval
    from tools.capability_approval import request_exact_approval
    monkeypatch.setattr(approval, "_presence", lambda: (None, False, False, False))
    with pytest.raises(broker.CapabilityDenied) as failure:
        request_exact_approval(_action())
    assert failure.value.pending
    assert live_runtime.db.list_effect_approvals("session", _actor(live_runtime))[0]["status"] == "pending"
    shown = []
    def human(command, description, **kwargs):
        shown.append((command, description, kwargs))
        return "once"
    monkeypatch.setattr(approval, "_presence", lambda: (human, True, False, False))
    monkeypatch.setattr("tools.approval_prompt._present_with_selected_transport", lambda **_kw: {"selected": False})
    preview = request_exact_approval(_action())
    assert _record(live_runtime, preview)["status"] == "approved"
    assert len(shown) == 1
    assert not shown[0][2]["allow_session"] and not shown[0][2]["allow_permanent"]


def test_policy_revocation_and_cancellation_do_not_consume_existing_approval(live_runtime):
    runtime, action = live_runtime, _action()
    preview = broker.preview_action(action)
    broker.resolve_approval(preview, preview.approval_digest, "once")
    policy = runtime.raw["agent_identity"]["agents"]["primary"]
    policy["allowed_tools"].remove("todo_list")
    (runtime.home / "config.yaml").write_text(json.dumps(runtime.raw))
    with pytest.raises(broker.CapabilityDenied, match="policy changed"):
        broker.issue_capability(action, approval=preview)
    assert _record(runtime, preview)["status"] == "approved"
    policy["allowed_tools"].insert(0, "todo_list")
    (runtime.home / "config.yaml").write_text(json.dumps(runtime.raw))
    actor = _actor(runtime)
    runtime.db.submit_runtime_command("session", actor=actor, command={"schema_version": 1,
        "command_id": "cancel", "idempotency_key": "cancel", "expected_revision": None,
        "operation": "cancel", "payload": {"reason": "fixture cancellation"}, "identity_binding": actor})
    with pytest.raises(broker.CapabilityDenied) as failure:
        broker.issue_capability(action, approval=preview)
    assert failure.value.code == "run_cancelled"
    assert _record(runtime, preview)["status"] == "approved"


def test_ticket_expiry_never_outlives_its_exact_approval(live_runtime, monkeypatch):
    action = _action()
    preview = broker.preview_action(action, ttl_seconds=1)
    broker.resolve_approval(preview, preview.approval_digest, "once")
    capability = broker.issue_capability(action, approval=preview, ttl_seconds=60)
    assert capability.expires_at == preview.expires_at
    monkeypatch.setattr(broker.time, "time", lambda: preview.expires_at + 1)
    with pytest.raises(broker.CapabilityDenied, match="expiry"):
        broker.consume_capability(capability, action)


def test_owner_replacement_does_not_inherit_pending_or_approved_decisions(live_runtime):
    from agent.runtime_commands import RuntimeFenceError
    pending = broker.preview_action(_action())
    approved = broker.preview_action(_action())
    broker.resolve_approval(approved, approved.approval_digest, "once")
    live_runtime.db.release_session_turn_lease("session", "owner")
    assert live_runtime.db.acquire_session_turn_lease("session", "successor", wait_seconds=0)
    with pytest.raises(RuntimeFenceError, match="generation"):
        broker.resolve_approval(pending, pending.approval_digest, "once")
    with pytest.raises(RuntimeFenceError, match="generation"):
        broker.issue_capability(_action(), approval=approved)
    assert _record(live_runtime, pending)["status"] == "pending"
    assert _record(live_runtime, approved)["status"] == "approved"


@pytest.mark.parametrize("withdrawn", [False, True])
def test_existing_gateway_ui_projects_durable_request_and_only_records_real_answer(live_runtime, monkeypatch, withdrawn):
    from tools import approval, approval_context
    from tools.capability_approval import request_exact_approval
    session_key = "durable-exact-approval-fixture"
    shown = []
    def notify(data):
        shown.append(data)
        record = live_runtime.db.get_effect_approval(data["approval_id"], _actor(live_runtime))
        assert record["status"] == "pending"
        assert data["approval_digest"] == record["approval_digest"]
        assert data["action_digest"] == record["binding"]["action_digest"]
        if withdrawn:
            assert approval.withdraw_gateway_approval(session_key, data["request_id"], "fixture UI closed")
        else:
            assert approval.resolve_gateway_approval(session_key, "once", request_id=data["request_id"]) == 1
    monkeypatch.setattr(approval, "_presence", lambda: (None, False, True, False))
    monkeypatch.setattr("tools.approval_prompt._present_with_selected_transport", lambda **_kw: {"selected": False})
    token = approval_context.set_current_session_key(session_key)
    approval.register_gateway_notify(session_key, notify)
    try:
        if withdrawn:
            with pytest.raises(broker.CapabilityDenied) as failure:
                request_exact_approval(_action())
            assert failure.value.pending
        else:
            preview = request_exact_approval(_action())
            assert preview.approval_id == shown[0]["approval_id"]
        assert len(shown) == 1
        assert live_runtime.db.get_effect_approval(shown[0]["approval_id"], _actor(live_runtime))["status"] == (
            "pending" if withdrawn else "approved")
        assert approval.resolve_gateway_approval(session_key, "once", request_id=shown[0]["request_id"]) == 0
    finally:
        approval.unregister_gateway_notify(session_key)
        approval_context.reset_current_session_key(token)
