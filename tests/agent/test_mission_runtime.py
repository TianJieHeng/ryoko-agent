"""Actual model turns verify committed artifacts before releasing their owner."""
from contextlib import contextmanager
import hashlib
import json
from types import SimpleNamespace

import pytest

from agent.artifact_commands import artifact_control_scope, begin_artifact_control, finish_artifact_control
from agent.identity_lifecycle import agent_runtime_scope
from agent.mission_runtime import begin_mission_turn, consume_goal_decision, finalize_mission_result
from agent.project_context import project_access
from agent.result_artifacts import artifact_actor
from hermes_cli import projects_db
from hermes_cli.goals import GoalManager
from tests.agent.test_budget_runtime import active, factory  # noqa: F401
from tests.agent.test_mission_verifier import criterion, ref
from tests.hermes_cli.test_artifact_store import publish_fixture

pytestmark = pytest.mark.platforms("linux")


@pytest.fixture
def mission_runtime(factory, monkeypatch):
    import tests.agent.test_budget_runtime as budget_tests
    from tui_gateway import server
    from tui_gateway.transport import bind_transport, reset_transport
    make, db, home = factory
    # Synthetic HTTP clients never inherit a host's real proxy route.
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(key, raising=False)
    with projects_db.connect_closing() as conn:
        project = projects_db.create_project(conn, name="Mission outputs", owner_principal_id="owner", grants=[
            {"principal_id": "owner", "agent_id": "primary", "permissions": ["read", "write", "share"]}])
    original = budget_tests.config
    def config(budget=None):
        raw = original(budget)
        raw["agent_identity"]["agents"]["primary"]["project_grants"] = [project]
        raw["agent_identity"]["agents"]["primary"]["allowed_tools"].append("execute_code")
        return raw
    monkeypatch.setattr(budget_tests, "config", config)
    agent, calls = make()
    peer = SimpleNamespace(write=lambda frame: True)
    session = {"agent": agent, "transport": peer, "session_key": agent.session_id, "profile_home": str(home)}
    monkeypatch.setattr(server, "_sessions", {"ui": session})

    @contextmanager
    def control():
        token = bind_transport(peer)
        method = server._current_rpc_method.set("runtime.artifact.prepare")
        with agent_runtime_scope(agent.runtime_context):
            try:
                run = begin_artifact_control(agent, "ui", "publication", {"operation": "fixture"})
                with artifact_control_scope(run):
                    yield run
                    finish_artifact_control(run, {"done": True})
            finally:
                server._current_rpc_method.reset(method)
                reset_transport(token)

    def create(*, valid=True, policy="direct", gates=None, dependencies=None, test_code=None):
        with control() as run:
            brief = publish_fixture(run, project, "brief", "# Decision\nShip blue.\n")
            checklist = publish_fixture(run, project, "checklist", "# Decision\nShip blue.\n")
            checks = [criterion("outputs", "existence", [brief, checklist]),
                criterion("sections", "markdown_sections", [brief, checklist],
                          required_sections=["Decision" if valid else "Missing"])]
            if test_code is not None:
                checks.append(criterion("execute-test", "test_execution", [brief, checklist],
                    adapter="isolated_python_v1", code=test_code, code_sha256=hashlib.sha256(test_code.encode()).hexdigest()))
            mission = db.create_mission(agent.session_id, artifact_actor(run.context), holder=run.holder,
                generation=run.generation, access=project_access(run.context), contract={
                    "outcome": "Deliver two complete artifacts", "project_id": project, "policy": policy,
                    "risk": "low", "uncertainty": "low",
                    "deliverables": [{"deliverable_id": name, "artifact_ref": ref(row), "required": True}
                                     for name, row in (("brief", brief), ("checklist", checklist))],
                    "acceptance": checks, "gates": gates or [], "dependencies": dependencies or []})
        return mission

    yield SimpleNamespace(agent=agent, calls=calls, db=db, project=project, create=create, session=session)


