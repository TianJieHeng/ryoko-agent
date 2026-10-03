"""Pause is a persisted owner/profile fence, never a rollback or ownership move."""
import json
import pytest

from hermes_state import SessionDB
from hermes_state_runtime import RuntimeStoreError
from hermes_state_runtime_controls import RuntimeOwnerControls, assert_owner_running
from agent.result_artifacts import artifact_actor
from tests.tui_gateway.test_runtime_rpc import runtime, envelope  # noqa: F401
from tests.tui_gateway.test_runtime_effects_rpc import _run, _effect


def test_pause_recovery_idempotency_and_profile_isolation(runtime):
    before = runtime.call("runtime.control.get")["result"]
    assert before["control"]["revision"] == 0
    paused = runtime.call("runtime.control.pause", operation_id="pause-1", expected_revision=0)["result"]
    assert paused["control"]["paused"] and paused["control"]["accepted_work_retained"]
    assert paused["control"]["scheduled_dispatch_blocked"]
    assert not paused["control"]["provider_cancelled"] and not paused["control"]["remote_effects_undone"]
    assert runtime.call("runtime.control.pause", operation_id="pause-1", expected_revision=0)["result"] == paused
    assert runtime.call("runtime.control.resume", operation_id="pause-1", expected_revision=0)["error"]["data"]["code"] == "idempotency_conflict"
    assert runtime.call("runtime.control.resume", operation_id="resume-stale", expected_revision=0)["error"]["data"]["code"] == "revision_conflict"
    assert not runtime.call("runtime.control.get", "b")["result"]["control"]["paused"]
    assert runtime.call("runtime.control.get", operation_id="pause-1", via=runtime.peers["b"])["error"]["code"] == 4001
    assert runtime.call("runtime.command", **envelope())["error"]["data"]["code"] == "owner_paused"
    agent = runtime.agents["a"]
    with SessionDB(agent._session_db.db_path) as reopened:
        control = RuntimeOwnerControls(reopened, artifact_actor(agent.runtime_context))
        assert control.get("pause-1") == paused
    resumed = runtime.call("runtime.control.resume", operation_id="resume-1", expected_revision=1)["result"]
    assert not resumed["control"]["paused"]
    recovered = runtime.call("runtime.control.get", operation_id="pause-1")["result"]
    assert recovered["operation"] == paused["operation"]
    assert not recovered["control"]["paused"]
    assert runtime.call("runtime.control.get", operation_id="missing")["result"]["operation"] is None
    assert runtime.dispatched == []


def test_inflight_dispatch_is_fenced_but_receipts_and_ownership_survive(runtime):
    from agent.identity_lifecycle import agent_runtime_scope
    from agent.runtime_commands import bind_runtime_run, reset_runtime_run, assert_runtime_dispatch
    run = _run(runtime)
    effect, _, _ = _effect(run)
    actor = artifact_actor(run.context)
    before = run.db.read_runtime_command(run.session_id, run.command_id)
    paused = runtime.call("runtime.control.pause", operation_id="pause", expected_revision=0)["result"]
    assert paused["control"]["claimed_commands"] == 1
    assert run.db.read_runtime_command(run.session_id, run.command_id) == before
    token = bind_runtime_run(run)
    try:
        with agent_runtime_scope(run.context), pytest.raises(RuntimeStoreError) as failure:
            assert_runtime_dispatch()
        assert failure.value.code == "owner_paused"
    finally:
        reset_runtime_run(token, run)
    run.db.record_effect_outcome(effect["effect_id"], actor, holder=run.holder, generation=run.generation,
                                state="confirmed", receipt={"sha256": "a" * 64})
    assert run.db.get_effect(effect["effect_id"], actor)["state"] == "confirmed"
    other_specialist = {**actor, "agent_id": "specialist"}
    with pytest.raises(RuntimeStoreError):
        assert_owner_running(run.db, other_specialist)
    assert runtime.dispatched == []


@pytest.mark.parametrize("forged", [{"profile": "b"}, {"agent_id": "other"}, {"paused": True}, {"expected_revision": True}])
def test_pause_rejects_client_authority(runtime, forged):
    result = runtime.call("runtime.control.pause", operation_id="no", expected_revision=0, **{
        key: value for key, value in forged.items() if key != "expected_revision"}) if "expected_revision" not in forged else runtime.call(
            "runtime.control.pause", operation_id="no", **forged)
    assert result["error"]["code"] == 4000
    assert not runtime.call("runtime.control.get")["result"]["control"]["paused"]
