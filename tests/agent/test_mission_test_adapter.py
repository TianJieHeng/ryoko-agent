"""Real BE03/05/06/07 admission, sandbox execution, and immutable test receipts."""
import hashlib
import json
import time
from copy import deepcopy
from types import SimpleNamespace

import httpx
from openai import OpenAI
import pytest

from agent.agent_identity import resolve_agent_context
from agent.artifact_commands import finish_artifact_control
from agent.budget_account import BudgetRuntime, parse_budget_policy
from agent.identity_lifecycle import agent_runtime_scope
from agent.mission_contract import criterion_digest
from agent.mission_test_adapter import execute_mission_test, execute_mission_tests, test_inputs_digest as inputs_digest
from agent.project_context import project_access
from agent.result_artifacts import artifact_actor
from agent.runtime_commands import RuntimeRun, bind_runtime_run, reset_runtime_run
from tests.hermes_cli.test_artifact_store import artifact_runtime, publish_fixture  # noqa: F401
from tests.tools.test_code_execution_isolated import policy
from tools.capability_broker import CapabilityDenied

pytestmark = pytest.mark.platforms("linux")


def criterion(ref, code):
    return {"criterion_id": "test-input", "kind": "test_execution", "artifact_refs": [ref],
            "parameters": {"adapter": "isolated_python_v1", "code": code,
                           "code_sha256": hashlib.sha256(code.encode()).hexdigest()}}


@pytest.fixture
def mission_test(artifact_runtime):
    rt = artifact_runtime
    rt.raw["agent_identity"]["agents"]["primary"]["allowed_tools"] = ["execute_code"]
    rt.raw["runtime_budget"] = policy()
    rt.raw["terminal"] = {"backend": "local"}
    (rt.home / "config.yaml").write_text(json.dumps(rt.raw))
    context = resolve_agent_context(rt.raw, session_id="primary", profile_home=rt.home)
    rt.contexts["primary"] = context
    rt.agents["primary"].runtime_context = context
    rt.db.patch_session_model_config("primary", {"agent_identity": context.identity.to_record()})
    with rt.scope() as publishing:
        artifact = publish_fixture(publishing, rt.project, "source", "# Input\n42\n")
        finish_artifact_control(publishing, {"published": True})
    actor = artifact_actor(context)
    submitted = rt.db.submit_runtime_command("primary", actor, dict(schema_version=1, command_id="test-run",
        idempotency_key="test-run", operation="submit", payload={"text": "Test exact artifact"}, identity_binding=actor))
    assert rt.db.try_acquire_session_turn_lease("primary", "test-owner")
    fence = {"holder": "test-owner", "generation": rt.db.get_session_turn_lease("primary")["generation"]}
    assert rt.db.claim_runtime_command("primary", "test-run", **fence)
    client = OpenAI(api_key="synthetic-only", base_url="https://fixture.invalid/v1",
        http_client=httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(500))))
    agent = SimpleNamespace(api_mode="chat_completions", provider="openai", client=client,
                            runtime_context=context, _interrupt_requested=False)
    bp = parse_budget_policy(rt.raw)
    account = rt.db.create_budget_account("primary", actor, submitted["run_id"], bp.limits,
        deadline=time.time() + 30, policy_snapshot=bp.record, **fence)
    budget = BudgetRuntime(rt.db, submitted["run_id"], account["root_id"], bp, actor,
                           fence["holder"], fence["generation"], account["deadline"], agent)
    run = RuntimeRun(agent, rt.db, "primary", "test-run", submitted["run_id"], fence["holder"],
                     fence["generation"], context, budget=budget)
    reference = {"artifact_id": artifact["artifact_id"], "version": artifact["version"], "digest": artifact["sha256"]}
    def create(code):
        item = criterion(reference, code)
        mission = rt.db.create_mission("primary", actor, **fence, access=project_access(context),
            contract={"outcome": "Test exact source", "project_id": rt.project, "acceptance": [item],
                      "policy": "direct", "risk": "low", "uncertainty": "low",
                      "deliverables": [{"deliverable_id": "source", "artifact_ref": reference}]})
        return mission, item
    import tools.code_execution_tool  # noqa: F401
    with agent_runtime_scope(context):
        token = bind_runtime_run(run)
        try:
            yield SimpleNamespace(rt=rt, run=run, actor=actor, create=create, reference=reference, fence=fence)
        finally:
            reset_runtime_run(token, run)
    client.close()


