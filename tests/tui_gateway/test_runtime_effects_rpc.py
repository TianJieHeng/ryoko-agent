"""Owned effect/approval RPCs use the real dispatcher, profile scopes and SQLite."""
import json
import time
from dataclasses import replace

import pytest

from agent.result_artifacts import artifact_actor, descriptor_digest, result_artifact_descriptor
from agent.runtime_commands import RuntimeRun
from tests.tui_gateway.test_runtime_rpc import runtime  # noqa: F401


def _run(runtime, label="a", *, session_id=None):
    agent = runtime.agents[label]
    db, context = agent._session_db, agent.runtime_context
    sid = session_id or agent.session_id
    actor = artifact_actor(context)
    if session_id is not None:
        db.create_session(sid, source="tui")
    receipt = db.submit_runtime_command(sid, actor, {"schema_version": 1,
        "command_id": "approval-command", "idempotency_key": "approval-command", "expected_revision": None,
        "operation": "submit", "payload": {"text": "private fixture prompt"}, "identity_binding": actor})
    holder = "fixture-rpc-owner"
    assert db.try_acquire_session_turn_lease(sid, holder)
    generation = db.get_session_turn_lease(sid)["generation"]
    assert db.claim_runtime_command(sid, "approval-command", holder=holder, generation=generation)
    run = RuntimeRun(agent, db, sid, "approval-command", receipt["run_id"], holder, generation, context)
    if session_id is None:
        agent._active_runtime_run = run
    return run


def _binding(run):
    return {"session_id": run.session_id, "run_id": run.run_id, "holder": run.holder,
            "generation": run.generation, "action_digest": descriptor_digest({"action": "fixture"}),
            "input_digest": descriptor_digest({"input": "private fixture"}), "target_ref": "/private/target",
            "policy_version": str(run.context.policy.policy_version), "policy_digest": run.context.policy.digest,
            "input_revision": "/private/input/revision", "artifact_revision": "/private/artifact/revision"}


def _approval(run):
    return run.db.request_effect_approval(actor=artifact_actor(run.context), expires_at=time.time() + 300, **_binding(run))


def _effect(run, *, operation_id="effect-one", operation_type="artifact_publish", publish=False):
    payload = b"private durable result"
    descriptor = result_artifact_descriptor(run.context, run.run_id, payload, operation_id)
    binding = {**_binding(run), "input_digest": descriptor_digest(descriptor),
               "target_ref": f"artifact:{descriptor['artifact_id']}:{descriptor['version']}"}
    row = run.db.prepare_effect(actor=artifact_actor(run.context), operation_id=operation_id,
        intent_key=operation_id, operation_type=operation_type, input_ref=descriptor, **binding)
    row = run.db.dispatch_effect(row["effect_id"], artifact_actor(run.context), holder=run.holder, generation=run.generation)
    if publish:
        from agent.identity_lifecycle import agent_runtime_scope
        from agent.result_artifacts import _publish_bytes
        with agent_runtime_scope(run.context):
            _publish_bytes(run.context, descriptor, payload)
    return row, descriptor, payload


def test_owned_approval_decision_is_exact_replay_safe_and_never_dispatches(runtime):
    run = _run(runtime)
    approval = _approval(run)
    before = list(runtime.dispatched)
    listed = runtime.call("runtime.approvals.list")["result"]
    assert listed["complete"] is False and listed["truncated"] is False
    assert listed["approvals"][0]["status"] == "pending"
    serialized = json.dumps(listed)
    assert "/private/" not in serialized and "holder" not in serialized and "identity_binding" not in serialized
    wrong = runtime.call("runtime.approval.resolve", approval_id=approval["approval_id"], approval_digest="0" * 64, choice="once")
    assert wrong["error"]["data"]["code"] == "approval_mismatch"
    decision = runtime.call("runtime.approval.resolve", approval_id=approval["approval_id"],
                            approval_digest=approval["approval_digest"], choice="once")["result"]
    assert decision["approval"]["status"] == "approved" and not decision["dispatch_performed"]
    replay = runtime.call("runtime.approval.resolve", approval_id=approval["approval_id"],
                          approval_digest=approval["approval_digest"], choice="once")
    assert replay["error"]["data"]["code"] == "approval_mismatch"
    run.db.consume_effect_approval(approval["approval_id"], artifact_actor(run.context), consumer_id="fixture-consumer", **approval["binding"])
    assert runtime.call("runtime.approvals.list")["result"]["approvals"][0]["status"] == "consumed"
    assert runtime.dispatched == before
    events = runtime.call("runtime.events.since")["result"]["events"]
    statuses = [row["payload"].get("approval_status") for row in events if row["type"].startswith("approval.")]
    assert statuses == ["pending", "approved", "consumed"]
    assert next(row["payload"]["expires_at"] for row in events if row["type"] == "approval.requested") == approval["expires_at"]