@pytest.mark.parametrize("policy", ["direct", "reviewed"])
def test_real_turn_finishes_after_verification_and_existing_consumers_only_read_decision(mission_runtime, monkeypatch, policy):
    from agent.runtime_commands import _RUN
    import agent.mission_verifier as verifier
    from tui_gateway import server
    rt = mission_runtime
    initial = rt.create(policy=policy)
    checked = []
    original = verifier.verify_mission
    def verify(context, db, mission, **kwargs):
        run = _RUN.get()
        assert run is not None and db.read_runtime_command(run.session_id, run.command_id)["status"] == "claimed"
        checked.append(run.run_id)
        return original(context, db, mission, **kwargs)
    monkeypatch.setattr(verifier, "verify_mission", verify)
    def forbidden(*args, **kwargs):
        pytest.fail("No post-owner model judge or shell gate may run")
    monkeypatch.setattr("hermes_cli.goals.judge_goal", forbidden)
    result = rt.agent.run_conversation("Complete the mission")
    expected = "completed" if policy == "direct" else "ready_to_review"
    assert result["mission"]["state"] == expected
    assert len(result["mission"]["artifact_refs"]) == 2
    assert result["runtime_budget"]["root_id"] == initial["budget_ref"]
    assert checked == [result["mission"]["run_id"]]
    manager = GoalManager(rt.agent.session_id, runtime_agent=rt.agent)
    assert manager.state.status == ("done" if policy == "direct" else "paused") and manager.state.turns_used == 1
    events = []
    monkeypatch.setattr(server, "_emit", lambda *args: events.append(args))
    assert server._goal_followup_after_turn("ui", rt.session, result, "complete", result["final_response"]) is None
    assert events and expected.replace("_", " ") in json.dumps(events)
    assert not manager.evaluate_after_turn("model says done")["should_continue"]
    assert len(rt.calls) == 1


def test_model_success_cannot_complete_failed_artifacts_and_no_progress_survives_turns(mission_runtime):
    from gateway.durable_outbox import load_committed_result
    rt = mission_runtime
    mission = rt.create(valid=False)
    roots = []
    first_result = None
    for turn in range(3):
        result = rt.agent.run_conversation("Keep working within the same mission")
        roots.append(result["runtime_budget"]["root_id"])
        if first_result is None:
            first_result = result
        else:
            replay = load_committed_result(rt.agent, first_result["runtime_command_id"])
            assert replay["mission"]["run_id"] == first_result["mission"]["run_id"]
            assert not consume_goal_decision(rt.agent, run_id=replay["mission"]["run_id"])["should_continue"]
        decision = consume_goal_decision(rt.agent)
        assert decision["should_continue"] is (turn < 2)
        assert result["mission"]["state"] == ("working" if turn < 2 else "partially_completed")
    assert roots == [mission["budget_ref"]] * 3
    with agent_runtime_scope(rt.agent.runtime_context):
        saved = rt.db.get_mission(rt.agent.session_id, artifact_actor(rt.agent.runtime_context),
                                  access=project_access(rt.agent.runtime_context))
        assert saved["consecutive_no_progress"] == 2 and saved["turns_used"] == 3
        assert len(saved["artifact_refs"]) == 2 and saved["recovery_choices"]
    manager = GoalManager(rt.agent.session_id, runtime_agent=rt.agent)
    with pytest.raises(RuntimeError, match="mission controls"):
        manager.resume(reset_budget=True)
    assert manager.state.turns_used == 3


