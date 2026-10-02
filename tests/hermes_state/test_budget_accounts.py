"""BE03 budget contracts against real independent SQLite writers and restart."""
from concurrent.futures import ThreadPoolExecutor
import threading
import time
from types import SimpleNamespace

import pytest

from hermes_state import SessionDB
from hermes_state_runtime import RuntimeStoreError
import hermes_state_budgets as budgets

ACTOR = {"principal_id": "owner", "profile_id": "profile", "agent_id": "ryoko"}
LIMITS = dict(tokens=100, attempts=10, cost_micros=10_000, wall_ms=100_000,
              provider_slots=2, executor_slots=2)


@pytest.fixture
def stores(tmp_path):
    path = tmp_path / "state.db"
    first, second = SessionDB(path), SessionDB(path)
    yield first, second
    first.close()
    second.close()


def run(db, sid="session", *, actor=ACTOR, source="test", key="submit"):
    if not db.get_session(sid):
        db.create_session(sid, source=source)
    receipt = db.submit_runtime_command(sid, actor, dict(schema_version=1,command_id=key,idempotency_key=key,
        identity_binding=actor,operation="submit",payload={"text":"hello"}))
    assert db.try_acquire_session_turn_lease(sid, "owner-" + sid)
    fence = dict(holder="owner-" + sid,generation=db.get_session_turn_lease(sid)["generation"])
    return receipt["run_id"], fence


def root(db, *, limits=None, deadline=None, policy_snapshot=None):
    rid,fence = run(db)
    account = db.create_budget_account("session",ACTOR,rid,limits or LIMITS,
        deadline=deadline or time.time()+600,policy_snapshot=policy_snapshot,**fence)
    return account,fence


def child(db, parent, parent_fence, sid="child", *, actor=ACTOR, limits=None, deadline=None):
    rid,fence = run(db,sid,actor=actor,source="subagent")
    parent_actor = ACTOR
    db.bind_budget_child(parent["account_id"],parent_actor,sid,**parent_fence)
    account = db.create_budget_account(sid,actor,rid,limits or LIMITS,
        parent_id=parent["account_id"],deadline=deadline or parent["deadline"],**fence)
    return account,fence


def reject(code, fn):
    with pytest.raises(RuntimeStoreError) as exc:
        fn()
    assert exc.value.code == code


def test_two_real_writers_cannot_oversubscribe_ancestor(stores):
    first,second=stores
    ancestor,fence=root(first)
    one,one_fence=child(first,ancestor,fence,"one")
    two,two_fence=child(second,ancestor,fence,"two")
    barrier=threading.Barrier(2)
    def reserve(args):
        db,account,owner=args
        barrier.wait(timeout=10)
        try:
            db.reserve_budget(account["account_id"],ACTOR,"op-"+account["session_id"],
                              dict(tokens=70,attempts=1),**owner)
            return "accepted"
        except RuntimeStoreError as exc:
            return exc.code
    with ThreadPoolExecutor(2) as pool:
        results=list(pool.map(reserve,[(first,one,one_fence),(second,two,two_fence)]))
    assert sorted(results)==["accepted","budget_exhausted"]
    assert first.get_budget_account(ancestor["account_id"],ACTOR)["reserved"]["tokens"]==70
    assert sum(db.get_budget_account(account["account_id"],ACTOR)["reserved"]["tokens"]
               for db,account in [(first,one),(second,two)])==70


def test_child_uses_own_owner_after_parent_lease_release_and_restart(stores):
    first,second=stores
    ancestor,fence=root(first)
    actor={**ACTOR,"agent_id":"child-agent"}
    leaf,owner=child(first,ancestor,fence,actor=actor)
    first.release_session_turn_lease("session",fence["holder"],generation=fence["generation"])
    second.reserve_budget(leaf["account_id"],actor,"inference",dict(tokens=60,provider_slots=1),**owner)
    first.close()
    reopened=SessionDB(second.db_path)
    try:
        assert reopened.get_budget_account(ancestor["account_id"],ACTOR)["reserved"]["tokens"]==60
        reject("budget_exhausted",lambda: reopened.reserve_budget(leaf["account_id"],actor,"retry",dict(tokens=60),**owner))
        reject("identity_mismatch",lambda: reopened.get_budget_account(leaf["account_id"],ACTOR))
    finally:
        reopened.close()


