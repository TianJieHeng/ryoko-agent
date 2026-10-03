"""Real owned research/brief controls, durable dependencies and exact approvals."""
import base64
import hashlib
import json

import pytest

from tests.tui_gateway.test_artifact_rpc import artifacts, denied, publish, result

pytestmark = pytest.mark.platforms("linux")


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def rpc_json(rpc, method, **params):
    return json.loads(result(rpc.call(method, **params))["response_json"])


def source_request(pid, artifact, text, quote):
    data, exact = text.encode(), quote.encode()
    start = data.index(exact)
    return {"source_id": artifact["artifact_id"], "source_type": "project_artifact", "project_id": pid,
            "version": artifact["version"], "sha256": artifact["sha256"], "authority": "authoritative_spec",
            "evidence_ranges": [{"start": start, "end": start + len(exact), "sha256": sha(quote), "quote": quote}]}


def read_bytes(rpc, pid, ref):
    data, offset = bytearray(), 0
    while True:
        chunk = result(rpc.call("runtime.artifact.get", project_id=pid, artifact_id=ref["artifact_id"],
                               version=ref["version"], offset=offset, limit=4096))
        data.extend(base64.b64decode(chunk["data_base64"]))
        if chunk["eof"]:
            assert hashlib.sha256(data).hexdigest() == ref["sha256"]
            return bytes(data)
        offset = chunk["next_offset"]


def brief_fixture(rpc):
    project = rpc.project()
    pid = project["id"]
    text = "# Source\nLimit 10\n"
    first = publish(rpc, {"project_id": pid, "command_id": "source-create", "request_id": "source-create", "content": text})
    original = source_request(pid, first, text, "10")
    previous = rpc_json(rpc, "runtime.research.resolve", request_json=json.dumps([original]))
    brief_text = "# Facts\nThe limit is 10.\n# Advice\nTry ten items.\n# Locked\nKeep exactly.\n"
    brief = publish(rpc, {"project_id": pid, "command_id": "brief-create", "request_id": "brief-create",
                          "content": brief_text, "locked_sections": ["Locked"]})
    changed = publish(rpc, {"project_id": pid, "command_id": "source-change", "request_id": "source-change",
        "content": text.replace("10", "20"), "artifact_id": first["artifact_id"], "parent_version": first["version"]})
    incoming = source_request(pid, changed, text.replace("10", "20"), "20")
    claims = [{"claim_id": name, "section": section, "text": claim, "kind": kind,
        "section_sha256": sha(f"# {section}\n{claim}\n"), "citations": [{"source_id": first["artifact_id"], "range_index": 0}]}
        for name, section, claim, kind in (("limit", "Facts", "The limit is 10.", "fact"),
                                           ("advice", "Advice", "Try ten items.", "interpretation"))]
    body = {"previous_sources": previous["sources"], "claims": claims, "requests": [incoming],
            "updates": [{"claim_id": "limit", "replacement": "The limit is 20."}]}
    params = {"project_id": pid, "command_id": "refresh", "request_id": "refresh", "artifact_id": brief["artifact_id"],
              "parent_version": brief["version"], "request_json": json.dumps(body)}
    return project, brief, incoming, params


def approvals(preview):
    return {"brief_approval_id": preview["brief"]["approval_id"], "brief_approval_digest": preview["brief"]["approval_digest"],
            "manifest_approval_id": preview["manifest"]["approval_id"], "manifest_approval_digest": preview["manifest"]["approval_digest"]}


def test_research_owned_scope_original_bytes_and_missing_source_status(artifacts):
    project = artifacts.project()
    pid, text = project["id"], "# Evidence\nαβ real source\n"
    source = publish(artifacts, {"project_id": pid, "command_id": "source", "request_id": "source", "content": text})
    request = source_request(pid, source, text, "αβ real source")
    response = rpc_json(artifacts, "runtime.research.resolve", request_json=json.dumps([request]))
    assert response["coverage"]["available"] == 1
    assert response["sources"][0]["sha256"] == source["sha256"]
    assert response["sources"][0]["claim_verification"] == "not_performed"
    assert response["validator_manifest"]["live_two_source_acceptance"] == "pending_P08"
    denied(artifacts.call("runtime.research.resolve", via=artifacts.peers["b"], request_json=json.dumps([request])))
    missing = rpc_json(artifacts, "runtime.research.resolve", request_json=json.dumps([{**request, "source_id": "missing"}]))
    assert missing["sources"][0]["availability"] == "missing"
    assert "locator" not in json.dumps(response)
    assert not hasattr(artifacts.agents["a"], "client")


