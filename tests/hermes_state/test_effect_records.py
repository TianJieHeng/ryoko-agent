"""BE06 authoritative effects and approvals against real SQLite and process death."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import time

import pytest

from hermes_state import SessionDB
from hermes_state_effects import effect_digest
from hermes_state_runtime import RuntimeStoreError

ACTOR = {"principal_id": "owner", "profile_id": "profile", "agent_id": "ryoko"}


@pytest.fixture
def stores(tmp_path):
    first = SessionDB(tmp_path / "state.db")
    first.create_session("session", source="test")
    receipt = first.submit_runtime_command("session", ACTOR, dict(schema_version=1, command_id="submit",
        idempotency_key="submit", operation="submit", identity_binding=ACTOR, payload={"text": "publish"}))
    assert first.try_acquire_session_turn_lease("session", "worker")
    generation = first.get_session_turn_lease("session")["generation"]
    assert first.claim_runtime_command("session", "submit", holder="worker", generation=generation)
    second = SessionDB(first.db_path)
    yield first, second, receipt["run_id"], {"holder": "worker", "generation": generation}
    first.close()
    second.close()


def scope(stores, **changes):
    _, _, run_id, fence = stores
    value = dict(session_id="session", run_id=run_id, **fence, action_digest=effect_digest({"action": "publish"}),
        input_digest=effect_digest({"payload": "immutable"}), target_ref="artifact:result:1", policy_version="1",
        policy_digest=effect_digest({"policy": "strict"}), input_revision="source-1", artifact_revision="1")
    value.update(changes)
    return value


def prepare(stores, **changes):
    first, _, _, _ = stores
    args = scope(stores)
    args.update(operation_id="publish-result-1", intent_key="user-action-1", operation_type="artifact_publish")
    args.update(changes)
    return first.prepare_effect(actor=ACTOR, **args)


def approval(stores, **changes):
    first, _, _, _ = stores
    args = scope(stores)
    args.update(expires_at=time.time() + 300)
    args.update(changes)
    row = first.request_effect_approval(actor=ACTOR, **args)
    return first.resolve_effect_approval(row["approval_id"], ACTOR, **stores[3], approval_digest=row["approval_digest"], choice="once")


def reject(code, fn):
    with pytest.raises(RuntimeStoreError) as error:
        fn()
    assert error.value.code == code


def test_explicit_intent_identity_and_provider_support(stores):
    first, second, _, fence = stores
    effect = prepare(stores)
    assert prepare(stores) == effect
    reject("idempotency_conflict", lambda: prepare(stores, input_digest=effect_digest({"changed": 1})))
    distinct = prepare(stores, intent_key="user-action-2", operation_id="publish-result-2")
    assert distinct["effect_id"] != effect["effect_id"]
    reject("invalid_effect", lambda: prepare(stores, provider_idempotency="unsupported", idempotency_key="pretend"))
    reject("invalid_effect", lambda: prepare(stores, provider_idempotency="supported"))
    assert second.dispatch_effect(effect["effect_id"], ACTOR, **fence)["dispatched_now"]
    assert not first.dispatch_effect(effect["effect_id"], ACTOR, **fence)["dispatched_now"]
    assert not first.get_effect(effect["effect_id"], ACTOR)["exactly_once_external"]


def test_two_real_writers_consume_approval_and_dispatch_once(stores):
    first, second, _, fence = stores
    approved = approval(stores)
    effect = prepare(stores, approval_id=approved["approval_id"])
    barrier = threading.Barrier(2)
    def dispatch(db):
        barrier.wait(timeout=10)
        return db.dispatch_effect(effect["effect_id"], ACTOR, **fence)
    with ThreadPoolExecutor(2) as pool:
        receipts = list(pool.map(dispatch, [first, second]))
    assert sum(item["dispatched_now"] for item in receipts) == 1
    row = first.get_effect_approval(approved["approval_id"], ACTOR)
    assert row["status"] == "consumed" and row["consumer_id"] == effect["effect_id"]
    reject("approval_mismatch", lambda: first.resolve_effect_approval(approved["approval_id"], ACTOR,
        **fence, approval_digest=approved["approval_digest"], choice="once"))


@pytest.mark.parametrize("field,value", [("action_digest", effect_digest("different")),
    ("input_digest", effect_digest("different")), ("target_ref", "artifact:other:1"),
    ("policy_digest", effect_digest("different")), ("policy_version", "2"),
    ("input_revision", "changed"), ("artifact_revision", "2")])
def test_approval_never_broadens_for_changed_scope(stores, field, value):
    first, _, _, _ = stores
    approved = approval(stores)
    reject("approval_mismatch", lambda: first.consume_effect_approval(approved["approval_id"], ACTOR,
        consumer_id="capability", **scope(stores, **{field: value})))
    assert first.get_effect_approval(approved["approval_id"], ACTOR)["status"] == "approved"


def test_approval_expiry_and_durable_one_use_survive_reopen(stores):
    first, _, _, _ = stores
    approved = approval(stores)
    reopened = SessionDB(first.db_path)
    try:
        reopened.consume_effect_approval(approved["approval_id"], ACTOR, consumer_id="capability", **scope(stores))
        reject("approval_mismatch", lambda: first.consume_effect_approval(approved["approval_id"], ACTOR,
            consumer_id="next-capability", **scope(stores)))
        expired = approval(stores)
        first._write_sql("UPDATE runtime_effect_approvals SET expires_at=0 WHERE approval_id=?", (expired["approval_id"],))
        reject("approval_expired", lambda: reopened.consume_effect_approval(expired["approval_id"], ACTOR,
            consumer_id="late", **scope(stores)))
    finally:
        reopened.close()


def test_actor_run_and_owner_cannot_be_substituted(stores):
    first, _, _, fence = stores
    approved = approval(stores)
    prepared = prepare(stores, approval_id=approved["approval_id"])
    reject("identity_mismatch", lambda: first.get_effect(prepared["effect_id"], {**ACTOR, "agent_id": "sibling"}))
    reject("effect_run_not_found", lambda: prepare(stores, run_id="invented"))
    first.release_session_turn_lease("session", "worker", generation=fence["generation"])
    assert first.try_acquire_session_turn_lease("session", "successor")
    new_fence = dict(holder="successor", generation=first.get_session_turn_lease("session")["generation"])
    reject("stale_owner", lambda: first.dispatch_effect(prepared["effect_id"], ACTOR, **fence))
    reject("claim_conflict", lambda: first.dispatch_effect(prepared["effect_id"], ACTOR, **new_fence))
    reject("claim_conflict", lambda: first.resolve_effect_approval(approved["approval_id"], ACTOR,
        **new_fence, approval_digest=approved["approval_digest"], choice="once"))
    assert first.get_effect_approval(approved["approval_id"], ACTOR)["status"] == "approved"


def test_unknown_is_not_retryable_and_successor_reconciles_with_evidence(stores):
    first, second, _, fence = stores
    effect = prepare(stores)
    first.dispatch_effect(effect["effect_id"], ACTOR, **fence)
    first.release_session_turn_lease("session", "worker", generation=fence["generation"])
    assert second.try_acquire_session_turn_lease("session", "successor")
    new_fence = dict(holder="successor", generation=second.get_session_turn_lease("session")["generation"])
    reject("effect_reconciliation_required", lambda: second.record_effect_outcome(effect["effect_id"], ACTOR, **new_fence,
        state="confirmed", receipt={"receipt_id": "unverified-successor"}))
    unknown = second.record_effect_outcome(effect["effect_id"], ACTOR, **new_fence,
        state="outcome_unknown", evidence={"kind": "owner_lost", "reason": "no_receipt"})
    assert unknown["state"] == "outcome_unknown"
    assert not second.dispatch_effect(effect["effect_id"], ACTOR, **new_fence)["dispatched_now"]
    reject("stale_owner", lambda: first.record_effect_outcome(effect["effect_id"], ACTOR, **fence,
        state="confirmed", receipt={"receipt_id": "stale"}))
    reject("effect_evidence_required", lambda: second.record_effect_outcome(effect["effect_id"], ACTOR, **new_fence,
        state="confirmed", receipt={"receipt_id": "not-enough"}))
    second.record_effect_outcome(effect["effect_id"], ACTOR, **new_fence, state="reconciliation_required",
        evidence={"kind": "provider_current_state", "state": "missing"})
    second.record_effect_outcome(effect["effect_id"], ACTOR, **new_fence, state="confirmed",
        receipt={"receipt_id": "remote-id"}, evidence={"kind": "provider_current_state", "sha256": effect["input_digest"]})
    evidence = first.get_effect(effect["effect_id"], ACTOR)["evidence"]
    assert [item["state"] for item in evidence] == ["outcome_unknown", "reconciliation_required", "confirmed"]
    reject("effect_transition_conflict", lambda: second.record_effect_outcome(effect["effect_id"], ACTOR, **new_fence,
        state="failed", evidence={"kind": "guess"}))
    assert first.get_effect(effect["effect_id"], ACTOR)["evidence"] == evidence


def test_cancellation_retains_dispatched_set_and_stops_new_dispatch(stores):
    first, _, _, fence = stores
    dispatched = prepare(stores)
    first.dispatch_effect(dispatched["effect_id"], ACTOR, **fence)
    pending = prepare(stores, operation_id="second", intent_key="second", operation_type="external_mutation")
    first.submit_runtime_command("session", ACTOR, dict(schema_version=1, command_id="cancel", idempotency_key="cancel",
        identity_binding=ACTOR, operation="cancel", payload={}))
    reject("run_cancelled", lambda: first.dispatch_effect(pending["effect_id"], ACTOR, **fence))
    assert {item["state"] for item in first.list_effects("session", ACTOR, unresolved_only=True)} == {"dispatched", "prepared"}
    first.record_effect_outcome(dispatched["effect_id"], ACTOR, **fence, state="confirmed", receipt={"receipt_id": "accepted-before-cancel"})
    assert first.get_effect(dispatched["effect_id"], ACTOR)["state"] == "confirmed"
    final_artifact = prepare(stores, operation_id="final", intent_key="final")
    assert first.dispatch_effect(final_artifact["effect_id"], ACTOR, **fence)["dispatched_now"]


def test_approval_and_dispatch_roll_back_together_on_event_failure(stores, monkeypatch):
    first, _, _, fence = stores
    approved = approval(stores)
    effect = prepare(stores, approval_id=approved["approval_id"])
    append = first._effect_event_on_conn
    def crash(*args):
        append(*args)
        raise RuntimeError("crash before transaction commit")
    monkeypatch.setattr(first, "_effect_event_on_conn", crash)
    with pytest.raises(RuntimeError, match="before transaction"):
        first.dispatch_effect(effect["effect_id"], ACTOR, **fence)
    assert first.get_effect(effect["effect_id"], ACTOR)["state"] == "prepared"
    assert first.get_effect_approval(approved["approval_id"], ACTOR)["status"] == "approved"


@pytest.mark.parametrize("after_dispatch", [False, True])
def test_hard_process_exit_keeps_intent_without_implicit_replay(stores, tmp_path, after_dispatch):
    first, _, _, fence = stores
    effect = prepare(stores)
    remote = tmp_path / "remote-accepted"
    script = '''import json, os, sys
from hermes_state import SessionDB
from pathlib import Path
args=json.loads(sys.argv[1]); db=SessionDB(Path(args["db"]))
if args["dispatch"]:
    receipt=db.dispatch_effect(args["effect"],args["actor"],**args["fence"])
    assert receipt["dispatched_now"]
    with open(args["remote"],"w") as stream:
        stream.write("accepted"); stream.flush(); os.fsync(stream.fileno())
os._exit(47)
'''
    args = dict(db=str(first.db_path), effect=effect["effect_id"], actor=ACTOR, fence=fence,
                remote=str(remote), dispatch=after_dispatch)
    result = subprocess.run([sys.executable, "-c", script, json.dumps(args)], cwd=Path(__file__).resolve().parents[2],
                            env=dict(os.environ), capture_output=True, text=True, timeout=30)
    assert result.returncode == 47, result.stderr
    reopened = SessionDB(first.db_path)
    try:
        record = reopened.get_effect(effect["effect_id"], ACTOR)
        assert record["state"] == ("dispatched" if after_dispatch else "prepared")
        assert remote.exists() == after_dispatch
        if after_dispatch:
            assert not reopened.dispatch_effect(effect["effect_id"], ACTOR, **fence)["dispatched_now"]
    finally:
        reopened.close()


def test_audit_survives_transcript_delete_and_refuses_private_payloads(stores):
    first, _, _, fence = stores
    effect = prepare(stores)
    first.dispatch_effect(effect["effect_id"], ACTOR, **fence)
    reject("invalid_effect", lambda: first.record_effect_outcome(effect["effect_id"], ACTOR, **fence,
        state="outcome_unknown", receipt={"access_token": "must-not-persist"}))
    first._write_sql("DELETE FROM sessions WHERE id=?", ("session",))
    assert first.get_effect(effect["effect_id"], ACTOR)["state"] == "dispatched"
    assert first.list_effects("session", ACTOR)[0]["effect_id"] == effect["effect_id"]
    assert "must-not-persist" not in first.db_path.read_bytes().decode(errors="ignore")


def test_additive_schema_migrates_legacy_delivery_without_reclassifying(tmp_path):
    path = tmp_path / "state.db"
    db = SessionDB(path)
    db.close()
    with sqlite3.connect(path) as conn:
        conn.execute("DROP TABLE delivery_obligations")
        conn.execute("CREATE TABLE delivery_obligations(obligation_id TEXT PRIMARY KEY,session_key TEXT NOT NULL,"
                     "platform TEXT NOT NULL,chat_id TEXT NOT NULL,thread_id TEXT,content TEXT NOT NULL,state TEXT NOT NULL,"
                     "attempts INTEGER NOT NULL DEFAULT 0,created_at REAL NOT NULL,updated_at REAL NOT NULL,"
                     "owner_pid INTEGER,owner_started_at INTEGER,last_error TEXT,adapter_profile TEXT)")
        conn.execute("INSERT INTO delivery_obligations(obligation_id,session_key,platform,chat_id,content,state,created_at,updated_at)"
                     " VALUES('old','session','test','recipient','old reply','attempting',1,1)")
        conn.execute("UPDATE schema_version SET version=34")
    reopened = SessionDB(path)
    try:
        with reopened._read_ctx() as conn:
            legacy = dict(conn.execute("SELECT * FROM delivery_obligations WHERE obligation_id='old'").fetchone())
        assert legacy["authority"] == "legacy"
        assert legacy["state"] == "attempting" and legacy["content"] == "old reply"
        assert legacy["acknowledgement_json"] == "{}"
    finally:
        reopened.close()


def test_future_record_versions_fail_closed_on_direct_and_list_reads(stores):
    first, _, _, _ = stores
    effect = prepare(stores)
    approved = approval(stores)
    first._write_sql("UPDATE runtime_effects SET schema_version=99 WHERE effect_id=?", (effect["effect_id"],))
    first._write_sql("UPDATE runtime_effect_approvals SET schema_version=99 WHERE approval_id=?", (approved["approval_id"],))
    reject("unsupported_schema", lambda: first.get_effect(effect["effect_id"], ACTOR))
    reject("unsupported_schema", lambda: first.list_effects("session", ACTOR))
    reject("unsupported_schema", lambda: first.get_effect_approval(approved["approval_id"], ACTOR))
    reject("unsupported_schema", lambda: first.list_effect_approvals("session", ACTOR))


def test_intent_and_projection_commit_or_rollback_on_same_writer(stores):
    first, _, _, _ = stores
    baseline = first.read_runtime_snapshot("session")
    arguments = scope(stores)
    arguments.update(operation_id="atomic", intent_key="atomic", operation_type="artifact_publish")
    def write_then_crash(conn):
        first._prepare_effect_on_conn(conn, actor=ACTOR, **arguments)
        raise RuntimeError("transaction aborted")
    with pytest.raises(RuntimeError, match="aborted"):
        first._execute_write(write_then_crash)
    assert first.list_effects("session", ACTOR) == []
    assert first.read_runtime_snapshot("session") == baseline


def test_profile_maintenance_cannot_rebind_or_abandon_runtime_delivery(stores):
    first, _, _, _ = stores
    for authority in ("legacy", "runtime.v1"):
        first._write_sql("INSERT INTO delivery_obligations(obligation_id,session_key,platform,chat_id,content,state,"
            "created_at,updated_at,adapter_profile,authority) VALUES(?,?,?,?,?,'pending',1,1,?,?)",
            (authority, "agent:old:local:dm:recipient", "local", "recipient", "", "old", authority))
    counts = first.rekey_profile_state("old", "new")
    assert counts["delivery_obligations_adapter_profile"] == 1
    assert counts["delivery_obligations_session_key"] == 1
    first.purge_profile_state("old")
    first.purge_profile_state("new")
    with first._read_ctx() as conn:
        rows = {row["authority"]: dict(row) for row in conn.execute("SELECT * FROM delivery_obligations")}
    assert rows["legacy"]["state"] == "abandoned"
    assert rows["runtime.v1"]["state"] == "pending"
    assert rows["runtime.v1"]["adapter_profile"] == "old"
    assert rows["runtime.v1"]["session_key"] == "agent:old:local:dm:recipient"


def test_snapshot_reads_authoritative_effect_approval_and_artifact_refs(stores):
    from tui_gateway.contracts.runtime_v1 import MissionSnapshot
    from tui_gateway.methods_runtime import _runtime_snapshot_projection
    first, _, run_id, fence = stores
    effect = prepare(stores)
    pending = first.request_effect_approval(actor=ACTOR, **scope(stores), expires_at=time.time() + 300)
    first.dispatch_effect(effect["effect_id"], ACTOR, **fence)
    first.append_runtime_event("session", "tool.started", {}, **fence, operation_id=effect["operation_id"])
    first.append_runtime_event("session", "tool.completed", {"result": {"text": "private provider response"}},
        **fence, operation_id=effect["operation_id"])
    first.record_effect_outcome(effect["effect_id"], ACTOR, **fence, state="outcome_unknown",
        evidence={"kind": "lost_receipt", "reference": "private-provider-ref"})
    first._write_sql("INSERT INTO runtime_artifact_versions(artifact_id,version,session_id,command_id,run_id,"
        "principal_id,profile_id,agent_id,descriptor_json,created_at) VALUES('artifact',1,'session','result',?,?,?,?,?,1)",
        (run_id, ACTOR["principal_id"], ACTOR["profile_id"], ACTOR["agent_id"], '{"locator":"private-file-path"}'))
    snapshot = first.read_runtime_snapshot("session")
    assert snapshot["unresolved_effects"] == [{"effect_id": effect["effect_id"], "status": "outcome_unknown"}]
    assert snapshot["unresolved_invocations"] == []
    assert snapshot["outstanding_requests"] == [{"request_id": pending["approval_id"], "kind": "approval", "status": "pending"}]
    assert snapshot["artifacts"] == [{"artifact_id": "artifact", "version": "1"}]
    public = MissionSnapshot.model_validate(_runtime_snapshot_projection(snapshot)).model_dump()
    assert "private-" not in json.dumps(public) and "private provider" not in json.dumps(public)
    first.resolve_effect_approval(pending["approval_id"], ACTOR, **fence,
        approval_digest=pending["approval_digest"], choice="deny")
    first.record_effect_outcome(effect["effect_id"], ACTOR, **fence, state="confirmed", evidence={"kind": "observed"})
    settled = first.read_runtime_snapshot("session")
    assert settled["unresolved_effects"] == [] and settled["outstanding_requests"] == []


def test_snapshot_expired_or_replaced_approval_is_not_an_actionable_request(stores):
    first, second, _, fence = stores
    pending = first.request_effect_approval(actor=ACTOR, **scope(stores), expires_at=time.time() + 300)
    first._write_sql("UPDATE runtime_effect_approvals SET expires_at=0 WHERE approval_id=?", (pending["approval_id"],))
    assert first.read_runtime_snapshot("session")["outstanding_requests"] == []
    next_pending = first.request_effect_approval(actor=ACTOR, **scope(stores), expires_at=time.time() + 300)
    first.release_session_turn_lease("session", fence["holder"], generation=fence["generation"])
    assert second.try_acquire_session_turn_lease("session", "successor")
    assert second.read_runtime_snapshot("session")["outstanding_requests"] == []
    assert first.get_effect_approval(next_pending["approval_id"], ACTOR)["status"] == "pending"


def test_snapshot_bound_is_explicit_and_checkpoint_cannot_clear_effect_truth(stores):
    first, _, _, fence = stores
    for index in range(101):
        prepare(stores, intent_key=f"intent-{index}", operation_id=f"operation-{index}")
    snapshot = first.read_runtime_snapshot("session")
    assert len(snapshot["unresolved_effects"]) == snapshot["reference_limit"] == 100
    assert snapshot["reference_counts"]["unresolved_effects"] == 101 and snapshot["references_truncated"]
    first.publish_runtime_checkpoint("session", {"schema_version": 1, "config_version": "c", "policy_version": "p",
        "runtime_version": "r", "prompt_projection_version": "1", "unresolved_effects": [],
        "unresolved_invocations": [], "artifacts": [{"artifact_id": "invented", "version": "1"}],
        "outstanding_requests": [{"request_id": "invented", "kind": "approval", "status": "pending"}]},
        **fence, expected_revision=snapshot["revision"], included_seq=snapshot["revision"])
    after = first.read_runtime_snapshot("session")
    assert after["unresolved_effects"] == snapshot["unresolved_effects"]
    assert after["artifacts"] == [] and after["outstanding_requests"] == []
    assert after["reference_counts"]["unresolved_effects"] == 101


def test_snapshot_reference_overlay_uses_the_same_sqlite_read_transaction(stores, monkeypatch):
    first, second, _, fence = stores
    effect = prepare(stores)
    expected = first.read_runtime_snapshot("session")
    overlay = first._runtime_references_on_conn
    def complete_between_reads(conn, session_id, state, projection):
        second.dispatch_effect(effect["effect_id"], ACTOR, **fence)
        second.record_effect_outcome(effect["effect_id"], ACTOR, **fence, state="confirmed", receipt={"receipt_id": "receipt"})
        return overlay(conn, session_id, state, projection)
    monkeypatch.setattr(first, "_runtime_references_on_conn", complete_between_reads)
    assert first.read_runtime_snapshot("session") == expected
    monkeypatch.setattr(first, "_runtime_references_on_conn", overlay)
    assert first.read_runtime_snapshot("session")["unresolved_effects"] == []
