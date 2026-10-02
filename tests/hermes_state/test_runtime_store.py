"""BE02 durable runtime invariants against independent real SQLite handles."""
from concurrent.futures import ThreadPoolExecutor
import sqlite3
import threading

import pytest

from hermes_state import SessionDB
from hermes_state_runtime import RuntimeStoreError

ACTOR = {"principal_id": "owner", "profile_id": "profile", "agent_id": "ryoko"}


def command(key="one", **changes):
    value = dict(schema_version=1, command_id=key, idempotency_key=key,
                 expected_revision=0, identity_binding=ACTOR,
                 operation="submit", payload={"text": "hello"})
    value.update(changes)
    return value


@pytest.fixture
def stores(tmp_path):
    path = tmp_path / "state.db"
    first, second = SessionDB(path), SessionDB(path)
    first.create_session("session", source="test")
    yield first, second
    first.close()
    second.close()


def own(db, holder="worker"):
    assert db.try_acquire_session_turn_lease("session", holder)
    return db.get_session_turn_lease("session")["generation"]


def assert_error(code, fn):
    with pytest.raises(RuntimeStoreError) as error:
        fn()
    assert error.value.code == code


def test_duplicate_race_is_one_receipt_and_single_execution_admission(stores):
    first, second = stores
    barrier = threading.Barrier(2)

    def accept(db):
        barrier.wait(timeout=10)
        return db.submit_runtime_command("session", ACTOR, command())

    with ThreadPoolExecutor(2) as pool:
        receipts = list(pool.map(accept, stores))
    assert receipts[0] == receipts[1]
    assert receipts[0]["status"] == "accepted"
    assert len(first.replay_runtime_events("session")["events"]) == 1
    generation = own(first)
    assert first.claim_runtime_command("session", "one", holder="worker", generation=generation)
    assert not second.claim_runtime_command("session", "one", holder="worker", generation=generation)
    first.finish_runtime_command("session", "one", holder="worker", generation=generation,
                                 result={"recorded": "result"})
    assert second.submit_runtime_command("session", ACTOR, command()) == receipts[0]
    assert second.read_runtime_snapshot("session")["state"]["status"] == "completed"


def test_conflicts_do_not_append_or_change_original_receipt(stores):
    first, second = stores
    original = first.submit_runtime_command("session", ACTOR, command())
    assert_error("idempotency_conflict", lambda: second.submit_runtime_command(
        "session", ACTOR, command(payload={"text": "different"})))
    assert_error("revision_conflict", lambda: second.submit_runtime_command("session", ACTOR, command("two")))
    assert_error("identity_mismatch", lambda: second.submit_runtime_command(
        "session", ACTOR, command(identity_binding={**ACTOR, "agent_id": "other"})))
    assert_error("unsupported_schema", lambda: second.submit_runtime_command(
        "session", ACTOR, command(schema_version=99)))
    assert first.read_runtime_snapshot("session")["revision"] == original["durable_revision"]


def test_fencing_survives_release_expiry_transfer_and_compression(stores):
    first, second = stores
    generation = own(first)
    first.release_session_turn_lease("session", "worker", generation=generation)
    next_generation = own(second)
    assert next_generation > generation
    assert not first.refresh_session_turn_lease("session", "worker", generation=generation)
    first.release_session_turn_lease("session", "worker", generation=generation)
    assert second.get_session_turn_lease("session")["generation"] == next_generation
    assert_error("stale_owner", lambda: first.append_runtime_event(
        "session", "runtime.output", {"text": "stale"}, holder="worker", generation=generation))
    first.end_session("session", "compression")
    first.create_session("child", source="test", parent_session_id="session")
    assert second.get_session_turn_lease("child")["generation"] == next_generation
    assert not first.try_acquire_session_turn_lease("child", "competitor")
    second._write_sql("UPDATE session_turn_leases SET expires_at=0 WHERE conversation_id=?", ("session",))
    assert_error("stale_owner", lambda: second.append_runtime_event(
        "session", "runtime.output", {}, holder="worker", generation=next_generation))
    assert first.try_acquire_session_turn_lease("child", "competitor")
    successor = first.get_session_turn_lease("child")["generation"]
    assert successor > next_generation
    first.append_runtime_event("child", "runtime.output", {"text": "recorded"},
                               holder="competitor", generation=successor)


