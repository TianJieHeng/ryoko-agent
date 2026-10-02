"""BE07 same-writer version CAS, grant-checked metadata and preserving schema migration."""
import hashlib
import json
import sqlite3
import time
from types import SimpleNamespace

import pytest

from agent.agent_identity import resolve_agent_context
from agent.identity_lifecycle import agent_runtime_scope
from agent.project_context import project_access
from hermes_cli import projects_db
from hermes_state import SessionDB
from hermes_state_effects import effect_digest
from hermes_state_runtime import RuntimeStoreError


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    with projects_db.connect_closing(tmp_path / "projects.db") as conn:
        project = projects_db.create_project(conn, name="Project", owner_principal_id="owner", grants=[
            {"principal_id": "owner", "agent_id": "ryoko", "permissions": ["read", "write", "share"]}])
    raw = {"agent_identity": {"schema_version": 1, "principal_id": "owner", "profile_id": "profile",
        "primary_agent_id": "ryoko", "active_agent_id": "ryoko", "agents": {"ryoko": {
        "policy_version": 1, "role": "primary", "memory_backend": "personal_mcp", "project_grants": [project]}}}}
    (tmp_path / "config.yaml").write_text(json.dumps(raw))
    context = resolve_agent_context(raw, session_id="session", profile_home=tmp_path)
    actor = {key: getattr(context.identity, key) for key in ("principal_id", "profile_id", "agent_id")}
    db = SessionDB(tmp_path / "state.db")
    db.create_session("session", source="test")
    receipt = db.submit_runtime_command("session", actor, dict(schema_version=1, command_id="control", idempotency_key="control",
        identity_binding=actor, operation="artifact", payload={"kind": "fixture"}))
    assert db.try_acquire_session_turn_lease("session", "owner")
    fence = dict(holder="owner", generation=db.get_session_turn_lease("session")["generation"])
    assert db.claim_runtime_command("session", "control", **fence)
    with agent_runtime_scope(context):
        yield SimpleNamespace(db=db, actor=actor, context=context, project=project, access=project_access(context),
            fence=fence, run_id=receipt["run_id"], home=tmp_path)
    db.close()


def reserve(f, request, *, artifact="doc", content="bytes", parent=None, metadata=None):
    return f.db.reserve_artifact_version("session", f.actor, **f.fence, run_id=f.run_id, command_id="control",
        project_id=f.project, artifact_id=artifact, request_id=request, parent_version=parent,
        content_sha256=hashlib.sha256(content.encode()).hexdigest(), size=len(content.encode()), mime="text/markdown",
        metadata=metadata or {}, access=f.access)


def publish(f, reserved, *, content="bytes", metadata=None, parent=None):
    descriptor = dict(artifact_id=reserved["artifact_id"], version=reserved["version"], producing_run=f.run_id,
        locator=f"fixture/{reserved['artifact_id']}/{reserved['version']}", sha256=hashlib.sha256(content.encode()).hexdigest(),
        size=len(content.encode()), mime="text/markdown")
    key = f"publish-{reserved['artifact_id']}-{reserved['version']}"
    effect = f.db.prepare_effect("session", f.actor, **f.fence, run_id=f.run_id, operation_id=key,
        intent_key=key, operation_type="project_artifact_publish", input_digest=effect_digest(descriptor),
        target_ref=f"artifact:{descriptor['artifact_id']}:{descriptor['version']}", policy_digest=f.context.policy.digest,
        policy_version="1", input_revision=descriptor["sha256"], artifact_revision=str(descriptor["version"]), input_ref=descriptor)
    f.db.dispatch_effect(effect["effect_id"], f.actor, **f.fence)
    f.db.record_effect_outcome(effect["effect_id"], f.actor, **f.fence, state="confirmed", receipt={"kind": "storage_fixture", "sha256": descriptor["sha256"]})
    return f.db.register_artifact_version("session", f.actor, **f.fence, run_id=f.run_id, command_id="control",
        descriptor=descriptor, project_id=f.project, metadata=metadata or {}, expected_head_version=parent, access=f.access)


def reject(code, call):
    with pytest.raises(RuntimeStoreError) as exc:
        call()
    assert exc.value.code == code


def test_version_allocation_is_idempotent_and_stale_cas_retains_branch(store):
    f = store
    one = reserve(f, "one")
    assert reserve(f, "one") == one
    reject("idempotency_conflict", lambda: reserve(f, "one", content="changed"))
    reject("artifact_not_ready", lambda: f.db.read_artifact_version("doc", one["version"], f.actor, access=f.access))
    first = publish(f, one)
    assert first["disposition"] == "head" and first["head_version"] == 1
    left, right = reserve(f, "left", parent=1), reserve(f, "right", parent=1)
    assert left["version"] != right["version"]
    changed = publish(f, left, parent=1)
    branch = publish(f, right, parent=1)
    assert changed["disposition"] == "head" and branch["disposition"] == "branch"
    assert f.db.get_artifact_head("doc", f.actor, access=f.access)["version"] == left["version"]
    assert f.db.read_artifact_version("doc", right["version"], f.actor, access=f.access)["descriptor"] == branch["descriptor"]
    restored = f.db.restore_artifact_head("doc", 1, f.actor, expected_revision=changed["head_revision"], access=f.access)
    assert restored["head_version"] == 1
    reject("revision_conflict", lambda: f.db.restore_artifact_head("doc", left["version"], f.actor,
        expected_revision=changed["head_revision"], access=f.access))


