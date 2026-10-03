"""Mission verification inside the existing turn; stored decisions feed existing queues."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import time

from agent.project_context import project_access
from agent.runtime_context import AgentContext


def is_mission_agent(agent):
    return isinstance(getattr(agent, "runtime_context", None), AgentContext)


def _actor(context):
    return {key: getattr(context.identity, key) for key in ("principal_id", "profile_id", "agent_id")}


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def _pending_steer(agent):
    lock = getattr(agent, "_pending_steer_lock", None)
    if lock is None:
        return bool(getattr(agent, "_pending_steer", None))
    with lock:
        return bool(getattr(agent, "_pending_steer", None))


@contextmanager
def _owned(agent):
    from agent.identity_lifecycle import agent_runtime_scope
    from gateway.durable_outbox import _authority
    context, db, session_id, actor = _authority(agent)
    with agent_runtime_scope(context):
        yield db, session_id, actor, project_access(context)


def goal_state_for_agent(agent):
    if not is_mission_agent(agent):
        return None
    from hermes_cli.goals import GoalState, GoalContract
    with _owned(agent) as (db, sid, actor, access):
        mission = db.get_mission(sid, actor, access=access)
    if mission is None:
        return None
    status = {"ready": "active", "working": "active", "completed": "done", "cancelled": "cleared"}.get(
        mission["state"], "paused")
    decision = mission.get("goal_decision") or {}
    return GoalState(goal=mission["outcome"], status=status, turns_used=mission["turns_used"],
        max_turns=mission["max_turns"], created_at=mission["created_at"], last_turn_at=mission["updated_at"],
        last_verdict=decision.get("verdict"), last_reason=decision.get("reason"),
        paused_reason=decision.get("reason") if status == "paused" else None,
        contract=GoalContract.from_dict(mission.get("legacy_contract")), subgoals=list(mission.get("subgoals", [])))


def _inactive(reason="No unconsumed mission decision"):
    return {"status": None, "should_continue": False, "continuation_prompt": None,
            "verdict": "inactive", "reason": reason, "message": ""}


def consume_goal_decision(agent, *, run_id=None):
    if not is_mission_agent(agent):
        return _inactive("Mission decisions require their authenticated agent")
    with _owned(agent) as (db, sid, actor, access):
        if db.get_mission(sid, actor, access=access) is None:
            return _inactive()
        return db.consume_mission_decision(sid, actor, run_id=run_id, access=access) or _inactive()


def begin_mission_turn(run):
    if run is None:
        return
    from agent.runtime_commands import assert_runtime_finalization
    assert_runtime_finalization(run)
    actor, access = _actor(run.context), project_access(run.context)
    mission = run.db.get_mission(run.session_id, actor, access=access)
    if mission is None:
        mission = run.db.migrate_legacy_mission(run.session_id, actor, holder=run.holder,
            generation=run.generation, access=access)
    if mission is not None and mission["state"] == "ready":
        mission = run.db.update_mission(run.session_id, actor, holder=run.holder,
            generation=run.generation, expected_revision=mission["revision"], changes={"state": "working"}, access=access)
    run.agent._mission_turn_start = (run.run_id,
        mission["revision"] if mission and mission["state"] in {"ready", "working"} else None)


def assert_mission_project_scope(run, project_id):
    """Constrain a model run's publication target; never grant project authority."""
    from agent.runtime_commands import RuntimeRun, assert_runtime_finalization
    from tools.capability_broker import CapabilityDenied
    if not isinstance(run, RuntimeRun):
        return None  # Separate, explicit artifact controls retain their own authority.
    assert_runtime_finalization(run)
    mission = run.db.get_mission(run.session_id, _actor(run.context), access=project_access(run.context))
    start = getattr(run.agent, "_mission_turn_start", None)
    started = bool(start and start[0] == run.run_id and start[1] is not None)
    if mission is None or not started and mission["state"] in {"completed", "cancelled"}:
        return None
    if (mission["state"] not in {"ready", "working"} or mission["project_id"] != project_id
            or started and start[1] != mission["revision"]):
        raise CapabilityDenied("mission_scope_changed", "Publication differs from this run's active mission scope or revision")
    return mission["revision"]


