"""BE08 one-writer compaction/checkpoint with fenced exact recovery references."""
import hashlib
import json
import time

import pytest

from hermes_state import SessionDB
from hermes_state_runtime import RuntimeStoreError

ACTOR = {"principal_id": "owner", "profile_id": "profile", "agent_id": "primary"}
SUMMARY = [{"role": "user", "content": "summary"}, {"role": "assistant", "content": "continue"}]


@pytest.fixture
def db(tmp_path):
    value = SessionDB(tmp_path / "state.db")
    value.create_session("session", source="test", system_prompt="frozen prefix")
    receipt = value.submit_runtime_command("session", ACTOR, {"schema_version": 1, "command_id": "run", "idempotency_key": "run",
        "identity_binding": ACTOR, "operation": "submit", "payload": {"text": "work"}})
    assert value.try_acquire_session_turn_lease("session", "owner")
    fence = {"holder": "owner", "generation": value.get_session_turn_lease("session")["generation"]}
    assert value.claim_runtime_command("session", "run", **fence)
    value.append_message("session", "user", "old user", api_content="old user + exact injected context")
    value.append_message("session", "assistant", "old response")
    yield value, receipt["run_id"], fence
    value.close()


def prepare(value, run_id, fence, **changes):
    watermark = value.get_active_message_watermark("session")
    args = dict(holder=fence["holder"], generation=fence["generation"], run_id=run_id,
        immutable_prefix_digest=hashlib.sha256(b"new prefix at compression boundary").hexdigest(),
        config_version="config", policy_version="policy", source=value.capture_context_source("session", watermark=watermark),
        fresh_context_versions={"cursor": "namespace:4", "scope_key": "individual", "records": [{"record_id": "preference", "version": 2}], "invalidation_refs": []},
        system_prompt="new prefix at compression boundary", fallback_state="summary")
    args.update(changes)
    return value.prepare_context_commit("session", ACTOR, **args)


def test_transcript_projection_checkpoint_prompt_and_exact_approval_commit_together(db):
    value, run_id, fence = db
    approved = value.request_effect_approval("session", ACTOR, **fence, run_id=run_id,
        action_digest="a" * 64, input_digest="b" * 64, target_ref="artifact:exact:1", policy_version="1",
        policy_digest="c" * 64, input_revision="original", artifact_revision="1", expires_at=time.time() + 300)
    commit = prepare(value, run_id, fence)
    watermark = commit["source"]["message_watermark"]
    sidecar = json.dumps({"schema_version": 1, "fields": {"anthropic_content_blocks": [{"type": "thinking", "thinking": "opaque-α", "signature": "sig+=="}]}})
    tail = value.append_message("session", "assistant", "concurrent native response", api_content=" exact tail bytes ",
        codex_reasoning_items=[{"type": "reasoning", "id": "native", "encrypted_content": "opaque+=="}])
    value._write_sql("UPDATE messages SET provider_sidecar=? WHERE id=?", (sidecar, tail))
    value.archive_and_compact("session", [dict(item) for item in SUMMARY], watermark=watermark, context_commit=commit)
    projection = value.read_context_projection("session", ACTOR)
    assert projection["fresh_context_versions"]["scope_key"] == "individual"
    assert projection["fresh_context_versions"]["records"] == [{"record_id": "preference", "version": 2}]
    assert projection["anchors"] == [{"kind": "approval", "anchor_id": approved["approval_id"], "approval_digest": approved["approval_digest"]}]
    with value._read_ctx() as conn:
        checkpoint = conn.execute("SELECT checkpoint_id FROM runtime_checkpoints WHERE session_id='session'").fetchone()[0]
        clone = conn.execute("SELECT * FROM messages WHERE session_id='session' AND active=1 ORDER BY id DESC LIMIT 1").fetchone()
    assert projection["checkpoint_id"] == checkpoint
    assert clone["api_content"] == " exact tail bytes " and clone["provider_sidecar"] == sidecar
    assert "opaque+==" in clone["codex_reasoning_items"]
    assert value.get_session("session")["system_prompt"] == "new prefix at compression boundary"
    snapshot = value.read_runtime_snapshot("session")
    value.publish_runtime_checkpoint("session", {"schema_version": 1, "config_version": "config", "policy_version": "policy",
        "runtime_version": "be08.v1", "prompt_projection_version": "1", **{key: snapshot[key] for key in
        ("artifacts", "outstanding_requests", "unresolved_effects", "unresolved_invocations")}}, **fence,
        expected_revision=snapshot["revision"], included_seq=snapshot["revision"])
    with value._read_ctx() as conn:
        latest = json.loads(conn.execute("SELECT checkpoint_json FROM runtime_checkpoints WHERE session_id='session'").fetchone()[0])
    assert latest["context_projection_ref"]["projection_id"] == projection["projection_id"]