def test_two_handle_owner_race_and_claim_fence(stores):
    barrier = threading.Barrier(2)

    def claim(index):
        barrier.wait(timeout=10)
        return stores[index].try_acquire_session_turn_lease("session", f"worker-{index}")

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(claim, range(2)))
    assert sum(results) == 1
    loser = results.index(False)
    stores[0].submit_runtime_command("session", ACTOR, command())
    generation = stores[0].get_session_turn_lease("session")["generation"]
    assert_error("stale_owner", lambda: stores[loser].claim_runtime_command(
        "session", "one", holder=f"worker-{loser}", generation=generation))


def checkpoint():
    return dict(schema_version=1, config_version="config-digest", policy_version="policy-digest",
                runtime_version="runtime-v1", prompt_projection_version="projection-v1",
                unresolved_effects=[{"effect_id": "effect", "status": "outcome_uncertain"}],
                artifacts=[{"artifact_id": "artifact", "version": "1"}], outstanding_requests=[])


def test_checkpoint_cas_retention_and_restart_cursor(stores):
    first, second = stores
    first.submit_runtime_command("session", ACTOR, command())
    generation = own(first)
    before = first.read_runtime_snapshot("session")
    saved = first.publish_runtime_checkpoint("session", checkpoint(), holder="worker", generation=generation,
                                            expected_revision=before["revision"], included_seq=before["revision"])
    assert saved["included_seq"] == before["revision"]
    assert_error("revision_conflict", lambda: second.publish_runtime_checkpoint(
        "session", checkpoint(), holder="worker", generation=generation,
        expected_revision=before["revision"], included_seq=before["revision"]))
    first.prune_runtime_events("session", through_seq=before["revision"], holder="worker", generation=generation)
    expected = second.read_runtime_snapshot("session")
    assert expected["unresolved_effects"] == []
    assert expected["unresolved_invocations"] == [{"operation_id": "effect", "status": "outcome_uncertain"}]
    assert expected["artifacts"] == []  # A checkpoint reference alone is not a committed BE06 artifact.
    first.close()
    second.close()
    restarted = SessionDB(first.db_path)
    try:
        assert restarted.read_runtime_snapshot("session") == expected
        epoch = expected["last_cursor"].rsplit(":", 1)[0]
        for cursor in (f"{epoch}:0", f"{epoch}:99999", "old-process:1", "broken"):
            replay = restarted.replay_runtime_events("session", cursor)
            assert replay["status"] == "snapshot_required"
            assert replay["snapshot"] == expected
            assert replay["last_cursor"] == expected["last_cursor"]
        replay = restarted.replay_runtime_events("session", expected["last_cursor"])
        assert replay["status"] == "ok" and replay["events"] == []
        assert restarted.submit_runtime_command("session", ACTOR, command())["durable_revision"] == before["revision"]
    finally:
        restarted.close()


def test_checkpoint_bounds_versions_and_atomic_rollback(stores, monkeypatch):
    db = stores[0]
    generation = own(db)
    assert_error("unsupported_schema", lambda: db.publish_runtime_checkpoint(
        "session", {**checkpoint(), "schema_version": 9}, holder="worker", generation=generation,
        expected_revision=0, included_seq=0))
    assert_error("payload_too_large", lambda: db.append_runtime_event(
        "session", "runtime.output", {"text": "x" * 300000}, holder="worker", generation=generation))
    append = db._append_runtime_event_on_conn

    def fail_after_append(*args, **kwargs):
        append(*args, **kwargs)
        raise RuntimeError("simulated crash before commit")

    monkeypatch.setattr(db, "_append_runtime_event_on_conn", fail_after_append)
    with pytest.raises(RuntimeError, match="simulated crash"):
        db.submit_runtime_command("session", ACTOR, command())
    assert stores[1].read_runtime_snapshot("session")["revision"] == 0
    monkeypatch.setattr(db, "_append_runtime_event_on_conn", append)
    receipt = db.submit_runtime_command("session", ACTOR, command())
    assert receipt["durable_revision"] == 1


