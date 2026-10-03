import json

import pytest

from tests.tui_gateway.test_artifact_rpc import artifacts, publish, denied  # noqa: F401
from tests.tui_gateway.test_schedules_rpc import decoded

pytestmark = pytest.mark.platforms("linux")


def initialize(rpc):
    project = rpc.project()["id"]
    publish(rpc, {"project_id": project, "command_id": "artifact", "request_id": "artifact", "content": "# Fixture\nbody"})
    agent = rpc.agents["a"]
    return agent, agent._session_db, agent.session_id


def test_owned_repair_requires_exact_fresh_plan_and_does_not_claim_remote_stop(artifacts):
    agent, db, sid = initialize(artifacts)
    db.append_message(sid, "user", content="private fixture payload")
    assert db.try_acquire_session_turn_lease(sid, "synthetic-owner")
    view = decoded(artifacts.call("runtime.operations.inspect"))
    assert "private fixture payload" not in json.dumps(view)
    plan = decoded(artifacts.call("runtime.operations.repair.prepare", action="revoke-lease", target_id=sid))
    denied(artifacts.call("runtime.operations.repair.apply", plan_json=json.dumps(plan), authorization_digest="0" * 64),
           "operations_exact_authorization_required")
    denied(artifacts.call("runtime.operations.repair.apply", via=artifacts.peers["b"], plan_json=json.dumps(plan),
                          authorization_digest=plan["plan_digest"]))
    changed = {**plan, "target_id": "other-session"}
    denied(artifacts.call("runtime.operations.repair.apply", plan_json=json.dumps(changed), authorization_digest=plan["plan_digest"]))
    assert db.get_session_turn_lease(sid) is not None
    outcome = decoded(artifacts.call("runtime.operations.repair.apply", plan_json=json.dumps(plan),
                                     authorization_digest=plan["plan_digest"]))
    assert outcome == {"state": "revoked", "remote_work_stopped": False}
    assert db.get_session_turn_lease(sid) is None
    denied(artifacts.call("runtime.operations.repair.apply", plan_json=json.dumps(plan), authorization_digest=plan["plan_digest"]))
    audit = decoded(artifacts.call("runtime.operations.audit", limit=100))
    assert "private fixture payload" not in json.dumps(audit)
    assert audit["events"]


def test_deletion_preview_is_scope_bound_and_stale_manifest_cannot_delete(artifacts):
    _agent, db, sid = initialize(artifacts)
    db.append_message(sid, "user", content="synthetic transcript")
    preview = decoded(artifacts.call("runtime.operations.deletion.prepare"))
    assert preview["kind"] == "transcript_payload" and not preview["complete_deletion"]
    assert db.get_messages(sid)
    db.append_message(sid, "assistant", content="newer retained content")
    denied(artifacts.call("runtime.operations.deletion.apply", plan_json=json.dumps(preview),
                          authorization_digest=preview["manifest_digest"]), "deletion_preview_changed")
    assert len(db.get_messages(sid)) == 2
    current = decoded(artifacts.call("runtime.operations.deletion.prepare"))
    result = decoded(artifacts.call("runtime.operations.deletion.apply", plan_json=json.dumps(current),
                                   authorization_digest=current["manifest_digest"]))
    assert result["acknowledgments"] and not result["complete_deletion"]
    assert db.get_messages(sid) == []
    assert decoded(artifacts.call("runtime.operations.retention"))["limitations"]
    denied(artifacts.call("runtime.operations.deletion.prepare", memory_record_id="unconfigured-primary-record"),
           "deletion_backend_unsupported")