@pytest.mark.parametrize("forged", [{"actor": "other"}, {"principal_id": "other"}, {"holder": "fixture-rpc-owner"},
                                     {"generation": 1}, {"profile": "b"}, {"input_ref": {}}, {"choice": "always"}])
def test_resolution_rejects_client_authority_and_broad_choice(runtime, forged):
    approval = _approval(_run(runtime))
    params = {"approval_id": approval["approval_id"], "approval_digest": approval["approval_digest"], "choice": "once", **forged}
    assert runtime.call("runtime.approval.resolve", **params)["error"]["code"] == 4000
    assert runtime.call("runtime.approvals.list")["result"]["approvals"][0]["status"] == "pending"


def test_approval_resolution_requires_live_run_policy_and_owner(runtime):
    run = _run(runtime)
    approval = _approval(run)
    params = {"approval_id": approval["approval_id"], "approval_digest": approval["approval_digest"], "choice": "once"}
    run.agent._active_runtime_run = None
    assert runtime.call("runtime.approval.resolve", **params)["error"]["data"]["code"] == "approval_owner_unavailable"
    run.agent._active_runtime_run = run
    home = runtime.homes["a"]
    raw = json.loads((home / "config.yaml").read_text())
    original = json.loads(json.dumps(raw))
    raw["agent_identity"]["agents"]["ryoko"]["allowed_tools"] = ["todo_list"]
    (home / "config.yaml").write_text(json.dumps(raw))
    assert runtime.call("runtime.approval.resolve", **params)["error"]["data"]["code"] == "policy_revoked"
    (home / "config.yaml").write_text(json.dumps(original))
    run.db.release_session_turn_lease(run.session_id, run.holder)
    assert run.db.try_acquire_session_turn_lease(run.session_id, "successor")
    assert runtime.call("runtime.approval.resolve", **params)["error"]["data"]["code"] == "stale_owner"
    assert run.db.get_effect_approval(approval["approval_id"], artifact_actor(run.context))["status"] == "pending"


def test_lists_are_bounded_and_effect_detail_never_exposes_paths_or_receipt_references(runtime):
    run = _run(runtime)
    for _ in range(2):
        _approval(run)
    first, _descriptor, _payload = _effect(run)
    _effect(run, operation_id="effect-two")
    for method, key in (("runtime.approvals.list", "approvals"), ("runtime.effects.list", "effects")):
        page = runtime.call(method, limit=1)["result"]
        assert len(page[key]) == 1 and page["truncated"] and not page["complete"]
        assert runtime.call(method, limit=201)["error"]["code"] == 4000
    run.db.record_effect_outcome(first["effect_id"], artifact_actor(run.context), holder=run.holder,
        generation=run.generation, state="outcome_unknown", receipt={"reference": "/private/provider/receipt", "sha256": "a" * 64},
        evidence={"reason": "/private/provider/evidence"})
    detail = runtime.call("runtime.effect.get", effect_id=first["effect_id"])["result"]
    assert detail["effect"]["state"] == "outcome_unknown"
    assert detail["evidence"][0]["receipt_available"] and detail["evidence"][0]["receipt_sha256"] == "a" * 64
    assert "/private/" not in json.dumps(detail) and "input_ref" not in json.dumps(detail)
    assert not detail["effect"]["replay_permitted"] and not detail["effect"]["exactly_once_external"]
    events = runtime.call("runtime.events.since")["result"]["events"]
    states = [item["payload"].get("effect_state") for item in events if item["type"] == "effect.recorded"]
    assert "dispatched" in states and "outcome_unknown" in states
    assert "/private/" not in json.dumps(events)


def test_foreign_transport_profile_and_same_actor_other_session_cannot_read_or_resolve(runtime):
    run = _run(runtime)
    approval = _approval(run)
    effect, _, _ = _effect(run)
    requests = [("runtime.approvals.list", {}), ("runtime.effects.list", {}),
                ("runtime.effect.get", {"effect_id": effect["effect_id"]}),
                ("runtime.effect.reconcile", {"effect_id": effect["effect_id"]}),
                ("runtime.approval.resolve", {"approval_id": approval["approval_id"], "approval_digest": approval["approval_digest"], "choice": "once"})]
    for method, params in requests:
        assert runtime.call(method, via=runtime.peers["b"], **params)["error"]["code"] == 4001
    assert runtime.call("runtime.effect.get", "b", effect_id=effect["effect_id"])["error"]["data"]["code"] == "effect_not_found"
    other_run = _run(runtime, session_id="another-owned-conversation")
    other_effect, _, _ = _effect(other_run, operation_id="other-effect")
    other_approval = _approval(other_run)
    assert runtime.call("runtime.effect.get", effect_id=other_effect["effect_id"])["error"]["data"]["code"] == "identity_mismatch"
    assert runtime.call("runtime.approval.resolve", approval_id=other_approval["approval_id"],
        approval_digest=other_approval["approval_digest"], choice="once")["error"]["data"]["code"] == "identity_mismatch"