def _available_artifacts(run, mission, access):
    """Discoverable committed outputs survive a failed criterion or cancelled turn."""
    available = []
    seen = set()
    references = [item.get("artifact_ref") for item in mission["deliverables"]]
    references += [ref for criterion in mission["acceptance"] for ref in criterion["artifact_refs"]]
    for reference in references[:100]:
        if reference is None:
            continue
        key = (reference["artifact_id"], reference["version"])
        if key in seen:
            continue
        seen.add(key)
        try:
            row = run.db.read_artifact_version(*key, _actor(run.context), access=access)
        except (ValueError, PermissionError):
            continue
        if (row["project_id"] == mission["project_id"] and row["publication_state"] == "committed"
                and row["descriptor"]["sha256"] == reference["digest"]):
            available.append(dict(reference))
    return available


def _personal_memory_unavailable(run, mission):
    if not any(dep["kind"] == "input" and dep["reference"] == "personal_memory"
               for dep in mission.get("dependencies", [])):
        return False
    if run.context.policy.role != "primary" or run.context.policy.memory_backend != "personal_mcp":
        return True
    from agent.memory_router import RoutedMemoryManager
    manager = getattr(run.agent, "_memory_manager", None)
    if not isinstance(manager, RoutedMemoryManager):
        return True
    try:
        manager.assert_owner()
        health, capabilities = manager.health(), manager.capability_manifest()
    except (ValueError, PermissionError):
        return True
    # Availability alone proves no fact, provenance or freshness. This gate
    # never fetches content and cannot lend the primary backend to another role.
    return (health.get("status") != "ready" or health.get("reason_code") == "live_unverified"
            or capabilities.get("backend") != "personal_mcp" or capabilities.get("recall") is not True)


def _observe(run, mission, result):
    from agent.mission_verifier import verify_mission
    access = project_access(run.context)
    if (any(item["kind"] == "test_execution" for item in mission["acceptance"])
            and not result.get("interrupted") and not result.get("budget_blocked")
            and not run.dispatch_blocked.is_set()):
        from agent.runtime_commands import assert_runtime_dispatch, RuntimeFenceError
        from agent.budget_account import BudgetBlocked
        try:
            assert_runtime_dispatch(run.agent)
        except (RuntimeFenceError, BudgetBlocked):
            pass  # Finalization may read retained proof but must not launch more work.
        else:
            from agent.mission_test_adapter import execute_mission_tests
            execute_mission_tests(run, mission)
    deadline = min(time.time() + 10, mission.get("deadline") or float("inf"),
                   run.budget.deadline if run.budget is not None else float("inf"))
    criteria = mission["acceptance"]
    receipts = verify_mission(run.context, run.db, mission, deadline_at=deadline) if criteria else []
    guard = getattr(run.agent, "_tool_guardrails", None)
    from agent.tool_guardrails import ToolCallGuardrailController
    guardrails = guard.mission_evidence() if isinstance(guard, ToolCallGuardrailController) else []
    effects = run.db.list_effects(run.session_id, _actor(run.context), run_id=run.run_id, limit=100)
    effects = [{"effect_id": row["effect_id"], "state": row["state"]} for row in effects
               if row["state"] in {"dispatched", "confirmed", "outcome_unknown", "reconciliation_required"}]
    start = getattr(run.agent, "_mission_turn_start", None)
    changed = bool(start and start[0] == run.run_id and start[1] is not None and start[1] != mission["revision"])
    pending = int(bool(result.get("pending_steer")) or _pending_steer(run.agent)) + int(changed)
    verification_digest = _digest([{
        **{key: receipt[key] for key in ("criterion_id", "criterion_digest", "artifact_refs", "result")},
        "reason_codes": receipt["details"].get("reason_codes", []), "checks": receipt["details"].get("checks", []),
        "dependencies": [{key: dep.get(key) for key in ("kind", "reference", "version", "digest", "status")}
                         for dep in receipt["details"].get("dependencies", []) if dep.get("kind") in {"artifact", "evidence"}],
    } for receipt in receipts])
    no_progress = bool(guardrails) or (
        mission.get("last_verification_digest") == verification_digest and mission.get("turns_used", 0) > 0)
    available = _available_artifacts(run, mission, access)
    cancelled = bool(mission["state"] == "cancelled" or result.get("interrupted")
                     or run.task_scope is not None and run.task_scope.cancelled.is_set())
    budget_blocked = bool(result.get("budget_blocked") or run.budget is not None and (
        run.budget.blocked_reason or time.time() >= run.budget.deadline))
    evidence = {"guardrails": guardrails, "no_progress": no_progress, "budget_blocked": budget_blocked,
                "cancelled": cancelled, "pending_steer_count": pending, "effect_refs": effects,
                "recovery_choices": [], "verification_digest": verification_digest}
    return receipts, evidence, available, _personal_memory_unavailable(run, mission)