def test_two_approved_outputs_have_stable_bytes_and_resume_from_durable_manifest(artifacts):
    project, brief, incoming, params = brief_fixture(artifacts)
    pid = project["id"]
    preview = rpc_json(artifacts, "runtime.brief.prepare", **params)
    again = rpc_json(artifacts, "runtime.brief.prepare", **params)
    assert preview["brief"] == again["brief"] and preview["manifest"] == again["manifest"]
    assert preview["manifest"]["mime"] == "application/json"
    denied(artifacts.call("runtime.artifact.get", project_id=pid, artifact_id=preview["manifest"]["artifact_id"],
                          version=preview["manifest"]["version"]))
    bad = {**approvals(preview), "manifest_approval_digest": "0" * 64}
    denied(artifacts.call("runtime.brief.publish", **params, **bad), "approval_mismatch")
    done = rpc_json(artifacts, "runtime.brief.publish", **params, **approvals(preview))
    assert done["state"] == "published" and done["publication_atomic"] is False
    assert done["brief"]["sha256"] == preview["brief"]["sha256"]
    assert done["manifest"]["sha256"] == preview["manifest"]["sha256"]
    assert read_bytes(artifacts, pid, done["brief"]) == b"# Facts\nThe limit is 20.\n# Advice\nTry ten items.\n# Locked\nKeep exactly.\n"
    manifest = json.loads(read_bytes(artifacts, pid, done["manifest"]))
    assert manifest["pending_claims"] == ["advice"]
    assert all("retrieved_at" not in source for source in manifest["dependency_sources"])
    assert "not a completed-read" in manifest["observation_started_at_basis"]
    assert manifest["brief_ref"]["sha256"] == done["brief"]["sha256"]
    status = result(artifacts.call("runtime.artifact.status", command_id=params["command_id"]))
    assert status["status"] == "completed" and json.loads(status["result"]["response_json"])["state"] == "published"
    denied(artifacts.call("runtime.brief.publish", **params, **approvals(preview)), "artifact_control_finished")
    # No previous_sources or claims are sent: reopen the approved immutable sidecar.
    follow = {**params, "command_id": "advice-refresh", "request_id": "advice-refresh", "parent_version": done["brief"]["version"],
              "manifest_ref": {key: done["manifest"][key] for key in ("artifact_id", "version", "sha256")},
              "request_json": json.dumps({"requests": [incoming], "updates": [{"claim_id": "advice", "replacement": "Try twenty items."}]})}
    next_preview = rpc_json(artifacts, "runtime.brief.prepare", **follow)
    assert next_preview["factual_changes"] == [] and next_preview["interpretation_changes"] == ["advice"]
    final = rpc_json(artifacts, "runtime.brief.publish", **follow, **approvals(next_preview))
    assert final["manifest"]["artifact_id"] == done["manifest"]["artifact_id"]
    assert final["manifest"]["parent_version"] == done["manifest"]["version"]
    assert b"Try twenty items." in read_bytes(artifacts, pid, final["brief"])
    assert json.loads(read_bytes(artifacts, pid, final["manifest"]))["pending_claims"] == []
    assert b"The limit is 10." in read_bytes(artifacts, pid, brief)


def test_live_source_revocation_and_changed_replay_deny_publication(artifacts):
    project, brief, incoming, params = brief_fixture(artifacts)
    preview = rpc_json(artifacts, "runtime.brief.prepare", **params)
    altered = json.loads(params["request_json"])
    altered["updates"][0]["replacement"] = "Unapproved replacement."
    denied(artifacts.call("runtime.brief.publish", **{**params, "request_json": json.dumps(altered)}, **approvals(preview)), "idempotency_conflict")
    result(artifacts.call("runtime.project.grants.set", project_id=project["id"], expected_revision=project["revision"], grants=[]))
    denied(artifacts.call("runtime.brief.publish", **params, **approvals(preview)), "project_grant_revoked")
    status = result(artifacts.call("runtime.artifact.status", command_id=params["command_id"]))
    assert status["status"] == "claimed" and status["result"] is None
    response = rpc_json(artifacts, "runtime.research.resolve", request_json=json.dumps([incoming]))
    assert response["sources"][0]["availability"] == "inaccessible"


def test_second_publication_failure_is_partial_and_exact_retry_does_not_duplicate_brief(artifacts, monkeypatch):
    from hermes_cli import artifact_store
    project, _brief, _incoming, params = brief_fixture(artifacts)
    preview = rpc_json(artifacts, "runtime.brief.prepare", **params)
    actual = artifact_store.publish_artifact
    def fail_manifest(run, proposal):
        if proposal.scope["descriptor"]["mime"] == "application/json":
            raise OSError("fixture failure before the second effect")
        return actual(run, proposal)
    with monkeypatch.context() as patch:
        patch.setattr(artifact_store, "publish_artifact", fail_manifest)
        partial = rpc_json(artifacts, "runtime.brief.publish", **params, **approvals(preview))
    assert partial["state"] == "partial" and partial["manifest"] is None
    assert partial["publication_atomic"] is False
    assert b"The limit is 20." in read_bytes(artifacts, project["id"], partial["brief"])
    assert result(artifacts.call("runtime.artifact.status", command_id=params["command_id"]))["status"] == "claimed"
    done = rpc_json(artifacts, "runtime.brief.publish", **params, **approvals(preview))
    assert done["state"] == "published" and done["brief"] == partial["brief"]
    assert done["manifest"]["sha256"] == preview["manifest"]["sha256"]
