"""Real local publication, durable crash recovery, and read-only scope fencing."""
import contextvars
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from openai import OpenAI

from agent.agent_identity import resolve_agent_context
from agent.effect_reconciler import reconcile_effect
from agent.identity_lifecycle import agent_runtime_scope
from agent.result_artifacts import (
    ArtifactConflict, artifact_actor, publish_result_artifact, read_result_artifact,
    result_artifact_descriptor,
)
from agent.runtime_commands import (
    RuntimeFenceError, RuntimeRun, assert_runtime_dispatch, bind_runtime_run, reset_runtime_run,
)
from hermes_state import SessionDB
from tools.capability_broker import CapabilityDenied, invoke_effect_dispatch

pytestmark = pytest.mark.platforms("linux")


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    raw = {"agent_identity": {"schema_version": 1, "principal_id": "owner",
        "profile_id": "profile", "primary_agent_id": "primary", "active_agent_id": "primary",
        "agents": {"primary": {"policy_version": 1, "role": "primary", "memory_backend": "personal_mcp",
                              "allowed_tools": []}}}}
    (tmp_path / "config.yaml").write_text(json.dumps(raw))
    context = resolve_agent_context(raw, session_id="session", profile_home=tmp_path)
    actor = artifact_actor(context)
    db = SessionDB(tmp_path / "state.db")
    db.create_session("session", source="cli")
    db.claim_session_agent_identity("session", context.identity.to_record())
    command = {"schema_version": 1, "command_id": "command", "idempotency_key": "command",
               "expected_revision": None, "operation": "submit", "payload": {"text": "fixture"},
               "identity_binding": actor}
    receipt = db.submit_runtime_command("session", actor=actor, command=command)
    assert db.acquire_session_turn_lease("session", "owner", wait_seconds=0)
    generation = db.get_session_turn_lease("session")["generation"]
    assert db.claim_runtime_command("session", "command", holder="owner", generation=generation)
    client = OpenAI(api_key="fixture-only", base_url="https://fixture.invalid/v1",
                    http_client=httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(500))))
    agent = SimpleNamespace(api_mode="chat_completions", provider="openai", client=client, runtime_context=context)
    run = RuntimeRun(agent, db, "session", "command", receipt["run_id"], "owner", generation, context)
    with agent_runtime_scope(context):
        token = bind_runtime_run(run)
        try:
            yield SimpleNamespace(run=run, db=db, context=context, actor=actor, home=tmp_path, raw=raw)
        finally:
            reset_runtime_run(token, run)
    client.close()
    db.close()


def effects(runtime):
    return runtime.db.list_effects("session", runtime.actor)


def reconcile(runtime, effect_id, **kwargs):
    return reconcile_effect(runtime.db, effect_id, context=runtime.context,
        holder=kwargs.pop("holder", "owner"), generation=kwargs.pop("generation", runtime.run.generation),
        deadline_at=kwargs.pop("deadline_at", time.time() + 30), **kwargs)


def test_publication_is_real_immutable_idempotent_and_scoped(runtime, monkeypatch):
    from agent import effect_reconciler
    real_write = effect_reconciler._publish_bytes
    invoked = []
    def checked_write(*args):
        rows = effects(runtime)
        assert len(rows) == 1 and rows[0]["state"] == "dispatched"
        invoked.append(True)
        return real_write(*args)
    monkeypatch.setattr(effect_reconciler, "_publish_bytes", checked_write)
    payload = b'{"answer":"durable result"}'
    descriptor = publish_result_artifact(runtime.run, payload, "result-one")
    path = runtime.home / descriptor["locator"]
    assert path.read_bytes() == payload
    assert read_result_artifact(runtime.context, descriptor) == payload
    inode = path.stat().st_ino
    assert publish_result_artifact(runtime.run, payload, "result-one") == descriptor
    assert len(invoked) == 1 and path.stat().st_ino == inode
    assert effects(runtime)[0]["state"] == "confirmed"
    assert effects(runtime)[0]["provider_idempotency"] == "supported"
    with pytest.raises(ValueError, match="different action"):
        publish_result_artifact(runtime.run, b"changed", "result-one")
    assert path.read_bytes() == payload
    with pytest.raises(CapabilityDenied, match="No durable adapter"):
        invoke_effect_dispatch("opaque_mutation", run=runtime.run, input_ref=descriptor,
                               payload=payload, operation_id="opaque", intent_key="opaque")
    assert len(invoked) == 1
    with pytest.raises(ArtifactConflict, match="locator"):
        read_result_artifact(runtime.context, {**descriptor, "locator": "../../private"})
    (runtime.home / "config.yaml").write_text(json.dumps({**runtime.raw, "agent_identity": None}))
    with pytest.raises((CapabilityDenied, ValueError, PermissionError)):
        read_result_artifact(runtime.context, descriptor)