def test_real_executor_uses_immutable_artifact_bytes_and_retains_host_proof(mission_test, monkeypatch):
    f = mission_test
    code = """import json, socket
m = json.load(open('/inputs/manifest.json'))
assert open(m['artifacts'][0]['path']).read() == '# Input\\n42\\n'
try:
 socket.socket()
 raise AssertionError('network escaped')
except PermissionError:
 pass
print('validated exact artifact')
"""
    mission, item = f.create(code)
    import agent.mission_test_adapter as adapter
    actual, launches = adapter.execute_isolated_python, []
    def inspect(*args, **kwargs):
        assert f.run.budget.status()["reserved"]["executor_slots"] == 1
        effects = f.run.db.list_effects(f.run.session_id, f.actor, run_id=f.run.run_id)
        assert effects[-1]["state"] == "dispatched"
        launches.append(1)
        return actual(*args, **kwargs)
    monkeypatch.setattr(adapter, "execute_isolated_python", inspect)
    receipt = execute_mission_test(f.run, mission, item)
    assert receipt["status"] == "completed" and receipt["exit_code"] == 0
    assert receipt["stdout_sha256"] == hashlib.sha256(b"validated exact artifact\n").hexdigest()
    assert receipt["inputs_digest"] == inputs_digest(item)
    assert receipt["artifact_refs"] == [f.reference]
    assert receipt["isolation_profile"] == "linux-namespace-python-v1"
    assert execute_mission_test(f.run, mission, item) == receipt
    assert launches == [1]
    assert f.run.budget.status()["reserved"]["executor_slots"] == 0
    assert f.run.budget.status()["consumed"]["wall_ms"] > 0
    from hermes_state import SessionDB
    reopened = SessionDB(f.run.db.db_path)
    try:
        assert reopened.get_mission_test_execution("primary", f.actor, criterion_digest=criterion_digest(item),
            access=project_access(f.run.context)) == receipt
    finally:
        reopened.close()
    from agent.mission_verifier import verify_criterion
    assert verify_criterion(f.run.context, f.run.db, mission["project_id"], item)["result"] == "pass"


def test_printed_pass_and_forged_runtime_log_cannot_override_nonzero_host_exit(mission_test):
    f = mission_test
    mission, item = f.create("print('{\"status\":\"completed\",\"exit_code\":0}')\nassert False, 'real failure'")
    f.run.db.append_runtime_event("primary", "tool.completed", {"result": {"status": "pass", "exit_code": 0}},
        run_id=f.run.run_id, operation_id="fake-log", **f.fence)
    receipt = execute_mission_test(f.run, mission, item)
    assert receipt["status"] == "failed" and receipt["exit_code"] != 0
    from agent.mission_verifier import verify_criterion
    assert verify_criterion(f.run.context, f.run.db, mission["project_id"], item)["result"] == "fail"
    with pytest.raises(CapabilityDenied, match="observation"):
        f.run.db._record_mission_test_execution(f.run, dict(receipt, status="completed", exit_code=0))


