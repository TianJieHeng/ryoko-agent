"""Deterministic proof from real approved artifacts, never model success prose."""
import json
import os
import time
from copy import deepcopy

import pytest

from agent.evidence_ledger import create_evidence_anchor
from agent.mission_contract import VerificationReceipt
from agent.mission_verifier import verify_criterion, verify_mission
from agent.project_context import project_access
from agent.result_artifacts import artifact_actor
from hermes_cli.artifact_store import prepare_markdown, publish_markdown
from tests.hermes_cli.test_artifact_store import artifact_runtime, approve, publish_fixture  # noqa: F401

pytestmark = pytest.mark.platforms("linux")


def ref(artifact):
    return {"artifact_id": artifact["artifact_id"], "version": artifact["version"], "digest": artifact["sha256"]}


def criterion(key, kind, refs, **params):
    return {"criterion_id": key, "kind": kind, "description": "Exact user-requested artifact criterion",
            "artifact_refs": [ref(row) for row in refs], "parameters": params, "required": True}


def verify(run, project, item, **kwargs):
    receipt = verify_criterion(run.context, run.db, project, item, **kwargs)
    VerificationReceipt.from_dict(receipt)
    return receipt


def test_two_complete_linked_outputs_fail_then_repair_without_losing_passes(artifact_runtime):
    rt = artifact_runtime
    with rt.scope() as run:
        brief = publish_fixture(run, rt.project, "brief", "# Brief\nComplete brief.\n# Decision\nShip blue.\n")
        checklist = publish_fixture(run, rt.project, "checklist", "# Checklist\n- Reviewed\n# Decision\nShip red.\n")
        existence = criterion("complete-files", "existence", [brief, checklist])
        completeness = criterion("complete-sections", "markdown_sections", [brief, checklist], required_sections=["Decision"])
        linked = criterion("linked-decision", "linked_consistency", [brief, checklist], sections=[
            {"artifact_id": brief["artifact_id"], "heading": "Decision"},
            {"artifact_id": checklist["artifact_id"], "heading": "Decision"}])
        snapshot = {"project_id": rt.project, "acceptance": [existence, completeness, linked]}
        original = deepcopy(snapshot)
        receipts = verify_mission(run.context, run.db, snapshot)
        assert [row["result"] for row in receipts] == ["pass", "pass", "fail"]
        assert receipts[2]["details"]["checks"] == ["linked_sections_differ"]
        assert snapshot == original
        for receipt in receipts:
            VerificationReceipt.from_dict(receipt)
            assert receipt["evidence_ref"].startswith("sha256:")
            assert all(row["digest"] for row in receipt["details"]["dependencies"])
        repaired = publish_fixture(run, rt.project, "checklist-repair", "# Checklist\n- Reviewed\n# Decision\nShip blue.\n",
            artifact_id=checklist["artifact_id"], parent_version=1)
        fixed = deepcopy(linked)
        fixed["artifact_refs"][1] = ref(repaired)
        assert verify(run, rt.project, fixed)["result"] == "pass"
        assert verify(run, rt.project, linked)["result"] == "blocked"
        pinned = criterion("accepted-old-version", "existence", [checklist], require_current_head=False)
        assert verify(run, rt.project, pinned)["result"] == "pass"
        assert receipts[0]["result"] == receipts[1]["result"] == "pass"
        assert run.db.read_artifact_version(checklist["artifact_id"], 1, artifact_actor(run.context),
            access=project_access(run.context))["publication_state"] == "committed"


def test_sections_exact_changes_and_bounded_schema_use_full_bytes(artifact_runtime):
    rt = artifact_runtime
    with rt.scope() as run:
        markdown = publish_fixture(run, rt.project, "doc", "# Complete\nNew value.\n```md\n# Fake\nignored\n```\n# Empty\n")
        assert verify(run, rt.project, criterion("real", "markdown_sections", [markdown], required_sections=["Complete"]))["result"] == "pass"
        for heading in ("Fake", "Empty"):
            assert verify(run, rt.project, criterion("section", "markdown_sections", [markdown], required_sections=[heading]))["result"] == "fail"
        assert verify(run, rt.project, criterion("change", "text_exact", [markdown], contains=["New value."], excludes=["Old value."]))["result"] == "pass"
        assert verify(run, rt.project, criterion("full", "text_exact", [markdown], equals="# Complete\nNew value.\n"))["result"] == "fail"
        # JSON bytes can be retained as the current Markdown/plain-text artifact;
        # the verifier parses the entire immutable content, never a model excerpt.
        data = publish_fixture(run, rt.project, "json", '{"label":"blue","count":2}')
        schema = {"type": "object", "required": ["label", "count"], "additionalProperties": False,
                  "properties": {"label": {"type": "string", "enum": ["blue"]}, "count": {"type": "integer", "minimum": 1}}}
        assert verify(run, rt.project, criterion("schema", "json_schema", [data], schema=schema))["result"] == "pass"
        bad = {**schema, "properties": {**schema["properties"], "count": {"const": 3}}}
        assert verify(run, rt.project, criterion("schema", "json_schema", [data], schema=bad))["result"] == "fail"
        for unsupported in ({"$ref": "https://no-fetch.invalid/schema"}, {"type": ["number", "string"]}):
            assert verify(run, rt.project, criterion("schema", "json_schema", [data], schema=unsupported))["result"] == "unsupported"


