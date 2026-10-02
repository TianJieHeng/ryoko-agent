"""Artifact-control authority uses the real journal, lease and owned UI transport."""
import json
import threading
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.artifact_commands import (
    artifact_control_scope, assert_artifact_dispatch, begin_artifact_control,
    current_artifact_run, finish_artifact_control,
)
from hermes_state_runtime import RuntimeStoreError


@pytest.fixture
def control(tmp_path, monkeypatch):
    from agent.agent_identity import resolve_agent_context
    from hermes_state import SessionDB
    from tui_gateway import server
    from tui_gateway.transport import bind_transport, reset_transport

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    config = {"agent_identity": {
        "schema_version": 1, "principal_id": "fixture-owner", "profile_id": "fixture-profile",
        "primary_agent_id": "ryoko", "active_agent_id": "ryoko",
        "agents": {"ryoko": {"policy_version": 1, "role": "primary", "memory_backend": "personal_mcp",
                              "allowed_tools": ["todo_list"]}},
    }}
    (tmp_path / "config.yaml").write_text(json.dumps(config))
    context = resolve_agent_context(config, session_id="stored-session", profile_home=tmp_path)
    db = SessionDB(tmp_path / "state.db")
    sid = context.identity.session_id
    db.create_session(sid, source="tui")
    db.claim_session_agent_identity(sid, context.identity.to_record())
    # No provider/client exists: artifact commands must not need inference authority.
    agent = SimpleNamespace(runtime_context=context, _session_db=db, session_id=sid)
    peer = SimpleNamespace(write=lambda _frame: True)
    session = {"agent": agent, "profile_home": str(tmp_path), "transport": peer,
               "session_key": sid, "history": [], "history_lock": threading.RLock()}
    monkeypatch.setattr(server, "_sessions", {"live-session": session})

    @contextmanager
    def owned(via=None):
        transport_token = bind_transport(peer if via is None else via)
        session_token = server._current_runtime_session_record.set(session)
        method_token = server._current_rpc_method.set("runtime.artifact.prepare")
        try:
            yield
        finally:
            server._current_rpc_method.reset(method_token)
            server._current_runtime_session_record.reset(session_token)
            reset_transport(transport_token)

    def begin(command="artifact-1", payload=None):
        return begin_artifact_control(agent, "live-session", command,
                                      payload if payload is not None else {"artifact_id": "draft", "action": "preview"})

    yield SimpleNamespace(agent=agent, db=db, sid=sid, peer=peer, session=session,
                          server=server, owned=owned, begin=begin)
    db.close()


def test_artifact_command_needs_no_client_and_grants_no_inference_or_tool_dispatch(control):
    from agent.runtime_commands import RuntimeFenceError, assert_runtime_dispatch, _RUN
    from tools.registry import registry
    import tools.todo_tool  # Register the real certified tool, not a mock handler.

    with control.owned():
        run = control.begin()
        assert not hasattr(control.agent, "client")
        assert control.db.read_runtime_command(control.sid, run.command_id)["command"]["operation"] == "artifact"
        with artifact_control_scope(run):
            assert current_artifact_run() is run
            assert _RUN.get() is None
            assert assert_artifact_dispatch() is run
            with pytest.raises(RuntimeFenceError):
                assert_runtime_dispatch(control.agent)
            result = registry.dispatch("todo_list", {"todos": [{"id": "forbidden", "content": "must not run"}]})
            assert "error" in (json.loads(result) if isinstance(result, str) else result)
            finish_artifact_control(run, {"artifact_id": "draft", "previewed": True})
        assert current_artifact_run() is None
        assert control.db.get_session_turn_lease(control.sid) is None
        second = control.begin("artifact-2")
        assert second.run_id != run.run_id
        with artifact_control_scope(second):
            finish_artifact_control(second, {})


def test_exact_reopen_preserves_claim_and_changed_content_conflicts(control):
    with control.owned():
        first = control.begin()
        before = control.db.get_session_turn_lease(control.sid)
        again = control.begin()
        assert (again.run_id, again.holder, again.generation, again.deadline_at) == (
            first.run_id, first.holder, first.generation, first.deadline_at)
        assert control.db.get_session_turn_lease(control.sid) == before
        with pytest.raises(RuntimeStoreError) as conflict:
            control.begin(payload={"artifact_id": "different", "action": "preview"})
        assert conflict.value.code == "idempotency_conflict"
        with artifact_control_scope(again):
            finish_artifact_control(again, {"previewed": True})