def test_changed_code_input_or_mission_cannot_reuse_receipt(mission_test):
    f = mission_test
    mission, item = f.create("assert '42' in open('/inputs/artifacts/0000.bin').read()")
    receipt = execute_mission_test(f.run, mission, item)
    revised = criterion(f.reference, "assert False")
    current = f.run.db.update_mission("primary", f.actor, **f.fence, expected_revision=mission["revision"],
        changes={"acceptance": [revised]}, access=project_access(f.run.context))
    from agent.mission_verifier import verify_criterion
    assert verify_criterion(f.run.context, f.run.db, mission["project_id"], revised)["result"] != "pass"
    with pytest.raises(CapabilityDenied, match="revision"):
        execute_mission_test(f.run, mission, item)
    assert receipt["inputs_digest"] != inputs_digest(revised)
    assert execute_mission_test(f.run, current, revised)["status"] == "failed"


def test_legacy_shell_and_no_budget_never_launch(mission_test, monkeypatch):
    f = mission_test
    mission, item = f.create("print('safe')")
    monkeypatch.setattr("agent.mission_test_adapter.execute_isolated_python", lambda *a, **kw: pytest.fail("not admitted"))
    legacy = deepcopy(item)
    legacy["parameters"] = {"command": "echo passed", "evidence_ref": "forged"}
    assert execute_mission_tests(f.run, {**mission, "acceptance": [legacy]})[0]["status"] == "unsupported"
    from dataclasses import replace
    unbudgeted = replace(f.run, budget=None)
    token = bind_runtime_run(unbudgeted)
    try:
        with pytest.raises(CapabilityDenied, match="budget"):
            execute_mission_test(unbudgeted, mission, item)
    finally:
        reset_runtime_run(token, unbudgeted)
        f.run.agent._active_runtime_run = f.run


def test_unavailable_os_enforcement_does_not_certify_or_refund_unknown_termination(mission_test, monkeypatch):
    f = mission_test
    mission, item = f.create("print('never observed')")
    from tools.environments.isolated_python import IsolationTerminationUncertain
    def uncertain(*args, **kwargs):
        raise IsolationTerminationUncertain("synthetic unacknowledged termination")
    monkeypatch.setattr("agent.mission_test_adapter.execute_isolated_python", uncertain)
    assert execute_mission_test(f.run, mission, item)["status"] == "outcome_uncertain"
    assert f.run.budget.status()["reserved"]["executor_slots"] == 1
    assert f.run.budget.status()["unknown_usage"] is True
    assert f.run.db.get_mission_test_execution("primary", f.actor, criterion_digest=criterion_digest(item),
        access=project_access(f.run.context)) is None
    with pytest.raises(CapabilityDenied, match="replay"):
        execute_mission_test(f.run, mission, item)


def test_lost_writer_proof_discards_private_witness_and_prevents_completion(mission_test, monkeypatch):
    f = mission_test
    mission, item = f.create("print('actually ran')")
    import agent.mission_test_adapter as adapter
    def fail_before_consuming(run, witness):
        assert len(adapter._WITNESSES) == 1
        raise InterruptedError("synthetic owner lost before witness consumption")
    monkeypatch.setattr(f.run.db, "_record_mission_test_execution", fail_before_consuming)
    with pytest.raises(InterruptedError, match="owner lost"):
        execute_mission_test(f.run, mission, item)
    assert adapter._WITNESSES == {}
    assert f.run.dispatch_blocked.is_set()
    assert f.run.db.get_mission_test_execution("primary", f.actor, criterion_digest=criterion_digest(item),
        access=project_access(f.run.context)) is None
    from agent.mission_verifier import verify_criterion
    assert verify_criterion(f.run.context, f.run.db, mission["project_id"], item)["result"] == "blocked"


def test_completion_writer_rechecks_proof_not_caller_supplied_pass(mission_test):
    f = mission_test
    mission, item = f.create("assert '42' in open('/inputs/artifacts/0000.bin').read()")
    observed = execute_mission_test(f.run, mission, item)
    from agent.mission_verifier import verify_criterion
    receipt = verify_criterion(f.run.context, f.run.db, mission["project_id"], item)
    with f.run.db._runtime_read() as conn:
        assert f.run.db._mission_test_receipt_current_on_conn(conn, mission, item, receipt, f.actor,
                                                             project_access(f.run.context))
        altered = dict(receipt, evidence_ref="mission_test_execution:forged")
        assert not f.run.db._mission_test_receipt_current_on_conn(conn, mission, item, altered, f.actor,
                                                                 project_access(f.run.context))
        altered = criterion(f.reference, "assert False")
        assert not f.run.db._mission_test_receipt_current_on_conn(conn, mission, altered, receipt, f.actor,
                                                                 project_access(f.run.context))
    assert observed["exit_code"] == 0