def test_unknown_charge_holds_units_releases_slots_and_can_reconcile(stores):
    db,_=stores
    account,fence=root(db)
    aid=account["account_id"]
    maxima=dict(tokens=80,attempts=1,cost_micros=9000,provider_slots=2)
    db.reserve_budget(aid,ACTOR,"op",maxima,**fence)
    assert db.mark_budget_dispatched(aid,ACTOR,"op",**fence)["dispatch_granted"]
    assert not db.mark_budget_dispatched(aid,ACTOR,"op",**fence)["dispatch_granted"]
    assert db.get_budget_account(aid,ACTOR)["unknown_usage"]
    receipt=db.settle_budget(aid,ACTOR,"op",unknown_usage=True,**fence)
    assert receipt["reserved"]["tokens"]==80
    assert receipt["reserved"]["provider_slots"]==0
    assert receipt["actual"] is None and receipt["unknown_usage"]
    reject("budget_exhausted",lambda: db.reserve_budget(aid,ACTOR,"next",dict(tokens=21),**fence))
    db.reserve_budget(aid,ACTOR,"parallel",dict(provider_slots=2),**fence)
    final=db.settle_budget(aid,ACTOR,"op",dict(tokens=40,attempts=1,cost_micros=4000),**fence)
    assert final["settlement_state"]=="settled" and not final["unknown_usage"]
    state=db.get_budget_account(aid,ACTOR)
    assert state["consumed"]["tokens"]==40 and state["reserved"]["tokens"]==0
    assert state["consumed"]["provider_slots"]==0
    assert not state["unknown_usage"]


def test_cancel_and_expiry_never_refund_dispatched_work(stores,monkeypatch):
    db,_=stores
    parent,fence=root(db)
    leaf,owner=child(db,parent,fence)
    aid=leaf["account_id"]
    db.reserve_budget(aid,ACTOR,"dispatched",dict(tokens=50,executor_slots=1),**owner)
    db.mark_budget_dispatched(aid,ACTOR,"dispatched",**owner)
    db.reserve_budget(aid,ACTOR,"undispatched",dict(tokens=10),**owner)
    db.close_budget_account(parent["account_id"],ACTOR,state="cancelled",**fence)
    reject("budget_closed",lambda: db.reserve_budget(aid,ACTOR,"next",dict(tokens=1),**owner))
    reject("budget_closed",lambda: db.mark_budget_dispatched(aid,ACTOR,"undispatched",**owner))
    reject("budget_dispatch_uncertain",lambda: db.release_budget_reservation(aid,ACTOR,"dispatched",**owner))
    db.release_budget_reservation(aid,ACTOR,"undispatched",**owner)
    db.settle_budget(aid,ACTOR,"dispatched",unknown_usage=True,slots_released=False,**owner)
    state=db.get_budget_account(parent["account_id"],ACTOR)
    assert state["reserved"]["tokens"]==50 and state["reserved"]["executor_slots"]==1
    db.settle_budget(aid,ACTOR,"dispatched",unknown_usage=True,slots_released=True,**owner)
    assert db.get_budget_account(parent["account_id"],ACTOR)["reserved"]["executor_slots"]==0
    monkeypatch.setattr(budgets,"time",SimpleNamespace(time=lambda:parent["deadline"]+1))
    db.settle_budget(aid,ACTOR,"dispatched",dict(tokens=20),**owner)
    assert db.get_budget_account(parent["account_id"],ACTOR)["consumed"]["tokens"]==20


