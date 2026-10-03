"""Real owned RPC/tick/storage/approval path for draft-only scheduled production."""
import base64
from copy import deepcopy
import json
import time

import pytest

from tests.tui_gateway.test_artifact_rpc import artifacts, result, denied, download  # noqa: F401
from tests.tui_gateway.test_schedules_rpc import definition, create, update, get, tick, decoded, source
from tests.tui_gateway.test_workflows_rpc import create_workflow, baselines, evaluate, decision

pytestmark = pytest.mark.platforms("linux")


def fixture_schedule(rpc, *, bindings=True, max_bytes=10000, max_fires=2, max_age=30):
    project = rpc.project()["id"]
    workflow = create_workflow(rpc, project)
    approved = decision(rpc, evaluate(rpc, workflow, baselines(rpc, project))["workflow"])["workflow"]
    original = source(rpc, project, "Current source: one\n", command="local-source")
    config = definition(project, original)
    config["kind"] = "workflow_draft"
    config["budget"]["max_bytes"] = max_bytes
    config["specification"] = {
        "workflow_ref": {key: approved[key] for key in ("workflow_id", "version", "sha256")},
        "parameters": {} if bindings else {"name": "Nightly"},
        "source_bindings": [{"parameter": "name", "artifact_id": original["artifact_id"]}] if bindings else [],
        "destination": {"kind": "project_artifact_drafts", "project_id": project}}
    paused = create(rpc, config)
    grant = decoded(rpc.call("runtime.schedule.grant", command_id="produce-grant", project_id=project,
        schedule_id=paused["schedule_id"], expected_revision=paused["revision"], expires_at=time.time() + 3600,
        max_age_seconds=max_age, max_fires=max_fires))
    return config, paused, approved, original, grant


def output_request(view):
    occurrence = view["occurrences"][0]
    return {"project_id": view["project_id"], "schedule_id": view["schedule_id"],
            "occurrence_id": occurrence["occurrence_id"], "output_index": 0}


def retained_bytes(rpc, request):
    raw, offset = bytearray(), 0
    while True:
        row = result(rpc.call("runtime.schedule.output.get", **request, offset=offset, limit=11))
        raw.extend(base64.b64decode(row["data_base64"]))
        offset = row["next_offset"]
        if row["eof"]:
            return bytes(raw), row


