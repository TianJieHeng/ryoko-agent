"""Reviewed delivery installs immutable knowledge only in new specialist sessions."""
from dataclasses import FrozenInstanceError
import json
import threading
from types import SimpleNamespace

import pytest

from agent.agent_identity import resolve_agent_context
from agent.identity_lifecycle import agent_runtime_scope
from tests.tui_gateway.test_artifact_rpc import artifacts, denied, grants, result  # noqa: F401
from tests.tui_gateway.test_workflows_rpc import baselines, create_workflow, decision, evaluate

pytestmark = pytest.mark.platforms("linux")


def configured(rpc, label="a"):
    from tui_gateway import server
    project = rpc.project(label)
    home, agent = rpc.homes[label], rpc.agents[label]
    config = json.loads((home / "config.yaml").read_text())
    for name in ("researcher", "writer"):
        config["agent_identity"]["agents"][name] = {"policy_version": 1, "role": "specialist",
            "memory_backend": "builtin", "project_grants": [project["id"]]}
    (home / "config.yaml").write_text(json.dumps(config))
    agent.session_id = "configured-specialists"
    agent.runtime_context = resolve_agent_context(config, session_id=agent.session_id, profile_home=home)
    agent._session_db.create_session(agent.session_id, source="tui")
    agent._session_db.claim_session_agent_identity(agent.session_id, agent.runtime_context.identity.to_record())
    server._sessions["live-" + label]["session_key"] = agent.session_id
    project = result(rpc.call("runtime.project.grants.set", label, project_id=project["id"],
        expected_revision=project["revision"], grants=grants() + [
            {"principal_id": "owner", "agent_id": name, "permissions": ["read", "write", "share"]}
            for name in ("researcher", "writer")]))["project"]
    return project


def start_specialist(rpc, session_id, *, label="a", name="researcher"):
    from agent.agent_configuration import configured_agent_selection, prepare_construction, bind_startup_configuration
    from tui_gateway import server
    home, db = rpc.homes[label], rpc.agents[label]._session_db
    base = json.loads((home / "config.yaml").read_text())
    db.create_session(session_id, source="tui")
    with agent_runtime_scope(rpc.agents[label].runtime_context), configured_agent_selection(name):
        config, _ = prepare_construction(base, db, session_id)
    context = resolve_agent_context(config, session_id=session_id, profile_home=home)
    db.claim_session_agent_identity(session_id, context.identity.to_record())
    agent = SimpleNamespace(runtime_context=context, _session_db=db, session_id=session_id)
    ui_id = "live-" + session_id
    server._sessions[ui_id] = {"agent": agent, "profile_home": str(home), "transport": rpc.peers[label],
        "session_key": session_id, "history": [], "history_lock": threading.RLock()}
    values = {}
    with agent_runtime_scope(context):
        bind_startup_configuration(context, db, values)
    return agent, values


def delivery_request(row, *, revision=0, command="delivery", specialist="researcher", action="deliver"):
    return {key: row[key] for key in ("project_id", "workflow_id", "version", "sha256")} | {
        "command_id": command, "expected_delivery_revision": revision, "specialist_id": specialist, "action": action}


def deliver(rpc, request):
    prepared = result(rpc.call("runtime.workflow.delivery.prepare", **request))
    assert result(rpc.call("runtime.workflow.delivery.prepare", **request)) == prepared
    delivered = result(rpc.call("runtime.workflow.delivery.commit", **request,
        **{key: prepared[key] for key in ("approval_id", "approval_digest")}))["delivery"]
    return prepared, delivered


def rejected(rpc, request, code):
    denied(rpc.call("runtime.workflow.delivery.prepare", **request), code)
    db, sid = rpc.agents["a"]._session_db, rpc.agents["a"].session_id
    if db.read_runtime_command(sid, request["command_id"]):
        result(rpc.call("runtime.artifact.cancel", command_id=request["command_id"]))


