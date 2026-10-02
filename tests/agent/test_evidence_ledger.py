"""Bounded project resume joins authorized references, never private memory or transcript text."""
import json
import time

import pytest

from agent import evidence_ledger as ledger
from agent.project_context import authorize_project, cas_project_metadata
from hermes_cli import project_sources as sources
from tests.hermes_cli.test_project_sources import source_runtime  # noqa: F401

pytestmark = pytest.mark.platforms("linux")


def _anchor(fixture, original, **changes):
    fields = {"project_id": fixture.project, "kind": "source_span", "source_ref": original,
              "source_version": str(original["version"]), "range_ref": {"unit": "byte", "start": 0, "end": 5},
              "authority": "observed", "validity": "current", "annotation": "Explicit source observation"}
    fields.update(changes)
    return ledger.create_evidence_anchor(fixture.context, fixture.db, **fields)


def test_anchor_source_revision_authority_and_freshness_are_not_confidence_proof(source_runtime):
    fixture = source_runtime
    original = fixture.publish()
    anchor = _anchor(fixture, original, fresh_until=time.time() - 1)
    restored = ledger.get_evidence_anchor(fixture.context, fixture.db, anchor["anchor_id"])
    assert restored["source_ref"] == original and restored["source_version"] == str(original["version"])
    assert restored["authority"] == "observed" and restored["effective_validity"] == "stale"
    with pytest.raises((TypeError, ValueError)):
        _anchor(fixture, original, confidence=0.99)
    with pytest.raises((ValueError, PermissionError)):
        _anchor(fixture, original, kind="approval", source_ref={"approval_id": "unrelated-or-invented-approval"},
                range_ref=None, authority="user_approved")


def test_resume_joins_current_versions_mission_and_blockers_without_private_prompt(source_runtime):
    fixture = source_runtime
    original = fixture.publish()
    capture = sources.create_capture(fixture.context, fixture.db, project_id=fixture.project, original_ref=original,
                                     annotation="Captured project context")
    sources.record_capture_extraction(fixture.context, fixture.db, capture["capture_id"], status="unavailable")
    _anchor(fixture, original, fresh_until=time.time() - 1)
    project = authorize_project(fixture.context, fixture.project)
    cas_project_metadata(fixture.context, fixture.project, project["revision"], {
        "purpose": "Produce a sourced Markdown brief", "source_refs": [{"capture_id": capture["capture_id"]}],
        "canonical_artifact_refs": [original],
        "active_mission_refs": [{"session_id": fixture.run.session_id, "run_id": fixture.run.run_id}]})
    newest = fixture.publish("# Revised\n\nUpdated project text\n", artifact_id=original["artifact_id"],
                             parent_version=original["version"], expected_head_version=original["version"])
    resumed = ledger.assemble_resume(fixture.context, fixture.db, fixture.project)
    assert resumed["artifacts"][0]["version"] == newest["version"]
    assert resumed["artifacts"][0]["filed_version"] == original["version"]
    assert resumed["missions"][0]["run_id"] == fixture.run.run_id
    assert resumed["sources"][0]["annotation"] == "Captured project context"
    assert {item["code"] for item in resumed["blockers"]} >= {"canonical_filing_stale", "extraction_unavailable", "evidence_stale"}
    assert not resumed["complete"] and resumed["project_revision_stable"]
    assert not resumed["evidence"][0]["execution_authority"]
    assert "PRIVATE_CONVERSATION_SENTINEL" not in json.dumps(resumed)
    assert "locator" not in json.dumps(resumed) and str(fixture.home) not in json.dumps(resumed)


def test_resume_exposes_bounds_and_stale_mission_refs_instead_of_claiming_completeness(source_runtime):
    fixture = source_runtime
    original = fixture.publish()
    for number in range(3):
        _anchor(fixture, original, annotation="note-" + str(number))
    project = authorize_project(fixture.context, fixture.project)
    cas_project_metadata(fixture.context, fixture.project, project["revision"], {
        "active_mission_refs": [{"session_id": "private-other-session", "run_id": "unavailable-run"}]})
    resumed = ledger.assemble_resume(fixture.context, fixture.db, fixture.project, evidence_limit=1)
    assert len(resumed["evidence"]) == 1 and resumed["truncated"]["evidence"]
    assert resumed["missions"] == []
    assert {item["code"] for item in resumed["blockers"]} >= {"mission_scope_unavailable"}
    with pytest.raises(ValueError):
        ledger.assemble_resume(fixture.context, fixture.db, fixture.project, evidence_limit=1000)
