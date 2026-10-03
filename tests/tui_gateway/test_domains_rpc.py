import base64
import json

import pytest

from tests.tui_gateway.test_artifact_rpc import artifacts, result, denied  # noqa: F401
from tests.hermes_cli.test_domain_decisions import decision  # noqa: F401

pytestmark = pytest.mark.platforms("linux")


def job(rpc):
    return {"job_id": "creative-example", "project_id": rpc.project()["id"], "adapter": "creative",
            "arguments": {"brief": "Storyboard brief", "prompts": ["A blue doorway"], "continuity": ["Blue door"]}}


def test_domain_rpc_prepares_exact_outputs_then_publishes_manifest_last(artifacts):
    request = {"command_id": "domain-control", "job_json": json.dumps(job(artifacts))}
    prepared = result(artifacts.call("runtime.domain.prepare", **request))
    assert len(prepared["proposals"]) == 2 and prepared["publication_atomic"] is False
    again = result(artifacts.call("runtime.domain.prepare", **request))
    assert again == prepared
    approvals = [{key: p[key] for key in ("approval_id", "approval_digest")} for p in prepared["proposals"]]
    denied(artifacts.call("runtime.domain.publish", **request, approvals=list(reversed(approvals))), "approval_mismatch")
    published = result(artifacts.call("runtime.domain.publish", **request, approvals=approvals))
    assert published["state"] == "published" and published["publication_atomic"] is False
    manifest = published["manifest"]
    fetched = result(artifacts.call("runtime.artifact.get", project_id=manifest["project_id"],
        artifact_id=manifest["artifact_id"], version=manifest["version"]))
    document = json.loads(base64.b64decode(fetched["data_base64"]))
    assert document["domain_metadata"]["production_status"] == "awaiting_production"
    assert document["artifact_refs"][0]["sha256"] == published["outputs"][0]["sha256"]
    assert document["external_effects"] == "none"
    status = result(artifacts.call("runtime.artifact.status", command_id="domain-control"))
    assert status["result"]["manifest"] == published["manifest"]
    denied(artifacts.call("runtime.artifact.get", "b", project_id=manifest["project_id"],
        artifact_id=manifest["artifact_id"], version=manifest["version"]))
    current = result(artifacts.call("runtime.project.get", project_id=manifest["project_id"]))["project"]
    result(artifacts.call("runtime.project.grants.set", project_id=manifest["project_id"],
        expected_revision=current["revision"], grants=[]))
    denied(artifacts.call("runtime.artifact.status", command_id="domain-control"))


def test_domain_rpc_cannot_replace_authority_or_dispatch_unknown_adapter(artifacts):
    data = job(artifacts)
    data["arguments"]["context"] = "other-user"
    denied(artifacts.call("runtime.domain.prepare", command_id="bad-authority", job_json=json.dumps(data)))
    # Failed preparation retains its bounded claim; explicit cancel ends it.
    result(artifacts.call("runtime.artifact.cancel", command_id="bad-authority"))
    data["arguments"].pop("context")
    data["adapter"] = "host_shell"
    denied(artifacts.call("runtime.domain.prepare", command_id="bad-adapter", job_json=json.dumps(data)))


def _publish_job(rpc, data, command):
    request = {"command_id": command, "job_json": json.dumps(data)}
    prepared = result(rpc.call("runtime.domain.prepare", **request))
    approvals = [{key: p[key] for key in ("approval_id", "approval_digest")} for p in prepared["proposals"]]
    return result(rpc.call("runtime.domain.publish", **request, approvals=approvals))


def _document(rpc, row):
    result_bytes = result(rpc.call("runtime.artifact.get", project_id=row["project_id"],
        artifact_id=row["artifact_id"], version=row["version"]))
    return base64.b64decode(result_bytes["data_base64"])