@pytest.mark.parametrize("crash_point", ["prepared", "dispatched", "accepted", "receipt"])
def test_real_process_death_is_reconciled_without_replaying_mutation(runtime, crash_point):
    # The child opens its own SQLite connection and uses the genuine broker edge.
    # os._exit models missing finally blocks and a missing durable result receipt.
    script = r'''
import json, os, sys
from pathlib import Path
from types import SimpleNamespace
from agent.agent_identity import resolve_agent_context
from agent.identity_lifecycle import agent_runtime_scope
from agent.runtime_commands import RuntimeRun, bind_runtime_run
from agent.result_artifacts import publish_result_artifact
from agent import effect_reconciler
from hermes_state import SessionDB
home = Path(sys.argv[1]); point = sys.argv[2]
context = resolve_agent_context(json.loads((home / "config.yaml").read_text()), session_id="session", profile_home=home)
db = SessionDB(home / "state.db")
command = db.read_runtime_command("session", "command")
lease = db.get_session_turn_lease("session")
run = RuntimeRun(SimpleNamespace(), db, "session", "command", command["receipt"]["run_id"], "owner", lease["generation"], context)
original = effect_reconciler._publish_bytes
def interrupted(*args):
    if point == "accepted":
        original(*args)
    os._exit(77)
if point == "prepared":
    db.dispatch_effect = lambda *args, **kwargs: os._exit(77)
elif point == "receipt":
    record = db.record_effect_outcome
    def recorded(*args, **kwargs):
        record(*args, **kwargs)
        os._exit(77)
    db.record_effect_outcome = recorded
else:
    effect_reconciler._publish_bytes = interrupted
with agent_runtime_scope(context):
    bind_runtime_run(run)
    publish_result_artifact(run, b"crash-proof output", "crash-result")
'''
    process = subprocess.run([sys.executable, "-c", script, str(runtime.home), crash_point],
                             cwd=Path(__file__).resolve().parents[2], timeout=30, capture_output=True, text=True)
    assert process.returncode == 77, process.stderr
    row = effects(runtime)[0]
    expected = {"prepared": "prepared", "dispatched": "dispatched", "accepted": "dispatched", "receipt": "confirmed"}
    assert row["state"] == expected[crash_point]
    if crash_point in {"dispatched", "accepted"}:
        with pytest.raises(CapabilityDenied, match="reconciliation"):
            publish_result_artifact(runtime.run, b"crash-proof output", "crash-result")
    accepted = crash_point in {"accepted", "receipt"}
    path = runtime.home / row["input_ref"]["locator"]
    assert path.exists() is accepted
    if accepted:
        before = (path.stat().st_ino, path.stat().st_mtime_ns)
    # Recovery by a successor owner still cannot dispatch the old intent.
    runtime.db.release_session_turn_lease("session", "owner")
    assert runtime.db.acquire_session_turn_lease("session", "successor", wait_seconds=0)
    generation = runtime.db.get_session_turn_lease("session")["generation"]
    recovered = reconcile(runtime, row["effect_id"], holder="successor", generation=generation)
    expected_state = "confirmed" if accepted else "prepared" if crash_point == "prepared" else "reconciliation_required"
    assert recovered["state"] == expected_state
    if crash_point == "prepared":
        with pytest.raises(ValueError, match="live claimed"):
            runtime.db.dispatch_effect(row["effect_id"], runtime.actor, holder="successor", generation=generation)
    else:
        assert runtime.db.dispatch_effect(row["effect_id"], runtime.actor,
            holder="successor", generation=generation)["dispatched_now"] is False
    if accepted:
        assert (path.stat().st_ino, path.stat().st_mtime_ns) == before
    else:
        assert not path.exists()
        assert reconcile(runtime, row["effect_id"], holder="successor", generation=generation)["state"] == expected_state


def test_same_named_actor_cannot_read_another_profile_blob(runtime):
    descriptor = publish_result_artifact(runtime.run, b"owner A", "one")
    other_home = runtime.home / "profile-b"
    other_home.mkdir()
    (other_home / "config.yaml").write_text(json.dumps(runtime.raw))
    other = resolve_agent_context(runtime.raw, session_id="session", profile_home=other_home)
    with agent_runtime_scope(other):
        with pytest.raises(ArtifactConflict, match="owning context"):
            read_result_artifact(runtime.context, descriptor)
        with pytest.raises(FileNotFoundError):
            read_result_artifact(other, descriptor)
    assert read_result_artifact(runtime.context, descriptor) == b"owner A"