def test_idempotent_restore_and_reservations_reject_changed_maxima(stores):
    db,_=stores
    account,fence=root(db,policy_snapshot={"rate_micros":12})
    aid=account["account_id"]
    first=db.reserve_budget(aid,ACTOR,"op",dict(tokens=40),**fence)
    assert db.reserve_budget(aid,ACTOR,"op",dict(tokens=40),**fence)==first
    reject("idempotency_conflict",lambda: db.reserve_budget(aid,ACTOR,"op",dict(tokens=41),**fence))
    original=db.create_budget_account("session",ACTOR,aid,LIMITS,deadline=account["deadline"],
                                      policy_snapshot={"rate_micros":12},**fence)
    assert original["reserved"]["tokens"]==40
    reject("idempotency_conflict",lambda: db.create_budget_account("session",ACTOR,aid,LIMITS,
        deadline=account["deadline"],policy_snapshot={"rate_micros":1},**fence))
    reject("idempotency_conflict",lambda: db.create_budget_account("session",ACTOR,aid,{**LIMITS,"tokens":101},
        deadline=account["deadline"],policy_snapshot={"rate_micros":12},**fence))
    db.mark_budget_dispatched(aid,ACTOR,"op",**fence)
    settled=db.settle_budget(aid,ACTOR,"op",dict(tokens=30),**fence)
    assert db.settle_budget(aid,ACTOR,"op",dict(tokens=30),**fence)==settled
    reject("idempotency_conflict",lambda: db.settle_budget(aid,ACTOR,"op",dict(tokens=20),**fence))


def test_deadline_is_checked_at_reserve_and_again_at_dispatch(stores,monkeypatch):
    db,_=stores
    account,fence=root(db)
    aid=account["account_id"]
    deadline=time.time()+20
    db.reserve_budget(aid,ACTOR,"op",dict(tokens=10),deadline=deadline,**fence)
    monkeypatch.setattr(budgets,"time",SimpleNamespace(time=lambda:deadline+1))
    reject("budget_expired",lambda: db.mark_budget_dispatched(aid,ACTOR,"op",**fence))
    assert db.release_budget_reservation(aid,ACTOR,"op",**fence)["settlement_state"]=="released"
    monkeypatch.setattr(budgets,"time",SimpleNamespace(time=lambda:account["deadline"]+1))
    reject("budget_expired",lambda: db.reserve_budget(aid,ACTOR,"later",dict(tokens=1),**fence))
    assert db.get_budget_account(aid,ACTOR)["expired"]


def test_child_cannot_enlarge_limits_or_manufacture_a_new_root(stores):
    db,_=stores
    parent,fence=root(db)
    rid,owner=run(db,"child",source="subagent")
    reject("budget_parent_required",lambda: db.create_budget_account("child",ACTOR,rid,LIMITS,
        deadline=parent["deadline"],**owner))
    reject("budget_parent_required",lambda: db.create_budget_account("child",ACTOR,rid,LIMITS,
        parent_id=parent["account_id"],deadline=parent["deadline"],**owner))
    db.bind_budget_child(parent["account_id"],ACTOR,"child",**fence)
    reject("budget_limit_enlarged",lambda: db.create_budget_account("child",ACTOR,rid,{**LIMITS,"tokens":101},
        parent_id=parent["account_id"],deadline=parent["deadline"],**owner))
    reject("budget_limit_enlarged",lambda: db.create_budget_account("child",ACTOR,rid,LIMITS,
        parent_id=parent["account_id"],deadline=parent["deadline"]+1,**owner))
    leaf=db.create_budget_account("child",ACTOR,rid,LIMITS,parent_id=parent["account_id"],deadline=parent["deadline"],**owner)
    retry,_=run(db,"child",source="subagent",key="retry")
    reject("budget_parent_required",lambda: db.create_budget_account("child",ACTOR,retry,LIMITS,
        deadline=parent["deadline"],**owner))
    assert leaf["root_id"]==parent["account_id"] and leaf["run_id"]!=parent["run_id"]