def test_changed_source_invalidates_only_affected_evidence_and_derivative(artifact_runtime):
    rt = artifact_runtime
    with rt.scope() as run:
        source = publish_fixture(run, rt.project, "source", "# Source\nOriginal fact.\n")
        anchor = create_evidence_anchor(run.context, run.db, project_id=rt.project, kind="artifact_version",
            source_ref={"artifact_id": source["artifact_id"], "version": 1}, source_version="1",
            authority="source_claim", validity="current")
        dependent = publish_fixture(run, rt.project, "derived", "# Finding\nSource-backed result.\n",
            derived_from=[{"artifact_id": source["artifact_id"], "version": 1}], source_refs=[anchor["anchor_id"]])
        cited = publish_fixture(run, rt.project, "cited", "# Citation\nDepends on original evidence.\n", source_refs=[anchor["anchor_id"]])
        independent = publish_fixture(run, rt.project, "independent", "# Separate\nUnrelated accepted result.\n")
        checks = [criterion("derived", "existence", [dependent]), criterion("cited", "existence", [cited]),
                  criterion("independent", "existence", [independent])]
        before = [verify(run, rt.project, item) for item in checks]
        assert [row["result"] for row in before] == ["pass"] * 3
        publish_fixture(run, rt.project, "source-change", "# Source\nCorrected fact.\n", artifact_id=source["artifact_id"], parent_version=1)
        after = [verify(run, rt.project, item) for item in checks]
        assert [row["result"] for row in after] == ["blocked", "blocked", "pass"]
        assert before[2]["details"]["inputs_digest"] == after[2]["details"]["inputs_digest"]
        assert before[2]["evidence_ref"] == after[2]["evidence_ref"]
        assert after[0]["details"]["reason_codes"] == ["artifact_derivative_stale"]
        assert after[1]["details"]["reason_codes"] == ["evidence_not_current"]


def test_unknown_execution_user_acceptance_and_uncommitted_bytes_cannot_pass(artifact_runtime, monkeypatch):
    from agent import verification_evidence
    rt = artifact_runtime
    monkeypatch.setattr(verification_evidence, "verification_status", lambda *_a, **_k: pytest.fail("Unscoped legacy evidence read"))
    with rt.scope() as run:
        artifact = publish_fixture(run, rt.project, "assertion", "# Verification\nAll tests passed. exit_code=0\n")
        for kind, params in (("test_execution", {"command": "pytest", "evidence_ref": "model-said-pass"}), ("user_acceptance", {})):
            outcome = verify(run, rt.project, criterion("unsupported", kind, [artifact], **params))
            assert outcome["result"] == "unsupported" and outcome["details"]["dependencies"] == []
        staged = prepare_markdown(run, project_id=rt.project, request_id="pending", content="# Pending\nNot approved.\n")
        item = criterion("pending", "existence", [])
        descriptor = staged.scope["descriptor"]
        item["artifact_refs"] = [{"artifact_id": descriptor["artifact_id"], "version": 1, "digest": descriptor["sha256"]}]
        assert verify(run, rt.project, item)["result"] == "blocked"
        assert verify(run, rt.project, criterion("deadline", "existence", [artifact]), deadline_at=time.time() - 1)["result"] == "blocked"
        approved = prepare_markdown(run, project_id=rt.project, request_id="orphan", content="# Orphan\nPublished only.\n")
        approve(approved)
        real_register = run.db.register_artifact_version
        monkeypatch.setattr(run.db, "register_artifact_version", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("catalog failure")))
        with pytest.raises(RuntimeError):
            publish_markdown(run, approved)
        monkeypatch.setattr(run.db, "register_artifact_version", real_register)
        orphan = approved.scope["descriptor"]
        item["artifact_refs"] = [{"artifact_id": orphan["artifact_id"], "version": 1, "digest": orphan["sha256"]}]
        assert verify(run, rt.project, item)["result"] == "blocked"


