"""BE14 operator boundaries against real isolated state and individual stores."""
from contextlib import contextmanager
import json
import sqlite3
import threading
import time
from types import SimpleNamespace

import pytest

from agent.agent_identity import resolve_agent_context
from agent.identity_lifecycle import agent_runtime_scope
from agent.operations_audit import OptionalAuditSink, project_event
from agent.operations_control import OperationsError, apply_repair, inspect_runtime, preview_repair, qualify_checkpoint_restore
from agent.operations_privacy import apply_deletion, preview_deletion, privacy_qualification, retention_inventory
from agent.result_artifacts import artifact_actor, result_artifact_descriptor, _publish_bytes, descriptor_digest
from hermes_state import SessionDB

pytestmark = pytest.mark.platforms("linux")


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    raw = {"agent_identity": {"schema_version": 1, "principal_id": "owner", "profile_id": "profile",
        "primary_agent_id": "primary", "active_agent_id": "specialist", "agents": {
            "primary": {"policy_version": 1, "role": "primary", "memory_backend": "personal_mcp"},
            "specialist": {"policy_version": 1, "role": "specialist", "memory_backend": "builtin", "allowed_tools": ["memory"]}}}}
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    (home / "config.yaml").write_text(json.dumps(raw))
    monkeypatch.setenv("HERMES_HOME", str(home))
    context = resolve_agent_context(raw, session_id="session", profile_home=home)
    db = SessionDB(home / "state.db")
    db.create_session("session", source="cli")
    db.claim_session_agent_identity("session", context.identity.to_record())
    actor = artifact_actor(context)
    command = {"schema_version": 1, "command_id": "command", "idempotency_key": "command", "expected_revision": None,
               "operation": "submit", "payload": {"text": "private-provider-token"}, "identity_binding": actor}
    receipt = db.submit_runtime_command("session", actor, command)
    with agent_runtime_scope(context):
        yield SimpleNamespace(home=home, db=db, context=context, actor=actor, run_id=receipt["run_id"], raw=raw)
    db.close()


def own(runtime):
    assert runtime.db.try_acquire_session_turn_lease("session", "fixture")
    generation = runtime.db.get_session_turn_lease("session")["generation"]
    runtime.db.claim_runtime_command("session", "command", holder="fixture", generation=generation)
    return generation


def finish(runtime):
    generation = own(runtime)
    runtime.db.finish_runtime_command("session", "command", holder="fixture", generation=generation,
                                     result={"private": "private-provider-token"})
    runtime.db.release_session_turn_lease("session", "fixture", generation=generation)


def make_effect(runtime, *, confirmed=False, publish=False, release=True):
    generation = own(runtime)
    payload = b"private-provider-token"
    descriptor = result_artifact_descriptor(runtime.context, runtime.run_id, payload, "artifact-one")
    effect = runtime.db.prepare_effect("session", runtime.actor, holder="fixture", generation=generation,
        run_id=runtime.run_id, operation_id="publish", intent_key="publish", operation_type="artifact_publish",
        input_digest=descriptor_digest(descriptor), target_ref="artifact:artifact-one:1", policy_digest=runtime.context.policy.digest,
        policy_version="1", input_revision=descriptor["sha256"], artifact_revision="1", input_ref=descriptor)
    runtime.db.dispatch_effect(effect["effect_id"], runtime.actor, holder="fixture", generation=generation)
    if publish:
        _publish_bytes(runtime.context, descriptor, payload)
    if confirmed:
        runtime.db.record_effect_outcome(effect["effect_id"], runtime.actor, state="confirmed",
            holder="fixture", generation=generation, receipt={"kind": "local_fsync", "reference": descriptor["locator"]},
            evidence={"kind": "local_atomic_publication"})
    if release:
        runtime.db.release_session_turn_lease("session", "fixture", generation=generation)
    return effect, descriptor


def apply(runtime, plan):
    return apply_repair(runtime.db, runtime.context, plan, authorization_digest=plan["plan_digest"])


def test_inspect_and_audit_never_leak_payload_or_initialize_operator_store(runtime):
    finish(runtime)
    before = runtime.db.read_runtime_snapshot("session")["revision"]
    result = inspect_runtime(runtime.db, runtime.context)
    events = runtime.db.replay_runtime_events("session")["events"]
    rendered = json.dumps([result, [project_event(row) for row in events]])
    assert "private-provider-token" not in rendered
    assert str(runtime.home) not in rendered
    assert result["memory_backend"] == "builtin"
    assert runtime.db.read_runtime_snapshot("session")["revision"] == before
    assert not privacy_qualification()["sensitive_ingestion_certified"]


