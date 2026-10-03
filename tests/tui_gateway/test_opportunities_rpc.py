"""On-demand selected-project evidence, durable human choices and no task creation."""
import json

import pytest

from tests.tui_gateway.test_artifact_rpc import artifacts, denied, publish, result  # noqa: F401
from tests.tui_gateway.test_workflows_rpc import create_workflow
from tests.tui_gateway.test_commitments_rpc import record, source
from tests.hermes_state.test_commitments import snapshot

pytestmark = pytest.mark.platforms("linux")


def discover(rpc, project, request="discover", label="a", **extra):
    return result(rpc.call("runtime.opportunity.discover", label, project_ids=[project], request_id=request, **extra))


def choose(rpc, candidate, disposition, request=None, **extra):
    return rpc.call("runtime.opportunity.disposition", project_id=candidate["project_id"], candidate_id=candidate["candidate_id"],
        expected_revision=candidate["revision"], expected_evidence_digest=candidate["evidence_digest"],
        disposition=disposition, request_id=request or disposition, **extra)


def counts(db):
    with db._runtime_read() as conn:
        return {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in
                ("runtime_commands", "runtime_missions", "accepted_commitments", "runtime_effects", "runtime_effect_approvals")}


def history(rpc, candidate):
    return result(rpc.call("runtime.opportunity.history", project_id=candidate["project_id"], candidate_id=candidate["candidate_id"]))["history"]


def test_real_selected_project_discovery_persisted_choices_revision_changes_and_restart(artifacts):
    from hermes_state import SessionDB
    project = artifacts.project()["id"]
    workflow = create_workflow(artifacts, project)
    db = artifacts.agents["a"]._session_db
    before = counts(db)
    assert result(artifacts.call("runtime.opportunity.list", project_ids=[project]))["candidates"] == []
    scan = discover(artifacts, project)
    candidate = scan["candidates"][0]
    assert candidate["kind"] == "workflow_draft" and candidate["authorized_project_refs"] == [project]
    assert candidate["evidence_refs"][0]["sha256"] == workflow["sha256"]
    assert scan["discovery_mode"] == "bounded_local_rules" and not scan["complete"]
    assert discover(artifacts, project) == scan
    saved = result(choose(artifacts, candidate, "saved"))["candidate"]
    assert result(choose(artifacts, candidate, "saved"))["candidate"] == saved
    denied(choose(artifacts, candidate, "dismissed", request="saved"), "idempotency_conflict")
    denied(choose(artifacts, candidate, "dismissed"), "opportunity_revision_conflict")
    dismissed = result(choose(artifacts, saved, "dismissed"))["candidate"]
    assert counts(db) == before
    assert discover(artifacts, project)["candidates"] == []  # Retrying an old scan cannot revive a dismissal.
    assert discover(artifacts, project, "again")["candidates"] == []
    assert discover(artifacts, project, "again")["suppressed_count"] == 1
    db.close()
    artifacts.agents["a"]._session_db = SessionDB(artifacts.homes["a"] / "state.db")
    assert discover(artifacts, project, "restart")["candidates"] == []
    assert [entry["event"] for entry in history(artifacts, dismissed)] == ["dismissed", "saved", "discovered"]
    second = create_workflow(artifacts, project, version=2,
        predecessor={key: workflow[key] for key in ("workflow_id", "version", "sha256")})
    changed = discover(artifacts, project, "changed")["candidates"][0]
    assert changed["candidate_id"] == candidate["candidate_id"] and changed["revision"] > dismissed["revision"]
    assert changed["evidence_digest"] != candidate["evidence_digest"]
    assert changed["changed_source_reason"] and "version 2" in changed["changed_source_reason"]
    assert changed["suggested_action"]["target_version"] == second["version"]
    accepted = result(choose(artifacts, changed, "accepted"))
    assert accepted["next_step"] == "open_existing_review_control"
    assert not accepted["tasks_created"] and not accepted["execution_authorized"]
    assert discover(artifacts, project, "accepted-repeat")["candidates"] == []
    assert result(artifacts.call("runtime.workflow.get", project_id=project,
        workflow_id=second["workflow_id"], version=second["version"]))["workflow"]["state"] == "draft"
    assert counts(artifacts.agents["a"]._session_db)["runtime_missions"] == before["runtime_missions"]
    assert not hasattr(artifacts.agents["a"], "client")


def waiting(rpc, project):
    ref = source(rpc, project, snapshot(), "inbox-source")
    preview = record(rpc.call("runtime.inbox.prepare", project_id=project, command_id="inbox",
        source_ref_json=json.dumps(ref), selection_json=json.dumps({"thread_ids": ["waiting-thread"]})))
    due = {"at": "2020-01-01T10:00:00+00:00", "timezone": "Etc/UTC", "kind": "check"}
    item = record(rpc.call("runtime.commitment.accept", project_id=project, command_id="accept-obligation",
        candidate_id=preview["candidate_ids"][0], expected_revision=1, owner="owner@example.test",
        outcome="Check whether the requested signature arrived", due_or_check_at=due))
    return item, ref, due


