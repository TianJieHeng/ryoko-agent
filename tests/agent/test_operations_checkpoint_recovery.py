"""Projection recovery keeps authoritative facts and old owners fenced."""
import json
import subprocess
import sys
import time

import pytest

from agent.operations_control import apply_repair, preview_repair, qualify_checkpoint_restore
from agent.operations_checkpoint_recovery import restore_projection
from hermes_state_effects import effect_digest
from hermes_state_runtime import RuntimeStoreError
from tests.agent.test_operations_control import runtime, own

pytestmark = pytest.mark.platforms("linux")


def checkpoint(runtime, generation):
    db, context = runtime.db, runtime.context
    seq = db.read_runtime_snapshot("session")["revision"]
    db.publish_runtime_checkpoint("session", {"schema_version": 1,
        "config_version": context.config_digest, "policy_version": context.policy.digest,
        "runtime_version": "be08.v1", "prompt_projection_version": "1",
        "outstanding_requests": [], "artifacts": [], "unresolved_effects": []},
        holder="fixture", generation=generation, expected_revision=seq, included_seq=seq)


def protected(db):
    with db._runtime_read() as conn:
        return {table: [tuple(row) for row in conn.execute("SELECT * FROM " + table)] for table in (
            "runtime_commands", "runtime_effects", "runtime_effect_evidence", "runtime_effect_approvals",
            "runtime_checkpoints", "runtime_context_projections", "messages")}


def evidence(runtime, generation):
    scope = {"session_id": "session", "run_id": runtime.run_id, "holder": "fixture", "generation": generation,
             "action_digest": effect_digest({"action": "publish"}), "input_digest": effect_digest("immutable"),
             "target_ref": "artifact:result:1", "policy_version": "1", "policy_digest": runtime.context.policy.digest,
             "input_revision": "one", "artifact_revision": "1"}
    approval = runtime.db.request_effect_approval(actor=runtime.actor, **scope, expires_at=time.time() + 300)
    runtime.db.resolve_effect_approval(approval["approval_id"], runtime.actor, holder="fixture", generation=generation,
        approval_digest=approval["approval_digest"], choice="once")
    effect = runtime.db.prepare_effect(actor=runtime.actor, **scope, approval_id=approval["approval_id"],
        operation_id="unknown-publish", intent_key="unknown-publish", operation_type="artifact_publish")
    runtime.db.dispatch_effect(effect["effect_id"], runtime.actor, holder="fixture", generation=generation)
    runtime.db.record_effect_outcome(effect["effect_id"], runtime.actor, state="outcome_unknown", holder="fixture",
                                    generation=generation, evidence={"kind": "interrupted"})
    return approval, effect


def test_reconstruct_from_checkpoint_plus_tail_keeps_consumed_approvals_unknown_effects_and_generations(runtime):
    generation = own(runtime)
    checkpoint(runtime, generation)
    approval, effect = evidence(runtime, generation)
    runtime.db.append_runtime_event("session", "tool.started", {}, holder="fixture", generation=generation,
                                    operation_id="missing-tool-result")
    runtime.db.finish_runtime_command("session", "command", holder="fixture", generation=generation, status="blocked")
    expected = runtime.db.read_runtime_snapshot("session")
    runtime.db.release_session_turn_lease("session", "fixture", generation=generation)
    before = protected(runtime.db)
    runtime.db._write_sql("UPDATE runtime_state SET snapshot_json='broken-cache' WHERE session_id='session'")
    qualified = qualify_checkpoint_restore(runtime.db, runtime.context)
    assert qualified["restore_allowed"] and qualified["restore_scope"] == "derived_projection_only"
    plan = preview_repair(runtime.db, runtime.context, "restore-checkpoint", "session")
    receipt = apply_repair(runtime.db, runtime.context, plan, authorization_digest=plan["plan_digest"])
    restored = runtime.db.read_runtime_snapshot("session")
    assert restored["state"] == expected["state"]
    assert restored["unresolved_invocations"] == expected["unresolved_invocations"]
    assert restored["unresolved_effects"] == [{"effect_id": effect["effect_id"], "status": "outcome_unknown"}]
    assert not restored["outstanding_requests"]
    assert protected(runtime.db) == before
    assert receipt["generation"] > generation and receipt["revision"] == expected["revision"] + 2
    assert runtime.db.get_effect_approval(approval["approval_id"], runtime.actor)["status"] == "consumed"
    with pytest.raises(RuntimeStoreError, match="lease lost"):
        runtime.db.append_runtime_event("session", "runtime.output", {}, holder="fixture", generation=generation)
    with pytest.raises(RuntimeStoreError):
        apply_repair(runtime.db, runtime.context, plan, authorization_digest=plan["plan_digest"])


