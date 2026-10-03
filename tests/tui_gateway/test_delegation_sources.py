"""Delegation methods/inputs traverse the actual artifact ACL and mission stores."""
from types import SimpleNamespace

import pytest

from tests.tui_gateway.test_artifact_rpc import artifacts, publish, result  # noqa: F401
from tests.agent.test_durable_delegation import make_run, policy
from agent.budget_account import parse_budget_policy
from agent.identity_lifecycle import agent_runtime_scope
from agent.runtime_commands import bind_runtime_run, reset_runtime_run
from agent.delegation_runtime import _verify_references, _stage_inputs


def test_exact_sources_cannot_cross_child_profile_or_parent_mission(artifacts, tmp_path):
    first, second = artifacts.project()["id"], artifacts.project()["id"]
    one = publish(artifacts, {"project_id": first, "command_id": "source-one", "request_id": "one", "content": "# Exact methods\n"})
    two = publish(artifacts, {"project_id": second, "command_id": "source-two", "request_id": "two", "content": "# Other project\n"})
    result(artifacts.call("runtime.mission.create", mission_id="source-mission", contract={"outcome": "Analyze accepted source", "project_id": first, "risk": "low", "uncertainty": "low",
        "acceptance": [{"criterion_id": "accepted", "kind": "user_acceptance"}]}))
    agent = artifacts.agents["a"]
    run = make_run(agent._session_db, agent.runtime_context, parse_budget_policy({"runtime_budget": policy()}), holder="source-owner")
    child = SimpleNamespace(runtime_context=run.context)
    task = {"artifacts": [{"id": one["artifact_id"], "version": one["version"], "sha256": one["sha256"]}]}
    with agent_runtime_scope(run.context):
        token = bind_runtime_run(run)
        try:
            _verify_references(run, child, task)
            workspace = _stage_inputs(run, child, task, tmp_path)
            assert (workspace.inputs / "artifact-0").read_bytes() == b"# Exact methods\n"
            from hermes_state_artifacts import ArtifactStoreError
            with pytest.raises(ArtifactStoreError) as denied:
                _verify_references(run, SimpleNamespace(runtime_context=artifacts.agents["b"].runtime_context), task)
            assert denied.value.code == "identity_mismatch"
            with pytest.raises(PermissionError, match="mission scope"):
                _verify_references(run, child, {"artifacts": [{"id": two["artifact_id"], "version": two["version"], "sha256": two["sha256"]}]})
            with pytest.raises(ValueError, match="digest/version"):
                _verify_references(run, child, {"artifacts": [{"id": one["artifact_id"], "version": one["version"], "sha256": "f" * 64}]})
        finally:
            reset_runtime_run(token, run)
            run.agent.client.close()