def test_reviewed_installation_rolls_back_only_target_pin_and_freezes_resumed_prompt(artifacts):
    from agent.agent_configuration import bind_startup_configuration, configured_agent_selection, prepare_construction
    from agent.workflow_delivery import snapshot_specialist_workflows
    from tui_gateway import server

    def activation(agent, *, active, desired):
        db, sid = agent._session_db, agent.session_id
        with db._runtime_read() as conn:
            before = conn.execute("SELECT startup_json FROM agent_configuration_sessions WHERE session_id=?",
                                  (sid,)).fetchone()[0]
        status = result(artifacts.call("runtime.agent.session.get", session_id="live-" + sid))
        from tui_gateway.contracts.agent_configuration import AgentSessionConfiguration
        AgentSessionConfiguration.model_validate(status)
        assert status["agent_id"] == "researcher" and status["role"] == "specialist"
        assert status["startup_frozen"] and status["authority_current"]
        assert status["revocation_code"] is None and status["execution_authority"] is False
        assert status["activation"] == "next_session"
        fields = ("project_id", "workflow_id", "version", "sha256", "delivery_revision")
        for name, expected in (("active_workflows", active), ("desired_workflows", desired)):
            assert status[name] == [
                {field: pin[field] for field in fields} | {"workflow_state": state}
                for pin, state in expected]
        assert "Hello ${input.name}" not in json.dumps(status)
        assert not {"instructions", "prompt", "definition", "content"}.intersection(status)
        with db._runtime_read() as conn:
            assert conn.execute("SELECT startup_json FROM agent_configuration_sessions WHERE session_id=?",
                                (sid,)).fetchone()[0] == before

    project = configured(artifacts)["id"]
    # A strict specialist conversation predating configuration enrollment must
    # not gain newly installed knowledge on its first post-upgrade reconnect.
    home, primary = artifacts.homes["a"], artifacts.agents["a"]
    base = json.loads((home / "config.yaml").read_text())
    base["agent_identity"]["active_agent_id"] = "researcher"
    (home / "config.yaml").write_text(json.dumps(base))
    with configured_agent_selection("ryoko"):
        prepare_construction(base, primary._session_db, primary.session_id)
    legacy = resolve_agent_context(base, session_id="pre-feature-specialist", profile_home=home)
    primary._session_db.create_session(legacy.identity.session_id, source="tui")
    primary._session_db.claim_session_agent_identity(legacy.identity.session_id, legacy.identity.to_record())
    primary._session_db.append_message(legacy.identity.session_id, "user", content="An existing conversation")
    old_agent, old_values = start_specialist(artifacts, "before-delivery")
    assert "ephemeral_system_prompt" not in old_values
    first = create_workflow(artifacts, project)
    cases = baselines(artifacts, project)
    first = decision(artifacts, evaluate(artifacts, first, cases)["workflow"])["workflow"]
    request = delivery_request(first)
    prepared = result(artifacts.call("runtime.workflow.delivery.prepare", **request))
    denied(artifacts.call("runtime.workflow.delivery.commit", **request,
        approval_id=prepared["approval_id"], approval_digest="0" * 64), "approval_mismatch")
    assert result(artifacts.call("runtime.workflow.delivery.list", project_id=project,
                                 specialist_id="researcher"))["deliveries"] == []
    _, delivered = deliver(artifacts, request)
    assert delivered["version"] == 1 and delivered["delivery_revision"] == 1
    assert delivered["activation"] == "next_session" and delivered["execution_authority"] is False
    activation(old_agent, active=[], desired=[(delivered, "approved")])
    denied(artifacts.call("runtime.agent.session.get", session_id="live-before-delivery",
                          via=artifacts.peers["b"]))
    denied(artifacts.call("runtime.agent.session.get", session_id="live-before-delivery", agent_id="writer"))
    resumed_config, _ = prepare_construction(base, primary._session_db, legacy.identity.session_id,
                                             stored=legacy.identity.to_record())
    assert resolve_agent_context(resumed_config, session_id=legacy.identity.session_id, profile_home=home,
                                 stored_binding=legacy.identity.to_record()) == legacy
    with agent_runtime_scope(legacy):
        legacy_values = {}
        bind_startup_configuration(legacy, primary._session_db, legacy_values)
    assert "ephemeral_system_prompt" not in legacy_values
    with agent_runtime_scope(old_agent.runtime_context):
        resumed = {}
        bind_startup_configuration(old_agent.runtime_context, old_agent._session_db, resumed)
    assert resumed == old_values
    current, current_values = start_specialist(artifacts, "after-first")
    assert first["sha256"] in current_values["ephemeral_system_prompt"]
    assert "Hello ${input.name}" in current_values["ephemeral_system_prompt"]
    with agent_runtime_scope(current.runtime_context):
        snapshot = snapshot_specialist_workflows(current.runtime_context, current._session_db)
        with pytest.raises(FrozenInstanceError):
            snapshot.prompt = "changed"
    _, writer_values = start_specialist(artifacts, "separate-writer", name="writer")
    assert "ephemeral_system_prompt" not in writer_values
    second = create_workflow(artifacts, project, version=2,
        predecessor={key: first[key] for key in ("workflow_id", "version", "sha256")})
    second = decision(artifacts, evaluate(artifacts, second, cases, "evaluate-two")["workflow"],
                      command="approve-two")["workflow"]
    _, updated = deliver(artifacts, delivery_request(second, revision=1, command="deliver-two"))
    assert updated["previous_delivery_id"] == delivered["delivery_id"]
    activation(current, active=[(delivered, "approved")], desired=[(updated, "approved")])
    with agent_runtime_scope(current.runtime_context):
        resumed = {}
        bind_startup_configuration(current.runtime_context, current._session_db, resumed)
    assert resumed == current_values
    newest, newest_values = start_specialist(artifacts, "after-second")
    assert second["sha256"] in newest_values["ephemeral_system_prompt"]
    _, rolled_back = deliver(artifacts, delivery_request(first, revision=2, action="rollback", command="rollback"))
    assert rolled_back["version"] == 1 and rolled_back["delivery_revision"] == 3
    activation(newest, active=[(updated, "approved")], desired=[(rolled_back, "approved")])
    assert result(artifacts.call("runtime.workflow.get", project_id=project, workflow_id=first["workflow_id"],
                                version=1))["workflow"]["active_version"] == 2
    _, rollback_values = start_specialist(artifacts, "after-rollback")
    assert first["sha256"] in rollback_values["ephemeral_system_prompt"]
    assert second["sha256"] not in rollback_values["ephemeral_system_prompt"]
    with agent_runtime_scope(newest.runtime_context):
        resumed = {}
        bind_startup_configuration(newest.runtime_context, newest._session_db, resumed)
    assert resumed == newest_values
    latest = result(artifacts.call("runtime.workflow.get", project_id=project, workflow_id=first["workflow_id"], version=1))["workflow"]
    decision(artifacts, latest, "revoke", "revoke-one")
    activation(current, active=[(delivered, "revoked")], desired=[(rolled_back, "revoked")])
    _, revoked_values = start_specialist(artifacts, "after-revocation")
    assert "ephemeral_system_prompt" not in revoked_values
    with current._session_db._runtime_read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM workflow_runs").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM workflow_deliveries").fetchone()[0] == 3
    denied(server.dispatch({"jsonrpc": "2.0", "id": "specialist-cannot-deliver",
        "method": "runtime.workflow.delivery.prepare", "params": {"schema_version": 1,
            "session_id": "live-after-first", **delivery_request(second, command="self-deliver", specialist="writer")}},
        transport=artifacts.peers["a"]), "agent_configuration_owner_required")
    from hermes_state import SessionDB
    old_db = current._session_db
    path = old_db.db_path
    old_db.close()
    restored = SessionDB(path)
    artifacts.agents["a"]._session_db = current._session_db = restored
    with agent_runtime_scope(current.runtime_context):
        resumed = {}
        bind_startup_configuration(current.runtime_context, restored, resumed)
    assert resumed == current_values


