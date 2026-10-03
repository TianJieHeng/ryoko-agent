"""BE10 coding consumes real BE09 host proof; browser projection never dispatches."""
from copy import deepcopy
import hashlib
import json
import time
from types import SimpleNamespace

import httpx
from openai import OpenAI
import pytest

from agent.agent_identity import resolve_agent_context
from agent.artifact_commands import finish_artifact_control
from agent.budget_account import BudgetRuntime, parse_budget_policy
from agent.identity_lifecycle import agent_runtime_scope
from agent.mission_contract import criterion_digest
from agent.mission_test_adapter import execute_mission_test
from agent.project_context import project_access
from agent.result_artifacts import artifact_actor, read_project_artifact
from agent.runtime_commands import RuntimeRun, bind_runtime_run, reset_runtime_run
from hermes_cli.artifact_store import prepare_artifact, publish_artifact
from hermes_cli.domain_execution import build_coding_package, build_browser_package, require_browser_certification
from hermes_state import SessionDB
from tests.hermes_cli.test_artifact_store import artifact_runtime, approve  # noqa: F401
from tests.tools.test_code_execution_isolated import policy
from tools.capability_broker import CapabilityDenied

pytestmark = pytest.mark.platforms("linux")

BASELINE = b"def total(values):\n    return sum(values) + 1\n"
CANDIDATE = b"def total(values):\n    return sum(values)\n"
TEST_CODE = """import json
manifest = json.load(open('/inputs/manifest.json'))
source = open(manifest['artifacts'][0]['path']).read()
namespace = {}
exec(compile(source, 'totals.py', 'exec'), namespace)
assert namespace['total']([2, 3]) == 5
assert namespace['total']([]) == 0
print('targeted totals tests passed')
"""


def publish(run, project, name, data, mime="text/plain"):
    proposal = prepare_artifact(run, project_id=project, request_id=name, content_bytes=data, mime=mime)
    approve(proposal)
    result = publish_artifact(run, proposal)
    return {key: result[key] for key in ("artifact_id", "version", "sha256")}


@pytest.fixture
def coding_runtime(artifact_runtime):
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
        before = publish(publishing, rt.project, "baseline", BASELINE)
        after = publish(publishing, rt.project, "candidate", CANDIDATE)
        page = publish(publishing, rt.project, "page", b"<html><button>Submit</button></html>")
        finish_artifact_control(publishing, {"published": True})
    actor = artifact_actor(context)
    accepted = rt.db.submit_runtime_command("primary", actor, dict(schema_version=1, command_id="coding-test",
        idempotency_key="coding-test", operation="submit", payload={"text": "Test exact source"}, identity_binding=actor))
    assert rt.db.try_acquire_session_turn_lease("primary", "coding-owner")
    fence = {"holder": "coding-owner", "generation": rt.db.get_session_turn_lease("primary")["generation"]}
    assert rt.db.claim_runtime_command("primary", "coding-test", **fence)
    client = OpenAI(api_key="synthetic-only", base_url="https://fixture.invalid/v1",
        http_client=httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(500))))
    agent = SimpleNamespace(api_mode="chat_completions", provider="openai", client=client,
                            runtime_context=context, _interrupt_requested=False)
    bp = parse_budget_policy(rt.raw)
    account = rt.db.create_budget_account("primary", actor, accepted["run_id"], bp.limits,
        deadline=time.time() + 30, policy_snapshot=bp.record, **fence)
    budget = BudgetRuntime(rt.db, accepted["run_id"], account["root_id"], bp, actor,
                           fence["holder"], fence["generation"], account["deadline"], agent)
    run = RuntimeRun(agent, rt.db, "primary", "coding-test", accepted["run_id"], fence["holder"],
                     fence["generation"], context, budget=budget)
    item = {"criterion_id": "totals-test", "kind": "test_execution", "artifact_refs": [
        {"artifact_id": after["artifact_id"], "version": after["version"], "digest": after["sha256"]}],
        "parameters": {"adapter": "isolated_python_v1", "code": TEST_CODE,
                       "code_sha256": hashlib.sha256(TEST_CODE.encode()).hexdigest()}}
    import tools.code_execution_tool  # noqa: F401
    with agent_runtime_scope(context):
        token = bind_runtime_run(run)
        try:
            mission = rt.db.create_mission("primary", actor, **fence, access=project_access(context), contract={
                "outcome": "Review corrected totals", "project_id": rt.project, "acceptance": [item],
                "policy": "direct", "risk": "low", "uncertainty": "low", "deliverables": [
                    {"deliverable_id": "source", "artifact_ref": item["artifact_refs"][0]}]})
            arguments = {"project_id": rt.project, "scope": {"repository_id": "fixture-totals", "worktree_id": "review-1",
                "baseline_sha": hashlib.sha256(BASELINE).hexdigest()}, "changes": [
                    {"path": "src/totals.py", "before": before, "after": after}],
                "mission_id": mission["mission_id"], "mission_revision": mission["revision"], "test_ids": [item["criterion_id"]]}
            yield SimpleNamespace(rt=rt, run=run, actor=actor, fence=fence, mission=mission, item=item,
                                  before=before, after=after, page=page, arguments=arguments)
        finally:
            reset_runtime_run(token, run)
    client.close()


