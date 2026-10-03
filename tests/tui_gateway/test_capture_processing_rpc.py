"""Capture retrieval and reviewed filing exercise the registered RPC and real stores."""
import base64
import json
import time

import pytest

from tests.tui_gateway.test_artifact_rpc import artifacts, denied, publish, result  # noqa: F401
from tests.tui_gateway.test_project_sources_rpc import capture, source

pytestmark = pytest.mark.platforms("linux")


def item(capture_id, revision=0, *, destination=None, target=None):
    return {"capture_id": capture_id, "expected_revision": revision,
            "filed_project_id": destination, "consolidated_into": target}


def batch(rpc, project_id, batch_id, items):
    args = {"project_id": project_id, "batch_id": batch_id, "items": items}
    preview = result(rpc.call("runtime.capture.batch.preview", **args))
    return result(rpc.call("runtime.capture.batch.commit", **args, preview_digest=preview["preview_digest"])), preview


def test_real_text_processing_typo_retrieval_stale_index_and_retained_original(artifacts):
    project = artifacts.project()["id"]
    original = source(artifacts, project, content="# Garden\nTomatoes irrigation schedule for summer\n")
    captured = capture(artifacts, project, original, annotation="Balcony notebook")
    before = result(artifacts.call("runtime.capture.inspect", capture_id=captured["capture_id"]))
    assert before["processing"]["status"] == "not_indexed"
    assert result(artifacts.call("runtime.capture.search", project_id=project, query="irrigation"))["matches"] == []
    processed = result(artifacts.call("runtime.capture.process", capture_id=captured["capture_id"], expected_extraction_sequence=0))
    assert processed["processing"]["status"] == "indexed"
    assert processed["processing"]["method"] == "utf8_identity"
    assert processed["capture"]["original_ref"] == original
    found = result(artifacts.call("runtime.capture.search", project_id=project, query="remember tomato irrigaton"))
    assert found["matches"][0]["capture"]["original_ref"] == original
    assert found["search_mode"] == "lexical_fuzzy" and not found["complete"]
    assert "irrigation" in found["matches"][0]["matched_terms"] and "owner_actor" not in json.dumps(found)
    denied(artifacts.call("runtime.capture.process", capture_id=captured["capture_id"], expected_extraction_sequence=0), "revision_conflict")
    result(artifacts.call("runtime.capture.extraction.record", capture_id=captured["capture_id"], status="failed", failure_code="external_failure"))
    stale = result(artifacts.call("runtime.capture.inspect", capture_id=captured["capture_id"]))
    assert stale["processing"]["status"] == "stale" and len(stale["capture"]["extractions"]) == 2
    assert result(artifacts.call("runtime.capture.search", project_id=project, query="irrigation"))["matches"] == []
    assert result(artifacts.call("runtime.capture.search", project_id=project, query="balcony"))["matches"][0]["capture"]["capture_id"] == captured["capture_id"]
    downloaded = result(artifacts.call("runtime.capture.read", capture_id=captured["capture_id"]))
    assert base64.b64decode(downloaded["data_base64"]) == b"# Garden\nTomatoes irrigation schedule for summer\n"


def test_unsupported_extraction_is_metadata_only_then_supplied_text_is_searchable(artifacts):
    from tests.hermes_cli.test_artifact_formats import _png
    project = artifacts.project()["id"]
    data = _png()
    binary = publish(artifacts, {"project_id": project, "command_id": "binary-command", "request_id": "binary-request",
        "mime": "image/png", "content_base64": base64.b64encode(data).decode()}, mode="bytes.")
    original = {"artifact_id": binary["artifact_id"], "version": binary["version"]}
    captured = capture(artifacts, project, original, annotation="Receipt from garden shop")
    failed = result(artifacts.call("runtime.capture.process", capture_id=captured["capture_id"], expected_extraction_sequence=0))
    assert failed["processing"]["status"] == "metadata_only"
    assert failed["processing"]["failure_code"] == "unsupported_local_extraction"
    assert failed["capture"]["original_ref"] == original
    assert base64.b64decode(result(artifacts.call("runtime.capture.read", capture_id=captured["capture_id"]))["data_base64"]) == data
    extracted = source(artifacts, project, name="extracted", content="# Supplied\nRare orchid fertilizer\n")
    result(artifacts.call("runtime.capture.extraction.record", capture_id=captured["capture_id"], status="succeeded", extracted_ref=extracted))
    indexed = result(artifacts.call("runtime.capture.process", capture_id=captured["capture_id"], expected_extraction_sequence=2, source="latest_extraction"))
    assert indexed["processing"]["method"] == "supplied_text" and indexed["capture"]["original_ref"] == original
    assert result(artifacts.call("runtime.capture.search", project_id=project, query="orchid"))["matches"][0]["capture"]["original_ref"] == original


