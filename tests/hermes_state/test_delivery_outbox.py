"""One-writer result visibility, independent delivery truth and bounded recovery."""
import hashlib
import json

import pytest

from hermes_state import SessionDB
from hermes_state_runtime import RuntimeStoreError

ACTOR = {"principal_id": "owner", "profile_id": "profile", "agent_id": "primary"}


def prepare(db, key="command"):
    db.create_session("session", source="test")
    receipt = db.submit_runtime_command("session", ACTOR, {
        "schema_version": 1, "command_id": key, "idempotency_key": key,
        "expected_revision": None, "identity_binding": ACTOR,
        "operation": "submit", "payload": {"text": "hello"}})
    assert db.try_acquire_session_turn_lease("session", "owner")
    generation = db.get_session_turn_lease("session")["generation"]
    assert db.claim_runtime_command("session", key, holder="owner", generation=generation)
    descriptor = {"artifact_id": "artifact", "version": 1, "producing_run": receipt["run_id"],
                  "locator": "runtime-artifacts/private/result.blob", "mime": "application/json",
                  "size": 2, "sha256": hashlib.sha256(b"{}").hexdigest()}
    from hermes_state_effects import effect_digest
    effect = db.prepare_effect("session", ACTOR, run_id=receipt["run_id"], holder="owner", generation=generation,
        operation_id="publish-result", intent_key="publish-result", operation_type="artifact_publish",
        input_digest=effect_digest(descriptor), target_ref="artifact:artifact:1",
        policy_digest="a" * 64, policy_version="1", input_revision=descriptor["sha256"], artifact_revision="1",
        provider_idempotency="supported", idempotency_key="immutable-result", input_ref=descriptor)
    db.dispatch_effect(effect["effect_id"], ACTOR, holder="owner", generation=generation)
    db.record_effect_outcome(effect["effect_id"], ACTOR, holder="owner", generation=generation, state="confirmed",
                            receipt={"kind": "fixture_publication", "sha256": descriptor["sha256"]})
    return descriptor, generation


def commit(db, descriptor, generation):
    return db.commit_runtime_result("session", "command", actor=ACTOR, descriptor=descriptor,
                                   holder="owner", generation=generation, result_summary={"completed": True})


def test_artifact_completion_and_outbox_are_one_atomic_visibility_boundary(tmp_path, monkeypatch):
    first, other = SessionDB(tmp_path / "state.db"), SessionDB(tmp_path / "state.db")
    try:
        descriptor, generation = prepare(first)
        original = first._append_runtime_event_on_conn
        def fail(*args, **kwargs):
            raise OSError("injected transaction failure")
        monkeypatch.setattr(first, "_append_runtime_event_on_conn", fail)
        with pytest.raises(OSError):
            commit(first, descriptor, generation)
        assert other.read_runtime_command("session", "command")["status"] == "claimed"
        with pytest.raises(RuntimeStoreError, match="No committed result"):
            other.read_runtime_result_artifact("session", ACTOR, "command")
        with other._runtime_read() as conn:
            assert conn.execute("SELECT COUNT(*) FROM delivery_obligations").fetchone()[0] == 0
        monkeypatch.setattr(first, "_append_runtime_event_on_conn", original)
        reference = commit(first, descriptor, generation)
        assert other.read_runtime_command("session", "command")["status"] == "completed"
        assert other.read_runtime_result_artifact("session", ACTOR, "command") == descriptor
        assert other.read_runtime_delivery("session", ACTOR, reference["delivery_id"])["state"] == "pending"
        with pytest.raises(RuntimeStoreError, match="different actor"):
            other.read_runtime_delivery("session", {**ACTOR, "agent_id": "foreign"}, reference["delivery_id"])
        with pytest.raises(RuntimeStoreError, match="not owned"):
            commit(first, descriptor, generation)
        assert len(other.read_runtime_snapshot("session")["artifacts"]) == 1
    finally:
        first.close()
        other.close()


def test_uncertainty_held_and_budgets_never_destroy_committed_result(tmp_path, monkeypatch):
    import hermes_state_delivery as store
    from gateway import delivery_ledger
    db = SessionDB(tmp_path / "state.db")
    try:
        descriptor, generation = prepare(db)
        reference = commit(db, descriptor, generation)
        delivery_id = reference["delivery_id"]
        now = store.time.time()
        claim = db.claim_runtime_delivery("session", ACTOR, delivery_id)
        assert db.claim_runtime_delivery("session", ACTOR, delivery_id) is None
        assert db.finish_runtime_delivery_attempt("session", ACTOR, delivery_id, claim["attempt_token"],
                                                  accepted=False)["state"] == "outcome_unknown"
        assert db.claim_runtime_delivery("session", ACTOR, delivery_id) is None
        # Legacy sweeps cannot spend or finalize runtime attempts in the same table.
        monkeypatch.setattr(delivery_ledger, "_db_path", lambda: db.db_path)
        assert delivery_ledger.sweep_recoverable(now=now + 500) == []
        delivery_ledger.mark_delivered(delivery_id)
        assert db.read_runtime_delivery("session", ACTOR, delivery_id)["state"] == "outcome_unknown"
        # Explicit local retry is bounded, and a partial receipt never claims its artifact arrived.
        db.repair_runtime_delivery("session", ACTOR, delivery_id)
        second = db.claim_runtime_delivery("session", ACTOR, delivery_id)
        db.finish_runtime_delivery_attempt("session", ACTOR, delivery_id, second["attempt_token"], accepted=True)
        partial = db.acknowledge_runtime_delivery("session", ACTOR, delivery_id,
            attempt_token=second["attempt_token"], sha256=descriptor["sha256"], text_received=True)
        assert partial["state"] == "partial" and partial["components"]["artifact"] == "not_sent"
        monkeypatch.setattr(store.time, "time", lambda: now + 90000)
        assert db.repair_runtime_delivery("session", ACTOR, delivery_id)["state"] == "dead_letter"
        assert db.claim_runtime_delivery("session", ACTOR, delivery_id) is None
        assert db.read_runtime_result_artifact("session", ACTOR, "command") == descriptor
        assert db.read_runtime_command("session", "command")["status"] == "completed"
    finally:
        db.close()
