"""Sequential missions retain immutable history and reject ambiguous stale controls."""
import json
import pytest
from tests.tui_gateway.test_runtime_rpc import runtime  # noqa: F401


def result(value):
    assert "result" in value, value
    return value["result"]


def create(runtime, mission_id="first", **params):
    return result(runtime.call("runtime.mission.create", mission_id=mission_id,
        contract={"outcome": "Produce " + mission_id}, **params))["mission"]


def test_replace_terminal_mission_preserves_history_and_exact_controls(runtime):
    first = create(runtime)
    rejected = runtime.call("runtime.mission.create", mission_id="second", contract={"outcome": "Second"},
        previous_mission_id="first", previous_revision=first["revision"])
    assert rejected["error"]["data"]["code"] == "mission_not_terminal"
    cancelled = result(runtime.call("runtime.mission.cancel", mission_id="first", expected_revision=1))["mission"]
    second = create(runtime, "second", previous_mission_id="first", previous_revision=cancelled["revision"])
    assert second["mission_id"] == "second" and second["revision"] == 1
    previous = result(runtime.call("runtime.mission.get", mission_id="first"))["mission"]
    assert previous["archived"] and previous["state"] == "cancelled" and previous["revision"] == cancelled["revision"]
    assert result(runtime.call("runtime.mission.get"))["mission"]["mission_id"] == "second"
    assert result(runtime.call("runtime.mission.get", mission_id="missing"))["mission"] is None
    history = result(runtime.call("runtime.mission.history"))
    assert {row["mission_id"] for row in history["missions"]} == {"first", "second"}
    assert not history["complete"]
    for operation in ("pause", "resume", "cancel", "verify", "accept", "revise"):
        params = {"contract": {"outcome": "stale overwrite"}} if operation == "revise" else {}
        wrong = runtime.call("runtime.mission." + operation, mission_id="first", expected_revision=1, **params)
        assert wrong["error"]["data"]["code"] == "mission_identity_conflict"
        ambiguous = runtime.call("runtime.mission." + operation, expected_revision=1, **params)
        assert ambiguous["error"]["data"]["code"] == "mission_id_required"
    assert result(runtime.call("runtime.mission.get", mission_id="first"))["mission"] == previous
    current = result(runtime.call("runtime.mission.get", mission_id="second"))["mission"]
    assert all(current[key] == value for key, value in second.items())
    paused = result(runtime.call("runtime.mission.pause", mission_id="second", expected_revision=1))["mission"]
    assert paused["state"] == "paused"
    assert runtime.call("runtime.mission.history", via=runtime.peers["b"])["error"]["code"] == 4001
    assert result(runtime.call("runtime.mission.history", "b"))["missions"] == []
    assert runtime.dispatched == []


def test_replacement_cas_and_failure_roll_back_archive(runtime):
    create(runtime)
    cancelled = result(runtime.call("runtime.mission.cancel", mission_id="first", expected_revision=1))["mission"]
    bad_revision = runtime.call("runtime.mission.create", mission_id="next", contract={"outcome": "Next"},
        previous_mission_id="first", previous_revision=1)
    assert bad_revision["error"]["data"]["code"] == "revision_conflict"
    # Reusing an archived ID must roll back the entire archive/swap transaction.
    reused = runtime.call("runtime.mission.create", mission_id="first", contract={"outcome": "Replace history"},
        previous_mission_id="first", previous_revision=cancelled["revision"])
    assert reused["error"]["data"]["code"] == "mission_identity_conflict"
    history = result(runtime.call("runtime.mission.history"))["missions"]
    assert len(history) == 1 and not history[0]["archived"]


def test_accepted_work_prevents_replacement_without_relabeling(runtime):
    from agent.result_artifacts import artifact_actor
    agent = runtime.agents["a"]
    create(runtime)
    cancelled = result(runtime.call("runtime.mission.cancel", mission_id="first", expected_revision=1))["mission"]
    actor = artifact_actor(agent.runtime_context)
    agent._session_db.submit_runtime_command(agent.session_id, actor, {"schema_version": 1, "operation": "submit",
        "command_id": "accepted", "idempotency_key": "accepted", "payload": {"text": "Old mission continuation"}})
    blocked = runtime.call("runtime.mission.create", mission_id="next", contract={"outcome": "Next"},
        previous_mission_id="first", previous_revision=cancelled["revision"])
    assert blocked["error"]["data"]["code"] == "mission_owner_busy"
    assert agent._session_db.read_runtime_command(agent.session_id, "accepted")["status"] == "accepted"
    assert result(runtime.call("runtime.mission.get"))["mission"]["mission_id"] == "first"