def test_keyed_projection_is_purpose_and_scope_separated():
    event = {"type": "tool.completed", "session_id": "low-entropy-user", "payload": {"innocent": "secret"}}
    key = b"a" * 32
    first = project_event(event, purpose="analytics", correlation_key=key, scope="a")
    assert "secret" not in json.dumps(first) and "low-entropy-user" not in json.dumps(first)
    assert first != project_event(event, purpose="analytics", correlation_key=key, scope="b")
    with pytest.raises(ValueError):
        project_event(event, purpose="analytics")


def test_optional_outage_and_hung_sink_have_bounded_nonblocking_buffers():
    started, release = threading.Event(), threading.Event()
    def hanging(_):
        started.set()
        assert release.wait(10)
        raise OSError("private-secret-in-error")
    sink = OptionalAuditSink(hanging, consent=True, capacity=2)
    event = {"type": "runtime.output", "session_id": "session", "payload": {"text": "secret"}}
    try:
        assert sink.offer(event, correlation_key=b"a" * 32, scope="profile")
        assert started.wait(5)
        for _ in range(100):
            sink.offer(event, correlation_key=b"a" * 32, scope="profile")
        assert sink.status()["queued"] == 2 and sink.status()["dropped"] == 98
        sink.close()
        assert sink.status()["queued"] == 0
    finally:
        release.set()
        sink._thread.join(5)
    assert sink.status()["failed"] == 1
    with pytest.raises(ValueError):
        OptionalAuditSink(lambda _: None)


def test_revoke_is_exact_generation_cas_and_old_owner_cannot_write(runtime):
    generation = own(runtime)
    plan = preview_repair(runtime.db, runtime.context, "revoke-lease", "session")
    with pytest.raises(OperationsError, match="authorization"):
        apply_repair(runtime.db, runtime.context, plan, authorization_digest="not-approved")
    assert apply(runtime, plan)["remote_work_stopped"] is False
    assert runtime.db.get_session_turn_lease("session") is None
    with pytest.raises(ValueError):
        runtime.db.append_runtime_event("session", "runtime.output", {}, holder="fixture", generation=generation)
    with pytest.raises(OperationsError):
        apply(runtime, plan)
    events = runtime.db.replay_runtime_events("session")["events"]
    assert [row["type"] for row in events][-2:] == ["operations.repair_started", "operations.repair_finished"]


def test_reconcile_reads_actual_bytes_without_repeating_effect(runtime, monkeypatch):
    effect, descriptor = make_effect(runtime, publish=True)
    inode = (runtime.home / descriptor["locator"]).stat().st_ino
    plan = preview_repair(runtime.db, runtime.context, "reconcile-effect", effect["effect_id"])
    result = apply(runtime, plan)
    assert result["state"] == "confirmed" and not result["redispatched"]
    assert (runtime.home / descriptor["locator"]).stat().st_ino == inode
    assert len(runtime.db.list_effects("session", runtime.actor)) == 1


def test_missing_effect_bytes_are_not_marked_success(runtime):
    effect, _ = make_effect(runtime)
    plan = preview_repair(runtime.db, runtime.context, "reconcile-effect", effect["effect_id"])
    assert apply(runtime, plan)["state"] == "reconciliation_required"
    with pytest.raises(OperationsError, match="unsupported"):
        preview_repair(runtime.db, runtime.context, "mark-success", effect["effect_id"])
    with pytest.raises(OperationsError, match="checkpoint missing"):
        preview_repair(runtime.db, runtime.context, "restore-checkpoint", "session")


