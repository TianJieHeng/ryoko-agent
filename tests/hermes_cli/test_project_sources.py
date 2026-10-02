"""Captures/templates retain authorized immutable sources through real Markdown publication."""
import base64
import json
import time
from types import SimpleNamespace

import httpx
from openai import OpenAI
import pytest

from agent.agent_identity import resolve_agent_context
from agent.identity_lifecycle import agent_runtime_scope
from agent.runtime_commands import RuntimeRun, bind_runtime_run, reset_runtime_run
from hermes_cli import project_sources as sources
from hermes_cli import projects_db
from hermes_state import SessionDB

pytestmark = pytest.mark.platforms("linux")


@pytest.fixture
def source_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    grants = [{"principal_id": "owner", "agent_id": "primary", "permissions": ["read", "write", "share"]}]
    with projects_db.connect_closing() as conn:
        project = projects_db.create_project(conn, name="Sources", owner_principal_id="owner", grants=grants)
        other = projects_db.create_project(conn, name="Other", owner_principal_id="owner", grants=grants)
    raw = {"agent_identity": {"schema_version": 1, "principal_id": "owner", "profile_id": "fixture",
        "primary_agent_id": "primary", "active_agent_id": "primary", "agents": {
            "primary": {"policy_version": 1, "role": "primary", "memory_backend": "personal_mcp",
                        "project_grants": [project, other]}}}}
    (tmp_path / "config.yaml").write_text(json.dumps(raw))
    context = resolve_agent_context(raw, session_id="session", profile_home=tmp_path)
    db = SessionDB(tmp_path / "state.db")
    db.create_session("session", source="cli")
    db.claim_session_agent_identity("session", context.identity.to_record())
    actor = {"principal_id": "owner", "profile_id": "fixture", "agent_id": "primary"}
    receipt = db.submit_runtime_command("session", actor, {"schema_version": 1, "command_id": "source-run",
        "idempotency_key": "source-run", "operation": "submit", "payload": {"text": "PRIVATE_CONVERSATION_SENTINEL"}})
    assert db.try_acquire_session_turn_lease("session", "source-owner", ttl_seconds=300)
    generation = db.get_session_turn_lease("session")["generation"]
    assert db.claim_runtime_command("session", "source-run", holder="source-owner", generation=generation)
    client = OpenAI(api_key="fixture", base_url="https://fixture.invalid/v1",
                    http_client=httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(500))))
    agent = SimpleNamespace(runtime_context=context, _session_db=db, session_id="session", api_mode="chat_completions",
                            provider="openai", client=client)
    run = RuntimeRun(agent, db, "session", "source-run", receipt["run_id"], "source-owner", generation, context)
    from tools import approval
    def human(*_a, **kwargs):
        assert kwargs["allow_session"] is False and kwargs["allow_permanent"] is False
        return "once"
    monkeypatch.setattr(approval, "_presence", lambda: (human, True, False, False))
    monkeypatch.setattr("tools.approval_prompt._present_with_selected_transport", lambda **_kw: {"selected": False})
    counter = 0
    def publish(content="# Original\n\nCaptured text\n", *, artifact_id=None, parent_version=None, expected_head_version=None):
        nonlocal counter
        from hermes_cli.artifact_store import prepare_markdown, publish_markdown
        counter += 1
        proposal = prepare_markdown(run, project_id=project, request_id="source-publication-" + str(counter),
            content=content, artifact_id=artifact_id, parent_version=parent_version, expected_head_version=expected_head_version)
        db.resolve_effect_approval(proposal.approval_id, actor, holder=run.holder,
            generation=run.generation, approval_digest=proposal.approval_digest, choice="once")
        row = publish_markdown(run, proposal)
        return {"artifact_id": row["artifact_id"], "version": row["version"]}
    with agent_runtime_scope(context):
        token = bind_runtime_run(run)
        try:
            yield SimpleNamespace(context=context, db=db, project=project, other=other, run=run, publish=publish,
                                  home=tmp_path, actor=actor, raw=raw)
        finally:
            reset_runtime_run(token, run)
    client.close()
    db.close()


