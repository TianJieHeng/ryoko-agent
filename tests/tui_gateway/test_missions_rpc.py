"""Owned mission controls use real SQLite, project artifacts, CAS and deterministic receipts."""
import json

import pytest

from tests.tui_gateway.test_artifact_rpc import artifacts, result, denied, publish  # noqa: F401

pytestmark = pytest.mark.platforms("linux")


def create(rpc, contract=None, **kwargs):
    return result(rpc.call("runtime.mission.create", mission_id="fixture-mission",
        contract=contract or {"outcome": "Produce a checked deliverable"}, **kwargs))["mission"]


def artifact_ref(row):
    return {"artifact_id": row["artifact_id"], "version": row["version"], "digest": row["sha256"]}


def two_output_contract(rpc):
    project = rpc.project()["id"]
    published = [publish(rpc, {"project_id": project, "command_id": "publish-" + name,
        "request_id": "publish-" + name, "content": "# Summary\nShared answer\n"}) for name in ("one", "two")]
    refs = [artifact_ref(row) for row in published]
    return {"outcome": "Two complete, consistent summaries", "project_id": project, "policy": "reviewed", "risk": "low", "uncertainty": "low",
        "deliverables": [{"deliverable_id": name, "artifact_ref": ref} for name, ref in zip(("one", "two"), refs)],
        "acceptance": [{"criterion_id": "sections", "kind": "markdown_sections", "artifact_refs": refs,
                        "parameters": {"required_sections": ["Summary"]}},
                       {"criterion_id": "consistent", "kind": "linked_consistency", "artifact_refs": refs,
                        "parameters": {"tokens": ["Shared answer"]}}]}


def test_owned_create_read_list_pause_resume_cancel_cas(artifacts):
    mission = create(artifacts)
    assert mission["state"] == "waiting_for_user" and mission["turns_used"] == 0
    assert artifacts.agents["a"]._session_db.get_session_turn_lease(artifacts.agents["a"].session_id) is None
    assert result(artifacts.call("runtime.mission.get"))["mission"]["mission_id"] == mission["mission_id"]
    listing = result(artifacts.call("runtime.mission.list", limit=1))
    assert listing["limit_reached"] and not listing["complete"]
    denied(artifacts.call("runtime.mission.pause", expected_revision=99), "revision_conflict")
    paused = result(artifacts.call("runtime.mission.pause", expected_revision=1))["mission"]
    assert paused["state"] == "paused" and paused["turns_used"] == 0
    denied(artifacts.call("runtime.mission.resume", expected_revision=paused["revision"]), "mission_criteria_required")
    cancelled = result(artifacts.call("runtime.mission.cancel", expected_revision=paused["revision"]))["mission"]
    assert cancelled["state"] == "cancelled"
    assert cancelled["execution_status"] != "completed"
    denied(artifacts.call("runtime.mission.accept", expected_revision=cancelled["revision"]))
    assert result(artifacts.call("runtime.mission.get", "b"))["mission"] is None
    denied(artifacts.call("runtime.mission.get", via=artifacts.peers["b"]))
    denied(artifacts.call("runtime.mission.create", mission_id="another", contract={"outcome": "reset"}), "mission_exists")


def test_two_outputs_verify_then_explicit_accept_retains_versions(artifacts):
    contract = two_output_contract(artifacts)
    created = create(artifacts, contract)
    paused = result(artifacts.call("runtime.mission.pause", expected_revision=created["revision"]))["mission"]
    created = result(artifacts.call("runtime.mission.resume", expected_revision=paused["revision"]))["mission"]
    assert created["turns_used"] == 0 and created["max_turns"] == paused["max_turns"]
    denied(artifacts.call("runtime.mission.accept", expected_revision=created["revision"]), "mission_verification_required")
    checked = result(artifacts.call("runtime.mission.verify", expected_revision=created["revision"]))
    assert checked["mission"]["state"] == "ready_to_review"
    assert all(receipt["result"] == "pass" for receipt in checked["receipts"])
    assert not checked["dispatch_performed"]
    accepted = result(artifacts.call("runtime.mission.accept", expected_revision=checked["mission"]["revision"]))["mission"]
    assert accepted["state"] == "completed" and accepted["acceptance_status"] == "accepted"
    assert accepted["deliverables"] == created["deliverables"]
    denied(artifacts.call("runtime.mission.accept", expected_revision=checked["mission"]["revision"]), "revision_conflict")
    receipts = result(artifacts.call("runtime.mission.receipts.list", limit=1))
    assert len(receipts["receipts"]) == 1 and not receipts["complete"]
    encoded = json.dumps(checked)
    assert str(artifacts.homes["a"]) not in encoded and '"locator"' not in encoded
    assert '"principal_id"' not in encoded and '"profile_id"' not in encoded