def test_legacy_database_additive_generation_migration(tmp_path):
    path = tmp_path / "state.db"
    db = SessionDB(path)
    db.create_session("session", source="test")
    db.close()
    with sqlite3.connect(path) as conn:
        conn.execute("ALTER TABLE sessions DROP COLUMN turn_owner_generation")
        conn.execute("ALTER TABLE session_turn_leases DROP COLUMN generation")
        conn.execute("UPDATE schema_version SET version=31")
    migrated = SessionDB(path)
    try:
        assert migrated.read_runtime_snapshot("session")["compatibility_status"] == "legacy"
        generation = own(migrated)
        assert generation > 0
        migrated.release_session_turn_lease("session", "worker")
        assert own(migrated) > generation
    finally:
        migrated.close()


def test_live_legacy_lease_is_upgraded_without_stealing_and_claim_is_never_replayed(stores):
    first, second = stores
    own(first)
    first._write_sql("UPDATE session_turn_leases SET generation=0 WHERE conversation_id=?", ("session",))
    assert not second.try_acquire_session_turn_lease("session", "different")
    generation = own(first)
    assert generation > 0
    first.submit_runtime_command("session", ACTOR, command())
    assert first.claim_runtime_command("session", "one", holder="worker", generation=generation)
    first.release_session_turn_lease("session", "worker", generation=generation)
    successor = own(second, "next")
    assert not second.claim_runtime_command("session", "one", holder="next", generation=successor)
    assert_error("stale_owner", lambda: first.finish_runtime_command(
        "session", "one", holder="worker", generation=generation))
    record = second.read_runtime_command("session", "one")
    assert record["status"] == "claimed" and record["result"] is None
    assert record["claimed_generation"] == generation
    second._write_sql("UPDATE session_turn_leases SET expires_at=0 WHERE conversation_id=?", ("session",))
    assert not second.refresh_session_turn_lease("session", "next", generation=successor)


def test_checkpoint_crash_rolls_back_projection_and_unknown_persisted_versions_fail_closed(stores, monkeypatch):
    db, other = stores
    generation = own(db)
    db.submit_runtime_command("session", ACTOR, command())
    before = db.read_runtime_snapshot("session")
    append = db._append_runtime_event_on_conn

    def crash(*args, **kwargs):
        append(*args, **kwargs)
        raise RuntimeError("checkpoint interrupted")

    monkeypatch.setattr(db, "_append_runtime_event_on_conn", crash)
    with pytest.raises(RuntimeError, match="checkpoint interrupted"):
        db.publish_runtime_checkpoint("session", checkpoint(), holder="worker", generation=generation,
                                      expected_revision=before["revision"], included_seq=before["revision"])
    assert other.read_runtime_snapshot("session") == before
    assert_error("checkpoint_required", lambda: other.prune_runtime_events(
        "session", through_seq=1, holder="worker", generation=generation))
    monkeypatch.setattr(db, "_append_runtime_event_on_conn", append)
    db._write_sql("UPDATE runtime_events SET schema_version=99 WHERE session_id=?", ("session",))
    assert_error("unsupported_schema", lambda: other.replay_runtime_events("session"))
    db._write_sql("UPDATE runtime_state SET schema_version=99 WHERE session_id=?", ("session",))
    assert_error("unsupported_schema", lambda: other.read_runtime_snapshot("session"))


def test_control_acknowledgements_do_not_complete_or_replace_the_active_run(stores):
    db, observer = stores
    receipt = db.submit_runtime_command("session", ACTOR, command())
    generation = own(db)
    db.claim_runtime_command("session", "one", holder="worker", generation=generation)
    running = observer.read_runtime_snapshot("session")["state"]
    for operation in ("steer", "cancel", "approval"):
        revision = observer.read_runtime_snapshot("session")["revision"]
        db.submit_runtime_command("session", ACTOR, command(operation, operation=operation, expected_revision=revision))
        assert observer.read_runtime_snapshot("session")["state"] == running
        db.claim_runtime_command("session", operation, holder="worker", generation=generation)
        db.finish_runtime_command("session", operation, holder="worker", generation=generation)
        assert observer.read_runtime_snapshot("session")["state"] == running
    db.finish_runtime_command("session", "one", holder="worker", generation=generation)
    finished = observer.read_runtime_snapshot("session")["state"]
    assert finished == {**running, "status": "completed"}
    assert finished["run_id"] == receipt["run_id"]