def test_batch_exact_preview_cas_idempotency_and_reversible_duplicate_history(artifacts):
    project, destination = artifacts.project()["id"], artifacts.project()["id"]
    original = source(artifacts, project)
    first = capture(artifacts, project, original, annotation="First receipt", acquired_at=time.time() - 100)
    second = capture(artifacts, project, original, "second", annotation="Later annotation", acquired_at=time.time() - 50)
    items = [item(first["capture_id"], destination=destination), item("second", destination=destination, target=first["capture_id"])]
    preview = result(artifacts.call("runtime.capture.batch.preview", project_id=project, batch_id="file-two", items=items))
    assert preview["scope"] == "capture_metadata_only" and preview["originals_preserved"]
    assert result(artifacts.call("runtime.capture.get", capture_id="second"))["capture"] == second
    denied(artifacts.call("runtime.capture.batch.commit", project_id=project, batch_id="file-two", items=items,
        preview_digest="0" * 64), "revision_conflict")
    committed = result(artifacts.call("runtime.capture.batch.commit", project_id=project, batch_id="file-two", items=items,
        preview_digest=preview["preview_digest"]))
    replay = result(artifacts.call("runtime.capture.batch.commit", project_id=project, batch_id="file-two", items=items,
        preview_digest=preview["preview_digest"]))
    assert replay["replayed"] and replay["items"] == committed["items"] and not committed["replayed"]
    inspected = result(artifacts.call("runtime.capture.inspect", capture_id="second"))
    assert inspected["capture"]["annotation"] == second["annotation"] and inspected["capture"]["acquired_at"] == second["acquired_at"]
    assert inspected["consolidated_into"] == first["capture_id"] and inspected["capture"]["project_id"] == project
    assert len(inspected["filing_history"]) == 1 and len(inspected["consolidation_history"]) == 1
    undone, _ = batch(artifacts, project, "undo-two", [item(first["capture_id"], 1), item("second", 1)])
    assert all(record["revision"] == 2 for record in undone["items"])
    restored = result(artifacts.call("runtime.capture.inspect", capture_id="second"))
    assert restored["capture"]["filed_project_id"] is None and restored["consolidated_into"] is None
    assert [entry["consolidated_into"] for entry in restored["consolidation_history"]] == [first["capture_id"], None]
    assert len(result(artifacts.call("runtime.capture.list", project_id=project))["captures"]) == 2
    assert base64.b64decode(result(artifacts.call("runtime.capture.read", capture_id="second"))["data_base64"])


def test_batch_stale_member_refuses_entire_batch_and_requires_matching_duplicate(artifacts):
    project, destination = artifacts.project()["id"], artifacts.project()["id"]
    one = source(artifacts, project)
    other = source(artifacts, project, name="different", content="# Different\ncontent\n")
    capture(artifacts, project, one, "one")
    capture(artifacts, project, other, "two")
    denied(artifacts.call("runtime.capture.batch.preview", project_id=project, batch_id="not-duplicates",
        items=[item("two", target="one")]), "duplicate_mismatch")
    items = [item("one", destination=destination), item("two", destination=destination)]
    preview = result(artifacts.call("runtime.capture.batch.preview", project_id=project, batch_id="stale", items=items))
    result(artifacts.call("runtime.capture.file", capture_id="two", expected_revision=0, filed_project_id=destination))
    denied(artifacts.call("runtime.capture.batch.commit", project_id=project, batch_id="stale", items=items,
        preview_digest=preview["preview_digest"]), "revision_conflict")
    assert result(artifacts.call("runtime.capture.get", capture_id="one"))["capture"]["revision"] == 0
    assert result(artifacts.call("runtime.capture.inspect", capture_id="one"))["filing_history"] == []