def test_competing_command_cannot_claim_current_lease(control):
    with control.owned():
        first = control.begin()
        with pytest.raises(RuntimeStoreError) as busy:
            control.begin("competitor")
        assert busy.value.code == "artifact_owner_busy"
        waiting = control.db.read_runtime_command(control.sid, "competitor")
        assert waiting["status"] == "accepted"
        assert waiting["claimed_holder"] is None
        assert control.db.get_session_turn_lease(control.sid)["holder"] == first.holder
        with artifact_control_scope(first):
            finish_artifact_control(first, {})
        second = control.begin("competitor")
        assert second.run_id == waiting["receipt"]["run_id"]
        assert second.generation > first.generation


def test_missing_foreign_or_detached_transport_cannot_use_claim(control):
    with pytest.raises(RuntimeStoreError) as missing:
        control.begin()
    assert missing.value.code == "artifact_control_required"
    foreign = SimpleNamespace(write=lambda _frame: True)
    with control.owned(foreign), pytest.raises(RuntimeStoreError) as denied:
        control.begin()
    assert denied.value.code == "identity_mismatch"
    with control.owned():
        run = control.begin()
        with artifact_control_scope(run):
            control.session["transport"] = foreign
            with pytest.raises(RuntimeStoreError) as detached:
                assert_artifact_dispatch(run)
            assert detached.value.code == "identity_mismatch"
            control.session["transport"] = control.peer
            assert assert_artifact_dispatch(run) is run
    with pytest.raises(RuntimeStoreError) as scope_missing:
        assert_artifact_dispatch(run)
    assert scope_missing.value.code == "artifact_control_required"


@pytest.mark.parametrize("successor", [False, True])
def test_expired_or_successor_lease_never_replays_claim(control, successor):
    with control.owned():
        run = control.begin()
        control.db._execute_write(lambda conn: conn.execute(
            "UPDATE session_turn_leases SET expires_at=0 WHERE conversation_id=?", (run.session_id,)))
        if successor:
            assert control.db.try_acquire_session_turn_lease(control.sid, "successor", ttl_seconds=60)
            assert control.db.get_session_turn_lease(control.sid)["generation"] > run.generation
        with pytest.raises(RuntimeStoreError) as stale:
            control.begin()
        assert stale.value.code == "artifact_stale_owner"
        with pytest.raises(RuntimeStoreError) as stale_scope:
            with artifact_control_scope(run):
                pytest.fail("Stale claim entered dispatch scope")
        assert stale_scope.value.code == "artifact_stale_owner"
        assert control.db.read_runtime_command(control.sid, run.command_id)["status"] == "claimed"


@pytest.mark.parametrize("reason", ["interrupt", "deadline"])
def test_cancel_or_deadline_blocks_effect_dispatch(control, monkeypatch, reason):
    with control.owned():
        run = control.begin()
        with artifact_control_scope(run):
            if reason == "interrupt":
                control.agent._interrupt_requested = True
            else:
                monkeypatch.setattr("agent.artifact_commands.time.time", lambda: run.deadline_at)
            with pytest.raises(RuntimeStoreError) as cancelled:
                assert_artifact_dispatch(run)
            assert cancelled.value.code == "artifact_control_cancelled"


@pytest.mark.parametrize("status", ["completed", "blocked", "failed", "cancelled"])
def test_terminal_outcome_is_retrievable_and_never_rerun(control, status):
    result = {"artifact_id": "draft", "outcome": status}
    with control.owned():
        run = control.begin()
        with artifact_control_scope(run):
            finish_artifact_control(run, result, status=status)
        record = control.db.read_runtime_command(control.sid, run.command_id)
        assert record["status"] == status and record["result"] == result
        with pytest.raises(RuntimeStoreError) as terminal:
            control.begin()
        assert terminal.value.code == "artifact_control_finished"
        assert control.db.read_runtime_command(control.sid, run.command_id) == record
        assert control.db.get_session_turn_lease(control.sid) is None