def _decision(mission, receipts, evidence, available, result, source_unavailable=False):
    required = {item["criterion_id"] for item in mission["acceptance"]
                if item.get("required", True) and item["kind"] != "user_acceptance"}
    needs_acceptance = any(item["kind"] == "user_acceptance" and item.get("required", True)
                           for item in mission["acceptance"])
    failed = [receipt for receipt in receipts if receipt["criterion_id"] in required and receipt["result"] != "pass"]
    required_outputs = [item for item in mission["deliverables"] if item.get("required", True)]
    covered = {_digest(ref) for receipt in receipts if receipt["result"] == "pass" for ref in receipt["artifact_refs"]}
    outputs_proven = bool(required_outputs) and all(item.get("artifact_ref") and
        _digest(item["artifact_ref"]) in covered for item in required_outputs)
    missing_sources = [dep for dep in mission.get("dependencies", [])
                       if dep.get("status") not in {"available", "current", "satisfied", "resolved"}]
    no_progress_count = mission.get("consecutive_no_progress", 0) + int(evidence["no_progress"])
    halt = any(row["action"] in {"block", "halt"} for row in evidence["guardrails"])
    state, reason = "working", "Required artifact checks are incomplete"
    choices = ["Revise the affected artifact or criterion", "Pause for a decision"]
    if evidence["cancelled"]:
        state, reason = "cancelled", "Execution was cancelled; retained artifacts and completed effects remain recorded"
        choices = ["Review retained artifacts and effects", "Resume only after revising the remaining work"]
    elif evidence["pending_steer_count"]:
        state = "paused" if mission["state"] == "paused" else "waiting_for_user"
        reason = "A late steer or mission revision missed this execution; recorded effects were not undone"
        choices = ["Review the missed steer and recorded effects", "Confirm the revised next step"]
    elif evidence["budget_blocked"] or result.get("failed") or run_deadline_expired(mission):
        state = "partially_completed" if available else "paused"
        reason = "Execution stopped before all acceptance criteria were satisfied"
        choices = ["Review retained artifacts and remaining criteria", "Revise or pause within the existing budget ceiling"]
    elif result.get("runtime_status") == "specialist_review_required":
        state, reason = "waiting_for_user", "The selected specialist returned; parent review is required"
        choices = ["Review the specialist result and recorded evidence", "Confirm the next step within the existing mission"]
    elif any(ref["state"] != "confirmed" for ref in evidence["effect_refs"]):
        state = "partially_completed" if available else "paused"
        reason = "An already dispatched effect requires reconciliation before further execution"
        choices = ["Reconcile the recorded effect outcome", "Review retained artifacts"]
    elif missing_sources or source_unavailable:
        state, reason = "waiting_for_source", "Required source or dependency is unavailable or not live verified"
        choices = ["Provide the missing authorized source", "Revise the dependency or scope"]
    elif mission.get("gates") or not required or not required_outputs:
        state, reason = "waiting_for_user", "Explicit deliverables and supported artifact acceptance criteria are required"
        choices = ["Define machine-checkable artifact criteria", "Review the result manually"]
    elif any(item.get("artifact_ref") is None for item in required_outputs):
        state, reason = "waiting_for_user", "Required deliverables need exact committed artifact references"
        choices = ["Select the committed artifact versions", "Revise the deliverable scope"]
    elif any(row["result"] == "unsupported" for row in failed):
        state, reason = "waiting_for_user", "A required criterion has no certified deterministic verifier"
        choices = ["Replace the unsupported criterion", "Provide explicit user acceptance through mission controls"]
    elif any(row["result"] == "blocked" for row in failed):
        state, reason = "waiting_for_source", "Required artifact or source evidence is unavailable or stale"
        choices = ["Supply or refresh authorized evidence", "Revise the affected artifact reference"]
    elif not failed and outputs_proven:
        state = "completed" if mission["policy"] == "direct" and not needs_acceptance else "ready_to_review"
        reason = "Every required artifact criterion passed deterministic verification"
        choices = [] if state == "completed" else ["Review and explicitly accept the verified result"]
    elif halt or no_progress_count >= mission.get("no_progress_limit", 2):
        state = "partially_completed" if available else "paused"
        reason = "Observed tool repetition or unchanged verification reached the no-progress limit"
        choices = ["Change the approach or acceptance inputs", "Pause for a decision"]
    elif mission["turns_used"] + 1 >= mission["max_turns"]:
        state = "partially_completed" if available else "paused"
        reason = "The mission continuation limit was reached"
    should_continue = state == "working"
    next_step = "Continue the existing mission within its current grants and budget. Resolve these criteria: " + ", ".join(
        row["criterion_id"] for row in failed) if should_continue else None
    evidence["recovery_choices"] = choices
    verdict = "done" if state == "completed" else "continue" if should_continue else "blocked"
    goal_status = "done" if state == "completed" else "active" if should_continue else "paused"
    decision = {"status": goal_status, "should_continue": should_continue, "continuation_prompt": next_step,
                "verdict": verdict, "reason": reason, "message": f"Mission {state.replace('_', ' ')}: {reason}"}
    return state, decision