def test_fences_actor_binding_and_real_run_identity_are_mandatory(stores):
    db,other=stores
    account,fence=root(db)
    aid=account["account_id"]
    reject("budget_run_not_found",lambda: db.create_budget_account("session",ACTOR,"invented-run",LIMITS,
        deadline=account["deadline"],**fence))
    reject("invalid_budget",lambda: db.create_budget_account("session",ACTOR,aid,LIMITS,
        account_id="invented-account",deadline=account["deadline"],**fence))
    reject("identity_mismatch",lambda: db.reserve_budget(aid,{**ACTOR,"principal_id":"other"},"op",{},**fence))
    db.release_session_turn_lease("session",fence["holder"],generation=fence["generation"])
    _,next_fence=run(other)
    assert next_fence["generation"]>fence["generation"]
    reject("stale_owner",lambda: db.reserve_budget(aid,ACTOR,"op",dict(tokens=1),**fence))
    assert other.reserve_budget(aid,ACTOR,"op",dict(tokens=1),**next_fence)["reserved"]["tokens"]==1


def test_measured_overrun_retains_debt_and_blocks_new_work(stores):
    db,_=stores
    account,fence=root(db)
    aid=account["account_id"]
    db.reserve_budget(aid,ACTOR,"op",dict(tokens=10,provider_slots=1),**fence)
    db.mark_budget_dispatched(aid,ACTOR,"op",**fence)
    final=db.settle_budget(aid,ACTOR,"op",dict(tokens=120),**fence)
    assert final["debt"] and final["actual"]["tokens"]==120
    state=db.get_budget_account(aid,ACTOR)
    assert state["debt"] and state["consumed"]["tokens"]==120
    assert state["reserved"]["provider_slots"]==0
    reject("budget_debt",lambda: db.reserve_budget(aid,ACTOR,"next",{},**fence))


def test_storage_payload_integer_and_depth_bounds_fail_closed(stores,monkeypatch):
    db,_=stores
    account,fence=root(db)
    aid=account["account_id"]
    for invalid in (True,1.5,-1,1<<64):
        reject("invalid_budget",lambda: db.reserve_budget(aid,ACTOR,"bad",dict(tokens=invalid),**fence))
    for invalid in (True,0,float("inf"),float("nan"),10**1000):
        reject("invalid_budget",lambda: db.reserve_budget(aid,ACTOR,"bad-deadline",{},deadline=invalid,**fence))
    monkeypatch.setattr(budgets,"MAX_BUDGET_RESERVATIONS",1)
    db.reserve_budget(aid,ACTOR,"one",{},**fence)
    reject("budget_storage_limit",lambda: db.reserve_budget(aid,ACTOR,"two",{},**fence))
    assert db.reserve_budget(aid,ACTOR,"one",{},**fence)["operation_id"]=="one"
    rid,owner=run(db,"oversize")
    reject("budget_payload_limit",lambda: db.create_budget_account("oversize",ACTOR,rid,LIMITS,
        deadline=account["deadline"],policy_snapshot={"large":"x"*20_000},**owner))
    monkeypatch.setattr(budgets,"MAX_BUDGET_DEPTH",0)
    reject("budget_depth_limit",lambda: child(db,account,fence))


def test_same_session_continuation_inherits_root_and_cannot_reset(stores):
    db,_=stores
    account,fence=root(db)
    aid=account["account_id"]
    db.reserve_budget(aid,ACTOR,"spent",dict(tokens=80),**fence)
    binding=db.bind_budget_child(aid,ACTOR,"session",**fence)
    stored=db.get_budget_child_binding("session",ACTOR)
    assert all(stored[key]==value for key,value in binding.items())
    assert stored["parent_account"]["account_id"]==aid
    rid,_=run(db,key="continue")
    reject("budget_parent_required",lambda: db.create_budget_account("session",ACTOR,rid,LIMITS,
        deadline=account["deadline"],**fence))
    continuation=db.create_budget_account("session",ACTOR,rid,LIMITS,parent_id=aid,deadline=account["deadline"],**fence)
    reject("budget_exhausted",lambda: db.reserve_budget(continuation["account_id"],ACTOR,"new",dict(tokens=30),**fence))
    assert db.get_budget_account(aid,ACTOR)["reserved"]["tokens"]==80