def _budget_policy():
    from agent.budget_account import parse_budget_policy
    return parse_budget_policy({"runtime_budget": {
        "schema_version": 1, "mode": "tokens",
        "limits": {"tokens": 1000, "attempts": 5, "cost_micros": None, "wall_ms": 120000,
                   "provider_slots": 1, "executor_slots": 1},
        "deadline_seconds": 120, "request_timeout_ms": 10000,
        "routes": [{"model": "fixture", "base_url": "https://fixture.invalid/v1",
                    "max_input_tokens": 100, "max_output_tokens": 20, "input_overhead_tokens": 10,
                    "output_token_parameter": "max_tokens", "input_cost_micros_per_million": None,
                    "output_cost_micros_per_million": None, "bounds_verified": True}]}})


def test_reopen_and_next_command_keep_original_budget_root_and_deadline(control):
    control.agent._runtime_budget_policy = _budget_policy()
    with control.owned():
        first = control.begin()
        accepted = control.db.read_runtime_run_accepted_at(control.sid, first.run_id)
        assert first.budget.deadline == accepted + first.budget.policy.deadline_seconds
        reservation = first.budget.reserve({"tokens": 10})
        first.budget.dispatched(reservation)
        first.budget.settle(reservation, {"tokens": 10})
        before = control.db.get_budget_account(first.budget.account_id, first.budget.actor)
        again = control.begin()
        assert again.budget.root_id == first.budget.root_id
        assert again.budget.deadline == first.budget.deadline
        assert control.db.get_budget_account(again.budget.account_id, again.budget.actor) == before
        with artifact_control_scope(again):
            finish_artifact_control(again, {})
        next_run = control.begin("artifact-2")
        assert next_run.run_id != first.run_id
        assert next_run.budget.root_id == first.budget.root_id
        assert next_run.budget.deadline <= first.budget.deadline


def test_active_inference_run_prevents_artifact_admission(control):
    control.agent._active_runtime_run = object()
    with control.owned(), pytest.raises(RuntimeStoreError) as busy:
        control.begin()
    assert busy.value.code == "artifact_owner_busy"
    assert control.db.read_runtime_command(control.sid, "artifact-1") is None


def test_simultaneous_commands_get_only_one_writer_claim(control):
    from concurrent.futures import ThreadPoolExecutor

    barrier = threading.Barrier(2)

    def contend(command):
        with control.owned():
            barrier.wait(timeout=5)
            try:
                return control.begin(command)
            except RuntimeStoreError as exc:
                return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(contend, ["concurrent-a", "concurrent-b"]))
    winners = [item for item in outcomes if not isinstance(item, str)]
    assert len(winners) == 1
    assert [item for item in outcomes if isinstance(item, str)] == ["artifact_owner_busy"]
    winner = winners[0]
    assert control.db.get_session_turn_lease(control.sid)["holder"] == winner.holder
    statuses = [control.db.read_runtime_command(control.sid, command)["status"]
                for command in ("concurrent-a", "concurrent-b")]
    assert sorted(statuses) == ["accepted", "claimed"]


def test_reopen_cannot_disable_accepted_budget(control):
    from agent.budget_account import BudgetBlocked

    control.agent._runtime_budget_policy = _budget_policy()
    with control.owned():
        run = control.begin()
        control.agent._runtime_budget_policy = None
        with pytest.raises(BudgetBlocked, match="changed after command acceptance"):
            control.begin()
        assert control.db.get_session_turn_lease(control.sid)["holder"] == run.holder


