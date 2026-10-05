"""Real admitted journal writes cannot hold cancellation behind an optional sink."""
from concurrent.futures import Future
from contextvars import copy_context
import json
import sqlite3
import threading
import time

import pytest

from agent.decisions.batching import ReceiptWorkers
from agent.decisions.client import DecisionClient
from agent.decisions.contracts import DecisionError
from agent.decisions.planner_context import build_planner_context
from agent.decisions.planner_runtime import prepare_front_door
from agent.decisions.policy import PointPolicy
from agent.decisions.receipts import JournalSink, scope_digest
from agent.runtime_commands import finish_turn_command, submit_command
from tests.agent.test_budget_runtime import active
from tests.agent.test_decision_planner_runtime import agents
from tests.agent.test_laya_bundle_owner import BUNDLE, SyntheticTransport


def start(callback):
    future = Future()
    def work():
        try:
            future.set_result(callback())
        except BaseException as exc:
            future.set_exception(exc)
    thread = threading.Thread(target=copy_context().run, args=(work,), daemon=True)
    thread.start()
    return future, thread


@pytest.mark.parametrize("event_type", ["decision.observed", "decision.policy", "decision.tool_plan"])
@pytest.mark.parametrize("budget", [False, True])
def test_blocked_real_append_abandons_before_transaction_without_blocking_lifecycle(
        agents, monkeypatch, event_type, budget):
    _, agent, db, _ = agents("blocked_journal", "off", budget=budget)
    entered, release, blocked_threads = threading.Event(), threading.Event(), []
    workers = ReceiptWorkers(1)
    monkeypatch.setattr("agent.decisions.batching.SHARED_RECEIPT_WORKERS", workers)
    def context(run, **kwargs):
        return build_planner_context(kwargs["request"], scope_digest=scope_digest(run.context),
                                     classification="synthetic")
    monkeypatch.setattr("agent.decisions.planner_runtime.owner_planner_context", context)
    append = db.append_runtime_event
    def blocked(session, kind, payload, **kwargs):
        if kind == event_type:
            blocked_threads.append(threading.current_thread())
            entered.set()
            assert release.wait(10), "test did not release the synthetic storage stall"
        return append(session, kind, payload, **kwargs)
    monkeypatch.setattr(db, "append_runtime_event", blocked)

    with active(agent) as run:
        # Include the unchanged production default in the complete operation's
        # policy preamble; later-stage cases allow deterministic setup time.
        timeout = PointPolicy().timeout_seconds if event_type == "decision.policy" else 1
        client = DecisionClient(bundle=BUNDLE, transport=SyntheticTransport(), sink=JournalSink(run),
            policies={"DP16": PointPolicy("shadow", timeout_seconds=timeout)}, receipt_workers=workers)
        planner, thread = start(lambda: prepare_front_door(run, client, request_definitions=agent.tools,
            request="synthetic bounded journal", turn_id="journal-deadline"))
        try:
            assert entered.wait(2)
            with pytest.raises(DecisionError, match="receipt_deadline"):
                planner.result(timeout=2)
            assert not release.is_set() and not run.dispatch_blocked.is_set()
            assert getattr(agent, "_decision_prepared_frontdoor", None) is None
            with pytest.raises(DecisionError, match="receipt_capacity"):
                workers.persist(lambda row: None, ({},), deadline=time.time() + 1)
            cancelled, cancellation_thread = start(lambda: submit_command(agent, {
                "schema_version": 1, "command_id": "cancel-journal", "idempotency_key": "cancel-journal",
                "expected_revision": None, "operation": "cancel", "payload": {}}))
            assert cancelled.result(timeout=2)["status"] == "accepted"
            assert run.task_scope.cancelled.is_set()
            finished, finalizer = start(lambda: finish_turn_command(run,
                {"interrupted": True, "completed": False, "final_response": ""}))
            finished.result(timeout=2)
            assert not release.is_set()
            assert db.read_runtime_command(run.session_id, run.command_id)["status"] == "cancelled"
            cancellation_thread.join(2)
            finalizer.join(2)
        finally:
            release.set()
            thread.join(2)
            for worker in blocked_threads:
                worker.join(2)
        assert all(not worker.is_alive() for worker in blocked_threads)
        events = db.replay_runtime_events(run.session_id, limit=500)["events"]
        assert not any(event["type"] == event_type for event in events)
        assert not any(event["type"] == "decision.tool_plan" for event in events)
        assert getattr(agent, "_decision_prepared_frontdoor", None) is None
        assert "synthetic bounded journal" not in json.dumps([event for event in events
                                                              if event["type"].startswith("decision.")])
        assert workers.persist(lambda row: None, ({},), deadline=time.time() + 1)


@pytest.mark.parametrize("phase", ["before_commit", "after_commit", "storage_error"])
def test_expiry_distinguishes_rollback_safe_work_from_unknown_commit(agents, monkeypatch, phase):
    _, agent, db, _ = agents("transaction_journal", "off")
    entered, release, threads = threading.Event(), threading.Event(), []
    def stall(kind):
        if kind == "decision.observed":
            threads.append(threading.current_thread())
            entered.set()
            if phase == "storage_error":
                raise sqlite3.DatabaseError("synthetic-sensitive-storage-error")
            assert release.wait(10)
    if phase != "after_commit":
        append = db._append_runtime_event_on_conn
        def blocked(conn, session, kind, payload, *args, **kwargs):
            result = append(conn, session, kind, payload, *args, **kwargs)
            stall(kind)
            return result
        monkeypatch.setattr(db, "_append_runtime_event_on_conn", blocked)
    else:
        append = db.append_runtime_event
        def blocked(session, kind, payload, **kwargs):
            result = append(session, kind, payload, **kwargs)
            stall(kind)
            return result
        monkeypatch.setattr(db, "append_runtime_event", blocked)
    workers = ReceiptWorkers(1)
    with active(agent) as run:
        future, caller = start(lambda: workers.persist(JournalSink(run),
            ({"scope_digest": scope_digest(run.context)},),
            deadline=time.time() + PointPolicy().timeout_seconds))
        try:
            assert entered.wait(2)
            reason = "receipt_unavailable" if phase == "storage_error" else "receipt_deadline"
            with pytest.raises(DecisionError, match="^" + reason + "$"):
                future.result(timeout=2)
            # Before the final guard, abandonment still guarantees rollback.
            # Once commit can have happened, missing acknowledgement is unknown.
            assert run.dispatch_blocked.is_set() == (phase != "before_commit")
            assert run.control_lock.acquire(blocking=False)
            run.control_lock.release()
        finally:
            release.set()
            caller.join(2)
            for thread in threads:
                thread.join(2)
        assert all(not thread.is_alive() for thread in threads)
        recorded = [event for event in db.replay_runtime_events(run.session_id)["events"]
                    if event["type"] == "decision.observed"]
        assert len(recorded) == int(phase == "after_commit")
        assert run.dispatch_blocked.is_set() == (phase != "before_commit")