def test_actual_tick_retains_refreshed_bytes_and_human_review_never_rerenders(artifacts, monkeypatch):
    config, paused, approved, original, grant = fixture_schedule(artifacts)
    assert paused["state"] == "paused"
    assert tick(artifacts, monkeypatch, paused["next_due"]) == 0
    active = update(artifacts, paused, "active")
    now = active["next_due"]
    assert tick(artifacts, monkeypatch, now) == 1
    view = get(artifacts, active)
    receipt = view["occurrences"][0]["result"]
    assert receipt["draft_only"] and receipt["publication_state"] == "human_review_required"
    assert receipt["source_refs"] == [{key: original[key] for key in ("artifact_id", "version", "sha256")}]
    request = output_request(view)
    data, read = retained_bytes(artifacts, request)
    assert data == b"# Greeting\nHello Current source: one\n\n"
    assert read["draft_only"] and read["occurrence_state"] == "completed"
    denied(artifacts.call("runtime.artifact.get", project_id=config["project_id"],
                         artifact_id=read["artifact_id"], version=read["version"]))
    denied(artifacts.call("runtime.schedule.output.get", "b", **request))
    denied(artifacts.call("runtime.schedule.output.get", via=artifacts.peers["b"], **request))
    assert retained_bytes(artifacts, request)[0] == data
    # The second fire samples its explicitly granted local source, not a stale input.
    newer = source(artifacts, config["project_id"], "Current source: two\n", prior=original, command="refresh-source")
    assert tick(artifacts, monkeypatch, now + 60) == 1
    refreshed = get(artifacts, active)
    refreshed_receipt = refreshed["occurrences"][0]["result"]
    assert refreshed_receipt["source_refs"][0]["version"] == newer["version"]
    assert refreshed_receipt["parameters_sha256"] != receipt["parameters_sha256"]
    assert retained_bytes(artifacts, output_request(refreshed))[0] == b"# Greeting\nHello Current source: two\n\n"
    assert retained_bytes(artifacts, request)[0] == data
    import agent.workflow_runtime as runtime
    monkeypatch.setattr(runtime, "execute_workflow", lambda *_a, **_k: pytest.fail("Review or repeated tick reran production"))
    assert tick(artifacts, monkeypatch, now + 60) == 0
    assert tick(artifacts, monkeypatch, now + 120) == 0  # grant consumed, no admission
    assert get(artifacts, active)["last_error"] == "schedule_grant_required"
    review = {**request, "command_id": "review-produced", "expected_sha256": read["sha256"]}
    proposal = result(artifacts.call("runtime.schedule.output.prepare", **review))
    assert proposal["sha256"] == read["sha256"]
    denied(artifacts.call("runtime.schedule.output.publish", **review, approval_id=proposal["approval_id"], approval_digest="0" * 64), "approval_mismatch")
    published = result(artifacts.call("runtime.schedule.output.publish", **review,
        **{key: proposal[key] for key in ("approval_id", "approval_digest")}))
    assert download(artifacts, config["project_id"], published["artifact_id"], published["version"]) == data
    db = artifacts.agents["a"]._session_db
    from agent.result_artifacts import artifact_actor
    approval = db.get_effect_approval(proposal["approval_id"], artifact_actor(artifacts.agents["a"].runtime_context))
    assert approval["status"] == "consumed" and approval["binding"]["session_id"] == artifacts.agents["a"].session_id
    assert approval["binding"]["run_id"] != view["occurrences"][0]["run_id"]
    denied(artifacts.call("runtime.schedule.output.publish", **review,
        **{key: proposal[key] for key in ("approval_id", "approval_digest")}))
    assert retained_bytes(artifacts, request)[0] == data


def test_default_pause_requires_exact_grant_and_new_definition_invalidates_it(artifacts, monkeypatch):
    config, paused, _approved, _source, _grant = fixture_schedule(artifacts, bindings=False)
    db = artifacts.agents["a"]._session_db
    db._execute_write(lambda conn: conn.execute("UPDATE durable_condition_grants SET state='revoked'"))
    denied(artifacts.call("runtime.schedule.update", project_id=config["project_id"], schedule_id=config["schedule_id"],
        expected_revision=paused["revision"], state="active", command_id="no-grant"), "schedule_grant_required")
    result(artifacts.call("runtime.artifact.cancel", command_id="no-grant"))
    successor = deepcopy(config)
    successor["version"] = 2
    successor["specification"]["parameters"]["name"] = "Changed"
    revised = decoded(artifacts.call("runtime.schedule.create", command_id="new-version", definition_json=json.dumps(successor), expected_revision=paused["revision"]))
    assert revised["state"] == "paused"
    denied(artifacts.call("runtime.schedule.update", project_id=config["project_id"], schedule_id=config["schedule_id"],
        expected_revision=revised["revision"], state="active", command_id="stale-grant"), "schedule_grant_required")


def test_restart_after_admission_preserves_one_grant_debit_and_original_deadline(artifacts, monkeypatch):
    from cron import durable_runtime
    config, paused, _approved, _source, grant = fixture_schedule(artifacts, max_fires=1)
    active = update(artifacts, paused, "active")
    now = active["next_due"]
    execute = durable_runtime.execute_occurrence
    monkeypatch.setattr(durable_runtime, "execute_occurrence", lambda *_a: False)
    assert tick(artifacts, monkeypatch, now) == 0
    pending = get(artifacts, active)
    assert pending["occurrences"][0]["state"] == "accepted"
    db = artifacts.agents["a"]._session_db
    with db._runtime_read() as conn:
        expiry = conn.execute("SELECT deadline_at FROM durable_occurrences").fetchone()[0]
        assert conn.execute("SELECT remaining FROM durable_condition_grants WHERE grant_id=?", (grant["grant_id"],)).fetchone()[0] == 0
    monkeypatch.setattr(durable_runtime, "execute_occurrence", execute)
    assert tick(artifacts, monkeypatch, now + 1) == 1
    view = get(artifacts, active)
    assert view["occurrences"][0]["state"] == "completed"
    with db._runtime_read() as conn:
        assert conn.execute("SELECT deadline_at FROM durable_occurrences").fetchone()[0] == expiry
        assert conn.execute("SELECT remaining FROM durable_condition_grants WHERE grant_id=?", (grant["grant_id"],)).fetchone()[0] == 0
    assert retained_bytes(artifacts, output_request(view))[0].startswith(b"# Greeting")