def test_retry_delivery_preserves_outbox_identity_budget_and_strict_fence(runtime):
    _, descriptor = make_effect(runtime, confirmed=True, publish=True, release=False)
    generation = runtime.db.get_session_turn_lease("session")["generation"]
    result = runtime.db.commit_runtime_result("session", "command", actor=runtime.actor,
        descriptor=descriptor, holder="fixture", generation=generation, result_summary={})
    runtime.db.release_session_turn_lease("session", "fixture", generation=generation)
    delivery_id = result["delivery_id"]
    attempt = runtime.db.claim_runtime_delivery("session", runtime.actor, delivery_id)
    runtime.db.finish_runtime_delivery_attempt("session", runtime.actor, delivery_id, attempt["attempt_token"],
                                               accepted=False, definitely_not_sent=False)
    plan = preview_repair(runtime.db, runtime.context, "retry-delivery", delivery_id)
    receipt = apply(runtime, plan)
    assert receipt["state"] == "pending" and receipt["delivery_id"] == delivery_id
    assert receipt["attempt_count"] == 1
    with pytest.raises(ValueError, match="complete owner"):
        runtime.db.repair_runtime_delivery("session", runtime.actor, delivery_id, holder="incomplete")
    with pytest.raises(ValueError, match="lease lost or expired"):
        runtime.db.repair_runtime_delivery("session", runtime.actor, delivery_id, holder="old", generation=generation,
                                           expected_attempt_count=1, expected_state="pending")


def test_mandatory_journal_outage_blocks_repair_before_adapter(runtime, monkeypatch):
    effect, _ = make_effect(runtime)
    plan = preview_repair(runtime.db, runtime.context, "reconcile-effect", effect["effect_id"])
    original = runtime.db._append_runtime_event_on_conn
    def fail(conn, sid, kind, *args, **kwargs):
        if kind == "operations.repair_started":
            raise OSError("disk unavailable")
        return original(conn, sid, kind, *args, **kwargs)
    monkeypatch.setattr(runtime.db, "_append_runtime_event_on_conn", fail)
    with pytest.raises(OSError):
        apply(runtime, plan)
    assert runtime.db.get_effect(effect["effect_id"], runtime.actor)["state"] == "dispatched"
    assert runtime.db.get_session_turn_lease("session") is None


def test_preview_drift_and_wrong_profile_fail_without_fallback(runtime, tmp_path):
    effect, _ = make_effect(runtime)
    plan = preview_repair(runtime.db, runtime.context, "reconcile-effect", effect["effect_id"])
    assert runtime.db.try_acquire_session_turn_lease("session", "other")
    lease = runtime.db.get_session_turn_lease("session")
    runtime.db.append_runtime_event("session", "runtime.state", {}, holder="other", generation=lease["generation"])
    runtime.db.release_session_turn_lease("session", "other", generation=lease["generation"])
    with pytest.raises(OperationsError, match="preview changed"):
        apply(runtime, plan)
    other = SessionDB(tmp_path / "other.db")
    try:
        with pytest.raises(OperationsError, match="wrong profile"):
            inspect_runtime(other, runtime.context)
    finally:
        other.close()


def test_transcript_manifest_is_readonly_and_logical_erasure_preserves_recovery(runtime):
    finish(runtime)
    runtime.db.append_message("session", "user", "transcript-private-word")
    assert runtime.db.search_messages("transcript-private-word")
    before = runtime.db.read_runtime_snapshot("session")["revision"]
    manifest = preview_deletion(runtime.db, runtime.context)
    assert runtime.db.get_messages("session")[0]["content"] == "transcript-private-word"
    assert runtime.db.read_runtime_snapshot("session")["revision"] == before
    with pytest.raises(OperationsError):
        apply_deletion(runtime.db, runtime.context, manifest, authorization_digest="not-approved")
    receipt = apply_deletion(runtime.db, runtime.context, manifest, authorization_digest=manifest["manifest_digest"])
    assert runtime.db.get_messages("session") == []
    assert runtime.db.search_messages("transcript-private-word") == []
    assert receipt["acknowledgments"] and not receipt["complete_deletion"]
    assert runtime.db.read_runtime_command("session", "command") is not None
    assert "mandatory_recovery_journal_retained" in receipt["limitations"]
    assert retention_inventory(runtime.db, runtime.context)["stores"]["messages"] == 0


def test_deletion_refuses_unresolved_work_and_changed_transcript(runtime):
    with pytest.raises(OperationsError, match="unresolved run"):
        preview_deletion(runtime.db, runtime.context)
    finish(runtime)
    manifest = preview_deletion(runtime.db, runtime.context)
    runtime.db.append_message("session", "user", "new work")
    with pytest.raises(OperationsError, match="preview changed"):
        apply_deletion(runtime.db, runtime.context, manifest, authorization_digest=manifest["manifest_digest"])
    assert runtime.db.get_messages("session")