def test_digest_tamper_revoked_grant_and_private_namespace_fail_closed(artifact_runtime):
    from hermes_cli import projects_db
    rt = artifact_runtime
    with rt.scope() as run:
        artifact = publish_fixture(run, rt.project, "secure", "# Result\nPrivate unquoted body.\n")
        item = criterion("owner", "existence", [artifact])
        changed = deepcopy(item)
        changed["artifact_refs"][0]["digest"] = "0" * 64
        assert verify(run, rt.project, changed)["result"] == "blocked"
        row = run.db.read_artifact_version(artifact["artifact_id"], 1, artifact_actor(run.context), access=project_access(run.context))
        path = rt.home / row["descriptor"]["locator"]
        original = path.read_bytes()
        path.unlink()  # Controlled fixture corruption replaces the read-only published inode.
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as stream:
            stream.write(b"altered immutable bytes")
        assert verify(run, rt.project, item)["result"] == "blocked"
        path.write_bytes(original)
        assert verify(run, rt.project, item)["result"] == "pass"
        with projects_db.connect_closing() as db:
            db.execute("DELETE FROM project_grants WHERE project_id=?", (rt.project,))
            db.commit()
        blocked = verify(run, rt.project, item)
        assert blocked["result"] == "blocked"
        assert "Private unquoted body" not in json.dumps(blocked)
    with pytest.raises(Exception):
        verify_criterion(rt.contexts["primary"], rt.db, rt.project, {"criterion_id": "malformed", "kind": "model_judge"})


def test_writer_revalidation_rejects_changed_inputs_and_preserves_pinned_outputs(artifact_runtime):
    from agent.mission_verifier import dependency_is_current
    rt = artifact_runtime
    with rt.scope() as run:
        source = publish_fixture(run, rt.project, "race-source", "# Source\nFirst input.\n")
        anchor = create_evidence_anchor(run.context, run.db, project_id=rt.project, kind="artifact_version",
            source_ref={"artifact_id": source["artifact_id"], "version": 1}, source_version="1",
            authority="observed", validity="current")
        output = publish_fixture(run, rt.project, "race-output", "# Output\nVerified against first input.\n",
                                 source_refs=[anchor["anchor_id"]])
        item = criterion("fresh-source", "existence", [output])
        observed = verify(run, rt.project, item)
        actor, access = artifact_actor(run.context), project_access(run.context)
        def recheck(dependencies, rule):
            with access.guard(rt.project, actor, "read"), run.db._runtime_read() as conn:
                return [dependency_is_current(run.db, conn, dep, actor, access, criterion=rule) for dep in dependencies]
        assert all(recheck(observed["details"]["dependencies"], item))
        pinned = criterion("pinned-source", "existence", [source], require_current_head=False)
        pinned_receipt = verify(run, rt.project, pinned)
        publish_fixture(run, rt.project, "race-changed", "# Source\nInput changed before receipt commit.\n",
                        artifact_id=source["artifact_id"], parent_version=1)
        assert not all(recheck(observed["details"]["dependencies"], item))
        assert all(recheck(pinned_receipt["details"]["dependencies"], pinned))
        assert not all(recheck(pinned_receipt["details"]["dependencies"], {**pinned, "parameters": {}}))


def test_same_profile_wrong_context_and_newly_stale_source_do_not_leak(artifact_runtime, monkeypatch):
    import agent.mission_verifier as verifier
    rt = artifact_runtime
    with rt.scope() as run:
        source = publish_fixture(run, rt.project, "expiring-source", "# Source\nFresh source.\n")
        now = time.time()
        anchor = create_evidence_anchor(run.context, run.db, project_id=rt.project, kind="artifact_version",
            source_ref={"artifact_id": source["artifact_id"], "version": 1}, source_version="1",
            authority="source_claim", validity="current", fresh_until=now + 5)
        output = publish_fixture(run, rt.project, "expiring-output", "# Output\nScoped source result.\n", source_refs=[anchor["anchor_id"]])
        item = criterion("fresh", "existence", [output])
        assert verify(run, rt.project, item)["result"] == "pass"
        wrong = verify_criterion(rt.contexts["specialist"], rt.db, rt.project, item)
        assert wrong["result"] == "blocked" and wrong["details"]["dependencies"] == []
        monkeypatch.setattr(verifier.time, "time", lambda: now + 10)
        stale = verify(run, rt.project, item)
        assert stale["result"] == "blocked" and stale["details"]["reason_codes"] == ["evidence_not_current"]