@pytest.mark.parametrize("stop", ["expiry", "grant", "workflow", "budget"])
def test_execution_rechecks_original_authority_and_bounds(artifacts, monkeypatch, stop):
    from cron import durable_runtime
    config, paused, approved, _source, _grant = fixture_schedule(artifacts, max_bytes=25 if stop == "budget" else 10000)
    active = update(artifacts, paused, "active")
    now = active["next_due"]
    execute = durable_runtime.execute_occurrence
    monkeypatch.setattr(durable_runtime, "execute_occurrence", lambda *_a: False)
    tick(artifacts, monkeypatch, now)
    db = artifacts.agents["a"]._session_db
    if stop == "grant":
        db._execute_write(lambda conn: conn.execute("UPDATE durable_condition_grants SET state='revoked'"))
    if stop == "workflow":
        decision(artifacts, approved, action="revoke", command="revoke-workflow")
    monkeypatch.setattr(durable_runtime, "execute_occurrence", execute)
    assert tick(artifacts, monkeypatch, now + (31 if stop == "expiry" else 0)) == 1
    view = get(artifacts, active)
    assert view["occurrences"][0]["state"] == "failed"
    with db._runtime_read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM runtime_effects WHERE operation_type='artifact_publish'").fetchone()[0] == 0
    assert tick(artifacts, monkeypatch, now + 40) == 0


def test_scheduled_authority_cannot_publish_projects_change_inputs_or_invent_results(artifacts, monkeypatch):
    from agent import workflow_runtime
    from cron.durable_workflows import assert_scheduled_result_dispatch
    from agent.result_artifacts import result_artifact_descriptor
    from agent.artifact_commands import assert_artifact_dispatch
    config, paused, _approved, _source, _grant = fixture_schedule(artifacts, bindings=False)
    active = update(artifacts, paused, "active")
    execute = workflow_runtime.execute_workflow
    def malicious(run, workflow, parameters, **kwargs):
        with pytest.raises(ValueError):
            assert_artifact_dispatch(run)
        with pytest.raises(ValueError):
            execute(run, workflow, {"name": "Not granted"}, **kwargs)
        with pytest.raises(ValueError):
            assert_scheduled_result_dispatch(run, result_artifact_descriptor(run.context, run.run_id, b"foreign", "foreign"))
        return execute(run, workflow, parameters, **kwargs)
    monkeypatch.setattr(workflow_runtime, "execute_workflow", malicious)
    assert tick(artifacts, monkeypatch, active["next_due"]) == 1
    assert get(artifacts, active)["occurrences"][0]["state"] == "completed"


def test_lost_claim_after_private_write_is_unknown_and_never_replayed(artifacts, monkeypatch):
    from agent import effect_reconciler
    config, paused, _approved, _source, _grant = fixture_schedule(artifacts)
    active = update(artifacts, paused, "active")
    now = active["next_due"]
    publish = effect_reconciler._publish_bytes
    calls = []
    def lose_owner(context, descriptor, payload):
        value = publish(context, descriptor, payload)
        calls.append(descriptor)
        db = artifacts.agents["a"]._session_db
        lease = db.get_session_turn_lease(context.identity.session_id)
        db.release_session_turn_lease(context.identity.session_id, lease["holder"], generation=lease["generation"])
        return value
    monkeypatch.setattr(effect_reconciler, "_publish_bytes", lose_owner)
    assert tick(artifacts, monkeypatch, now) == 0
    assert len(calls) == 1
    for offset in (1, 60, 120):
        assert tick(artifacts, monkeypatch, now + offset) == 0
    view = get(artifacts, active)
    assert view["occurrences"][0]["state"] == "outcome_unknown" and len(calls) == 1
    denied(artifacts.call("runtime.schedule.output.get", **output_request(view)), "artifact_not_confirmed")


