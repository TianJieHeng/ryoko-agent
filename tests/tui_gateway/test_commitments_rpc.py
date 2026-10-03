"""BE12 owned consumers exercise inbox, acceptance and availability in two profiles."""
import base64
import json

import pytest

from tests.tui_gateway.test_artifact_rpc import artifacts, result, denied, publish  # noqa: F401
from tests.hermes_state.test_commitments import snapshot

pytestmark = pytest.mark.platforms("linux")


def record(response):
    return json.loads(result(response)["record_json"])


def source(rpc, project, value, name, label="a"):
    output = publish(rpc, {"project_id": project, "command_id": name, "request_id": name,
        "mime": "application/json", "content_base64": base64.b64encode(json.dumps(value).encode()).decode()},
        mode="bytes.", label=label)
    return {key: output[key] for key in ("artifact_id", "version", "sha256")}


def test_owned_import_accept_due_waiting_terminal_and_profile_recovery(artifacts):
    project = artifacts.project()["id"]
    ref = source(artifacts, project, snapshot(), "inbox-source")
    preview = record(artifacts.call("runtime.inbox.prepare", project_id=project, command_id="inbox",
        source_ref_json=json.dumps(ref), selection_json=json.dumps({"thread_ids": ["request-thread"]})))
    candidate_id = preview["candidate_ids"][0]
    candidate = record(artifacts.call("runtime.commitment.candidate", project_id=project, candidate_id=candidate_id))
    assert not candidate["accepted"] and candidate["owner"] is None
    assert record(artifacts.call("runtime.commitment.list", project_id=project))["commitments"] == []
    due = {"at": "2026-10-04T10:00:00+00:00", "timezone": "Etc/UTC", "kind": "due"}
    accepted = record(artifacts.call("runtime.commitment.accept", project_id=project, command_id="accept",
        candidate_id=candidate_id, expected_revision=1, owner="owner@example.test", outcome="Send reviewed report", due_or_check_at=due))
    assert accepted["accepted"] and accepted["due_or_check_at"] == due
    cid = accepted["commitment_id"]
    assert record(artifacts.call("runtime.commitment.get", project_id=project, commitment_id=cid)) == accepted
    denied(artifacts.call("runtime.commitment.get", "b", project_id=project, commitment_id=cid))
    denied(artifacts.call("runtime.commitment.get", via=artifacts.peers["b"], project_id=project, commitment_id=cid))
    assert record(artifacts.call("runtime.commitment.get", project_id=project, commitment_id=cid)) == accepted
    waiting = record(artifacts.call("runtime.commitment.update", project_id=project, command_id="waiting",
        commitment_id=cid, expected_revision=1, state="waiting", evidence_ref_json=json.dumps(ref)))
    review = record(artifacts.call("runtime.commitment.review", project_id=project))
    assert review["waiting"][0]["commitment_id"] == cid and not review["mutated"]
    done = record(artifacts.call("runtime.commitment.update", project_id=project, command_id="done",
        commitment_id=cid, expected_revision=waiting["revision"], state="done", evidence_ref_json=json.dumps(ref)))
    assert record(artifacts.call("runtime.commitment.review", project_id=project))["waiting"] == []
    denied(artifacts.call("runtime.commitment.update", project_id=project, command_id="reopen",
        commitment_id=cid, expected_revision=done["revision"], state="ready", evidence_ref_json=json.dumps(ref)), "commitment_terminal")
    result(artifacts.call("runtime.artifact.cancel", command_id="reopen"))
    assert record(artifacts.call("runtime.commitment.get", project_id=project, commitment_id=cid))["state"] == "done"
    assert not hasattr(artifacts.agents["a"], "client")


def test_calendar_correspondence_and_result_bounds_are_honest_owned_previews(artifacts):
    project = artifacts.project()["id"]
    availability = {"schema_version": 1, "timezone": "Etc/UTC", "captured_at": "2026-01-01T00:00:00Z",
        "participants": ["owner@example.test", "guest@example.test"],
        "window": {"start_at": "2026-10-04T09:00:00Z", "end_at": "2026-10-04T12:00:00Z"},
        "busy": [{"start_at": "2026-10-04T09:00:00Z", "end_at": "2026-10-04T10:00:00Z"}]}
    ref = source(artifacts, project, availability, "availability")
    preview = record(artifacts.call("runtime.calendar.preview", project_id=project,
        availability_ref_json=json.dumps(ref), timezone="Etc/UTC", participants=availability["participants"],
        start_at="2026-10-04T09:00:00Z", end_at="2026-10-04T12:00:00Z", duration_minutes=30))
    assert preview["slots"][0]["start_at"] == "2026-10-04T10:00:00+00:00"
    assert preview["stale"] and not preview["live_availability_verified"]
    inbox_ref = source(artifacts, project, snapshot(), "inbox")
    draft = record(artifacts.call("runtime.correspondence.draft", project_id=project, command_id="draft",
        correspondence_id="reply", recipients=["guest@example.test"], content="I’ll send the reviewed report tomorrow",
        source_refs_json=json.dumps([inbox_ref])))
    assert draft["possible_new_promise"] and not draft["sent"] and draft["effect_receipt"] is None
    assert record(artifacts.call("runtime.correspondence.get", project_id=project,
        correspondence_id=draft["correspondence_id"])) == draft
    from agent.result_artifacts import artifact_actor
    agent = artifacts.agents["a"]
    effect = agent._session_db.list_effects(agent.session_id, artifact_actor(agent.runtime_context))[0]
    denied(artifacts.call("runtime.correspondence.receipt", project_id=project, command_id="wrong-receipt",
        correspondence_id=draft["correspondence_id"], effect_id=effect["effect_id"]), "correspondence_receipt_mismatch")
    result(artifacts.call("runtime.artifact.cancel", command_id="wrong-receipt"))
    assert record(artifacts.call("runtime.correspondence.get", project_id=project,
        correspondence_id=draft["correspondence_id"])) == draft
    # A large legal artifact must fail before persisting an oversized inline preview.
    large = snapshot()
    large["messages"] = [{**large["messages"][0], "message_id": f"m-{index}", "body": "Please review",
                              "participants": ["a"], "attachments": []} for index in range(300)]
    large_ref = source(artifacts, project, large, "large-source")
    denied(artifacts.call("runtime.inbox.prepare", project_id=project, command_id="large-preview",
        source_ref_json=json.dumps(large_ref), selection_json=json.dumps({"thread_ids": ["request-thread"]})), "commitment_result_bound")
    result(artifacts.call("runtime.artifact.cancel", command_id="large-preview"))
    with artifacts.agents["a"]._session_db._runtime_read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM commitment_inbox_previews").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM commitment_candidates").fetchone()[0] == 0