def test_deletion_journal_failure_rolls_back_erasure(runtime, monkeypatch):
    finish(runtime)
    runtime.db.append_message("session", "user", "must survive audit failure")
    manifest = preview_deletion(runtime.db, runtime.context)
    original = runtime.db._append_runtime_event_on_conn
    def fail(conn, sid, kind, *args, **kwargs):
        if kind == "operations.deletion_finished":
            raise OSError("journal sink lost")
        return original(conn, sid, kind, *args, **kwargs)
    monkeypatch.setattr(runtime.db, "_append_runtime_event_on_conn", fail)
    with pytest.raises(OSError):
        apply_deletion(runtime.db, runtime.context, manifest, authorization_digest=manifest["manifest_digest"])
    assert runtime.db.get_messages("session")[0]["content"] == "must survive audit failure"
    assert runtime.db.read_runtime_snapshot("session")["revision"] == manifest["before_revision"]


def test_profile_a_b_a_never_reuses_a_foreign_inspection_or_plan(runtime, tmp_path):
    finish(runtime)
    before = inspect_runtime(runtime.db, runtime.context)
    home = tmp_path / "other-home"
    home.mkdir(mode=0o700)
    (home / "config.yaml").write_text(json.dumps(runtime.raw))
    other_context = resolve_agent_context(runtime.raw, session_id="session", profile_home=home)
    other = SessionDB(home / "state.db")
    other.create_session("session", source="cli")
    other.claim_session_agent_identity("session", other_context.identity.to_record())
    other.submit_runtime_command("session", runtime.actor, {"schema_version": 1, "command_id": "other-command",
        "idempotency_key": "other-command", "expected_revision": None, "operation": "submit",
        "payload": {"text": "other profile"}, "identity_binding": runtime.actor})
    try:
        with agent_runtime_scope(other_context):
            assert inspect_runtime(other, other_context)["state"]["last_command_id"] == "other-command"
            with pytest.raises(OperationsError):
                inspect_runtime(runtime.db, runtime.context)
        assert inspect_runtime(runtime.db, runtime.context) == before
    finally:
        other.close()


def test_memory_purge_erases_all_logical_versions_conflicts_and_projection(runtime):
    from tools.individual_memory_store import IndividualMemoryStore
    finish(runtime)
    store = IndividualMemoryStore(runtime.context)
    first = store.write_record("version-one-secret")["record"]
    store.write_record("version-two-secret", record_id=first["record_id"], expected_version=1)
    store.write_record("conflict-secret", record_id=first["record_id"], expected_version=1)
    store.load_from_disk()
    frozen = store.format_for_system_prompt("memory")
    before = {path.name: (path.stat().st_mtime_ns, path.read_bytes())
              for path in store._scope.directory.iterdir() if path.is_file()}
    manifest = preview_deletion(runtime.db, runtime.context, record_id=first["record_id"], memory_store=store)
    after = {path.name: (path.stat().st_mtime_ns, path.read_bytes())
             for path in store._scope.directory.iterdir() if path.is_file()}
    assert before == after
    receipt = apply_deletion(runtime.db, runtime.context, manifest,
        authorization_digest=manifest["manifest_digest"], memory_store=store)
    with sqlite3.connect(store._scope.directory / "records.sqlite3") as connection:
        rows = connection.execute("SELECT record_json FROM memory_records").fetchall()
        assert all("secret" not in row[0] for row in rows)
        assert not connection.execute("SELECT * FROM memory_conflicts").fetchall()
    assert "secret" not in store._path_for("memory").read_text()
    assert store.format_for_system_prompt("memory") == frozen
    assert "active_process_copies_require_session_end" in receipt["limitations"]


def test_index_rebuild_reuses_existing_admission_and_refuses_foreign_scope(runtime):
    finish(runtime)
    runtime.db.append_message("session", "user", "indexed phrase")
    plan = preview_repair(runtime.db, runtime.context, "rebuild-index", "session")
    assert apply(runtime, plan)["indexes_rebuilt"] > 0
    runtime.db.create_session("foreign", source="cli")
    with pytest.raises(OperationsError, match="scope mismatch"):
        preview_repair(runtime.db, runtime.context, "rebuild-index", "session")


