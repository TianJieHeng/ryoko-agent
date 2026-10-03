"""Exact approval display and lost-response recovery never issue another decision."""
import json
import time
from dataclasses import replace
import pytest

from agent.identity_lifecycle import agent_runtime_scope
from agent.result_artifacts import artifact_actor
from agent.runtime_commands import bind_runtime_run, reset_runtime_run
from hermes_state import SessionDB
from tools import capability_broker as broker
from tests.tui_gateway.test_runtime_rpc import runtime  # noqa: F401
from tests.tui_gateway.test_runtime_effects_rpc import _run, _approval


def preview(runtime, arguments=None):
    from agent.agent_identity import resolve_agent_context
    agent = runtime.agents["a"]
    config_path = runtime.homes["a"] / "config.yaml"
    raw = json.loads(config_path.read_text())
    raw["agent_identity"]["agents"]["ryoko"]["allowed_tools"] = ["todo_list"]
    config_path.write_text(json.dumps(raw))
    agent.runtime_context = resolve_agent_context(raw, session_id=agent.session_id, profile_home=runtime.homes["a"])
    agent._session_db.patch_session_model_config(agent.session_id, {"agent_identity": agent.runtime_context.identity.to_record()})
    run = _run(runtime)
    token = bind_runtime_run(run)
    try:
        with agent_runtime_scope(run.context):
            action = broker.tool_action("todo_list", arguments or {"action": "read", "target": "private exact target"})
            approval = broker.preview_action(action)
    finally:
        reset_runtime_run(token, run)
    run.agent._active_runtime_run = run
    return run, action, approval


def test_exact_detail_is_owned_and_decision_survives_ended_run(runtime):
    run, action, approval = preview(runtime)
    response = runtime.call("runtime.approval.get", approval_id=approval.approval_id)
    assert "result" in response, response
    detail = response["result"]
    assert detail["detail"]["reviewable"]
    assert detail["detail"]["review"]["action"] == action.to_record()
    assert detail["detail"]["review_digest"]
    assert detail["decision"]["choice"] is None
    assert "private exact target" not in json.dumps(runtime.call("runtime.approvals.list"))
    decision = runtime.call("runtime.approval.resolve", approval_id=approval.approval_id,
        approval_digest=approval.approval_digest, choice="once")["result"]
    run.agent._active_runtime_run = None
    run.db.release_session_turn_lease(run.session_id, run.holder)
    recovered = runtime.call("runtime.approval.get", approval_id=approval.approval_id)["result"]
    assert recovered["decision"]["choice"] == "once"
    assert recovered["approval"]["status"] == "approved" and not recovered["dispatch_performed"]
    assert recovered["detail"] == detail["detail"]
    with SessionDB(run.db.db_path) as reopened:
        from hermes_state_approval_reviews import get_approval_detail
        assert get_approval_detail(reopened, approval.approval_id, artifact_actor(run.context))[1] == detail["detail"]
    assert runtime.call("runtime.approval.get", approval_id=approval.approval_id, via=runtime.peers["b"])["error"]["code"] == 4001
    assert runtime.call("runtime.approval.get", "b", approval_id=approval.approval_id)["error"]["data"]["code"] == "approval_not_found"
    assert runtime.dispatched == []


@pytest.mark.parametrize("arguments", [{"password": "plainpassword"}, {"target": "https://site.invalid/?token=abc"},
                                       {"text": "sk-aaaaaaaaaaaaaaaaaaaaaaaaaaaa"}])
def test_sensitive_content_is_withheld_not_misrepresented_as_exact(runtime, arguments):
    run, action, approval = preview(runtime, arguments)
    response = runtime.call("runtime.approval.get", approval_id=approval.approval_id)
    assert "result" in response, response
    detail = response["result"]
    assert not detail["detail"]["reviewable"]
    assert detail["detail"]["unavailable_reason"] == "sensitive_content"
    assert detail["detail"]["review"] is None
    with run.db._runtime_read() as conn:
        stored = conn.execute("SELECT review_json FROM runtime_approval_reviews WHERE approval_id=?", (approval.approval_id,)).fetchone()[0]
    assert action.input_json not in stored
    assert all(value not in json.dumps(detail) for value in arguments.values())