def test_pending_submit_and_other_run_completion_do_not_replace_current_owner(stores):
    db, observer = stores
    db.submit_runtime_command("session", ACTOR, command())
    generation = own(db)
    db.claim_runtime_command("session", "one", holder="worker", generation=generation)
    first = observer.read_runtime_snapshot("session")
    second_receipt = db.submit_runtime_command("session", ACTOR, command("two", expected_revision=first["revision"]))
    assert observer.read_runtime_snapshot("session")["state"] == first["state"]
    db.claim_runtime_command("session", "two", holder="worker", generation=generation)
    second = observer.read_runtime_snapshot("session")["state"]
    assert second["run_id"] == second_receipt["run_id"]
    db.finish_runtime_command("session", "one", holder="worker", generation=generation)
    assert observer.read_runtime_snapshot("session")["state"] == second
    db.finish_runtime_command("session", "two", holder="worker", generation=generation)
    assert observer.read_runtime_snapshot("session")["state"] == {**second, "status": "completed"}


def test_inflight_operation_refs_survive_failed_recording_restart_and_checkpoint(stores, monkeypatch):
    db, observer = stores
    generation = own(db)

    def event(kind, operation_id, payload=None):
        return db.append_runtime_event("session", kind, payload or {}, holder="worker",
                                       generation=generation, operation_id=operation_id)

    event("model.started", "model-call")
    event("tool.started", "tool-call")
    event("model.completed", "model-call", {"result": {"text": "recorded output"}})
    event("tool.failed", "tool-call", {"error_type": "TimeoutError"})
    event("tool.started", "interrupted-call")
    before = observer.read_runtime_snapshot("session")
    assert before["unresolved_effects"] == []
    assert before["unresolved_invocations"] == [
        {"operation_id": "tool-call", "status": "outcome_uncertain"},
        {"operation_id": "interrupted-call", "status": "pending"},
    ]
    append = db._append_runtime_event_on_conn

    def crash(*args, **kwargs):
        append(*args, **kwargs)
        raise RuntimeError("outcome commit failed")

    monkeypatch.setattr(db, "_append_runtime_event_on_conn", crash)
    with pytest.raises(RuntimeError, match="outcome commit failed"):
        event("tool.completed", "interrupted-call", {"result": {"recorded": True}})
    assert observer.read_runtime_snapshot("session") == before
    monkeypatch.setattr(db, "_append_runtime_event_on_conn", append)
    saved = {**checkpoint(), "unresolved_effects": before["unresolved_effects"],
             "unresolved_invocations": before["unresolved_invocations"]}
    db.publish_runtime_checkpoint("session", saved, holder="worker", generation=generation,
                                  expected_revision=before["revision"], included_seq=before["revision"])
    db.prune_runtime_events("session", through_seq=before["revision"], holder="worker", generation=generation)
    db.close()
    observer.close()
    reopened = SessionDB(db.db_path)
    try:
        snapshot = reopened.read_runtime_snapshot("session")
        assert snapshot["unresolved_invocations"] == before["unresolved_invocations"]
        replay = reopened.replay_runtime_events("session", "expired:99")
        assert replay["status"] == "snapshot_required"
        assert replay["snapshot"]["unresolved_invocations"] == before["unresolved_invocations"]
    finally:
        reopened.close()


def test_unresolved_operation_limit_fails_before_dispatch_without_dropping_refs(stores):
    db, observer = stores
    generation = own(db)
    references = [{"effect_id": f"unresolved-{index}", "status": "outcome_uncertain"} for index in range(100)]
    db.publish_runtime_checkpoint("session", {**checkpoint(), "unresolved_effects": references},
                                  holder="worker", generation=generation, expected_revision=0, included_seq=0)
    before = observer.read_runtime_snapshot("session")
    assert_error("unresolved_limit", lambda: db.append_runtime_event(
        "session", "tool.started", {}, holder="worker", generation=generation, operation_id="overflow"))
    assert observer.read_runtime_snapshot("session") == before