@pytest.mark.parametrize("change", ["grant", "workflow", "pause", "policy", "deadline"])
def test_authority_is_rechecked_after_render_before_any_private_effect(artifacts, monkeypatch, change):
    from agent import workflow_runtime
    _config, paused, _approved, _source, _grant = fixture_schedule(artifacts)
    active = update(artifacts, paused, "active")
    now = active["next_due"]
    db = artifacts.agents["a"]._session_db
    execute = workflow_runtime.execute_workflow
    def stop_after_render(*args, **kwargs):
        produced = execute(*args, **kwargs)
        if change == "grant":
            db._execute_write(lambda conn: conn.execute("UPDATE durable_condition_grants SET state='revoked'"))
        elif change == "workflow":
            db._execute_write(lambda conn: conn.execute("UPDATE workflow_versions SET state='revoked'"))
        elif change == "pause":
            db._execute_write(lambda conn: conn.execute("UPDATE durable_schedules SET state='paused'"))
        elif change == "deadline":
            monkeypatch.setattr(time, "time", lambda: now + 61)
        else:
            path = artifacts.homes["a"] / "config.yaml"
            config = json.loads(path.read_text())
            config["agent_identity"]["agents"]["ryoko"]["policy_version"] += 1
            path.write_text(json.dumps(config))
        return produced
    monkeypatch.setattr(workflow_runtime, "execute_workflow", stop_after_render)
    assert tick(artifacts, monkeypatch, now) == 1
    with db._runtime_read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM runtime_effects WHERE operation_type='artifact_publish'").fetchone()[0] == 0
        assert conn.execute("SELECT state FROM durable_occurrences").fetchone()[0] == "failed"
        assert json.loads(conn.execute("SELECT record_json FROM workflow_runs").fetchone()[0])["state"] == "failed"


def test_fsync_exception_remains_unknown_and_history_never_promises_safe_failure(artifacts, monkeypatch):
    from agent import effect_reconciler
    config, paused, approved, _source, _grant = fixture_schedule(artifacts)
    active = update(artifacts, paused, "active")
    now = active["next_due"]
    publish = effect_reconciler._publish_bytes
    calls = []
    def uncertain(context, descriptor, payload):
        publish(context, descriptor, payload)
        calls.append(descriptor)
        raise OSError("synthetic fsync acknowledgement loss")
    monkeypatch.setattr(effect_reconciler, "_publish_bytes", uncertain)
    assert tick(artifacts, monkeypatch, now) == 0
    assert tick(artifacts, monkeypatch, now + 1) == 0
    view = get(artifacts, active)
    receipt = view["occurrences"][0]
    assert receipt["state"] == "outcome_unknown" and receipt["result"]["publication_state"] == "reconciliation_required"
    assert receipt["result"]["outputs"][0]["sha256"] == calls[0]["sha256"]
    assert tick(artifacts, monkeypatch, now + 60) == 0 and len(calls) == 1
    history = json.loads(result(artifacts.call("runtime.workflow.runs", project_id=config["project_id"],
        workflow_id=approved["workflow_id"], version=approved["version"]))["runs_json"])
    assert history[0]["state"] == "outcome_unknown"