def test_current_waiting_evidence_stale_acceptance_cosmetic_suppression_and_material_resurface(artifacts):
    project = artifacts.project()["id"]
    item, ref, due = waiting(artifacts, project)
    before = counts(artifacts.agents["a"]._session_db)
    candidate = discover(artifacts, project)["candidates"][0]
    assert candidate["kind"] == "waiting_check" and candidate["evidence_refs"][0]["record_id"] == item["commitment_id"]
    dismissed = result(choose(artifacts, candidate, "dismissed"))["candidate"]
    assert counts(artifacts.agents["a"]._session_db) == before
    # A canonical revision can advance without changing any rule-relevant fact.
    unchanged = record(artifacts.call("runtime.commitment.update", project_id=project, command_id="unchanged",
        commitment_id=item["commitment_id"], expected_revision=item["revision"], state="waiting", evidence_ref_json=json.dumps(ref)))
    assert discover(artifacts, project, "cosmetic")["candidates"] == []
    assert len(history(artifacts, dismissed)) == 2
    new_due = {**due, "at": "2020-01-02T10:00:00+00:00"}
    changed_item = record(artifacts.call("runtime.commitment.update", project_id=project, command_id="new-check",
        commitment_id=item["commitment_id"], expected_revision=unchanged["revision"], state="waiting",
        evidence_ref_json=json.dumps(ref), due_or_check_at=new_due))
    changed = discover(artifacts, project, "material")["candidates"][0]
    assert changed["changed_source_reason"] and changed["candidate_id"] == candidate["candidate_id"]
    assert new_due["at"] in changed["evidence_refs"][0]["detail"]
    record(artifacts.call("runtime.commitment.update", project_id=project, command_id="finished",
        commitment_id=item["commitment_id"], expected_revision=changed_item["revision"], state="done", evidence_ref_json=json.dumps(ref)))
    denied(choose(artifacts, changed, "accepted"), "opportunity_evidence_changed")
    listed = result(artifacts.call("runtime.opportunity.list", project_ids=[project]))["candidates"]
    assert not listed[0]["evidence_current"]
    assert discover(artifacts, project, "terminal")["candidates"] == []


def test_stale_artifact_rule_uses_exact_current_head_without_executing_refresh(artifacts):
    project = artifacts.project()["id"]
    original = publish(artifacts, {"project_id": project, "command_id": "original", "request_id": "original", "content": "Source one"})
    derivative = publish(artifacts, {"project_id": project, "command_id": "derived", "request_id": "derived", "content": "Derived",
        "derived_from": [{key: original[key] for key in ("artifact_id", "version")}]})
    assert discover(artifacts, project)["candidates"] == []
    publish(artifacts, {"project_id": project, "command_id": "new-source", "request_id": "new-source", "content": "Source two",
        "artifact_id": original["artifact_id"], "parent_version": original["version"]})
    candidate = discover(artifacts, project, "stale")["candidates"][0]
    assert candidate["kind"] == "stale_artifact" and candidate["evidence_refs"][0]["sha256"] == derivative["sha256"]
    before = counts(artifacts.agents["a"]._session_db)
    result(choose(artifacts, candidate, "accepted"))
    assert counts(artifacts.agents["a"]._session_db) == before
    assert result(artifacts.call("runtime.artifact.get", project_id=project,
        artifact_id=derivative["artifact_id"], version=derivative["version"]))["sha256"] == derivative["sha256"]