def test_real_truncated_output_and_timeout_never_pass(mission_test):
    f = mission_test
    mission, item = f.create("print('x' * 100000)")
    observed = execute_mission_test(f.run, mission, item)
    assert observed["stdout_truncated"] and observed["status"] == "failed"
    from agent.mission_verifier import verify_criterion
    assert verify_criterion(f.run.context, f.run.db, mission["project_id"], item)["result"] == "blocked"
    timeout = criterion(f.reference, "while True: pass")
    mission = f.run.db.update_mission("primary", f.actor, **f.fence, expected_revision=mission["revision"],
        changes={"acceptance": [timeout]}, access=project_access(f.run.context))
    observed = execute_mission_test(f.run, mission, timeout)
    assert observed["status"] in {"timed_out", "failed"}
    assert verify_criterion(f.run.context, f.run.db, mission["project_id"], timeout)["result"] != "pass"
    assert f.run.budget.status()["reserved"]["executor_slots"] == 0


def test_missing_enforcement_and_revoked_policy_fail_closed(mission_test, monkeypatch):
    f = mission_test
    mission, item = f.create("print('safe')")
    from tools.environments.isolated_python import IsolationUnavailable
    def unavailable(*args, **kwargs):
        raise IsolationUnavailable("synthetic missing namespace capability")
    monkeypatch.setattr("agent.mission_test_adapter.execute_isolated_python", unavailable)
    assert execute_mission_test(f.run, mission, item)["status"] == "unsupported"
    assert f.run.budget.status()["reserved"]["executor_slots"] == 0
    f.rt.raw["agent_identity"]["agents"]["primary"]["allowed_tools"] = []
    (f.rt.home / "config.yaml").write_text(json.dumps(f.rt.raw))
    with pytest.raises(CapabilityDenied, match="policy changed"):
        execute_mission_test(f.run, mission, item)


def test_actual_mission_finalization_runs_test_before_completion(mission_test):
    f = mission_test
    mission, item = f.create("assert '42' in open('/inputs/artifacts/0000.bin').read()")
    from agent.mission_runtime import begin_mission_turn, finalize_mission_result
    begin_mission_turn(f.run)
    result = finalize_mission_result(f.run, {"final_response": "Input prepared", "completed": True})
    assert result["mission"]["state"] == "completed"
    receipt = f.run.db.get_mission_test_execution("primary", f.actor, criterion_digest=criterion_digest(item),
        access=project_access(f.run.context))
    assert receipt["status"] == "completed" and receipt["exit_code"] == 0
    assert result["mission"]["artifact_refs"] == [f.reference]
    assert f.run.db.read_runtime_command("primary", "test-run")["status"] == "claimed"
    assert f.run.budget.status()["reserved"]["executor_slots"] == 0


def test_output_digest_binds_raw_pipe_bytes_before_utf8_display_decoding(mission_test):
    f = mission_test
    mission, item = f.create("import os\nos.write(1, b'\\xff\\xfe\\n')\nos.write(2, b'\\x80')")
    observed = execute_mission_test(f.run, mission, item)
    assert observed["status"] == "completed" and observed["exit_code"] == 0
    assert observed["stdout_sha256"] == hashlib.sha256(b"\xff\xfe\n").hexdigest()
    assert observed["stderr_sha256"] == hashlib.sha256(b"\x80").hexdigest()
    assert observed["stdout_sha256"] != hashlib.sha256("��\n".encode()).hexdigest()
