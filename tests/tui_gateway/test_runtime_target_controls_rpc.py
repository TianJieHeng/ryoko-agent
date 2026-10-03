"""Exact controls fence one run, including queue/lease races and uncertain retries."""
from dataclasses import replace

import pytest

from agent.admission import AdmissionQueue
from agent.runtime_commands import RuntimeRun
from agent.task_scope import create_task_scope
from tests.tui_gateway.test_runtime_rpc import envelope, runtime  # noqa: F401


def _submit(runtime, cid, label="a"):
    runtime.sessions[f"live-{label}"]["running"] = True
    response = runtime.call("runtime.command", label, **envelope(cid, expected_revision=None))
    assert "result" in response, response
    return response["result"]


def _active(runtime, receipt, label="a"):
    agent = runtime.agents[label]
    db, sid = agent._session_db, agent.session_id
    reservation = AdmissionQueue(db).reserve_next("worker", {sid})
    assert reservation["command_id"] == receipt["command_id"]
    assert db.try_acquire_session_turn_lease(sid, "worker", ttl_seconds=60)
    lease = db.get_session_turn_lease(sid)
    assert db.claim_runtime_command(sid, receipt["command_id"], holder="worker", generation=lease["generation"])
    run = RuntimeRun(agent, db, sid, receipt["command_id"], receipt["run_id"],
                     "worker", lease["generation"], agent.runtime_context,
                     task_scope=create_task_scope(agent, receipt["run_id"]))
    agent._active_runtime_run = run
    effects = []
    agent.interrupt = lambda *a, **k: effects.append("cancel") or True
    agent.steer = lambda text: effects.append("steer") or True
    return run, effects


def _control(runtime, cid, target, operation="cancel", label="a", revision=None):
    return runtime.call("runtime.command", label, **envelope(cid, operation=operation,
        target_run_id=target, expected_revision=revision,
        payload={"reason": "Stop this run"} if operation == "cancel" else {"text": "Use this correction"}))


def _result(response):
    assert "result" in response, response
    return response["result"]


@pytest.mark.parametrize("operation,target_kind", [("cancel", "active"), ("cancel", "queued"),
    ("cancel", "foreign"), ("steer", "active"), ("steer", "queued"), ("steer", "foreign")])
def test_exact_control_isolation_digest_cas_and_read_only_repeats(runtime, operation, target_kind):
    # Real RPC -> profile binding -> queue -> durable lease -> exact control.
    # Two profile homes serve equal session/command IDs; A -> B -> A is isolated.
    runs, effects, queued, spare = {}, {}, {}, {}
    for label in ("a", "b"):
        runs[label], effects[label] = _active(runtime, _submit(runtime, "active", label), label)
        queued[label] = _submit(runtime, "queued", label)
        spare[label] = _submit(runtime, "spare", label)
    targets = {"active": runs["a"].run_id, "queued": queued["a"]["run_id"], "foreign": runs["b"].run_id}
    target = targets[target_kind]
    db, sid = runs["a"].db, runs["a"].session_id
    revision = db.read_runtime_snapshot(sid)["revision"]
    accepted = target_kind == "active" or operation == "cancel" and target_kind == "queued"
    if accepted:
        stale = _control(runtime, "wrong-revision", target, operation, revision=revision - 1)
        assert stale.get("error", {}).get("data", {}).get("code") == "revision_conflict", stale
        assert db.read_runtime_command(sid, "wrong-revision") is None
    first = _result(_control(runtime, "control", target, operation, revision=revision))
    if accepted:
        assert first["status"] == "accepted" and first["run_id"] == target
        record = db.read_runtime_command(sid, "control")
        assert record["command"]["target_run_id"] == target and record["status"] == "completed"
        if operation == "cancel":
            assert record["result"]["cancelled_queued"] == int(target_kind == "queued")
            assert record["result"]["provider_cancelled"] is False
            assert record["result"]["cancellation"]["remote_effects_undone"] is False
            assert record["result"]["cancellation"]["upstream_ack"] is None
        else:
            assert record["result"]["applied"] is False
        current = db.read_runtime_snapshot(sid)["revision"]
        for _ in range(2):
            assert _result(_control(runtime, "control", target, operation, revision=revision)) == first
            observed = _result(runtime.call("runtime.command.receipt", command_id="control"))
            assert observed["receipt"] == first and observed["status"] == "completed"
        assert db.read_runtime_snapshot(sid)["revision"] == current
        changed = _control(runtime, "control", spare["a"]["run_id"], operation, revision=revision)
        assert changed["error"]["data"]["code"] == "idempotency_conflict"
    else:
        assert first["status"] == "rejected" and first["conflict"]["code"] == "target_run_ended"
        assert db.read_runtime_command(sid, "control") is None
        assert db.read_runtime_snapshot(sid)["revision"] == revision
    assert effects["a"] == ([operation] if target_kind == "active" else [])
    assert effects["b"] == []
    for label in ("a", "b", "a"):
        active = _result(runtime.call("runtime.command.receipt", label, command_id="active"))
        assert active["status"] == "claimed" and active["receipt"]["run_id"] == runs[label].run_id
        queued_state = _result(runtime.call("runtime.command.receipt", label, command_id="queued"))["status"]
        assert queued_state == ("cancelled" if label == "a" and target_kind == "queued" and operation == "cancel" else "accepted")
        assert _result(runtime.call("runtime.command.receipt", label, command_id="spare"))["status"] == "accepted"
    foreign_transport = runtime.call("runtime.command", via=runtime.peers["b"],
        **envelope("foreign-transport", operation="cancel", payload={}, target_run_id=target))
    assert foreign_transport["error"]["code"] == 4001
    for invalid in (None, 0, True, "", " padded ", "x" * 257):
        assert _control(runtime, "invalid", invalid, operation)["error"]["code"] == 4000
    assert runtime.call("runtime.command", **envelope("invalid-submit", target_run_id=target))["error"]["code"] == 4000
    agent = runtime.agents["a"]
    context = agent.runtime_context
    agent.runtime_context = replace(context, identity=replace(context.identity, principal_id="foreign-owner"))
    try:
        denied = _control(runtime, "foreign-owner", target, operation)
        assert denied["error"]["data"]["code"] == "identity_mismatch"
    finally:
        agent.runtime_context = context
    assert runtime.dispatched == []


