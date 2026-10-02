"""BE07 source controls are real owned RPC consumers with bounded typed projections."""
import base64
import json
import time

import pytest

from tests.tui_gateway.test_artifact_rpc import artifacts, denied, publish, result  # noqa: F401

pytestmark = pytest.mark.platforms("linux")


def source(rpc, project_id, *, name="original", content="# Original\n\nSource text\n"):
    record = publish(rpc, {"project_id": project_id, "command_id": "command-" + name,
                           "request_id": "request-" + name, "content": content})
    return {"artifact_id": record["artifact_id"], "version": record["version"]}


def capture(rpc, project_id, original, capture_id="capture-one", **kwargs):
    return result(rpc.call("runtime.capture.create", project_id=project_id, capture_id=capture_id,
                           original_ref=original, **kwargs))["capture"]


def test_rpc_capture_original_extraction_filing_dedup_and_safe_download(artifacts):
    project = artifacts.project()
    other = artifacts.project()
    original = source(artifacts, project["id"], content="# Original\n\n<script>inert</script>\n")
    first = capture(artifacts, project["id"], original, annotation="First acquisition", acquired_at=time.time() - 60,
                    source_url="https://unfetched.invalid/source")
    second = capture(artifacts, project["id"], original, "capture-two", annotation="Second acquisition", acquired_at=time.time() - 30)
    assert first["extractions"] == [] and second["capture_id"] != first["capture_id"]
    assert "owner_actor" not in json.dumps(first) and "locator" not in json.dumps(first)
    failed = result(artifacts.call("runtime.capture.extraction.record", capture_id=first["capture_id"],
                                  status="failed", failure_code="extractor_unavailable"))["capture"]
    assert failed["original_ref"] == original and failed["extractions"][-1]["status"] == "failed"
    filed = result(artifacts.call("runtime.capture.file", capture_id=first["capture_id"],
        expected_revision=failed["revision"], filed_project_id=other["id"]))["capture"]
    assert filed["project_id"] == project["id"] and filed["filed_project_id"] == other["id"]
    restored = result(artifacts.call("runtime.capture.file", capture_id=first["capture_id"],
        expected_revision=filed["revision"], filed_project_id=None))["capture"]
    assert restored["filed_project_id"] is None
    assert result(artifacts.call("runtime.capture.get", capture_id=first["capture_id"]))["capture"] == restored
    listed = result(artifacts.call("runtime.capture.list", project_id=project["id"], limit=1))
    assert len(listed["captures"]) == 1 and listed["limit_reached"] and not listed["complete"]
    duplicates = result(artifacts.call("runtime.capture.duplicates", project_id=project["id"]))
    assert set(duplicates["groups"][0]["capture_ids"]) == {first["capture_id"], second["capture_id"]}
    assert not duplicates["consolidation_performed"]
    read = result(artifacts.call("runtime.capture.read", capture_id=first["capture_id"], limit=65536))
    assert read["preview_mode"] == "plain_text"
    assert base64.b64decode(read["data_base64"]) == b"# Original\n\n<script>inert</script>\n"


def test_rpc_explicit_template_components_and_typed_evidence_resume(artifacts):
    project = artifacts.project()
    original = source(artifacts, project["id"], content="# Brand\n\nINCIDENTAL_NAME\n")
    first = capture(artifacts, project["id"], original, annotation="Explicit project source")
    template = result(artifacts.call("runtime.template.create", project_id=project["id"], template_id="template-one",
        baseline_ref=original, structure=["Title", "Evidence"], style={"tone": "plain"}, assets=[],
        slots=[{"name": "client", "purpose": "Named recipient", "required": True}], exclusions=["INCIDENTAL_NAME"]))["template"]
    assert template["baseline_ref"] == original and "owner_actor" not in json.dumps(template)
    assert result(artifacts.call("runtime.template.get", template_id=template["template_id"]))["template"] == template
    assert result(artifacts.call("runtime.template.list", project_id=project["id"]))["templates"] == [template]
    anchor = result(artifacts.call("runtime.evidence.create", project_id=project["id"], anchor_id="anchor-one",
        kind="source_span", source_ref=original, source_version=str(original["version"]),
        range_ref={"unit": "byte", "start": 0, "end": 5}, authority="source_claim", validity="current",
        fresh_until=time.time() - 1, annotation="Explicit supported observation"))["evidence"]
    assert anchor["effective_validity"] == "stale" and not anchor["grants_execution"]
    assert result(artifacts.call("runtime.evidence.get", anchor_id="anchor-one"))["evidence"] == anchor
    assert result(artifacts.call("runtime.evidence.list", project_id=project["id"]))["evidence"] == [anchor]
    current = result(artifacts.call("runtime.project.get", project_id=project["id"]))["project"]
    result(artifacts.call("runtime.project.update", project_id=project["id"], expected_revision=current["revision"],
        changes={"source_refs": [{"capture_id": first["capture_id"]}], "canonical_artifact_refs": [original],
                 "purpose": "Reusable project brief"}))
    resumed = result(artifacts.call("runtime.resume.get", project_id=project["id"]))
    assert resumed["artifacts"][0]["version"] == original["version"]
    assert resumed["sources"][0]["annotation"] == "Explicit project source"
    assert {item["code"] for item in resumed["blockers"]} >= {"evidence_stale"}
    assert not resumed["complete"] and not resumed["evidence"][0]["execution_authority"]
    assert "locator" not in json.dumps(resumed) and "owner_actor" not in json.dumps(resumed)


@pytest.mark.parametrize("injected", [{"actor": {"principal_id": "owner"}}, {"profile": "b"},
                                      {"locator": "/private/path"}, {"confidence": 0.99}, {"fetch_url": True}])
def test_rpc_source_control_rejects_authority_path_confidence_and_fetch_injection(artifacts, injected):
    project = artifacts.project()
    response = artifacts.call("runtime.capture.create", project_id=project["id"], capture_id="forged",
                              original_ref={"artifact_id": "unresolved", "version": 1}, **injected)
    assert denied(response)["code"] == 4000


def test_rpc_foreign_transport_profile_revocation_and_oversized_bounds_fail_closed(artifacts):
    project = artifacts.project()
    original = source(artifacts, project["id"])
    first = capture(artifacts, project["id"], original)
    for method, params in (("runtime.capture.get", {"capture_id": first["capture_id"]}),
                           ("runtime.capture.read", {"capture_id": first["capture_id"]}),
                           ("runtime.resume.get", {"project_id": project["id"]})):
        assert denied(artifacts.call(method, via=artifacts.peers["b"], **params))["code"] == 4001
    denied(artifacts.call("runtime.capture.get", "b", capture_id=first["capture_id"]), "source_not_found")
    assert denied(artifacts.call("runtime.capture.list", project_id=project["id"], limit=101))["code"] == 4000
    assert denied(artifacts.call("runtime.resume.get", project_id=project["id"], evidence_limit=101))["code"] == 4000
    current = result(artifacts.call("runtime.project.get", project_id=project["id"]))["project"]
    result(artifacts.call("runtime.project.grants.set", project_id=project["id"], expected_revision=current["revision"], grants=[]))
    denied(artifacts.call("runtime.capture.get", capture_id=first["capture_id"]), "project_grant_revoked")
    denied(artifacts.call("runtime.resume.get", project_id=project["id"]), "project_grant_revoked")