def test_project_selection_profile_switch_transport_grant_revocation_and_scan_bounds(artifacts):
    project_a = artifacts.project()["id"]
    create_workflow(artifacts, project_a, command="workflow-a")
    excluded = artifacts.project()["id"]
    excluded_workflow = create_workflow(artifacts, excluded, command="workflow-excluded")
    # Corrupt excluded evidence makes accidental enumeration observable.
    db = artifacts.agents["a"]._session_db
    db._write_sql("UPDATE workflow_versions SET definition_json='not-json' WHERE project_id=?", (excluded,))
    project_b = artifacts.project("b")["id"]
    # The standard helper defaults to profile A, so explicitly dispatch B.
    from tests.agent.test_workflow_contract import workflow_record
    definition = workflow_record()
    definition.update(project_id=project_b)
    result(artifacts.call("runtime.workflow.create", "b", command_id="workflow-b", definition_json=json.dumps(definition)))
    first = discover(artifacts, project_a)
    second = discover(artifacts, project_b, label="b")
    assert first["candidates"][0]["candidate_id"] != second["candidates"][0]["candidate_id"]
    assert discover(artifacts, project_a) == first
    assert excluded not in json.dumps(first)
    denied(artifacts.call("runtime.opportunity.list", "b", project_ids=[project_a]))
    denied(artifacts.call("runtime.opportunity.discover", via=artifacts.peers["b"], project_ids=[project_a], request_id="foreign"))
    denied(artifacts.call("runtime.opportunity.discover", project_ids=[project_a, project_a], request_id="duplicates"), "invalid_opportunity")
    denied(artifacts.call("runtime.opportunity.discover", project_ids=[project_a], request_id="large", scan_limit_per_source=51), "invalid_command")
    db._write_sql("UPDATE workflow_versions SET definition_json=? WHERE project_id=?", (excluded_workflow["definition_json"], excluded))
    bounded = result(artifacts.call("runtime.opportunity.discover", project_ids=[project_a, excluded], request_id="limited", limit=1, scan_limit_per_source=1))
    assert len(bounded["candidates"]) == 1 and bounded["result_limit_reached"]
    assert all(bound["scanned"] <= 1 for bound in bounded["scanned"])
    candidate = first["candidates"][0]
    current = result(artifacts.call("runtime.project.get", project_id=project_a))["project"]
    result(artifacts.call("runtime.project.grants.set", project_id=project_a, expected_revision=current["revision"], grants=[]))
    for response in (artifacts.call("runtime.opportunity.list", project_ids=[project_a]),
                     artifacts.call("runtime.opportunity.history", project_id=project_a, candidate_id=candidate["candidate_id"]),
                     choose(artifacts, candidate, "accepted"),
                     artifacts.call("runtime.opportunity.discover", project_ids=[project_a], request_id="discover")):
        denied(response, "project_grant_revoked")


def test_schema_upgrade_preserves_effect_approvals_and_persists_review_history(artifacts):
    from hermes_state import SessionDB
    from hermes_state_common import SCHEMA_VERSION
    project = artifacts.project()["id"]
    create_workflow(artifacts, project)
    publish(artifacts, {"project_id": project, "command_id": "proof", "request_id": "proof", "content": "Existing approved output"})
    db = artifacts.agents["a"]._session_db
    with db._runtime_read() as conn:
        protected = {table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table}")] for table in
                     ("runtime_effects", "runtime_effect_approvals")}
    for table in ("opportunity_requests", "opportunity_history", "opportunity_candidates"):
        db._write_sql(f"DROP TABLE {table}")
    db._write_sql("UPDATE schema_version SET version=?", (SCHEMA_VERSION - 1,))
    db.close()
    migrated = SessionDB(artifacts.homes["a"] / "state.db")
    artifacts.agents["a"]._session_db = migrated
    with migrated._runtime_read() as conn:
        assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == SCHEMA_VERSION
        for table, rows in protected.items():
            assert [tuple(row) for row in conn.execute(f"SELECT * FROM {table}")] == rows
    candidate = discover(artifacts, project)["candidates"][0]
    result(choose(artifacts, candidate, "dismissed"))
    assert discover(artifacts, project, "after")["candidates"] == []


def test_simultaneous_cas_idempotency_human_control_and_exact_scan_bounds(artifacts):
    from concurrent.futures import ThreadPoolExecutor
    from agent.identity_lifecycle import agent_runtime_scope
    from hermes_state_opportunities import OpportunityRegistry
    from hermes_state_runtime import RuntimeStoreError
    from tests.agent.test_workflow_contract import workflow_record
    project = artifacts.project()["id"]
    for index in range(3):
        definition = workflow_record()
        definition.update(project_id=project, workflow_id=f"bounded-{index}")
        result(artifacts.call("runtime.workflow.create", command_id=f"workflow-{index}", definition_json=json.dumps(definition)))
    bounded = discover(artifacts, project, scan_limit_per_source=1)
    assert len(bounded["candidates"]) == 1
    workflow_bound = next(row for row in bounded["scanned"] if row["kind"] == "workflow_draft")
    assert workflow_bound["limit_reached"] and workflow_bound["scanned"] == 1
    candidate = bounded["candidates"][0]
    before = counts(artifacts.agents["a"]._session_db)
    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda disposition: choose(artifacts, candidate, disposition), ("saved", "dismissed")))
    assert sum("result" in response for response in responses) == 1
    denied(next(response for response in responses if "error" in response), "opportunity_revision_conflict")
    accepted_change = next(response["result"]["candidate"] for response in responses if "result" in response)
    with ThreadPoolExecutor(max_workers=2) as executor:
        repeated = list(executor.map(lambda _: choose(artifacts, accepted_change, "accepted", request="same-choice"), range(2)))
    assert result(repeated[0]) == result(repeated[1])
    assert len(history(artifacts, candidate)) == 3
    assert counts(artifacts.agents["a"]._session_db) == before
    agent = artifacts.agents["a"]
    with agent_runtime_scope(agent.runtime_context):
        with pytest.raises(RuntimeStoreError, match="owned review control"):
            OpportunityRegistry(agent.runtime_context, agent._session_db).discover(None, project_ids=[project], request_id="model")