def test_delivery_denies_stale_target_acl_lifecycle_revision_and_cross_profile(artifacts, monkeypatch):
    from hermes_state_workflow_delivery import WorkflowDeliveryRegistry
    project = configured(artifacts)
    row = create_workflow(artifacts, project["id"])
    rejected(artifacts, delivery_request(row, command="draft"), "workflow_not_approved")
    row = decision(artifacts, evaluate(artifacts, row, baselines(artifacts, project["id"]))["workflow"])["workflow"]
    rejected(artifacts, delivery_request({**row, "sha256": "0" * 64}, command="changed-digest"), "workflow_digest_mismatch")
    rejected(artifacts, delivery_request(row, command="wrong-revision", revision=1), "workflow_delivery_revision_conflict")
    rejected(artifacts, delivery_request(row, command="target-primary", specialist="ryoko"), "workflow_delivery_target_invalid")
    rejected(artifacts, delivery_request(row, command="foreign-target", specialist="unknown"), "workflow_delivery_project_denied")
    rejected(artifacts, delivery_request(row, command="never-delivered", action="rollback"), "workflow_delivery_rollback_invalid")
    request = delivery_request(row, command="review-target")
    prepared = result(artifacts.call("runtime.workflow.delivery.prepare", **request))
    target = result(artifacts.call("runtime.agent.get", agent_id="researcher"))["agent"]
    result(artifacts.call("runtime.agent.update", agent_id="researcher", expected_revision=target["revision"],
        config={**target["config"], "instructions": "Changed while approval pending"}))
    denied(artifacts.call("runtime.workflow.delivery.commit", **request,
        **{key: prepared[key] for key in ("approval_id", "approval_digest")}), "approval_mismatch")
    result(artifacts.call("runtime.artifact.cancel", command_id=request["command_id"]))
    request = delivery_request(row, command="review-acl")
    prepared = result(artifacts.call("runtime.workflow.delivery.prepare", **request))
    project = result(artifacts.call("runtime.project.grants.set", project_id=project["id"],
        expected_revision=project["revision"], grants=grants()))["project"]
    denied(artifacts.call("runtime.workflow.delivery.commit", **request,
        **{key: prepared[key] for key in ("approval_id", "approval_digest")}), "workflow_delivery_project_denied")
    result(artifacts.call("runtime.artifact.cancel", command_id=request["command_id"]))
    project = result(artifacts.call("runtime.project.grants.set", project_id=project["id"], expected_revision=project["revision"],
        grants=grants()+[{"principal_id": "owner", "agent_id": "researcher", "permissions": ["read"]}]))["project"]
    prepared, installed = deliver(artifacts, delivery_request(row, command="valid-delivery"))
    assert installed["sha256"] == row["sha256"]
    # Real A -> B -> A scope switching cannot turn a project ID or matching
    # specialist name into a cross-profile/harness read or installation grant.
    configured(artifacts, "b")
    denied(artifacts.call("runtime.workflow.delivery.list", "b", project_id=project["id"], specialist_id="researcher"),
           "project_not_granted")
    assert result(artifacts.call("runtime.workflow.delivery.list", project_id=project["id"],
                                specialist_id="researcher"))["deliveries"] == [installed]
    agent = artifacts.agents["a"]
    with agent_runtime_scope(agent.runtime_context):
        from hermes_state_runtime import RuntimeStoreError
        with pytest.raises(RuntimeStoreError) as error:
            WorkflowDeliveryRegistry(agent.runtime_context, artifacts.agents["b"]._session_db)
        assert error.value.code == "identity_mismatch"
    # A source mutation inside approval resolution must be caught by the final
    # transactional recheck, after all read-side validation succeeded.
    next_row = create_workflow(artifacts, project["id"], version=2,
        predecessor={key: row[key] for key in ("workflow_id", "version", "sha256")})
    next_row = decision(artifacts, evaluate(artifacts, next_row, baselines(artifacts, project["id"], suffix="two"),
        "eval-two")["workflow"], command="approve-two")["workflow"]
    request = delivery_request(next_row, command="race", revision=1)
    prepared = result(artifacts.call("runtime.workflow.delivery.prepare", **request))
    db = agent._session_db
    original = db.resolve_effect_approval
    def revoke_after_approval(*args, **kwargs):
        approved = original(*args, **kwargs)
        db._execute_write(lambda conn: conn.execute("UPDATE workflow_versions SET state='revoked',revision=revision+1 WHERE version=2"))
        return approved
    monkeypatch.setattr(db, "resolve_effect_approval", revoke_after_approval)
    denied(artifacts.call("runtime.workflow.delivery.commit", **request,
        **{key: prepared[key] for key in ("approval_id", "approval_digest")}), "workflow_not_approved")
    assert result(artifacts.call("runtime.workflow.delivery.list", project_id=project["id"],
                                specialist_id="researcher"))["deliveries"] == [installed]
    with db._runtime_read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM workflow_deliveries").fetchone()[0] == 1
    # A damaged or incorrectly copied pin must not become startup instructions,
    # even when its outer row still names the correct specialist.
    def corrupt_pin(conn):
        saved = json.loads(conn.execute("SELECT record_json FROM workflow_deliveries").fetchone()[0])
        saved["delivery"]["sha256"] = saved["scope"]["sha256"] = "0" * 64
        conn.execute("UPDATE workflow_deliveries SET record_json=?", (json.dumps(saved),))
    db._execute_write(corrupt_pin)
    denied(artifacts.call("runtime.workflow.delivery.list", project_id=project["id"], specialist_id="researcher"),
           "workflow_delivery_changed")
    with pytest.raises(RuntimeStoreError) as error:
        start_specialist(artifacts, "inconsistent-pin")
    assert error.value.code == "workflow_delivery_changed"