def test_token_only_policy_discloses_untracked_cost(stores):
    db,_=stores
    account,fence=root(db,limits={**LIMITS,"cost_micros":None})
    assert account["cost_tracking"]=="untracked"
    receipt=db.reserve_budget(account["account_id"],ACTOR,"tokens",dict(tokens=10),**fence)
    assert receipt["cost_tracking"]=="untracked"


def test_cost_micros_remain_exact_above_float_precision(stores):
    db,_=stores
    ceiling=(1<<53)+7
    account,fence=root(db,limits={**LIMITS,"cost_micros":ceiling})
    aid=account["account_id"]
    db.reserve_budget(aid,ACTOR,"exact",dict(cost_micros=ceiling,attempts=1),**fence)
    reject("budget_exhausted",lambda: db.reserve_budget(aid,ACTOR,"one-more",dict(cost_micros=1),**fence))
    db.mark_budget_dispatched(aid,ACTOR,"exact",**fence)
    db.settle_budget(aid,ACTOR,"exact",dict(cost_micros=ceiling-1,attempts=1),**fence)
    assert db.get_budget_account(aid,ACTOR)["consumed"]["cost_micros"]==ceiling-1
    assert db.reserve_budget(aid,ACTOR,"last",dict(cost_micros=1),**fence)["reserved"]["cost_micros"]==1


def test_crash_after_dispatch_keeps_uncertainty_and_audit_survives_transcript_delete(stores):
    db,other=stores
    account,fence=root(db)
    aid=account["account_id"]
    db.reserve_budget(aid,ACTOR,"crashed",dict(tokens=90,provider_slots=2),**fence)
    db.mark_budget_dispatched(aid,ACTOR,"crashed",**fence)
    db.close()
    reopened=SessionDB(other.db_path)
    try:
        receipt=reopened.get_budget_reservation(aid,ACTOR,"crashed")
        assert receipt["unknown_usage"] and receipt["dispatched"]
        assert receipt["reserved"]["tokens"]==90 and receipt["reserved"]["provider_slots"]==2
        assert not reopened.mark_budget_dispatched(aid,ACTOR,"crashed",**fence)["dispatch_granted"]
        assert reopened.delete_session("session")
        assert reopened.get_budget_reservation(aid,ACTOR,"crashed")==receipt
        assert reopened.get_budget_account(aid,ACTOR)["unknown_usage"]
    finally:
        reopened.close()


def test_additive_migration_keeps_runtime_receipts_and_supports_budgets(tmp_path):
    import sqlite3
    from hermes_state_schema import schema_read_probe_statements

    path=tmp_path/"legacy.db"
    old=SessionDB(path)
    rid,fence=run(old)
    old.close()
    with sqlite3.connect(path) as conn:
        for table in ("budget_reservations","budget_child_bindings","budget_accounts",
                      "runtime_admission_queue","runtime_admission_control"):
            conn.execute(f"DROP TABLE {table}")
        conn.execute("UPDATE schema_version SET version=32")
    migrated=SessionDB(path)
    try:
        with migrated._read_ctx() as conn:
            for statement in schema_read_probe_statements():
                conn.execute(statement).fetchone()
        account=migrated.create_budget_account("session",ACTOR,rid,LIMITS,deadline=time.time()+60,**fence)
        assert account["run_id"]==rid
        assert migrated.submit_runtime_command("session",ACTOR,dict(schema_version=1,command_id="submit",
            idempotency_key="submit",identity_binding=ACTOR,operation="submit",payload={"text":"hello"}))["run_id"]==rid
    finally:
        migrated.close()
