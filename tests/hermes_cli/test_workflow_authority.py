"""BE11 model self-promotion and exact public executable-admission boundaries."""
from types import SimpleNamespace

import pytest

from agent.identity_lifecycle import agent_runtime_scope
from agent.result_artifacts import artifact_actor
from hermes_cli.workflows import commit_decision, prepare_decision, resolve_executable
from hermes_state_runtime import RuntimeStoreError
from tests.tui_gateway.test_artifact_rpc import artifacts, denied, grants, result  # noqa: F401
from tests.tui_gateway.test_workflows_rpc import baselines, create_workflow, decision, evaluate
from tools.capability_broker import CapabilityDenied

pytestmark = pytest.mark.platforms("linux")


def _reference(row):
    return {key: row[key] for key in ("project_id", "workflow_id", "version", "sha256")}


def _reject(code, action, error_type=RuntimeStoreError):
    with pytest.raises(error_type) as error:
        action()
    assert error.value.code == code


@pytest.fixture
def offline_client():
    import httpx
    from openai import OpenAI

    transport = httpx.MockTransport(lambda request: pytest.fail("Authority checks must not invoke inference"))
    with OpenAI(api_key="fixture-only", base_url="https://fixture.invalid/v1",
                http_client=httpx.Client(transport=transport, trust_env=False)) as client:
        yield client


def test_live_model_run_cannot_self_promote_directly_or_through_owned_rpc(artifacts, offline_client):
    from agent.runtime_commands import RuntimeRun, bind_runtime_run, reset_runtime_run, assert_runtime_dispatch

    project = artifacts.project()["id"]
    row = create_workflow(artifacts, project)
    row = evaluate(artifacts, row, baselines(artifacts, project))["workflow"]
    agent = artifacts.agents["a"]
    agent.client, agent.api_mode = offline_client, "chat_completions"
    db, context, sid = agent._session_db, agent.runtime_context, agent.session_id
    actor = artifact_actor(context)
    receipt = db.submit_runtime_command(sid, actor, {
        "schema_version": 1, "command_id": "model", "idempotency_key": "model",
        "operation": "submit", "payload": {"text": "Try to promote this procedure"},
    })
    assert db.try_acquire_session_turn_lease(sid, "model-owner")
    fence = dict(holder="model-owner", generation=db.get_session_turn_lease(sid)["generation"])
    assert db.claim_runtime_command(sid, "model", **fence)
    run = RuntimeRun(agent=agent, db=db, session_id=sid, command_id="model", run_id=receipt["run_id"],
                     context=context, **fence)
    request = {**_reference(row), "action": "approve", "recipient": None,
               "expected_revision": row["revision"], "expected_head_revision": row["head_revision"]}
    with db._runtime_read() as conn:
        approvals_before = conn.execute("SELECT COUNT(*) FROM runtime_effect_approvals").fetchone()[0]
    with agent_runtime_scope(context):
        token = bind_runtime_run(run)
        try:
            assert assert_runtime_dispatch(agent) is run
            _reject("workflow_human_control_required", lambda: prepare_decision(run, request))
            _reject("workflow_human_control_required", lambda: commit_decision(run, request, "forged", "0" * 64))
            # Even a genuine owned UI transport does not launder inherited
            # inference authority into a human approval context.
            denied(artifacts.call("runtime.workflow.decision.prepare", command_id="self-promote", **request),
                   "artifact_control_required")
            denied(artifacts.call("runtime.workflow.decision.commit", command_id="self-promote", **request,
                                 approval_id="forged", approval_digest="0" * 64), "artifact_control_required")
        finally:
            reset_runtime_run(token, run)
    assert db.read_runtime_command(sid, "self-promote") is None
    with db._runtime_read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM runtime_effect_approvals").fetchone()[0] == approvals_before
        assert conn.execute("SELECT COUNT(*) FROM workflow_decisions").fetchone()[0] == 0
    read = {key: row[key] for key in ("project_id", "workflow_id", "version")}
    assert result(artifacts.call("runtime.workflow.get", **read))["workflow"] == row
    db.finish_runtime_command(sid, "model", **fence, result={"self_promotion_denied": True})
    db.release_session_turn_lease(sid, **fence)
    approved = decision(artifacts, row)["workflow"]
    assert approved["state"] == "approved" and approved["sha256"] == row["sha256"]


def test_public_resolver_requires_exact_digest_approved_state_and_live_write_acl_across_profiles(artifacts):
    from agent.secret_scope import set_multiplex_active

    set_multiplex_active(True)
    rows, projects = {}, {}
    for label in ("a", "b"):
        rpc = SimpleNamespace(call=lambda method, *ignored, _label=label, **params: artifacts.call(method, _label, **params))
        projects[label] = artifacts.project(label)
        row = create_workflow(rpc, projects[label]["id"])
        agent = artifacts.agents[label]
        for state in ("draft", "tested"):
            assert row["state"] == state
            with agent_runtime_scope(agent.runtime_context):
                _reject("workflow_not_approved", lambda: resolve_executable(
                    agent.runtime_context, agent._session_db, **_reference(row)))
            if state == "draft":
                row = evaluate(rpc, row, baselines(rpc, row["project_id"]))["workflow"]
        rows[label] = decision(rpc, row)["workflow"]

    for label in ("a", "b", "a"):
        agent, row = artifacts.agents[label], rows[label]
        with agent_runtime_scope(agent.runtime_context):
            resolved = resolve_executable(agent.runtime_context, agent._session_db, **_reference(row))
            assert resolved.digest == row["sha256"] and resolved.project_id == projects[label]["id"]
            _reject("workflow_digest_mismatch", lambda: resolve_executable(
                agent.runtime_context, agent._session_db, **{**_reference(row), "sha256": "0" * 64}))
            foreign = rows["b" if label == "a" else "a"]
            _reject("project_not_granted", lambda: resolve_executable(
                agent.runtime_context, agent._session_db, **_reference(foreign)), CapabilityDenied)

    project = projects["a"]
    readonly = [{**grant, "permissions": ["read"]} for grant in grants()]
    project = result(artifacts.call("runtime.project.grants.set", project_id=project["id"],
                                   expected_revision=project["revision"], grants=readonly))["project"]
    agent, row = artifacts.agents["a"], rows["a"]
    assert result(artifacts.call("runtime.workflow.get", **{key: row[key] for key in
                                                          ("project_id", "workflow_id", "version")}))["workflow"] == row
    with agent_runtime_scope(agent.runtime_context):
        _reject("project_grant_revoked", lambda: resolve_executable(
            agent.runtime_context, agent._session_db, **_reference(row)), CapabilityDenied)
    result(artifacts.call("runtime.project.grants.set", project_id=project["id"],
                          expected_revision=project["revision"], grants=grants()))
    for label, action in (("a", "revoke"), ("b", "deprecate")):
        rpc = SimpleNamespace(call=lambda method, *ignored, _label=label, **params: artifacts.call(method, _label, **params))
        row = decision(rpc, rows[label], action=action, command=action)["workflow"]
        agent = artifacts.agents[label]
        with agent_runtime_scope(agent.runtime_context):
            _reject("workflow_not_approved", lambda: resolve_executable(
                agent.runtime_context, agent._session_db, **_reference(row)))