def test_inherited_model_authority_cannot_self_install_via_service_or_owned_rpc(artifacts):
    from agent.result_artifacts import artifact_actor
    from agent.runtime_commands import RuntimeRun, bind_runtime_run, reset_runtime_run
    from hermes_state_runtime import RuntimeStoreError
    from hermes_state_workflow_delivery import WorkflowDeliveryRegistry
    project = configured(artifacts)
    row = create_workflow(artifacts, project["id"])
    agent = artifacts.agents["a"]
    db, context, sid = agent._session_db, agent.runtime_context, agent.session_id
    actor = artifact_actor(context)
    receipt = db.submit_runtime_command(sid, actor, {"schema_version": 1,
        "command_id": "model", "idempotency_key": "model", "operation": "submit", "payload": {"text": "Install a procedure"}})
    assert db.try_acquire_session_turn_lease(sid, "model-owner")
    fence = {"holder": "model-owner", "generation": db.get_session_turn_lease(sid)["generation"]}
    assert db.claim_runtime_command(sid, "model", **fence)
    run = RuntimeRun(agent=agent, db=db, context=context, session_id=sid, command_id="model",
                     run_id=receipt["run_id"], **fence)
    request = delivery_request(row, command="self-install")
    with agent_runtime_scope(context):
        registry = WorkflowDeliveryRegistry(context, db)
        token = bind_runtime_run(run)
        try:
            with pytest.raises(RuntimeStoreError) as error:
                registry.prepare(run, {key: value for key, value in request.items() if key != "command_id"})
            assert error.value.code == "workflow_human_control_required"
            denied(artifacts.call("runtime.workflow.delivery.prepare", **request), "artifact_control_required")
            denied(artifacts.call("runtime.workflow.delivery.commit", **request,
                approval_id="forged", approval_digest="0" * 64), "artifact_control_required")
        finally:
            reset_runtime_run(token, run)
    assert db.read_runtime_command(sid, "self-install") is None
    with db._runtime_read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM workflow_deliveries").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM runtime_effect_approvals").fetchone()[0] == 0
    db.finish_runtime_command(sid, "model", **fence, result={"installation_denied": True})
    db.release_session_turn_lease(sid, **fence)