@pytest.mark.parametrize("race", ["queue_claim", "lease_changed", "target_finished", "local_replaced",
    "postclaim_finished", "accepted_retry", "claimed_retry", "legacy_digest", "handoff_bind", "handoff_reset", "effect_outcome_unknown"])
def test_control_races_and_unknown_outcomes_never_apply_to_a_successor(runtime, monkeypatch, race):
    agent = runtime.agents["a"]
    db, sid = agent._session_db, agent.session_id
    first = _submit(runtime, "first")
    run, effects = (None, []) if race == "queue_claim" else _active(runtime, first)
    other = _submit(runtime, "other")
    revision = db.read_runtime_snapshot(sid)["revision"]
    operation = "cancel" if race == "queue_claim" else "steer"
    value = {"schema_version": 1, **envelope("control", operation=operation,
        target_run_id=first["run_id"], expected_revision=revision,
        payload={"reason": "Stop this run"} if operation == "cancel" else {"text": "Use this correction"})}
    identity = agent.runtime_context.identity
    actor = {key: getattr(identity, key) for key in ("principal_id", "profile_id", "agent_id")}
    original = db.submit_runtime_command
    if race in {"handoff_bind", "handoff_reset"}:
        from concurrent.futures import ThreadPoolExecutor
        import threading
        from agent.runtime_commands import bind_runtime_run, reset_runtime_run

        ready, begin, attempted = (threading.Event() for _ in range(3))
        control_thread = threading.get_ident()

        class ObservedLock:
            def __init__(self):
                self.lock = threading.RLock()

            def __enter__(self):
                if threading.get_ident() != control_thread and begin.is_set():
                    attempted.set()
                self.lock.acquire()

            def __exit__(self, *exc):
                self.lock.release()

        run = replace(run, control_lock=ObservedLock())
        agent._active_runtime_run = run

        def handoff():
            token = bind_runtime_run(run)
            ready.set()
            assert begin.wait(5)
            if race == "handoff_bind":
                successor = replace(run, run_id=other["run_id"])
                next_token = bind_runtime_run(successor)
                reset_runtime_run(next_token, successor)
            reset_runtime_run(token, run)

        def steer(text):
            begin.set()
            assert attempted.wait(5)
            assert agent._active_runtime_run is run
            effects.append("steer")
            return True

        agent.steer = steer
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(handoff)
            assert ready.wait(5)
            response = _result(runtime.call("runtime.command", **value))
            assert response["status"] == "accepted"
            future.result(timeout=5)
        assert effects == ["steer"]
        assert db.read_runtime_command(sid, "other")["status"] == "accepted"
        assert runtime.dispatched == []
        return
    if race == "effect_outcome_unknown":
        def uncertain(text):
            effects.append("steer")
            raise OSError("Local control outcome was not recorded")

        agent.steer = uncertain
        # The isolation dispatcher deliberately rethrows unexpected errors.
        with pytest.raises(OSError, match="outcome was not recorded"):
            runtime.call("runtime.command", **value)
        record = db.read_runtime_command(sid, "control")
        assert record["status"] == "claimed" and record["result"] is None
        for _ in range(2):
            assert _result(runtime.call("runtime.command", **value)) == record["receipt"]
            assert _result(runtime.call("runtime.command.receipt", command_id="control"))["status"] == "claimed"
        assert effects == ["steer"] and runtime.dispatched == []
        assert db.read_runtime_command(sid, "other")["status"] == "accepted"
        return
    if race in {"accepted_retry", "claimed_retry", "legacy_digest"}:
        stored = {**value, "identity_binding": actor}
        if race == "legacy_digest":
            stored.pop("target_run_id")
            value.pop("target_run_id")
        receipt = original(sid, actor, stored)
        if race != "accepted_retry":
            assert db.claim_runtime_command(sid, "control", holder=run.holder, generation=run.generation)
        before = db.read_runtime_snapshot(sid)["revision"]
        for _ in range(2):
            response = runtime.call("runtime.command", **value)
            assert _result(response) == receipt
            result = _result(runtime.call("runtime.command.receipt", command_id="control"))
            assert result["status"] == ("accepted" if race == "accepted_retry" else "claimed")
        assert db.read_runtime_snapshot(sid)["revision"] == before
        if race == "legacy_digest":
            assert "target_run_id" not in db.read_runtime_command(sid, "control")["command"]
    else:
        def interleave(*args, **kwargs):
            if kwargs.get("command", {}).get("command_id") != "control":
                return original(*args, **kwargs)
            if race == "queue_claim":
                reserved = AdmissionQueue(db).reserve_next("winner", {sid})
                assert reserved["command_id"] == "first"
                assert db.try_acquire_session_turn_lease(sid, "winner", ttl_seconds=60)
                generation = db.get_session_turn_lease(sid)["generation"]
                assert db.claim_runtime_command(sid, "first", holder="winner", generation=generation)
            elif race == "lease_changed":
                db.release_session_turn_lease(sid, run.holder)
                assert db.try_acquire_session_turn_lease(sid, "successor", ttl_seconds=60)
            elif race == "target_finished":
                db.finish_runtime_command(sid, "first", holder=run.holder, generation=run.generation,
                                          result={"final_response": "Already committed"})
            # Revision CAS is independently exercised above; these races verify
            # exact target/generation fences even when the caller permits any revision.
            response = original(*args, **kwargs)
            if race == "local_replaced":
                agent._active_runtime_run = replace(run, run_id=other["run_id"])
            elif race == "postclaim_finished":
                db.finish_runtime_command(sid, "first", holder=run.holder, generation=run.generation,
                                          result={"final_response": "Already committed"})
            return response
        value["expected_revision"] = None
        monkeypatch.setattr(db, "submit_runtime_command", interleave)
        response = _result(runtime.call("runtime.command", **value))
        monkeypatch.setattr(db, "submit_runtime_command", original)
        record = db.read_runtime_command(sid, "control")
        if race in {"local_replaced", "postclaim_finished"}:
            assert response == record["receipt"] and response["status"] == "accepted"
            assert record["status"] == "claimed"
            for _ in range(2):
                assert _result(runtime.call("runtime.command", **value)) == record["receipt"]
                assert _result(runtime.call("runtime.command.receipt", command_id="control"))["status"] == "claimed"
        else:
            assert response["status"] == "rejected" and response["conflict"]["code"] == "target_run_ended"
            assert record is None
        if race in {"target_finished", "postclaim_finished"}:
            assert db.read_runtime_command(sid, "first")["result"]["final_response"] == "Already committed"
    assert effects == [] and runtime.dispatched == []
    assert db.read_runtime_command(sid, "other")["status"] == "accepted"
