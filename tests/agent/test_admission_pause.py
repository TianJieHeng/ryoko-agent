"""Owner pause survives reopen and cannot claim through a pre-pause reservation."""
import pytest
from agent.admission import AdmissionQueue
from agent.result_artifacts import artifact_actor
from hermes_state_runtime import RuntimeStoreError
from hermes_state_runtime_controls import RuntimeOwnerControls
from tests.agent.test_admission import store, command  # noqa: F401


def test_pause_preserves_queue_and_fences_existing_reservation(store):
    db, agents = store
    queue = AdmissionQueue(db)
    receipt = queue.submit(agents["a"], command("one"))
    reserved = queue.reserve_next("worker", {"a"})
    controls = RuntimeOwnerControls(db, artifact_actor(agents["a"].runtime_context))
    controls.set_paused(True, operation_id="pause", expected_revision=0)
    assert queue.submit(agents["a"], command("one")) == receipt
    assert db.try_acquire_session_turn_lease("a", "worker")
    fence = {"holder": "worker", "generation": db.get_session_turn_lease("a")["generation"]}
    with pytest.raises(RuntimeStoreError) as error:
        db.claim_runtime_command("a", "one", **fence)
    assert error.value.code == "owner_paused"
    assert db.read_runtime_command("a", "one")["status"] == "accepted"
    queue.submit(agents["b"], command("other-owner"))
    assert queue.reserve_next("worker-b", {"a", "b"})["session_id"] == "b"
    controls.set_paused(False, operation_id="resume", expected_revision=1)
    assert db.claim_runtime_command("a", "one", **fence)


def test_paused_owner_accepts_only_typed_stop_only_schedule_metadata(store):
    db, agents = store
    actor = artifact_actor(agents["a"].runtime_context)
    RuntimeOwnerControls(db, actor).set_paused(True, operation_id="pause", expected_revision=0)
    payload = {"mode": "runtime.schedule.update", "request_sha256": "a" * 64, "schedule_state": "paused"}
    envelope = {**command("schedule-stop"), "operation": "artifact", "payload": payload}
    def completed_control(conn, sid, encoded, receipt):
        conn.execute("UPDATE runtime_commands SET status='completed',result_json=? WHERE session_id=? AND command_id=?",
                     ('{"dispatch_performed": false}', sid, receipt["command_id"]))
    for value, callback in ((envelope, None), ({**envelope, "payload": {**payload, "schedule_state": "active"}}, completed_control),
                            ({**envelope, "payload": {**payload, "extra": True}}, completed_control)):
        with pytest.raises(RuntimeStoreError) as failure:
            db.submit_runtime_command("a", actor, value, admission=callback)
        assert failure.value.code == "owner_paused"
    receipt = db.submit_runtime_command("a", actor, envelope, admission=completed_control)
    assert receipt["status"] == "accepted"
    assert db.read_runtime_command("a", "schedule-stop")["status"] == "completed"
