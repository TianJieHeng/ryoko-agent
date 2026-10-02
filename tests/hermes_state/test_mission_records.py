"""BE09 real SQLite authority, CAS, selective invalidation and atomic mission finalization."""
from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
import threading
import time
from types import SimpleNamespace

import pytest

from agent.mission_contract import MissionContract, MissionContractError, VerificationReceipt, criterion_digest
from hermes_state import SessionDB
from hermes_state_effects import effect_digest
from hermes_state_runtime import RuntimeStoreError

ACTOR = {"principal_id": "owner", "profile_id": "profile", "agent_id": "ryoko"}


@pytest.fixture
def stores(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    db.create_session("session", source="test")
    receipt = db.submit_runtime_command("session", ACTOR, dict(schema_version=1, command_id="submit", idempotency_key="submit",
        identity_binding=ACTOR, operation="submit", payload={"text": "work"}))
    assert db.try_acquire_session_turn_lease("session", "worker")
    fence = dict(holder="worker", generation=db.get_session_turn_lease("session")["generation"])
    assert db.claim_runtime_command("session", "submit", **fence)
    second = SessionDB(db.db_path)
    yield SimpleNamespace(db=db, second=second, run=receipt["run_id"], fence=fence)
    second.close()
    db.close()


def contract(**changes):
    return {"outcome": "Produce complete outputs", "risk": "low", "uncertainty": "low", "acceptance": [{"criterion_id": "review", "kind": "user_acceptance"}], **changes}


def create(f, **changes):
    return f.db.create_mission("session", ACTOR, **f.fence, contract=contract(**changes))


def update(f, row, **changes):
    return f.db.update_mission("session", ACTOR, **f.fence, expected_revision=row["revision"], changes=changes)


def reject(code, action):
    with pytest.raises(RuntimeStoreError) as error:
        action()
    assert error.value.code == code


def decision(continuing=False):
    return {"status": "active" if continuing else "paused", "should_continue": continuing, "continuation_prompt": "next" if continuing else None,
        "verdict": "continue" if continuing else "blocked", "reason": "Need source", "message": "Source missing"}


def finalize(f, row, **changes):
    return f.db.finalize_mission_turn("session", ACTOR, **f.fence, expected_revision=row["revision"], run_id=f.run,
        **{"receipts": [], "evidence": {"no_progress": True}, "decision": decision(), "state": "waiting_for_source", **changes})


def approval(f, name, target, *, approved=False, consumed=False):
    binding = dict(session_id="session", run_id=f.run, **f.fence, action_digest=effect_digest(name), input_digest=effect_digest(name),
        target_ref=target, policy_digest=effect_digest("policy"), policy_version="1", input_revision=name, artifact_revision="1")
    row = f.db.request_effect_approval(actor=ACTOR, **binding, expires_at=time.time() + 100)
    if approved or consumed:
        row = f.db.resolve_effect_approval(row["approval_id"], ACTOR, **f.fence, approval_digest=row["approval_digest"], choice="once")
    if consumed:
        f.db.consume_effect_approval(row["approval_id"], ACTOR, consumer_id=name, **binding)
    return row


def test_root_owner_and_concurrent_revision_cas(stores):
    f = stores
    row = create(f)
    barrier = threading.Barrier(2)
    def revise(db):
        barrier.wait(timeout=10)
        try:
            return db.update_mission("session", ACTOR, **f.fence, expected_revision=1, changes={"next_step": "Write source"})["revision"]
        except RuntimeStoreError as exc:
            return exc.code
    with ThreadPoolExecutor(2) as pool:
        result = list(pool.map(revise, [f.db, f.second]))
    assert sorted(map(str, result)) == ["2", "revision_conflict"]
    reject("identity_mismatch", lambda: f.db.get_mission("session", {**ACTOR, "agent_id": "other"}))
    assert row["budget_ref"] is None
    f.db.release_session_turn_lease("session", "worker", generation=f.fence["generation"])
    assert f.second.try_acquire_session_turn_lease("session", "successor")
    reject("stale_owner", lambda: update(f, f.db.get_mission("session", ACTOR), state="paused"))


def test_explicit_binding_required_even_for_legacy_migration(stores):
    f = stores
    f.db.create_session("unowned", source="test")
    f.db.set_meta("goal:unowned", json.dumps({"goal": "old", "status": "done"}))
    assert f.db.try_acquire_session_turn_lease("unowned", "worker")
    generation = f.db.get_session_turn_lease("unowned")["generation"]
    reject("identity_mismatch", lambda: f.db.migrate_legacy_mission("unowned", ACTOR, holder="worker", generation=generation))
    assert f.db.get_meta("goal:unowned") is not None


def test_schema37_reopen_and_owner_aware_legacy_goal_retention(stores):
    f = stores
    raw = json.dumps({"goal": "old result", "status": "done", "turns_used": 7, "max_turns": 10, "contract": {"verification": "check it"}})
    f.db.set_meta("goal:session", raw)
    f.db._write_sql("UPDATE schema_version SET version=37")
    row = f.db.migrate_legacy_mission("session", ACTOR, **f.fence)
    assert row["state"] == "paused" and row["turns_used"] == 7 and row["legacy_imported"]
    assert row["acceptance_status"] == "not_requested" and f.db.get_meta("goal:session") == raw
    f.db.set_meta("goal:session", json.dumps({"goal": "stale second authority"}))
    assert f.second.migrate_legacy_mission("session", ACTOR, **f.fence)["outcome"] == "old result"
    reopened = SessionDB(f.db.db_path)
    with reopened._read_ctx() as conn:
        assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 38
    reopened.close()


def test_budget_root_limits_survive_set_clear_and_resume(stores):
    f = stores
    from hermes_state_budgets import BUDGET_RESOURCES
    limits = {key: 10 for key in BUDGET_RESOURCES}
    account = f.db.create_budget_account("session", ACTOR, f.run, limits, deadline=time.time() + 300, **f.fence)
    f.db.reserve_budget(account["account_id"], ACTOR, "prior", {"tokens": 3}, **f.fence)
    f.db.mark_budget_dispatched(account["account_id"], ACTOR, "prior", **f.fence)
    f.db.settle_budget(account["account_id"], ACTOR, "prior", actual={"tokens": 2}, **f.fence)
    row = create(f, max_turns=3)
    assert row["budget_ref"] == account["root_id"] and row["deadline"] == account["deadline"]
    row = update(f, row, state="cancelled")
    row = update(f, row, outcome="revised", state="ready")
    assert row["budget_ref"] == account["root_id"] and row["max_turns"] == 3
    reject("budget_limit_enlarged", lambda: update(f, row, max_turns=4))
    reject("budget_limit_enlarged", lambda: update(f, row, deadline=row["deadline"] + 1))
    reject("invalid_mission", lambda: update(f, row, turns_used=0))
    reject("invalid_mission", lambda: update(f, row, budget_ref="new"))
    assert f.db.get_budget_account(account["account_id"], ACTOR)["consumed"]["tokens"] == 2


def test_selective_approval_invalidation_retains_consumed_history(stores):
    f = stores
    affected = approval(f, "affected", "target:A")
    approved = approval(f, "approved", "target:B", approved=True)
    unaffected = approval(f, "unaffected", "target:C")
    consumed = approval(f, "consumed", "target:A", consumed=True)
    row = create(f, plan_steps=[{"step_id": "publish", "approval_ids": [approved["approval_id"]]}])
    revised = f.db.update_mission("session", ACTOR, **f.fence, expected_revision=row["revision"], changes={"plan_steps": []},
        changed_inputs=[effect_digest("affected")], changed_plan_steps=["publish"])
    for ref in (affected, approved):
        result = f.db.get_effect_approval(ref["approval_id"], ACTOR)
        assert result["status"] == "invalidated" and result["mission_revision"] == revised["revision"]
        assert result["invalidation_reason"] in {"input_changed", "plan_step_changed"}
    assert f.db.get_effect_approval(unaffected["approval_id"], ACTOR)["status"] == "pending"
    assert f.db.get_effect_approval(consumed["approval_id"], ACTOR)["status"] == "consumed"


def test_finalization_is_atomic_idempotent_and_consumed_once(stores, monkeypatch):
    f = stores
    row = create(f)
    original = f.db._mission_save_on_conn
    def fail(conn, record, generation):
        original(conn, record, generation)
        raise RuntimeError("crash after mission save")
    monkeypatch.setattr(f.db, "_mission_save_on_conn", fail)
    with pytest.raises(RuntimeError, match="crash"):
        finalize(f, row)
    assert f.second.get_mission("session", ACTOR)["turns_used"] == 0
    with f.db._read_ctx() as conn:
        assert conn.execute("SELECT COUNT(*) FROM runtime_mission_turns").fetchone()[0] == 0
    monkeypatch.setattr(f.db, "_mission_save_on_conn", original)
    finished = finalize(f, row)
    assert finalize(f, row) == finished and finished["consecutive_no_progress"] == 1
    assert f.second.consume_mission_decision("session", ACTOR) is None  # Result has not committed yet.
    f.db.finish_runtime_command("session", "submit", **f.fence, status="blocked", result={"reason": "source"})
    assert f.second.consume_mission_decision("session", ACTOR) == decision()
    assert f.db.consume_mission_decision("session", ACTOR) is None
    reject("idempotency_conflict", lambda: finalize(f, row, evidence={"no_progress": False}))


def test_receipts_cannot_complete_changed_criteria_or_model_judgment(stores):
    f = stores
    row = create(f)
    reject("mission_verification_required", lambda: finalize(f, row, state="completed"))
    receipt = {"criterion_id": "review", "criterion_digest": criterion_digest(row["acceptance"][0]), "artifact_refs": [],
        "verifier": "model_judge", "evidence_ref": "text:done", "result": "pass", "observed_at": time.time()}
    stored = f.db.append_verification_receipt("session", ACTOR, **f.fence, expected_revision=row["revision"], receipt=receipt)
    assert stored["result"] == "pass"  # Historical observation, never deterministic completion.
    revised = update(f, row, acceptance=[{"criterion_id": "review", "kind": "user_acceptance", "description": "new intent"}])
    reject("mission_verification_stale", lambda: f.db.append_verification_receipt("session", ACTOR, **f.fence,
        expected_revision=revised["revision"], receipt=receipt))
    assert f.db.list_verification_receipts("session", ACTOR)[0] == stored


def test_exhausted_mission_does_not_resume_or_consume_stale_continuation(stores):
    f = stores
    row = create(f, max_turns=1)
    reject("mission_continuation_denied", lambda: finalize(f, row, state="working", decision=decision(True)))
    paused = finalize(f, row)
    reject("mission_continuation_denied", lambda: update(f, paused, state="ready"))
    revised = update(f, paused, next_step="Review source")
    assert revised["turns_used"] == 1 and f.db.consume_mission_decision("session", ACTOR) is None


def test_contract_bounds_and_untyped_authority_are_rejected():
    for value in (contract(max_turns=101), contract(no_progress_limit=6), contract(deliverables=[{}]), contract(unrecognized="data")):
        with pytest.raises(MissionContractError):
            MissionContract.from_dict(value)
    with pytest.raises(MissionContractError):
        VerificationReceipt.from_dict({"result": "pass"})


def test_migration_schema_does_not_rewrite_prior_authorities(stores):
    f = stores
    with sqlite3.connect(f.db.db_path) as conn:
        command = conn.execute("SELECT command_json FROM runtime_commands").fetchone()[0]
        conn.execute("UPDATE schema_version SET version=37")
    reopened = SessionDB(f.db.db_path)
    with reopened._read_ctx() as conn:
        assert conn.execute("SELECT command_json FROM runtime_commands").fetchone()[0] == command
        assert conn.execute("SELECT COUNT(*) FROM runtime_missions").fetchone()[0] == 0
    reopened.close()


def test_risk_requires_explicit_direct_declaration_and_acyclic_checkpoint_plan(stores):
    f = stores
    with pytest.raises(MissionContractError, match="low risk"):
        MissionContract.from_dict({"outcome": "Publish", "policy": "direct"})
    for steps in ([{"step_id": "a", "depends_on": ["b"]}],
                  [{"step_id": "a", "depends_on": ["b"]}, {"step_id": "b", "depends_on": ["a"]}],
                  [{"step_id": "same"}, {"step_id": "same"}]):
        with pytest.raises(MissionContractError):
            MissionContract.from_dict(contract(plan_steps=steps))
    row = create(f, risk="consequential", uncertainty="high")
    assert row["state"] == "waiting_for_user"
    reject("mission_plan_required", lambda: update(f, row, state="ready"))
    ready = update(f, row, state="ready", plan_steps=[{"step_id": "review", "checkpoint": True}])
    assert ready["state"] == "ready" and ready["policy"] == "reviewed"


def test_plan_diff_invalidates_without_client_hint_and_preserves_unrelated(stores):
    f = stores
    pending = approval(f, "planned", "target:A")
    unaffected = approval(f, "other", "target:B")
    row = create(f, plan_steps=[{"step_id": "publish", "description": "Original", "approval_ids": [pending["approval_id"]]}])
    update(f, row, plan_steps=[{"step_id": "publish", "description": "Changed", "approval_ids": [pending["approval_id"]]}])
    assert f.db.get_effect_approval(pending["approval_id"], ACTOR)["status"] == "invalidated"
    assert f.db.get_effect_approval(unaffected["approval_id"], ACTOR)["status"] == "pending"


def test_control_acceptance_and_stranded_control_settlement_cannot_replay(stores):
    f = stores
    row = create(f)
    control = dict(schema_version=1, command_id="steer", idempotency_key="steer", operation="steer", identity_binding=ACTOR, payload={"text": "new scope"})
    f.db.submit_runtime_command("session", ACTOR, control)
    finished = finalize(f, row)
    f.db.finish_runtime_command("session", "submit", **f.fence, status="blocked", result={})
    reject("no_active_run", lambda: f.db.submit_runtime_command("session", ACTOR, {**control, "command_id": "new", "idempotency_key": "new"}))
    settled = f.db.settle_inactive_runtime_control("session", ACTOR, "steer")
    assert settled["status"] == "blocked" and settled["result"]["applied"] is False
    corrected = f.db.get_mission("session", ACTOR)
    assert corrected["revision"] > finished["revision"] and corrected["missed_steer"][-1]["run_id"] == f.run
    assert not f.db.consume_mission_decision("session", ACTOR)["should_continue"]
    assert f.db.settle_inactive_runtime_control("session", ACTOR, "steer") == settled


def test_approval_invalidation_failure_rolls_back_revision_and_history(stores, monkeypatch):
    f = stores
    pending = approval(f, "planned", "target:A", approved=True)
    row = create(f, plan_steps=[{"step_id": "publish", "approval_ids": [pending["approval_id"]]}])
    original = f.db._mission_save_on_conn
    def fail(conn, record, generation):
        original(conn, record, generation)
        raise RuntimeError("fail final state write")
    monkeypatch.setattr(f.db, "_mission_save_on_conn", fail)
    with pytest.raises(RuntimeError):
        update(f, row, plan_steps=[])
    assert f.second.get_mission("session", ACTOR)["revision"] == row["revision"]
    assert f.second.get_effect_approval(pending["approval_id"], ACTOR)["status"] == "approved"
    with f.db._read_ctx() as conn:
        assert conn.execute("SELECT COUNT(*) FROM runtime_mission_approval_invalidations").fetchone()[0] == 0


def test_cancel_accepted_during_verification_wins_finalization_state(stores):
    f = stores
    row = create(f)
    f.db.submit_runtime_command("session", ACTOR, dict(schema_version=1, command_id="cancel", idempotency_key="cancel",
        operation="cancel", identity_binding=ACTOR, payload={"reason": "stop"}))
    result = finalize(f, row, evidence={"pending_steer_count": 1}, state="working", decision=decision(True))
    assert result["state"] == "cancelled" and result["execution_status"] == "cancelled"
    assert not result["goal_decision"]["should_continue"] and result["missed_steer"]


@pytest.mark.parametrize("oversized", ["criteria", "artifacts", "dependencies"])
def test_contract_rejects_verifier_and_retention_overflow_before_admission(stores, oversized):
    f = stores
    proposal = contract()
    if oversized == "criteria":
        proposal["acceptance"] = [{"criterion_id": str(index), "kind": "existence"} for index in range(33)]
    elif oversized == "artifacts":
        refs = [{"artifact_id": "output-" + str(index), "version": 1, "digest": "a" * 64} for index in range(101)]
        proposal["acceptance"] = [{"criterion_id": str(index), "kind": "existence", "artifact_refs": refs[index:index + 16]}
                                  for index in range(0, 101, 16)]
    else:
        proposal["dependencies"] = [{"dependency_id": str(index), "kind": "input", "reference": str(index)} for index in range(65)]
    reject("mission_capacity", lambda: f.db.create_mission("session", ACTOR, **f.fence, contract=proposal))
    assert f.db.get_mission("session", ACTOR) is None
    with f.db._read_ctx() as conn:
        assert conn.execute("SELECT COUNT(*) FROM runtime_mission_verifications").fetchone()[0] == 0