@pytest.mark.parametrize("stop", ["cancelled", "budget"])
def test_guardrail_halt_and_late_steer_preserve_effects_and_partial_artifacts(mission_runtime, stop):
    from agent.tool_guardrails import ToolCallGuardrailConfig, ToolCallGuardrailController
    from agent.runtime_commands import finish_turn_command
    from agent.mission_runtime import assert_mission_project_scope
    from tools.capability_broker import CapabilityDenied
    from hermes_state_effects import effect_digest
    rt = mission_runtime
    rt.create(valid=False)
    with active(rt.agent) as run:
        begin_mission_turn(run)
        assert assert_mission_project_scope(run, rt.project) is not None
        with pytest.raises(CapabilityDenied, match="mission scope"):
            assert_mission_project_scope(run, "another-project")
        guard = ToolCallGuardrailController(ToolCallGuardrailConfig(hard_stop_enabled=True))
        for _ in range(5):
            guard.after_call("session_search", {"query": "unchanged"}, "unchanged")
        guard.before_call("session_search", {"query": "unchanged"})
        assert guard.halt_decision is not None
        rt.agent._tool_guardrails = guard
        effect = rt.db.prepare_effect(run.session_id, artifact_actor(run.context), run_id=run.run_id,
            holder=run.holder, generation=run.generation, operation_id="dispatch", intent_key="dispatch",
            operation_type="artifact_publish", action_digest=effect_digest({"action": "fixture"}),
            input_digest=effect_digest({"data": "fixture"}), target_ref="artifact:fixture:1", policy_version="1",
            policy_digest=run.context.policy.digest, input_revision="1", artifact_revision="1")
        rt.db.dispatch_effect(effect["effect_id"], artifact_actor(run.context), holder=run.holder, generation=run.generation)
        if stop == "budget":
            run.budget.blocked_reason = "Fixture budget exhausted"
        result = finalize_mission_result(run, {"completed": False, "interrupted": stop == "cancelled",
            "pending_steer": "Change the goal" if stop == "cancelled" else "", "final_response": "Partial work"})
        assert result["mission"]["state"] == ("cancelled" if stop == "cancelled" else "partially_completed")
        assert len(result["mission"]["artifact_refs"]) == 2
        if stop == "cancelled":
            assert effect["effect_id"] in result["mission"]["missed_steer"][0]["effect_ids"]
        saved = rt.db.get_mission(run.session_id, artifact_actor(run.context), access=project_access(run.context))
        assert saved["progress_evidence"]["guardrails"][0]["action"] in {"block", "halt"}
        assert rt.db.get_effect(effect["effect_id"], artifact_actor(run.context))["state"] == "dispatched"
        finish_turn_command(run, result)
    assert not consume_goal_decision(rt.agent)["should_continue"]


def test_steer_after_verification_before_result_commit_cannot_publish_current_completion(mission_runtime, monkeypatch):
    from agent.runtime_commands import submit_command
    from gateway import durable_outbox
    rt = mission_runtime
    rt.create()
    original = durable_outbox.commit_result
    def commit(run, result, status):
        assert result["mission"]["state"] == "completed"
        submit_command(rt.agent, {"schema_version": 1, "command_id": "late", "idempotency_key": "late",
            "expected_revision": None, "operation": "steer", "payload": {"text": "Reconsider the outcome"}})
        return original(run, result, status)
    monkeypatch.setattr(durable_outbox, "commit_result", commit)
    result = rt.agent.run_conversation("Complete the mission")
    assert result["mission"]["state"] in {"waiting_for_user", "partially_completed"}
    assert rt.db.read_runtime_command(rt.agent.session_id, "late")["result"]["missed_steer"] is True
    assert not consume_goal_decision(rt.agent)["should_continue"]
    assert len(result["mission"]["artifact_refs"]) == 2 and result["mission"]["missed_steer"]
    replay = durable_outbox.load_committed_result(rt.agent, result["runtime_command_id"])
    assert replay["mission"]["state"] == result["mission"]["state"]
    with agent_runtime_scope(rt.agent.runtime_context):
        receipts = rt.db.list_verification_receipts(rt.agent.session_id, artifact_actor(rt.agent.runtime_context),
            access=project_access(rt.agent.runtime_context))
        assert all(receipt["result"] == "pass" for receipt in receipts)