@pytest.mark.parametrize("entry", ["existing", "symlink", "hardlink", "ancestor_symlink"])
def test_conflicting_files_are_preserved_and_unknown_stays_held(runtime, entry, monkeypatch):
    descriptor = result_artifact_descriptor(runtime.context, runtime.run.run_id, b"wanted", "result")
    path = runtime.home / descriptor["locator"]
    outside = runtime.home / "unrelated"
    outside.write_bytes(b"preserve")
    if entry == "ancestor_symlink":
        namespace = runtime.home / "runtime-artifacts"
        elsewhere = runtime.home / "elsewhere"
        elsewhere.mkdir()
        namespace.symlink_to(elsewhere, target_is_directory=True)
    else:
        path.parent.mkdir(mode=0o700, parents=True)
        path.parent.parent.chmod(0o700)
        if entry == "existing":
            path.write_bytes(b"preserve")
        elif entry == "symlink":
            path.symlink_to(outside)
        else:
            os.link(outside, path)
    with pytest.raises((ArtifactConflict, OSError)):
        publish_result_artifact(runtime.run, b"wanted", "result")
    assert outside.read_bytes() == b"preserve"
    row = effects(runtime)[0]
    assert row["state"] == "outcome_unknown"
    assert reconcile(runtime, row["effect_id"])["state"] == "reconciliation_required"
    with pytest.raises(CapabilityDenied):
        publish_result_artifact(runtime.run, b"wanted", "result")


def test_journal_failure_prevents_writes_and_reconciliation_enforces_owner_deadline(runtime, monkeypatch):
    from agent import effect_reconciler
    def journal_failed(*args, **kwargs):
        raise OSError("journal unavailable")
    monkeypatch.setattr(runtime.db, "prepare_effect", journal_failed)
    with pytest.raises(OSError, match="journal unavailable"):
        publish_result_artifact(runtime.run, b"result", "not-written")
    assert not (runtime.home / "runtime-artifacts").exists()
    monkeypatch.undo()
    original = effect_reconciler._publish_bytes
    def interrupted(*args):
        original(*args)
        raise TimeoutError("receipt lost")
    monkeypatch.setattr(effect_reconciler, "_publish_bytes", interrupted)
    with pytest.raises(TimeoutError):
        publish_result_artifact(runtime.run, b"result", "written")
    row = effects(runtime)[0]
    with pytest.raises(CapabilityDenied, match="deadline"):
        reconcile(runtime, row["effect_id"], deadline_at=time.time() - 1)
    with pytest.raises(CapabilityDenied, match="generation"):
        reconcile(runtime, row["effect_id"], holder="wrong")
    copied = contextvars.copy_context()
    runtime.db.release_session_turn_lease("session", "owner")
    assert runtime.db.acquire_session_turn_lease("session", "successor", wait_seconds=0)
    with pytest.raises(RuntimeFenceError, match="generation"):
        copied.run(publish_result_artifact, runtime.run, b"result", "stale")
    assert len(effects(runtime)) == 1


def test_partial_output_persistence_does_not_restart_cancelled_work(runtime):
    from agent.task_scope import TaskScope
    from dataclasses import replace
    scope = TaskScope(runtime.run.agent, runtime.run.run_id)
    scope.cancelled.set()
    cancelled = replace(runtime.run, task_scope=scope)
    token = bind_runtime_run(cancelled)
    try:
        with pytest.raises(InterruptedError, match="cancellation"):
            assert_runtime_dispatch()
        descriptor = publish_result_artifact(cancelled, b"cancelled partial", "cancelled")
        assert read_result_artifact(runtime.context, descriptor) == b"cancelled partial"
        with pytest.raises(InterruptedError, match="cancellation"):
            assert_runtime_dispatch()
    finally:
        reset_runtime_run(token, cancelled)
        runtime.run.agent._active_runtime_run = runtime.run
    runtime.run.dispatch_blocked.set()
    runtime.run.agent._interrupt_requested = True
    descriptor = publish_result_artifact(runtime.run, b"partial output", "partial")
    assert read_result_artifact(runtime.context, descriptor) == b"partial output"
    assert runtime.run.dispatch_blocked.is_set()
    with pytest.raises(RuntimeFenceError, match="blocked"):
        assert_runtime_dispatch()