def test_derivative_and_evidence_freshness_invalidate_without_rewriting_sources(store):
    f = store
    source = publish(f, reserve(f, "source"))
    meta = {"derived_from": [{"artifact_id": "doc", "version": 1}]}
    derivative = publish(f, reserve(f, "derivative", artifact="pdf", metadata=meta), metadata=meta)
    anchor = f.db.create_evidence_anchor(f.actor, anchor_id="span", project_id=f.project, kind="source_span",
        source_ref={"artifact_id": "doc", "version": 1}, source_version="1", range_ref={"unit": "byte", "start": 0, "end": 1},
        captured_at=time.time(), authority="source_claim", validity="current", access=f.access)
    assert anchor["effective_validity"] == "current" and not anchor["grants_execution"]
    publish(f, reserve(f, "revision", parent=1), parent=1)
    old = f.db.read_artifact_version("doc", 1, f.actor, access=f.access)
    assert old["descriptor"] == source["descriptor"]
    assert f.db.read_artifact_version("pdf", 1, f.actor, access=f.access)["derived_validity"] == "stale"
    assert derivative["derived_validity"] == "current"
    assert f.db.get_evidence_anchor("span", f.actor, access=f.access)["effective_validity"] == "stale"


def test_capture_failure_filing_and_template_immutability_retain_original(store):
    f = store
    publish(f, reserve(f, "source"))
    ref = {"artifact_id": "doc", "version": 1}
    capture = f.db.create_capture(f.actor, capture_id="capture", project_id=f.project, original_ref=ref,
        source_url="https://example.invalid/source", acquired_at=time.time(), annotation="first observation", access=f.access)
    failed = f.db.record_capture_extraction("capture", f.actor, status="failed", failure_code="unsupported", access=f.access)
    assert failed["original_ref"] == ref and failed["extractions"][-1]["status"] == "failed"
    filed = f.db.file_capture("capture", f.actor, filed_project_id=f.project, expected_revision=capture["revision"], access=f.access)
    unfiled = f.db.file_capture("capture", f.actor, filed_project_id=None, expected_revision=filed["revision"], access=f.access)
    assert unfiled["original_ref"] == ref and unfiled["annotation"] == "first observation"
    template = dict(template_id="template", version=1, project_id=f.project, baseline_ref=ref,
        structure=["Title"], style={"tone": "plain"}, assets=[], slots=[{"name": "title", "purpose": "new title", "required": True}], exclusions=["personal facts"])
    assert f.db.create_template(f.actor, **template, access=f.access)["baseline_ref"] == ref
    reject("idempotency_conflict", lambda: f.db.create_template(f.actor, **{**template, "exclusions": []}, access=f.access))
    assert f.db.get_template("template", f.actor, access=f.access)["exclusions"] == ["personal facts"]


def test_live_revoked_grant_blocks_existing_access_object_and_fake_handle(store):
    from tools.capability_broker import CapabilityDenied
    f = store
    publish(f, reserve(f, "source"))
    assert f.db.read_runtime_snapshot("session")["artifacts"] == []  # Project refs require live ProjectAccess.
    reject("project_access_required", lambda: f.db.read_artifact_version("doc", 1, f.actor, access=SimpleNamespace(assert_access=lambda *a: True)))
    with projects_db.connect_closing() as conn:
        conn.execute("DELETE FROM project_grants WHERE project_id=?", (f.project,))
        conn.commit()
    with pytest.raises(CapabilityDenied, match="live project grant"):
        f.db.read_artifact_version("doc", 1, f.actor, access=f.access)


def _legacy_artifact_table(path):
    db = SessionDB(path)
    db.close()
    descriptor = '{ "artifact_id" : "legacy", "locator": "UNCHANGED_PRIVATE_BYTES" }'
    with sqlite3.connect(path) as conn:
        conn.execute("DROP TABLE runtime_artifact_versions")
        conn.execute("CREATE TABLE runtime_artifact_versions(artifact_id TEXT NOT NULL,version INTEGER NOT NULL,session_id TEXT NOT NULL,"
            "command_id TEXT NOT NULL,run_id TEXT NOT NULL,principal_id TEXT NOT NULL,profile_id TEXT NOT NULL,agent_id TEXT NOT NULL,"
            "descriptor_json TEXT NOT NULL,created_at REAL NOT NULL,PRIMARY KEY(artifact_id,version),UNIQUE(session_id,command_id))")
        conn.execute("INSERT INTO runtime_artifact_versions VALUES('legacy',1,'session','command','run','owner','profile','agent',?,1)", (descriptor,))
        conn.execute("UPDATE schema_version SET version=35")
    return descriptor