def test_live_grants_foreign_transport_profile_aba_and_batch_destination_revocation(artifacts):
    import os
    original_env = os.environ.copy()
    projects = {label: artifacts.project(label)["id"] for label in ("a", "b")}
    for label in ("a", "b"):
        artifact = publish(artifacts, {"project_id": projects[label], "command_id": "seed", "request_id": "seed",
            "content": "# Private\n" + label + " scopedheliotrope\n"}, label=label)
        result(artifacts.call("runtime.capture.create", label, project_id=projects[label], capture_id="same-id",
            original_ref={"artifact_id": artifact["artifact_id"], "version": artifact["version"]}, annotation=label))
        result(artifacts.call("runtime.capture.process", label, capture_id="same-id", expected_extraction_sequence=0))
    for label in ("a", "b", "a"):
        found = result(artifacts.call("runtime.capture.search", label, project_id=projects[label], query="scopedheliotrope"))
        assert len(found["matches"]) == 1 and found["matches"][0]["capture"]["annotation"] == label
    assert os.environ == original_env
    denied(artifacts.call("runtime.capture.search", "b", project_id=projects["a"], query="scopedheliotrope"), "project_not_granted")
    assert denied(artifacts.call("runtime.capture.search", project_id=projects["a"], query="scopedheliotrope", via=artifacts.peers["b"]))["code"] == 4001
    destination = artifacts.project()["id"]
    items = [item("same-id", destination=destination)]
    preview = result(artifacts.call("runtime.capture.batch.preview", project_id=projects["a"], batch_id="revoke-destination", items=items))
    current = result(artifacts.call("runtime.project.get", project_id=destination))["project"]
    result(artifacts.call("runtime.project.grants.set", project_id=destination, expected_revision=current["revision"], grants=[]))
    denied(artifacts.call("runtime.capture.batch.commit", project_id=projects["a"], batch_id="revoke-destination", items=items,
        preview_digest=preview["preview_digest"]), "project_grant_revoked")
    assert result(artifacts.call("runtime.capture.get", capture_id="same-id"))["capture"]["revision"] == 0
    current = result(artifacts.call("runtime.project.get", project_id=projects["a"]))["project"]
    result(artifacts.call("runtime.project.grants.set", project_id=projects["a"], expected_revision=current["revision"], grants=[]))
    denied(artifacts.call("runtime.capture.search", project_id=projects["a"], query="scopedheliotrope"), "project_grant_revoked")
    denied(artifacts.call("runtime.capture.inspect", capture_id="same-id"), "project_grant_revoked")


@pytest.mark.parametrize("extra", [{"actor": {}}, {"path": "/etc/passwd"}, {"fetch_url": True}, {"embeddings": True}])
def test_process_rejects_unowned_sources_and_authority_injection(artifacts, extra):
    assert denied(artifacts.call("runtime.capture.process", capture_id="missing", expected_extraction_sequence=0, **extra))["code"] == 4000


def test_read_only_project_grant_search_scan_bounds_and_no_implicit_cross_project_filing(artifacts):
    project, other = artifacts.project()["id"], artifacts.project()["id"]
    original = source(artifacts, project, content="# Orchid\nOrchid greenhouse watering\n")
    capture(artifacts, project, original, "one", annotation="Orchid first")
    capture(artifacts, project, original, "two", annotation="Orchid later")
    result(artifacts.call("runtime.capture.process", capture_id="one", expected_extraction_sequence=0))
    result(artifacts.call("runtime.capture.file", capture_id="one", expected_revision=0, filed_project_id=other))
    # Filing remains a label, never an implicit duplicate index or expanded grant.
    assert result(artifacts.call("runtime.capture.search", project_id=other, query="orchid"))["matches"] == []
    limited = result(artifacts.call("runtime.capture.search", project_id=project, query="orchid", scan_limit=1))
    assert limited["scanned"] == 1 and limited["truncated"] and not limited["complete"]
    current = result(artifacts.call("runtime.project.get", project_id=project))["project"]
    result(artifacts.call("runtime.project.grants.set", project_id=project, expected_revision=current["revision"],
        grants=[{"principal_id": "owner", "agent_id": "ryoko", "permissions": ["read"]}]))
    found = result(artifacts.call("runtime.capture.search", project_id=project, query="orchid"))
    assert len(found["matches"]) == 2
    assert result(artifacts.call("runtime.capture.inspect", capture_id="one"))["processing"]["status"] == "indexed"
    denied(artifacts.call("runtime.capture.process", capture_id="one", expected_extraction_sequence=1), "project_grant_revoked")
    assert denied(artifacts.call("runtime.capture.search", project_id=project, query="orchid", scan_limit=501))["code"] == 4000
    assert denied(artifacts.call("runtime.capture.search", project_id=project, query="orchid", limit=51))["code"] == 4000