def run_deadline_expired(mission):
    return mission.get("deadline") is not None and time.time() >= mission["deadline"]


def _summary(mission, available=None):
    return {"mission_id": mission["mission_id"], "revision": mission["revision"], "state": mission["state"],
            "artifact_refs": available if available is not None else mission.get("artifact_refs", []),
            "goal_decision": mission.get("goal_decision"), "run_id": mission.get("last_run_id"),
            "blockers": mission.get("blockers", []), "recovery_choices": mission.get("recovery_choices", []),
            "missed_steer": mission.get("missed_steer", []), "effect_refs": mission.get("effect_refs", []),
            "execution_status": mission.get("execution_status"), "acceptance_status": mission.get("acceptance_status"),
            "delivery_status": mission.get("delivery_status")}


def refresh_mission_result(agent, result):
    """Project current authority after a late control; immutable result bytes keep their revision."""
    if "mission" in result and is_mission_agent(agent):
        with _owned(agent) as (db, sid, actor, access):
            mission = db.get_mission(sid, actor, access=access)
        prior = result["mission"]
        if mission is not None and isinstance(prior, dict) and mission.get("last_run_id") == prior.get("run_id"):
            result["mission"] = _summary(mission)
    return result


def finalize_mission_result(run, result):
    """Persist bounded evidence while the exact submit owner is still claimed."""
    if run is None:
        return result
    from agent.runtime_commands import assert_runtime_finalization
    from hermes_state_runtime import RuntimeStoreError
    assert_runtime_finalization(run)
    actor, access = _actor(run.context), project_access(run.context)
    for attempt in range(2):
        mission = run.db.get_mission(run.session_id, actor, access=access)
        if mission is None:
            return result
        start = getattr(run.agent, "_mission_turn_start", None)
        started = bool(start and start[0] == run.run_id and start[1] is not None)
        if (mission.get("last_run_id") == run.run_id or mission["state"] in {"completed", "ready_to_review"}
                or not started and mission["state"] not in {"ready", "working"}):
            result["mission"] = _summary(mission)
            return result
        run.agent._mission_finalizing_run_id = run.run_id
        receipts, evidence, available, source_unavailable = _observe(run, mission, result)
        if _pending_steer(run.agent):
            evidence["pending_steer_count"] = max(1, evidence["pending_steer_count"])
        state, decision = _decision(mission, receipts, evidence, available, result, source_unavailable)
        try:
            saved = run.db.finalize_mission_turn(run.session_id, actor, holder=run.holder, generation=run.generation,
                expected_revision=mission["revision"], run_id=run.run_id, receipts=receipts, evidence=evidence,
                decision=decision, state=state, access=access)
        except RuntimeStoreError as exc:
            if exc.code == "revision_conflict" and attempt == 0:
                continue
            raise
        result["mission"] = _summary(saved, available)
        return result
    return result