def test_cli_readonly_consumer_and_error_redaction(runtime, capsys):
    from hermes_cli.operations_cli import main
    finish(runtime)
    revision = runtime.db.read_runtime_snapshot("session")["revision"]
    assert main(["inspect", "--session", "session"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["actor"] == runtime.actor
    assert "private-provider-token" not in json.dumps(output)
    assert runtime.db.read_runtime_snapshot("session")["revision"] == revision
    assert main(["inspect", "--session", "missing-private-token"]) == 1
    assert "missing-private-token" not in capsys.readouterr().out


def test_checkpoint_dry_run_checks_actual_versions_without_restoring_history(runtime):
    assert qualify_checkpoint_restore(runtime.db, runtime.context)["status"] == "unavailable"
    generation = own(runtime)
    before = runtime.db.read_runtime_snapshot("session")["revision"]
    checkpoint = {"schema_version": 1, "config_version": runtime.context.config_digest,
        "policy_version": runtime.context.policy.digest, "runtime_version": "be08.v1",
        "prompt_projection_version": "1", "artifacts": [], "outstanding_requests": [], "unresolved_effects": []}
    runtime.db.publish_runtime_checkpoint("session", checkpoint, holder="fixture", generation=generation,
                                         expected_revision=before, included_seq=before)
    state = runtime.db.read_runtime_snapshot("session")
    result = qualify_checkpoint_restore(runtime.db, runtime.context)
    assert result["status"] == "qualified_for_isolated_drill" and not result["restore_allowed"]
    assert all(result["checks"].values())
    assert runtime.db.read_runtime_snapshot("session") == state
    checkpoint["runtime_version"] = "future-incompatible-runtime"
    runtime.db.publish_runtime_checkpoint("session", checkpoint, holder="fixture", generation=generation,
        expected_revision=state["revision"], included_seq=state["revision"])
    assert qualify_checkpoint_restore(runtime.db, runtime.context)["status"] == "incompatible"


def test_derived_service_payloads_are_inventory_only_and_incomplete_work_blocks_erasure(runtime):
    finish(runtime)
    runtime.db._write_sql("INSERT INTO bounded_service_pipelines VALUES(?,?,?,?,?,?,?,?)",
        ("pipeline", json.dumps(runtime.actor), "session", "project", "request", '{"stages":[{},{}]}', "fixture", time.time()))
    inventory = retention_inventory(runtime.db, runtime.context)
    assert inventory["retained_derived_store_counts"]["bounded_service_pipelines"] == 1
    assert inventory["retained_derived_store_counts"]["bounded_service_stages"] == 0
    with pytest.raises(OperationsError, match="unresolved service"):
        preview_deletion(runtime.db, runtime.context)
    for stage in range(2):
        runtime.db._write_sql("INSERT INTO bounded_service_stages VALUES(?,?,?,?,?)",
                              ("pipeline", stage, "{}", b"private-derived-media", time.time()))
    manifest = preview_deletion(runtime.db, runtime.context)
    assert "bounded_service_source_receipt_blobs_and_channel_bindings_retained" in manifest["limitations"]
    assert "private-derived-media" not in json.dumps(retention_inventory(runtime.db, runtime.context))


def test_real_operational_journal_replay_serializes_through_generated_wire_contract(runtime):
    from tui_gateway.contracts.runtime_v1 import RuntimeEventEnvelope
    from tui_gateway.methods_runtime import _runtime_event_projection
    finish(runtime)
    runtime.db.append_message("session", "user", "wire-private-payload")
    manifest = preview_deletion(runtime.db, runtime.context)
    apply_deletion(runtime.db, runtime.context, manifest, authorization_digest=manifest["manifest_digest"])
    assert runtime.db.try_acquire_session_turn_lease("session", "wire-owner")
    plan = preview_repair(runtime.db, runtime.context, "revoke-lease", "session")
    apply(runtime, plan)
    page = runtime.db.replay_runtime_events("session")
    operational = [row for row in page["events"] if row["type"].startswith("operations.")]
    assert {row["type"] for row in operational} == {
        "operations.repair_started", "operations.repair_finished",
        "operations.deletion_requested", "operations.deletion_finished"}
    encoded = []
    for row in operational:
        projected = _runtime_event_projection(row)
        parsed = RuntimeEventEnvelope.model_validate(projected)
        assert parsed.operation_id in {plan["plan_digest"], manifest["manifest_digest"]}
        assert projected["payload"] == {}
        encoded.append(parsed.model_dump_json())
    assert "wire-private-payload" not in "".join(encoded)
    assert page["last_cursor"] == runtime.db.read_runtime_snapshot("session")["last_cursor"]