def test_stale_fence_incompatible_missing_tail_and_journal_crash_never_partially_repair(runtime, monkeypatch):
    generation = own(runtime)
    checkpoint(runtime, generation)
    runtime.db.release_session_turn_lease("session", "fixture", generation=generation)
    plan = preview_repair(runtime.db, runtime.context, "restore-checkpoint", "session")
    before = runtime.db.read_runtime_snapshot("session")
    with pytest.raises(RuntimeStoreError, match="lease lost"):
        restore_projection(runtime.db, runtime.context, plan, holder="fixture", generation=generation)
    append = runtime.db._append_runtime_event_on_conn
    def crash(conn, sid, kind, *args, **kwargs):
        if kind == "operations.repair_finished":
            raise OSError("synthetic crash before commit")
        return append(conn, sid, kind, *args, **kwargs)
    monkeypatch.setattr(runtime.db, "_append_runtime_event_on_conn", crash)
    with pytest.raises(OSError, match="synthetic crash"):
        apply_repair(runtime.db, runtime.context, plan, authorization_digest=plan["plan_digest"])
    assert runtime.db.read_runtime_snapshot("session") == before
    monkeypatch.setattr(runtime.db, "_append_runtime_event_on_conn", append)
    # Real process death bypasses Python cleanup; SQLite must undo both the
    # reconstructed cache and started journal entry, preserving generation CAS.
    script = """
import json, os, sys
from pathlib import Path
from agent.agent_identity import resolve_agent_context
from agent.identity_lifecycle import agent_runtime_scope
from agent.operations_control import apply_repair, preview_repair
from hermes_state import SessionDB
home = Path(sys.argv[1])
raw = json.loads((home / 'config.yaml').read_text())
context = resolve_agent_context(raw, session_id='session', profile_home=home)
db = SessionDB(home / 'state.db')
append = db._append_runtime_event_on_conn
def crash(conn, sid, kind, *args, **kwargs):
    if kind == 'operations.repair_finished':
        os._exit(73)
    return append(conn, sid, kind, *args, **kwargs)
db._append_runtime_event_on_conn = crash
with agent_runtime_scope(context):
    plan = preview_repair(db, context, 'restore-checkpoint', 'session')
    apply_repair(db, context, plan, authorization_digest=plan['plan_digest'])
"""
    result = subprocess.run([sys.executable, "-c", script, str(runtime.home)], capture_output=True, timeout=20)
    assert result.returncode == 73, result.stderr.decode()
    assert runtime.db.read_runtime_snapshot("session") == before
    with runtime.db._runtime_read() as conn:
        assert not conn.execute("SELECT 1 FROM runtime_events WHERE type LIKE 'operations.repair_%'").fetchone()
        assert conn.execute("SELECT turn_owner_generation FROM sessions WHERE id='session'").fetchone()[0] > generation
    runtime.db._write_sql("UPDATE runtime_checkpoints SET checkpoint_json=json_set(checkpoint_json,'$.runtime_version','future')")
    assert not qualify_checkpoint_restore(runtime.db, runtime.context)["restore_allowed"]
    runtime.db._write_sql("UPDATE runtime_checkpoints SET checkpoint_json=json_set(checkpoint_json,'$.runtime_version','be08.v1')")
    runtime.db._write_sql("DELETE FROM runtime_events WHERE type='checkpoint.published'")
    assert qualify_checkpoint_restore(runtime.db, runtime.context)["blocking_gates"] == ["checkpoint_replay_gap"]