def test_missing_legacy_review_is_explicit_and_never_invented(runtime):
    approval = _approval(_run(runtime))
    detail = runtime.call("runtime.approval.get", approval_id=approval["approval_id"])["result"]
    assert not detail["detail"]["reviewable"]
    assert detail["detail"]["unavailable_reason"] == "review_not_retained"
    assert detail["input_revision"] is None
    assert "/private/" not in json.dumps(detail)
    assert runtime.call("runtime.approval.get", approval_id="missing")["error"]["data"]["code"] == "approval_not_found"


def test_original_decision_survives_later_mission_invalidation(runtime):
    run, action, approval = preview(runtime)
    actor = artifact_actor(run.context)
    accepted = runtime.call("runtime.approval.resolve", approval_id=approval.approval_id,
        approval_digest=approval.approval_digest, choice="once")["result"]["approval"]
    mission = run.db.create_mission(run.session_id, actor, holder=run.holder, generation=run.generation,
        contract={"outcome": "Original work"})
    binding = run.db.get_effect_approval(approval.approval_id, actor)["binding"]
    run.db.update_mission(run.session_id, actor, holder=run.holder, generation=run.generation,
        expected_revision=mission["revision"], changes={"next_step": "Updated input"},
        changed_inputs=[binding["input_digest"]])
    before = run.db.read_runtime_snapshot(run.session_id)["revision"]
    recovered = runtime.call("runtime.approval.get", approval_id=approval.approval_id)["result"]
    assert recovered["approval"]["status"] == "invalidated"
    assert recovered["decision"]["choice"] == "once"
    assert recovered["decision"]["resolved_at"] == accepted["resolved_at"]
    assert run.db.read_runtime_snapshot(run.session_id)["revision"] == before


from tests.tui_gateway.test_artifact_rpc import artifacts, result  # noqa: E402,F401


@pytest.mark.platforms("linux")
def test_retained_artifact_review_has_exact_unpublished_bytes(artifacts):
    import base64
    import hashlib
    project = artifacts.project()["id"]
    body = "# Exact proposal\nUnicode: café ☀\n"
    prepared = result(artifacts.call("runtime.artifact.prepare", project_id=project,
        command_id="review", request_id="review", content=body))
    detail = result(artifacts.call("runtime.approval.get", approval_id=prepared["approval_id"]))
    assert detail["detail"]["reviewable"]
    content = detail["detail"]["review"]["content"]
    assert base64.b64decode(content["data"]) == body.encode()
    assert content["sha256"] == prepared["sha256"] == hashlib.sha256(body.encode()).hexdigest()
    assert detail["approval"]["status"] == "pending"
    assert not detail["dispatch_performed"]


def test_review_binding_and_corruption_fail_without_fabricating_exact_content(runtime):
    from hermes_state_runtime import RuntimeStoreError
    from copy import deepcopy
    run, action, approval = preview(runtime)
    actor = artifact_actor(run.context)
    binding = run.db.get_effect_approval(approval.approval_id, actor)["binding"]
    forged = deepcopy(action.to_record())
    forged["arguments"]["target"] = "changed target"
    with pytest.raises(RuntimeStoreError) as mismatch:
        run.db.request_effect_approval(actor=actor, expires_at=time.time() + 60,
            approval_id="changed-review", review={"action": forged, "content": None}, **binding)
    assert mismatch.value.code == "approval_review_mismatch"
    assert runtime.call("runtime.approval.get", approval_id="changed-review")["error"]["data"]["code"] == "approval_not_found"
    run.db._write_sql("UPDATE runtime_approval_reviews SET review_json=? WHERE approval_id=?",
                     ('{"reviewable":true,"review":{"injected":true}}', approval.approval_id))
    corrupted = runtime.call("runtime.approval.get", approval_id=approval.approval_id)
    assert corrupted["error"]["data"]["code"] == "approval_review_mismatch"
    assert "injected" not in json.dumps(corrupted)
