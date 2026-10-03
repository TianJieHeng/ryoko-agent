import json

import pytest

from tests.tui_gateway.test_artifact_rpc import artifacts, denied  # noqa: F401
from tests.tui_gateway.test_schedules_rpc import decoded, json_source
from tests.hermes_state.test_commitments import snapshot

pytestmark = pytest.mark.platforms("linux")


def test_decline_survives_reimport_and_cannot_be_accepted_or_cross_scope(artifacts):
    project = artifacts.project()["id"]
    ref = {key: value for key, value in json_source(artifacts, project, snapshot()).items()
           if key in {"artifact_id", "version", "sha256"}}
    params = {"project_id": project, "source_ref_json": json.dumps(ref),
              "selection_json": json.dumps({"thread_ids": ["request-thread"]})}
    preview = decoded(artifacts.call("runtime.inbox.prepare", command_id="inbox", **params))
    cid = preview["candidate_ids"][0]
    decline = {"project_id": project, "candidate_id": cid, "expected_revision": 1, "reason": "Not my obligation"}
    record = decoded(artifacts.call("runtime.commitment.decline", command_id="decline", **decline))
    assert record["review_state"] == "declined" and not record["accepted"] and record["revision"] == 2
    decoded(artifacts.call("runtime.inbox.prepare", command_id="reimport", **params))
    from hermes_state import SessionDB
    agent = artifacts.agents["a"]
    path = agent._session_db.db_path
    agent._session_db.close()
    agent._session_db = SessionDB(path)
    reread = decoded(artifacts.call("runtime.commitment.candidate", project_id=project, candidate_id=cid))
    assert reread == record
    denied(artifacts.call("runtime.commitment.accept", command_id="accept-declined", project_id=project,
        candidate_id=cid, expected_revision=2, owner="owner", outcome="Invented obligation"), "commitment_declined")
    denied(artifacts.call("runtime.commitment.candidate", label="b", project_id=project, candidate_id=cid))
    assert decoded(artifacts.call("runtime.commitment.review", project_id=project))["ready"] == []


def test_owned_agenda_rpc_is_a_sourced_preview_without_side_effects(artifacts):
    from tests.hermes_state.test_agenda import source
    data = source()
    data.update(captured_at="2099-01-01T08:00:00Z",
        window={"start_at": "2099-01-01T09:00:00Z", "end_at": "2099-01-01T17:00:00Z"}, busy=[])
    # Capture time is current/past, planning target may be future.
    data["captured_at"] = "2020-01-01T08:00:00Z"
    project = artifacts.project()["id"]
    ref = {key: value for key, value in json_source(artifacts, project, data).items()
           if key in {"artifact_id", "version", "sha256"}}
    args = dict(project_id=project, availability_ref_json=json.dumps(ref), timezone="Etc/UTC", participants=["owner"],
                windows=[data["window"]], work=[{"item_id": "draft", "duration_minutes": 90}],
                buffer_minutes=10, daily_capacity_minutes=120)
    record = decoded(artifacts.call("runtime.agenda.plan", **args))
    assert record["days"][0]["flexible"][0]["item_id"] == "draft"
    assert not record["calendar_changed"] and record["source_ref"] == ref
    denied(artifacts.call("runtime.agenda.plan", label="b", **args))