def test_plan_revision_cannot_reset_limits_or_self_accept(artifacts):
    created = create(artifacts, {"outcome": "Bounded work", "max_turns": 2, "no_progress_limit": 1})
    denied(artifacts.call("runtime.mission.revise", expected_revision=created["revision"],
        contract={"outcome": "Expanded work", "max_turns": 3}), "budget_limit_enlarged")
    revised = result(artifacts.call("runtime.mission.revise", expected_revision=created["revision"],
        contract={"outcome": "Clarified work"}))["mission"]
    assert revised["max_turns"] == 2 and revised["no_progress_limit"] == 1
    denied(artifacts.call("runtime.mission.revise", expected_revision=revised["revision"],
        contract={"outcome": "Fake done", "state": "completed"}))
    denied(artifacts.call("runtime.mission.accept", expected_revision=revised["revision"]))


@pytest.mark.parametrize("extra", [{"actor": {"agent_id": "ryoko"}}, {"holder": "invented"},
    {"generation": 1}, {"profile": "other"}, {"approval": True}])
def test_mission_controls_reject_client_authority(artifacts, extra):
    denied(artifacts.call("runtime.mission.create", mission_id="malicious",
        contract={"outcome": "no"}, **extra))


def test_busy_lease_and_revoked_project_grant_fail_closed(artifacts):
    contract = two_output_contract(artifacts)
    agent = artifacts.agents["a"]
    assert agent._session_db.try_acquire_session_turn_lease(agent.session_id, "another-owner", ttl_seconds=30)
    denied(artifacts.call("runtime.mission.create", mission_id="busy", contract=contract), "mission_owner_busy")
    lease = agent._session_db.get_session_turn_lease(agent.session_id)
    agent._session_db.release_session_turn_lease(agent.session_id, "another-owner", generation=lease["generation"])
    create(artifacts, contract)
    from hermes_cli import projects_db
    with projects_db.connect_closing(artifacts.homes["a"] / "projects.db") as conn:
        conn.execute("DELETE FROM project_grants WHERE project_id=?", (contract["project_id"],))
        conn.commit()
    denied(artifacts.call("runtime.mission.get"))
    denied(artifacts.call("runtime.mission.pause", expected_revision=1))


def test_owned_shared_goal_parser_creates_waiting_mission_without_judge(artifacts, monkeypatch):
    from tui_gateway import server
    monkeypatch.setattr("hermes_cli.goals.draft_contract", lambda *_a, **_k: pytest.fail("No model in mission controls"))
    def goal(text, via=None):
        import threading
        ready, frames = threading.Event(), []
        peer = via or artifacts.peers["a"]
        def receive(frame):
            if frame.get("id") == "goal-control":
                frames.append(frame)
                ready.set()
            return True
        peer.write = receive
        direct = server.dispatch({"jsonrpc": "2.0", "id": "goal-control", "method": "command.dispatch",
            "params": {"session_id": "live-a", "name": "goal", "arg": text}}, transport=peer)
        if direct is not None:
            return direct
        assert ready.wait(10), "Owned goal control did not reply"
        return frames[-1]
    created = result(goal("Write two summaries"))
    assert created["type"] == "exec" and "waiting" in created["output"]
    mission = result(artifacts.call("runtime.mission.get"))["mission"]
    assert mission["state"] == "waiting_for_user"
    assert result(goal("pause"))["type"] == "exec"
    assert result(artifacts.call("runtime.mission.get"))["mission"]["state"] == "paused"
    result(goal("clear"))
    assert result(artifacts.call("runtime.mission.get"))["mission"]["state"] == "cancelled"
    denied(goal("Replace with foreign goal", artifacts.peers["b"]))


from tests.tui_gateway.test_runtime_rpc import runtime  # noqa: E402,F401


def test_active_owner_revision_invalidates_only_affected_unconsumed_approvals(runtime):
    import time
    from tests.tui_gateway.test_runtime_effects_rpc import _run, _binding, _effect
    from agent.result_artifacts import artifact_actor, descriptor_digest
    run = _run(runtime)
    actor = artifact_actor(run.context)
    approvals = []
    for label in ("pending", "approved", "consumed", "unrelated"):
        binding = {**_binding(run), "action_digest": descriptor_digest({"action": label})}
        row = run.db.request_effect_approval(actor=actor, expires_at=time.time() + 300, **binding)
        if label in {"approved", "consumed"}:
            run.db.resolve_effect_approval(row["approval_id"], actor, holder=run.holder,
                generation=run.generation, approval_digest=row["approval_digest"], choice="once")
        if label == "consumed":
            run.db.consume_effect_approval(row["approval_id"], actor, consumer_id="fixture-consumer", **binding)
        approvals.append(row)
    contract = {"outcome": "A reviewed bounded mission", "plan_steps": [{"step_id": "affected",
        "description": "Original intent", "checkpoint": True,
        "approval_ids": [row["approval_id"] for row in approvals[:3]]}]}
    created = result(runtime.call("runtime.mission.create", mission_id="approval-mission", contract=contract))["mission"]
    effect, _descriptor, _payload = _effect(run)
    revised = result(runtime.call("runtime.mission.revise", expected_revision=created["revision"],
        contract={"outcome": "A reviewed bounded mission", "plan_steps": [{"step_id": "affected",
            "description": "Changed intent", "checkpoint": True}]}))["mission"]
    rows = result(runtime.call("runtime.approvals.list"))["approvals"]
    statuses = {row["approval_id"]: row for row in rows}
    assert [statuses[row["approval_id"]]["status"] for row in approvals] == ["invalidated", "invalidated", "consumed", "pending"]
    assert statuses[approvals[0]["approval_id"]]["invalidation_reason"] == "plan_step_changed"
    assert revised["missed_steer"][0]["effect_ids"] == [effect["effect_id"]]
    assert "/private/" not in json.dumps(statuses)
    events = result(runtime.call("runtime.events.since", limit=100))["events"]
    invalidated = [row for row in events if row["payload"].get("approval_status") == "invalidated"]
    assert len(invalidated) == 2
    assert all(row["payload"]["invalidation_reason"] == "plan_step_changed" for row in invalidated)
    assert any(row["payload"].get("mission_revision") == revised["revision"] for row in events)