def test_real_source_to_isolated_targeted_tests_to_reopened_review(coding_runtime):
    f = coding_runtime
    receipt = execute_mission_test(f.run, f.mission, f.item)
    assert receipt["status"] == "completed"
    assert receipt["stdout_sha256"] == hashlib.sha256(b"targeted totals tests passed\n").hexdigest()
    package = build_coding_package(f.run.context, f.run.db, **f.arguments)
    metadata = package["metadata"]
    assert metadata["targeted_validation"] == "passed"
    assert metadata["tests"][0]["execution_receipt"] == receipt
    assert metadata["tests"][0]["authenticated"] is True
    assert metadata["changes"][0]["candidate_source"].encode() == CANDIDATE
    assert "+    return sum(values)" in metadata["changes"][0]["diff"]
    assert "-    return sum(values) + 1" in metadata["changes"][0]["diff"]
    assert metadata["inputs"] == [f.before, f.after]
    assert metadata["release_gate"]["state"] == "blocked"
    assert metadata["review_gate"]["state"] == "pending"
    assert metadata["permissions"]["merge"] == metadata["permissions"]["deploy"] == "unsupported"
    refs = [publish(f.run, f.rt.project, output["name"], output["content_bytes"], output["mime"])
            for output in package["outputs"]]
    with SessionDB(f.rt.home / "state.db") as reopened:
        for ref, output in zip(refs, package["outputs"]):
            assert read_project_artifact(f.run.context, reopened, f.rt.project,
                ref["artifact_id"], ref["version"]) == output["content_bytes"]
        record = json.loads(read_project_artifact(f.run.context, reopened, f.rt.project,
                            refs[1]["artifact_id"], refs[1]["version"]))
        assert record == metadata
        assert build_coding_package(f.run.context, reopened, **f.arguments)["metadata"]["targeted_validation"] == "passed"


def test_coding_rejects_forged_logs_drift_and_receipt_state_mismatch(coding_runtime):
    f = coding_runtime
    f.run.db.append_runtime_event("primary", "tool.completed", {"result": {"status": "pass", "exit_code": 0}},
        run_id=f.run.run_id, operation_id="forged-log", **f.fence)
    package = build_coding_package(f.run.context, f.run.db, **f.arguments)
    assert package["metadata"]["targeted_validation"] == "blocked"
    assert package["metadata"]["tests"][0]["execution_receipt"] is None
    with pytest.raises(TypeError):
        build_coding_package(f.run.context, f.run.db, **f.arguments, release_approved=True)
    for key, value in (("scope", {**f.arguments["scope"], "baseline_sha": "HEAD"}),
                       ("mission_revision", f.mission["revision"] + 1),
                       ("changes", [{**f.arguments["changes"][0], "path": "../totals.py"}]),
                       ("changes", [{**f.arguments["changes"][0], "path": "package.json"}])):
        with pytest.raises(ValueError):
            build_coding_package(f.run.context, f.run.db, **{**f.arguments, key: value})
    mismatch = deepcopy(f.arguments)
    mismatch["changes"][0]["after"]["sha256"] = hashlib.sha256(b"forged").hexdigest()
    with pytest.raises(ValueError, match="digest"):
        build_coding_package(f.run.context, f.run.db, **mismatch)
    receipt = execute_mission_test(f.run, f.mission, f.item)
    assert receipt["status"] == "completed"
    # A damaged/reopened store cannot pass merely because the receipt still says
    # exit zero: the authoritative completion check also requires settled usage.
    f.run.db._execute_write(lambda conn: conn.execute(
        "UPDATE budget_reservations SET unknown_usage=1 WHERE account_id=? AND operation_id=?",
        (receipt["budget_account_id"], receipt["operation_id"])))
    package = build_coding_package(f.run.context, f.run.db, **f.arguments)
    assert package["metadata"]["targeted_validation"] == "blocked"
    assert package["metadata"]["tests"][0]["verification"]["result"] == "blocked"