def test_failure_after_checkpoint_write_rolls_back_transcript_and_all_metadata(db, monkeypatch):
    value, run_id, fence = db
    commit = prepare(value, run_id, fence)
    before = value.export_session("session")
    snapshot = value.read_runtime_snapshot("session")
    original = value._publish_runtime_checkpoint_on_conn
    def crash(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError("crash after checkpoint")
    monkeypatch.setattr(value, "_publish_runtime_checkpoint_on_conn", crash)
    rows = [dict(item) for item in SUMMARY]
    with pytest.raises(OSError, match="after checkpoint"):
        value.archive_and_compact("session", rows, watermark=commit["source"]["message_watermark"], context_commit=commit)
    assert value.export_session("session") == before
    assert value.read_context_projection("session", ACTOR) is None
    assert value.read_runtime_snapshot("session") == snapshot
    assert all("_row_id" not in row for row in rows)


@pytest.mark.parametrize("change", ["source", "revision", "owner"])
def test_source_revision_and_generation_fences_reject_torn_compaction(db, change):
    value, run_id, fence = db
    commit = prepare(value, run_id, fence)
    if change == "source":
        value._write_sql("UPDATE messages SET api_content='changed unseen bytes' WHERE session_id='session' AND role='user'")
    elif change == "revision":
        value.append_runtime_event("session", "runtime.state", {"changed": True}, **fence)
    else:
        value.release_session_turn_lease("session", fence["holder"], generation=fence["generation"])
        value.try_acquire_session_turn_lease("session", "successor")
    before = value.export_session("session")
    with pytest.raises(RuntimeStoreError) as error:
        value.archive_and_compact("session", [dict(item) for item in SUMMARY], watermark=commit["source"]["message_watermark"], context_commit=commit)
    assert error.value.code in {"context_source_changed", "revision_conflict", "stale_owner"}
    assert value.export_session("session") == before and value.read_context_projection("session", ACTOR) is None


def test_protected_approval_bound_refuses_instead_of_dropping_anchors(db):
    value, run_id, fence = db
    for index in range(101):
        value.request_effect_approval("session", ACTOR, **fence, run_id=run_id, action_digest="a" * 64, input_digest="b" * 64,
            target_ref=f"artifact:{index}:1", policy_version="1", policy_digest="c" * 64,
            input_revision="original", artifact_revision="1", expires_at=time.time() + 300)
    with pytest.raises(RuntimeStoreError) as error:
        prepare(value, run_id, fence)
    assert error.value.code == "context_reference_limit"
    assert len(value.get_messages("session")) == 2


def test_exact_evidence_anchor_survives_successive_compaction_and_revocation_denies_new_read(db, tmp_path):
    from agent.agent_identity import resolve_agent_context
    from agent.identity_lifecycle import agent_runtime_scope
    from agent.project_context import project_access
    from hermes_cli import projects_db
    value, run_id, fence = db
    with projects_db.connect_closing(tmp_path / "projects.db") as conn:
        project = projects_db.create_project(conn, name="Selected", owner_principal_id="owner", grants=[{
            "principal_id": "owner", "agent_id": "primary", "permissions": ["read", "write"]}])
    config = {"agent_identity": {"schema_version": 1, "principal_id": "owner", "profile_id": "profile", "primary_agent_id": "primary",
        "active_agent_id": "primary", "agents": {"primary": {"policy_version": 1, "role": "primary", "memory_backend": "personal_mcp", "project_grants": [project]}}}}
    (tmp_path / "config.yaml").write_text(json.dumps(config))
    context = resolve_agent_context(config, session_id="session", profile_home=tmp_path)
    value._write_sql("INSERT INTO artifact_evidence_anchors(anchor_id,project_id,principal_id,profile_id,agent_id,record_json,created_at) "
        "VALUES('exact-anchor',?,'owner','profile','primary',?,1)", (project, '{"source_version":"exact-v1"}'))
    with agent_runtime_scope(context):
        access = project_access(context)
        first = prepare(value, run_id, fence, project_access=access, fresh_context_versions={"cursor": "n:1", "scope_key": "individual",
            "records": [{"record_id": "source", "version": 1, "source_ref": "exact-anchor"}], "invalidation_refs": []})
        value.archive_and_compact("session", [dict(item) for item in SUMMARY], context_commit=first,
                                  watermark=first["source"]["message_watermark"])
        second = prepare(value, run_id, fence, project_access=access, fresh_context_versions={"cursor": "n:1", "records": [], "invalidation_refs": []})
        assert second["anchors"] == [{"kind": "evidence", "anchor_id": "exact-anchor", "project_id": project, "source_version": "exact-v1"}]
        before = value.export_session("session")
        with projects_db.connect_closing() as conn:
            conn.execute("DELETE FROM project_grants WHERE project_id=?", (project,))
            conn.commit()
        with pytest.raises(PermissionError):
            value.archive_and_compact("session", [dict(item) for item in SUMMARY], context_commit=second,
                                      watermark=second["source"]["message_watermark"])
        assert value.export_session("session") == before


def test_strict_micro_commit_failure_restores_context_under_real_runtime_owner(db, tmp_path, monkeypatch):
    import httpx
    import openai
    from types import SimpleNamespace
    from agent.agent_identity import resolve_agent_context
    from agent.identity_lifecycle import agent_runtime_scope
    from agent.micro_compaction import MicroCompactionMixin
    from agent.runtime_commands import RuntimeRun, bind_runtime_run, reset_runtime_run
    value, run_id, fence = db
    config = {"agent_identity": {"schema_version": 1, "principal_id": "owner", "profile_id": "profile", "primary_agent_id": "primary",
        "active_agent_id": "primary", "agents": {"primary": {"policy_version": 1, "role": "primary", "memory_backend": "personal_mcp"}}}}
    (tmp_path / "config.yaml").write_text(json.dumps(config))
    context = resolve_agent_context(config, session_id="session", profile_home=tmp_path)
    client = openai.OpenAI(api_key="fixture-no-network", base_url="https://fixture.invalid/v1",
        http_client=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(500))))
    agent = SimpleNamespace(runtime_context=context, _session_db=value, session_id="session", client=client,
        api_mode="chat_completions", provider="openai", _cached_system_prompt="frozen prefix")
    run = RuntimeRun(agent, value, "session", "run", run_id, fence["holder"], fence["generation"], context)
    compressor = MicroCompactionMixin()
    compressor._session_db, compressor._session_id = value, "session"
    held = value.get_messages_as_conversation("session")
    before = value.export_session("session")
    with agent_runtime_scope(context):
        token = bind_runtime_run(run)
        try:
            skipped, watermark = compressor._micro_start_watermark(held)
            assert skipped is None and compressor._micro_context_source is not None
            def fail(*args):
                raise OSError("injected projection commit failure")
            monkeypatch.setattr(value, "_commit_context_projection_on_conn", fail)
            compacted = [dict(item) for item in SUMMARY]
            assert compressor._sync_micro_compact_to_db(compacted, held=held, start_watermark=watermark) is False
            assert value.export_session("session") == before
            assert all("_row_id" not in message for message in compacted)
        finally:
            reset_runtime_run(token, run)
            client.close()