def test_failed_extraction_and_reversible_filing_preserve_original_and_annotations(source_runtime):
    fixture = source_runtime
    original = fixture.publish()
    capture = sources.create_capture(fixture.context, fixture.db, project_id=fixture.project,
        original_ref=original, source_url="https://never-fetch.invalid/original", acquired_at=time.time() - 60,
        annotation="Keep this distinct observation", suggested_project_id=fixture.other)
    assert capture["original_ref"] == original and capture["extractions"] == []
    failed = sources.record_capture_extraction(fixture.context, fixture.db, capture["capture_id"],
                                               status="failed", failure_code="fixture_extractor_unavailable")
    assert failed["original_ref"] == original
    assert failed["extractions"][-1]["status"] == "failed"
    filed = sources.file_capture(fixture.context, fixture.db, capture["capture_id"],
                                 filed_project_id=fixture.other, expected_revision=failed["revision"])
    assert filed["filed_project_id"] == fixture.other and filed["project_id"] == fixture.project
    with pytest.raises(ValueError):
        sources.file_capture(fixture.context, fixture.db, capture["capture_id"],
                             filed_project_id=None, expected_revision=failed["revision"])
    restored = sources.file_capture(fixture.context, fixture.db, capture["capture_id"],
                                    filed_project_id=None, expected_revision=filed["revision"])
    assert restored["filed_project_id"] is None and restored["original_ref"] == original
    assert restored["annotation"] == "Keep this distinct observation"
    read = sources.read_capture(fixture.context, fixture.db, capture["capture_id"])
    assert b"Captured text" in base64.b64decode(read["data_base64"])


def test_duplicate_review_proposes_only_and_keeps_dates_annotations_and_sources(source_runtime):
    fixture = source_runtime
    original = fixture.publish()
    captures = [sources.create_capture(fixture.context, fixture.db, project_id=fixture.project,
        original_ref=original, acquired_at=time.time() - elapsed, annotation=annotation)
        for elapsed, annotation in ((60, "First date"), (30, "Second date"))]
    proposal = sources.propose_capture_duplicates(fixture.context, fixture.db, fixture.project)
    assert set(proposal["groups"][0]["capture_ids"]) == {row["capture_id"] for row in captures}
    assert not proposal["consolidation_performed"] and not proposal["complete"]
    retained = sources.list_captures(fixture.context, fixture.db, fixture.project)
    assert {row["annotation"] for row in retained} == {"First date", "Second date"}
    assert len({row["acquired_at"] for row in retained}) == 2
    assert all(row["original_ref"] == original for row in retained)


def test_template_components_are_explicit_and_baseline_is_immutable(source_runtime):
    fixture = source_runtime
    baseline = fixture.publish("# Brand\n\nINCIDENTAL_CLIENT_NAME\n")
    template = sources.create_template(fixture.context, fixture.db, project_id=fixture.project,
        baseline_ref=baseline, structure=["Title", "Summary", "Evidence"], style={"tone": "plain"}, assets=[],
        slots=[{"name": "client", "purpose": "Recipient name", "required": True}], exclusions=["INCIDENTAL_CLIENT_NAME"])
    assert template["baseline_ref"] == baseline
    assert template["structure"] == ["Title", "Summary", "Evidence"]
    assert template["style"] == {"tone": "plain"}
    assert template["exclusions"] == ["INCIDENTAL_CLIENT_NAME"]
    assert "INCIDENTAL_CLIENT_NAME" not in json.dumps({key: template[key] for key in ("structure", "style", "assets", "slots")})
    assert sources.get_template(fixture.context, fixture.db, template["template_id"], version=template["version"])["baseline_ref"] == baseline
    assert [row["template_id"] for row in sources.list_templates(fixture.context, fixture.db, fixture.project)] == [template["template_id"]]


@pytest.mark.parametrize("url", ["file:///private/source", "https://user:password@example.invalid/file", "https://example.invalid/\nsecret"])
def test_capture_url_is_metadata_only_and_rejects_paths_credentials_and_controls(source_runtime, url):
    fixture = source_runtime
    original = fixture.publish()
    with pytest.raises(sources.ProjectSourceError, match="Source URL"):
        sources.create_capture(fixture.context, fixture.db, project_id=fixture.project, original_ref=original, source_url=url)
    assert sources.list_captures(fixture.context, fixture.db, fixture.project) == []


def test_revoked_grants_block_existing_source_reads_and_new_writes(source_runtime):
    fixture = source_runtime
    original = fixture.publish()
    capture = sources.create_capture(fixture.context, fixture.db, project_id=fixture.project, original_ref=original)
    with projects_db.connect_closing() as conn:
        conn.execute("DELETE FROM project_grants WHERE project_id=?", (fixture.project,))
        conn.commit()
    for operation in (
        lambda: sources.get_capture(fixture.context, fixture.db, capture["capture_id"]),
        lambda: sources.read_capture(fixture.context, fixture.db, capture["capture_id"]),
        lambda: sources.create_capture(fixture.context, fixture.db, project_id=fixture.project, original_ref=original),
    ):
        with pytest.raises((PermissionError, ValueError)):
            operation()