def test_failed_actual_test_cannot_be_hidden_by_printed_success(coding_runtime):
    f = coding_runtime
    failed = deepcopy(f.item)
    failed["parameters"]["code"] = "print('success, exit_code=0')\nassert False"
    failed["parameters"]["code_sha256"] = hashlib.sha256(failed["parameters"]["code"].encode()).hexdigest()
    mission = f.run.db.update_mission("primary", f.actor, **f.fence, expected_revision=f.mission["revision"],
        changes={"acceptance": [failed]}, access=project_access(f.run.context))
    receipt = execute_mission_test(f.run, mission, failed)
    assert receipt["status"] == "failed"
    package = build_coding_package(f.run.context, f.run.db, **{**f.arguments, "mission_revision": mission["revision"]})
    assert package["metadata"]["targeted_validation"] == "blocked"
    assert package["metadata"]["tests"][0]["verification"]["result"] == "fail"
    with pytest.raises(ValueError, match="revision"):
        build_coding_package(f.run.context, f.run.db, **f.arguments)


def browser_effect(f, state):
    input_digest = hashlib.sha256(b"exact submitted inputs").hexdigest()
    effect = f.run.db.prepare_effect("primary", f.actor, **f.fence, run_id=f.run.run_id,
        operation_id="browser-submit", intent_key="browser-submit", operation_type="browser_submit",
        input_digest=input_digest, target_ref="https://fixture.invalid/form", policy_digest=f.run.context.policy.digest,
        policy_version=str(f.run.context.policy.policy_version), input_revision=f.page["sha256"],
        artifact_revision=f.page["sha256"], provider_idempotency="unsupported")
    assert f.run.db.dispatch_effect(effect["effect_id"], f.actor, **f.fence)["dispatched_now"]
    # Fixture deliberately drives the generic existing effect ledger, not a
    # browser: these records are never advertised as live browser acceptance.
    f.run.db.record_effect_outcome(effect["effect_id"], f.actor, **f.fence, state=state,
        evidence={"kind": "fixture_timeout", "reason": "submit timed out; payload said success"})
    return {"project_id": f.rt.project, "effect_id": effect["effect_id"], "page_ref": f.page,
            "current_page_sha256": f.page["sha256"], "input_sha256": input_digest,
            "target_ref": "https://fixture.invalid/form", "observed_at": 1000, "now": 1010}


def test_browser_submit_timeout_projection_preserves_unknown_and_exact_bindings(coding_runtime):
    f = coding_runtime
    args = browser_effect(f, "outcome_unknown")
    package = build_browser_package(f.run.context, f.run.db, **args)
    metadata = package["metadata"]
    assert metadata["effect_state"] == "outcome_unknown"
    assert all(metadata["binding_checks"].values())
    assert metadata["completion"] == "unverified"
    assert not metadata["replay_permitted"] and not metadata["dispatch_permitted"]
    assert metadata["certification"] == "unsupported"
    for key, value, check in (("current_page_sha256", "a" * 64, "page_bytes"),
                              ("input_sha256", "b" * 64, "input_digest"),
                              ("target_ref", "https://fixture.invalid/other", "target"),
                              ("now", 1121, "age"), ("observed_at", 1020, "age")):
        changed = build_browser_package(f.run.context, f.run.db, **{**args, key: value})["metadata"]
        assert changed["binding_checks"][check] is False
        assert changed["effect_state"] == "outcome_unknown"
        assert changed["completion"] == "unverified"
    assert f.run.db.get_effect(args["effect_id"], f.actor)["state"] == "outcome_unknown"
    assert not f.run.db.dispatch_effect(args["effect_id"], f.actor, **f.fence)["dispatched_now"]
    with pytest.raises(CapabilityDenied, match="certified"):
        require_browser_certification()
    from tools.capability_broker import tool_action
    with pytest.raises(CapabilityDenied, match="certified"):
        tool_action("browser_click", {"selector": "button"})


def test_browser_generic_confirmed_record_never_certifies_completion(coding_runtime):
    f = coding_runtime
    args = browser_effect(f, "confirmed")
    metadata = build_browser_package(f.run.context, f.run.db, **args)["metadata"]
    assert metadata["effect_state"] == "confirmed"
    assert metadata["completion"] == "unverified"
    assert metadata["validator_manifest"]["current_page"] == "not_attested"
    assert metadata["validator_manifest"]["browser_completion"] == "unsupported"
    with pytest.raises(ValueError, match="Finite"):
        build_browser_package(f.run.context, f.run.db, **{**args, "observed_at": float("nan")})