@pytest.mark.parametrize("after_acceptance", [False, True])
def test_main_completion_during_control_admission_does_not_leave_replayable_steer(mission_runtime, monkeypatch, after_acceptance):
    from agent.runtime_commands import finish_turn_command, submit_command
    rt = mission_runtime
    rt.create()
    with active(rt.agent) as run:
        begin_mission_turn(run)
        result = finalize_mission_result(run, {"completed": True, "final_response": "Retained output"})
        original = rt.db.submit_runtime_command
        def submit(*args, **kwargs):
            accepted = original(*args, **kwargs) if after_acceptance else None
            finish_turn_command(run, result)
            return accepted if after_acceptance else original(*args, **kwargs)
        monkeypatch.setattr(rt.db, "submit_runtime_command", submit)
        envelope = {"schema_version": 1, "command_id": "raced", "idempotency_key": "raced",
            "expected_revision": None, "operation": "steer", "payload": {"text": "Too late for this run"}}
        receipt = submit_command(rt.agent, envelope)
        record = rt.db.read_runtime_command(run.session_id, "raced")
        assert receipt["status"] == "rejected" or record["status"] == "blocked"
        if record is not None:
            assert record["status"] == "blocked" and record["result"]["applied"] is False
            assert record["result"]["outcome"] == "target_run_ended"
        assert not rt.agent._pending_steer
        assert rt.db.read_runtime_command(run.session_id, run.command_id)["status"] == "completed"
        assert result["runtime_result"]


def test_explicit_personal_memory_dependency_waits_on_owned_health_without_fetch(mission_runtime, monkeypatch):
    from agent.mission_runtime import _personal_memory_unavailable
    rt = mission_runtime
    mission = rt.create(dependencies=[{"dependency_id": "needed-memory", "kind": "input",
                                      "reference": "personal_memory", "status": "available"}])
    manager = rt.agent._memory_manager
    def forbidden(*args, **kwargs):
        pytest.fail("Mission availability checks must not fetch private memory")
    monkeypatch.setattr(manager, "prefetch_all", forbidden)
    with active(rt.agent) as run:
        begin_mission_turn(run)
        assert _personal_memory_unavailable(run, mission)  # Actual manager was constructed disabled.
        monkeypatch.setattr(manager, "capability_manifest", lambda: {"backend": "personal_mcp", "recall": True})
        for status, reason in (("degraded", "outage"), ("unconfigured", None), ("disabled", None),
                               ("ready", "live_unverified")):
            monkeypatch.setattr(manager, "health", lambda: {"status": status, "reason_code": reason})
            assert _personal_memory_unavailable(run, mission)
        result = finalize_mission_result(run, {"completed": True, "final_response": "Model says complete"})
        assert result["mission"]["state"] == "waiting_for_source"
        assert not result["mission"]["goal_decision"]["should_continue"]
        monkeypatch.setattr(manager, "health", forbidden)
        assert not _personal_memory_unavailable(run, {**mission, "dependencies": []})
        from agent.agent_identity import resolve_agent_context
        from agent.identity_lifecycle import identity_config
        child = resolve_agent_context(identity_config(), session_id="memory-child", profile_home=run.context.profile_home,
                                      parent_context=run.context, is_child=True)
        assert _personal_memory_unavailable(SimpleNamespace(context=child, agent=run.agent), mission)


def test_real_model_turn_launches_declared_bounded_test_before_completion(mission_runtime):
    import tools.code_execution_tool  # noqa: F401
    rt = mission_runtime
    code = "import json\nm = json.load(open('/inputs/manifest.json'))\nassert len(m['artifacts']) == 2\n" \
           "assert all(open(a['path']).read() == '# Decision\\nShip blue.\\n' for a in m['artifacts'])\n"
    rt.create(test_code=code)
    result = rt.agent.run_conversation("Verify the exact two outputs")
    assert result["mission"]["state"] == "completed"
    with agent_runtime_scope(rt.agent.runtime_context):
        receipts = rt.db.list_verification_receipts(rt.agent.session_id, artifact_actor(rt.agent.runtime_context),
            access=project_access(rt.agent.runtime_context))
        tested = [row for row in receipts if row["criterion_id"] == "execute-test"]
        assert len(tested) == 1 and tested[0]["result"] == "pass"
    assert result["runtime_budget"]["reserved"]["executor_slots"] == 0
    assert len(rt.calls) == 1