@pytest.mark.platforms("linux")
def test_read_only_reconciliation_requires_idle_lease_and_never_replays_publication(runtime, monkeypatch):
    run = _run(runtime)
    effect, descriptor, payload = _effect(run, publish=True)
    busy = runtime.call("runtime.effect.reconcile", effect_id=effect["effect_id"])
    assert busy["error"]["data"]["code"] == "effect_owner_busy"
    assert run.db.get_effect(effect["effect_id"], artifact_actor(run.context))["state"] == "dispatched"
    run.db.release_session_turn_lease(run.session_id, run.holder)
    def no_mutation(*_a, **_kw):
        raise AssertionError("Reconciliation must never publish bytes")
    monkeypatch.setattr("agent.effect_reconciler._publish_bytes", no_mutation)
    result = runtime.call("runtime.effect.reconcile", effect_id=effect["effect_id"])["result"]
    assert result["inspection_only"] and not result["dispatch_performed"]
    assert result["effect"]["state"] == "confirmed"
    assert (runtime.homes["a"] / descriptor["locator"]).read_bytes() == payload
    assert run.db.get_session_turn_lease(run.session_id) is None
    replay = runtime.call("runtime.effect.reconcile", effect_id=effect["effect_id"])["result"]
    assert replay["effect"]["state"] == "confirmed" and replay["evidence"] == result["evidence"]
    assert runtime.dispatched == []


def test_missing_bytes_remain_unknown_and_unsupported_adapter_is_not_invoked(runtime):
    run = _run(runtime)
    missing, _, _ = _effect(run)
    unsupported, _, _ = _effect(run, operation_id="unsupported", operation_type="remote-provider")
    run.db.release_session_turn_lease(run.session_id, run.holder)
    result = runtime.call("runtime.effect.reconcile", effect_id=missing["effect_id"])["result"]
    assert result["effect"]["state"] == "reconciliation_required"
    assert run.db.get_session_turn_lease(run.session_id) is None
    assert runtime.call("runtime.effect.reconcile", effect_id=unsupported["effect_id"])["error"]["data"]["code"] == "effect_adapter_unsupported"
    assert runtime.call("runtime.effect.get", effect_id=unsupported["effect_id"])["result"]["effect"]["state"] == "dispatched"


@pytest.mark.parametrize("cause", ["task_cancelled", "local_interrupt", "budget_deadline"])
def test_stopped_local_run_cannot_resolve_without_a_durable_cancel_command(runtime, monkeypatch, cause):
    from agent.task_scope import TaskScope
    run = _run(runtime)
    approval = _approval(run)
    if cause == "budget_deadline":
        from agent.budget_account import BudgetRuntime, parse_budget_policy
        from tests.agent.test_budget_runtime import policy
        budget_policy = parse_budget_policy({"runtime_budget": policy()})
        actor = artifact_actor(run.context)
        deadline = time.time() + 60
        account = run.db.create_budget_account(run.session_id, actor, run.run_id, budget_policy.limits,
            deadline=deadline, holder=run.holder, generation=run.generation, policy_snapshot=budget_policy.record)
        budget = BudgetRuntime(run.db, account["account_id"], account["root_id"], budget_policy,
                               actor, run.holder, run.generation, deadline, run.agent)
        run.agent._active_runtime_run = replace(run, budget=budget)
        monkeypatch.setattr("agent.budget_account.time.time", lambda: deadline + 1)
        expected = "budget_blocked"
    else:
        scope = TaskScope(run.agent, run.run_id)
        run.agent._active_runtime_run = replace(run, task_scope=scope)
        if cause == "task_cancelled":
            scope.cancelled.set()
        else:
            run.agent._interrupt_requested = True
        expected = "run_cancelled"
    denied = runtime.call("runtime.approval.resolve", approval_id=approval["approval_id"],
                          approval_digest=approval["approval_digest"], choice="once")
    assert denied["error"]["data"]["code"] == expected
    assert run.db.get_effect_approval(approval["approval_id"], artifact_actor(run.context))["status"] == "pending"
    assert run.db.read_runtime_command(run.session_id, run.command_id)["status"] == "claimed"


def test_expired_approval_and_missing_effect_ids_return_safe_errors(runtime, monkeypatch):
    run = _run(runtime)
    approval = run.db.request_effect_approval(actor=artifact_actor(run.context), expires_at=time.time() + 1, **_binding(run))
    monkeypatch.setattr("time.time", lambda: approval["expires_at"] + 1)
    denied = runtime.call("runtime.approval.resolve", approval_id=approval["approval_id"],
                          approval_digest=approval["approval_digest"], choice="once")
    assert denied["error"]["data"]["code"] == "approval_expired"
    assert runtime.call("runtime.approvals.list")["result"]["approvals"][0]["expired"]
    for method in ("runtime.effect.get", "runtime.effect.reconcile"):
        missing = runtime.call(method, effect_id="absent-effect")
        assert missing["error"]["data"]["code"] == "effect_not_found"
        assert "/" not in missing["error"]["message"]