def test_active_cancel_preserves_dispatched_effect_and_stops_local_scope(runtime):
    from tests.tui_gateway.test_runtime_effects_rpc import _run, _effect
    from agent.task_scope import TaskScope
    run = _run(runtime)
    from dataclasses import replace
    run = replace(run, task_scope=TaskScope(run.agent, run.run_id))
    run.agent._active_runtime_run = run
    created = result(runtime.call("runtime.mission.create", mission_id="cancel-mission",
        contract={"outcome": "Keep honest effect state"}))["mission"]
    effect, _descriptor, _payload = _effect(run)
    cancelled = result(runtime.call("runtime.mission.cancel", expected_revision=created["revision"]))["mission"]
    assert cancelled["state"] == "cancelled" and run.task_scope.cancelled.is_set()
    assert cancelled["effect_refs"] == [{"effect_id": effect["effect_id"], "state": "dispatched"}]
    assert result(runtime.call("runtime.effect.get", effect_id=effect["effect_id"]))["effect"]["state"] == "dispatched"
    denied(runtime.call("runtime.mission.revise", expected_revision=cancelled["revision"],
        contract={"outcome": "New work"}), "mission_run_blocked")


def test_stale_artifact_head_does_not_rewrite_historical_acceptance(artifacts):
    contract = two_output_contract(artifacts)
    created = create(artifacts, contract)
    checked = result(artifacts.call("runtime.mission.verify", expected_revision=created["revision"]))
    accepted = result(artifacts.call("runtime.mission.accept", expected_revision=checked["mission"]["revision"]))["mission"]
    old = contract["deliverables"][0]["artifact_ref"]
    publish(artifacts, {"project_id": contract["project_id"], "command_id": "new-head", "request_id": "new-head",
        "artifact_id": old["artifact_id"], "parent_version": 1, "expected_head_version": 1,
        "content": "# Summary\nChanged answer\n"})
    current = result(artifacts.call("runtime.mission.get"))["mission"]
    assert current["state"] == "completed" and current["acceptance_status"] == "accepted"
    assert not current["verification_current"]
    assert current["deliverables"] == accepted["deliverables"]


def test_partial_and_unsupported_checks_never_become_user_accepted_proof(artifacts):
    contract = two_output_contract(artifacts)
    contract["acceptance"].append({"criterion_id": "execute", "kind": "test_execution",
        "parameters": {"command": "echo fabricated-pass"}})
    created = create(artifacts, contract)
    checked = result(artifacts.call("runtime.mission.verify", expected_revision=created["revision"]))
    assert checked["mission"]["state"] == "partially_completed"
    assert {r["criterion_id"]: r["result"] for r in checked["receipts"]}["execute"] == "unsupported"
    denied(artifacts.call("runtime.mission.accept", expected_revision=checked["mission"]["revision"]),
           "mission_verification_required")


def test_consequential_intent_requires_reviewed_plan_and_checkpoint(artifacts):
    contract = two_output_contract(artifacts)
    contract.update(risk="consequential", uncertainty="high")
    created = create(artifacts, contract)
    assert created["state"] == "waiting_for_user"
    denied(artifacts.call("runtime.mission.resume", expected_revision=created["revision"]))
    revised = result(artifacts.call("runtime.mission.revise", expected_revision=created["revision"],
        contract={"outcome": contract["outcome"], "plan_steps": [{"step_id": "review",
            "description": "Review the exact deliverables", "checkpoint": True}]}))["mission"]
    resumed = result(artifacts.call("runtime.mission.resume", expected_revision=revised["revision"]))["mission"]
    assert resumed["state"] == "ready"
    denied(artifacts.call("runtime.mission.revise", expected_revision=resumed["revision"],
        contract={"outcome": contract["outcome"], "policy": "direct"}))