def test_retained_corruption_is_not_served_or_recomputed_and_revocation_stops_publish(artifacts, monkeypatch):
    config, paused, approved, _source, _grant = fixture_schedule(artifacts)
    active = update(artifacts, paused, "active")
    tick(artifacts, monkeypatch, active["next_due"])
    view = get(artifacts, active)
    request = output_request(view)
    original, read = retained_bytes(artifacts, request)
    decision(artifacts, approved, action="revoke", command="revoke-after-produced")
    assert retained_bytes(artifacts, request)[0] == original
    denied(artifacts.call("runtime.schedule.output.prepare", **request, command_id="revoked-review", expected_sha256=read["sha256"]), "workflow_not_approved")
    result(artifacts.call("runtime.artifact.cancel", command_id="revoked-review"))
    db = artifacts.agents["a"]._session_db
    with db._runtime_read() as conn:
        saved = json.loads(conn.execute("SELECT record_json FROM workflow_runs").fetchone()[0])
    path = artifacts.homes["a"] / saved["output_descriptors"][0]["locator"]
    path.chmod(0o600)
    path.write_bytes(b"tampered")
    denied(artifacts.call("runtime.schedule.output.get", **request))
    assert tick(artifacts, monkeypatch, active["next_due"]) == 0


def test_canonical_template_pin_is_applied_and_all_source_bytes_are_accounted(artifacts, monkeypatch):
    from tests.tui_gateway.test_template_application_rpc import saved, pin
    from tests.agent.test_workflow_contract import workflow_record, object_schema
    from tests.tui_gateway.test_artifact_rpc import publish
    project = artifacts.project()["id"]
    template = saved(artifacts, project)
    workflow = workflow_record()
    workflow.update(project_id=project, template_ref=pin(template),
        input_schema=object_schema({key: {"type": "string"} for key in ("topic", "summary")}, ["topic", "summary"]),
        output_schema={"required_sections": ["Summary", "Policy"], "min_bytes": 1})
    workflow["steps"][0]["parameters"] = {"template": "__canonical_template__"}
    workflow["provenance"]["private_derived"] = True
    row = result(artifacts.call("runtime.workflow.create", command_id="template-workflow", definition_json=json.dumps(workflow)))["workflow"]
    cases = []
    for index, topic in enumerate(("Solar", "Wind", "Hydro", "Geothermal")):
        parameters = {"topic": topic, "summary": "Facts for " + topic}
        preview = result(artifacts.call("runtime.template.preview", project_id=project, template_ref=pin(template), slot_values=parameters))
        baseline = publish(artifacts, {"project_id": project, "command_id": f"expected-{index}", "request_id": f"expected-{index}", "content": preview["content"]})
        cases.append({"case_id": str(index), "split": "tuning" if index < 2 else "held_out", "parameters": parameters,
            "expected_sha256": baseline["sha256"], "generalist_ref": {key: baseline[key] for key in ("artifact_id", "version", "sha256")}})
    approved = decision(artifacts, evaluate(artifacts, row, cases)["workflow"])["workflow"]
    original = source(artifacts, project, "Fresh tidal facts", command="fresh-facts")
    config = definition(project, original)
    config.update(kind="workflow_draft", specification={"workflow_ref": {key: approved[key] for key in ("workflow_id", "version", "sha256")},
        "parameters": {"topic": "Tidal"}, "source_bindings": [{"parameter": "summary", "artifact_id": original["artifact_id"]}],
        "destination": {"kind": "project_artifact_drafts", "project_id": project}})
    paused = create(artifacts, config)
    decoded(artifacts.call("runtime.schedule.grant", command_id="template-grant", project_id=project,
        schedule_id=paused["schedule_id"], expected_revision=paused["revision"], expires_at=time.time() + 3600, max_age_seconds=30, max_fires=1))
    saved(artifacts, project, 2, 1)
    active = update(artifacts, paused, "active")
    assert tick(artifacts, monkeypatch, active["next_due"]) == 1
    view = get(artifacts, active)
    data, _read = retained_bytes(artifacts, output_request(view))
    assert b"# Tidal" in data and b"Fresh tidal facts" in data and b"INCIDENTAL_NAME" not in data
    receipt = view["occurrences"][0]["result"]
    assert len(receipt["source_refs"]) == 2 and receipt["bytes_read"] > original["size"]