def test_schema35_preserves_result_bytes_and_partial_uniqueness_after_reopen(tmp_path):
    path = tmp_path / "legacy.db"
    descriptor = _legacy_artifact_table(path)
    db = SessionDB(path)
    try:
        with db._read_ctx() as conn:
            row = dict(conn.execute("SELECT * FROM runtime_artifact_versions WHERE artifact_id='legacy'").fetchone())
        assert row["descriptor_json"] == descriptor and row["artifact_kind"] == "runtime_result"
        assert row["publication_state"] == "committed"
        with pytest.raises(sqlite3.IntegrityError):
            db._write_sql("INSERT INTO runtime_artifact_versions(artifact_id,version,session_id,command_id,run_id,principal_id,profile_id,agent_id,descriptor_json,created_at) "
                "VALUES('duplicate',1,'session','command','run','owner','profile','agent','{}',1)")
        db._write_sql("INSERT INTO runtime_artifact_versions(artifact_id,version,session_id,command_id,run_id,principal_id,profile_id,agent_id,descriptor_json,created_at,artifact_kind) "
            "VALUES('project',1,'session','command','run','owner','profile','agent','{}',1,'project_artifact')")
    finally:
        db.close()
    reopened = SessionDB(path)
    try:
        with reopened._read_ctx() as conn:
            assert conn.execute("SELECT descriptor_json FROM runtime_artifact_versions WHERE artifact_id='legacy'").fetchone()[0] == descriptor
    finally:
        reopened.close()


def test_interrupted_table_rebuild_rolls_back_all_existing_artifact_rows(tmp_path, monkeypatch):
    from hermes_state_schema import SessionSchemaMixin
    path = tmp_path / "legacy.db"
    descriptor = _legacy_artifact_table(path)
    original = SessionSchemaMixin._rebuild_table_statements
    def crash(cursor, table, *args):
        original(cursor, table, *args)
        if table == "runtime_artifact_versions":
            raise OSError("crash after copy")
    monkeypatch.setattr(SessionSchemaMixin, "_rebuild_table_statements", staticmethod(crash))
    with pytest.raises(OSError, match="after copy"):
        SessionDB(path)
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT descriptor_json FROM runtime_artifact_versions WHERE artifact_id='legacy'").fetchone()[0] == descriptor
        assert conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='runtime_artifact_versions_v35'").fetchone()[0] == 0


def test_independent_writers_allocate_distinct_versions_and_commit_atomically(store, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    f = store
    other = SessionDB(f.db.db_path)
    barrier = Barrier(2)
    def allocate(index):
        with agent_runtime_scope(f.context):
            item = SimpleNamespace(**vars(f))
            item.db = f.db if index == 0 else other
            barrier.wait(timeout=10)
            return reserve(item, f"race-{index}")
    try:
        with ThreadPoolExecutor(2) as pool:
            rows = list(pool.map(allocate, range(2)))
        assert len({row["version"] for row in rows}) == 2
        original = f.db._invalidate_artifact_derivatives_on_conn
        def crash(*args):
            original(*args)
            raise OSError("crash after head CAS")
        monkeypatch.setattr(f.db, "_invalidate_artifact_derivatives_on_conn", crash)
        with pytest.raises(OSError, match="after head CAS"):
            publish(f, rows[0])
        assert f.db.read_artifact_reservation("doc", rows[0]["version"], f.actor, access=f.access)["publication_state"] == "reserved"
        reject("artifact_not_found", lambda: f.db.get_artifact_head("doc", f.actor, access=f.access))
        with other._read_ctx() as conn:
            assert conn.execute("SELECT COUNT(*) FROM artifact_heads").fetchone()[0] == 0
    finally:
        other.close()


def test_evidence_ranges_and_capture_versions_cannot_invent_valid_source_spans(store):
    f = store
    publish(f, reserve(f, "source"))
    ref = {"artifact_id": "doc", "version": 1}
    args = dict(anchor_id="too-long", project_id=f.project, kind="source_span", source_ref=ref, source_version="1",
        range_ref={"unit": "byte", "start": 0, "end": 1000}, captured_at=time.time(), authority="observed", validity="current", access=f.access)
    reject("invalid_artifact", lambda: f.db.create_evidence_anchor(f.actor, **args))
    f.db.create_capture(f.actor, capture_id="capture", project_id=f.project, original_ref=ref, acquired_at=time.time(), access=f.access)
    reject("invalid_artifact", lambda: f.db.create_evidence_anchor(f.actor, **{**args, "source_ref": {"capture_id": "capture"}, "source_version": "2"}))
    lines = f.db.create_evidence_anchor(f.actor, **{**args, "anchor_id": "unverified-lines", "range_ref": {"unit": "line", "start": 100, "end": 200}})
    assert lines["effective_validity"] == "unverified" and not lines["grants_execution"]