def test_owned_decision_recompute_publishes_exact_arithmetic_not_authority(artifacts, decision):
    data = {"job_id": "decision", "project_id": artifacts.project()["id"], "adapter": "decision",
            "arguments": decision}
    published = _publish_job(artifacts, data, "decision-control")
    computed = json.loads(_document(artifacts, published["outputs"][0]))
    assert computed["leaders"] == ["a"] and computed["action_authorized"] is False
    manifest = json.loads(_document(artifacts, published["manifest"]))
    assert manifest["validator_manifest"]["fact_semantics"] == "caller_declared_unverified"


def test_owned_tutor_retries_have_stable_clock_and_revision_chain(artifacts):
    data = {"job_id": "tutor", "project_id": artifacts.project()["id"], "adapter": "tutor",
            "arguments": {"goal": "Practice fractions", "difficulty": 1}}
    published = _publish_job(artifacts, data, "tutor-control")
    first = published["outputs"][0]
    data["job_id"] = "tutor-hint"
    data["arguments"] = {"prior_ref": {key: first[key] for key in ("artifact_id", "version", "sha256")},
                         "action": {"type": "hint"}}
    second = _publish_job(artifacts, data, "tutor-hint-control")["outputs"][0]
    assert second["artifact_id"] == first["artifact_id"]
    assert second["version"] > first["version"] and second["parent_version"] == first["version"]


def test_real_csv_upload_transform_workbook_notebook_and_trace(artifacts):
    project = artifacts.project()["id"]
    source = {"project_id": project, "command_id": "csv-upload", "request_id": "csv-source",
              "mime": "text/csv", "content_base64": base64.b64encode(b"group,amount\nA,20\nA,40\n").decode()}
    preview = result(artifacts.call("runtime.artifact.bytes.prepare", **source))
    row = result(artifacts.call("runtime.artifact.bytes.publish", **source,
        **{key: preview[key] for key in ("approval_id", "approval_digest")}))
    options = {"encoding": "utf-8", "delimiter": ",", "date_format": None, "currency": None,
               "null_values": [""], "units": {"amount": "points"}, "duplicate_keys": "allow",
               "column_types": {"group": "text", "amount": "decimal"}}
    data = {"job_id": "data-total", "project_id": project, "adapter": "data", "arguments": {
        "inputs": [{"source_id": "original", "ref": {key: row[key] for key in ("artifact_id", "version", "sha256")},
                    "options": options}],
        "recipe": {"base": "original", "aggregate": {"group_by": ["group"],
            "aggregations": {"total": {"operation": "sum", "column": "amount"}}, "nulls": "reject"},
            "exports": ["csv", "xlsx", "ipynb"], "charts": [{"kind": "bar", "category": "group", "value": "total"}]}}}
    published = _publish_job(artifacts, data, "data-control")
    assert len(published["outputs"]) == 4
    assert b"60" in _document(artifacts, published["outputs"][0])
    workbook = _document(artifacts, published["outputs"][1])
    from hermes_cli.domain_xlsx import validate_xlsx
    assert validate_xlsx(workbook)
    notebook = json.loads(_document(artifacts, published["outputs"][2]))
    assert notebook["nbformat"] == 4
    from hermes_cli.artifact_formats import validate_artifact
    chart = _document(artifacts, published["outputs"][3])
    assert validate_artifact(chart, "image/png")["details"]["width"] > 0
    manifest = json.loads(_document(artifacts, published["manifest"]))
    trace = manifest["domain_metadata"]["lineage"][0]
    assert len(trace) == 2 and {entry["source_id"] for entry in trace} == {"original"}
    assert manifest["inputs"][0]["sha256"] == row["sha256"]


def test_bytes_import_rejects_unsupported_mime_and_malformed_base64(artifacts):
    project = artifacts.project()["id"]
    denied(artifacts.call("runtime.artifact.bytes.prepare", project_id=project, command_id="bad-bytes",
        request_id="bad", mime="application/octet-stream", content_base64="bad!!"))