def test_waiting_artifact_budget_deadline_starts_at_original_acceptance(control, monkeypatch):
    import time
    from agent.budget_account import BudgetBlocked, actor_for
    from hermes_state_budgets import BudgetStoreError

    control.agent._runtime_budget_policy = _budget_policy()
    policy = control.agent._runtime_budget_policy
    start = time.time()
    monkeypatch.setattr("agent.artifact_commands.time.time", lambda: start)
    actor = actor_for(control.agent.runtime_context)
    receipt = control.db.submit_runtime_command(control.sid, actor, {
        "schema_version": 1, "command_id": "artifact-1", "idempotency_key": "artifact-1",
        "expected_revision": None, "operation": "artifact",
        "payload": {"artifact_id": "draft", "action": "preview"},
    }, budget_policy_json=policy.snapshot)
    monkeypatch.setattr("agent.artifact_commands.time.time", lambda: start + policy.deadline_seconds + 1)
    with control.owned():
        # Allocation or scope may enforce the expired deadline, but no dispatch
        # may receive a fresh deadline simply because admission waited.
        with pytest.raises((BudgetBlocked, BudgetStoreError)) as expired:
            run = control.begin()
            assert run.run_id == receipt["run_id"]
            assert run.budget.deadline <= start + policy.deadline_seconds
            with artifact_control_scope(run):
                pytest.fail("Expired accepted command acquired dispatch authority")

    assert isinstance(expired.value, BudgetBlocked) or getattr(expired.value, "code", None) == "budget_expired"


def test_interrupted_cleanup_records_cancellation_without_undo_claim(control):
    from agent.artifact_commands import artifact_control_status, cancel_artifact_control

    with control.owned():
        run = control.begin()
        control.agent._interrupt_requested = True
        cancelled = cancel_artifact_control(control.agent, "live-session", run.command_id)
        assert cancelled["status"] == "cancelled"
        assert cancelled["result"] == {"cancel_requested": True, "effects_undone": False}
        assert not cancelled["owner_live"] and cancelled["expires_at"] is None
        assert control.db.get_session_turn_lease(control.sid) is None
        assert artifact_control_status(control.agent, "live-session", run.command_id) == cancelled
        assert cancel_artifact_control(control.agent, "live-session", run.command_id) == cancelled
        with pytest.raises(RuntimeStoreError) as terminal:
            control.begin()
        assert terminal.value.code == "artifact_control_finished"


@pytest.mark.parametrize("successor", [False, True])
def test_cancellation_cannot_adopt_expired_or_replaced_owner(control, successor):
    from agent.artifact_commands import artifact_control_status, cancel_artifact_control

    with control.owned():
        run = control.begin()
        control.db._execute_write(lambda conn: conn.execute(
            "UPDATE session_turn_leases SET expires_at=0 WHERE conversation_id=?", (run.session_id,)))
        if successor:
            assert control.db.try_acquire_session_turn_lease(control.sid, "successor", ttl_seconds=60)
        before = control.db.get_session_turn_lease(control.sid)
        status = artifact_control_status(control.agent, "live-session", run.command_id)
        assert not status["owner_live"] and status["status"] == "claimed"
        with pytest.raises(RuntimeStoreError) as stale:
            cancel_artifact_control(control.agent, "live-session", run.command_id)
        assert stale.value.code == "artifact_stale_owner"
        assert control.db.get_session_turn_lease(control.sid) == before
        assert control.db.read_runtime_command(control.sid, run.command_id)["status"] == "claimed"


def test_reconciliation_gate_blocks_control_dispatch(control):
    with control.owned():
        run = control.begin()
        with artifact_control_scope(run):
            run.dispatch_blocked.set()
            with pytest.raises(RuntimeStoreError) as unresolved:
                assert_artifact_dispatch(run)
            assert unresolved.value.code == "artifact_reconciliation_required"


def test_non_artifact_rpc_cannot_mint_control_even_with_owned_transport(control):
    with control.owned():
        token = control.server._current_rpc_method.set("runtime.command")
        try:
            with pytest.raises(RuntimeStoreError) as denied:
                control.begin()
            assert denied.value.code == "artifact_control_required"
        finally:
            control.server._current_rpc_method.reset(token)
    assert control.db.read_runtime_command(control.sid, "artifact-1") is None


def test_inherited_model_run_cannot_escalate_to_artifact_control(control):
    from agent.runtime_commands import _RUN, RuntimeRun

    inherited = RuntimeRun(agent=control.agent, db=control.db, session_id=control.sid,
        command_id="model-command", run_id="model-run", holder="model-owner", generation=1,
        context=control.agent.runtime_context)
    with control.owned():
        token = _RUN.set(inherited)
        try:
            with pytest.raises(RuntimeStoreError) as denied:
                control.begin()
            assert denied.value.code == "artifact_control_required"
        finally:
            _RUN.reset(token)
    assert control.db.read_runtime_command(control.sid, "artifact-1") is None
