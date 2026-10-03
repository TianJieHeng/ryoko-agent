"""Owned schedule RPC → canonical command → real retained-source tick/restart."""
from copy import deepcopy
import json
import time

import pytest

from tests.tui_gateway.test_artifact_rpc import artifacts, result, denied, publish  # noqa: F401

pytestmark = pytest.mark.platforms("linux")


def decoded(response):
    return json.loads(result(response)["record_json"])


def definition(project, source, *, schedule_id="watch", now=None):
    now = time.time() if now is None else now
    return {"schema_version": 1, "schedule_id": schedule_id, "version": 1, "project_id": project,
        "timezone": "Etc/UTC", "trigger": {"kind": "interval", "anchor": now + 60, "seconds": 60},
        "policy": {"missed_run": "latest", "grace_seconds": 120, "overlap": "block"},
        "budget": {"max_checks": 10, "max_bytes": 10000, "deadline_seconds": 30}, "expires_at": now + 86400,
        "kind": "monitor", "specification": {"question": "Has the selected status changed?", "source_set": [source["artifact_id"]],
            "predicate": {"kind": "normalized_text", "version": 1}, "notify_policy": "record_only", "condition_action": None}}


def create(rpc, record, *, command="create"):
    return decoded(rpc.call("runtime.schedule.create", command_id=command, definition_json=json.dumps(record)))


def update(rpc, record, state, *, command="activate"):
    return decoded(rpc.call("runtime.schedule.update", project_id=record["project_id"], schedule_id=record["schedule_id"],
        expected_revision=record["revision"], state=state, command_id=command))


def get(rpc, record, label="a"):
    return decoded(rpc.call("runtime.schedule.get", label, project_id=record["project_id"], schedule_id=record["schedule_id"]))


def source(rpc, project, content, *, prior=None, command="source"):
    params = {"project_id": project, "command_id": command, "request_id": command, "content": content}
    if prior:
        params.update(artifact_id=prior["artifact_id"], parent_version=prior["version"], expected_head_version=prior["version"])
    return publish(rpc, params)


def tick(rpc, monkeypatch, now, *, label="a", actual=True):
    from cron import scheduler
    from agent.identity_lifecycle import agent_runtime_scope
    from cron.durable_runtime import tick_durable_schedules
    monkeypatch.setattr(time, "time", lambda: now)
    with agent_runtime_scope(rpc.agents[label].runtime_context):
        if actual:
            monkeypatch.setattr(scheduler, "_should_yield_tick_to_fresh_gateway", lambda: None)
            monkeypatch.setattr(scheduler, "_maybe_run_worktree_maintenance", lambda: None)
            monkeypatch.setattr(scheduler, "_sweep_mcp_orphans", lambda: None)
            return scheduler.tick(verbose=False)
        return tick_durable_schedules()


def test_real_tick_baseline_cosmetic_change_meaningful_change_outage_and_pause(artifacts, monkeypatch):
    project = artifacts.project()["id"]
    first = source(artifacts, project, "Status: ready\n")
    config = definition(project, first)
    paused = create(artifacts, config)
    assert paused["state"] == "paused" and paused["authority"] == "hermes_cron"
    active = update(artifacts, paused, "active")
    now = active["next_due"]
    assert tick(artifacts, monkeypatch, now) == 1
    baseline = get(artifacts, active)
    assert baseline["health"] == "healthy" and baseline["occurrences"][0]["result"]["baseline"]
    assert baseline["intents"] == []
    assert tick(artifacts, monkeypatch, now) == 0
    cosmetic = source(artifacts, project, "Status:   ready\n\n", prior=first, command="cosmetic")
    assert tick(artifacts, monkeypatch, now + 60) == 1
    assert get(artifacts, active)["intents"] == []
    changed = source(artifacts, project, "Status: shipped\n", prior=cosmetic, command="meaningful")
    assert tick(artifacts, monkeypatch, now + 120) == 1
    view = get(artifacts, active)
    assert len(view["intents"]) == 1 and view["intents"][0]["state"] == "recorded"
    assert view["occurrences"][0]["result"]["live_connection_verified"] is False
    db = artifacts.agents["a"]._session_db
    with db._runtime_read() as conn:
        occurrences = conn.execute("SELECT * FROM durable_occurrences ORDER BY due_at").fetchall()
        assert len(occurrences) == 3 and all(row["generation"] > 0 for row in occurrences)
        command = db.read_runtime_command(occurrences[-1]["session_id"], occurrences[-1]["command_id"])
        assert command["status"] == "completed" and command["receipt"]["run_id"] == occurrences[-1]["run_id"]
    stopped = update(artifacts, view, "paused", command="pause")
    assert tick(artifacts, monkeypatch, now + 180) == 0
    active = update(artifacts, stopped, "active", command="resume")
    # Remove only this fixture's retained bytes, proving inaccessible != unchanged.
    from agent.result_artifacts import artifact_actor
    from agent.project_context import project_access
    from agent.identity_lifecycle import agent_runtime_scope
    with agent_runtime_scope(artifacts.agents["a"].runtime_context):
        row = db.read_artifact_version(changed["artifact_id"], changed["version"], artifact_actor(artifacts.agents["a"].runtime_context),
                                      access=project_access(artifacts.agents["a"].runtime_context))
    from pathlib import Path
    # Descriptor names are opaque; use the exact persisted local artifact path resolver.
    path = artifacts.homes["a"] / row["descriptor"]["locator"]
    Path(path).unlink()
    assert tick(artifacts, monkeypatch, now + 180) == 1
    unhealthy = get(artifacts, active)
    assert unhealthy["health"] == "unhealthy" and unhealthy["last_error"]
    assert len(unhealthy["intents"]) == 1 and unhealthy["occurrences"][0]["state"] == "failed"


def test_version_pause_import_unknown_owner_and_profile_isolation(artifacts, monkeypatch):
    project = artifacts.project()["id"]
    first = source(artifacts, project, "stable")
    config = definition(project, first)
    paused = create(artifacts, config)
    denied(artifacts.call("runtime.schedule.get", "b", project_id=project, schedule_id="watch"))
    active = update(artifacts, paused, "active")
    successor = deepcopy(config); successor["version"] = 2
    denied(artifacts.call("runtime.schedule.create", command_id="bad-version", definition_json=json.dumps(successor),
                         expected_revision=active["revision"]), "schedule_revision_conflict")
    result(artifacts.call("runtime.artifact.cancel", command_id="bad-version"))
    stopped = update(artifacts, active, "paused", command="pause")
    new = decoded(artifacts.call("runtime.schedule.create", command_id="new-version", definition_json=json.dumps(successor),
                               expected_revision=stopped["revision"]))
    assert new["version"] == 2 and new["state"] == "paused"
    from cron.durable_contract import digest
    imported = deepcopy(config); imported["schedule_id"] = "import_" + digest("foreign")[:32]
    request = {"definition_json": json.dumps(imported), "command_id": "import-active", "import_json": json.dumps({
        "authority": "dots_runner", "source_id": "foreign", "source_state": "active", "unresolved_occurrences": []})}
    denied(artifacts.call("runtime.schedule.import", **request), "schedule_cutover_blocked")
    result(artifacts.call("runtime.artifact.cancel", command_id="import-active"))
    request.update(command_id="import-paused", import_json=json.dumps({"authority": "dots_runner", "source_id": "foreign",
        "source_state": "paused", "unresolved_occurrences": []}))
    assert decoded(artifacts.call("runtime.schedule.import", **request))["state"] == "paused"


def json_source(rpc, project, value, *, prior=None, command="json-source"):
    import base64
    params = {"project_id": project, "command_id": command, "request_id": command,
              "content_base64": base64.b64encode(json.dumps(value).encode()).decode(), "mime": "application/json"}
    if prior:
        params.update(artifact_id=prior["artifact_id"], parent_version=prior["version"], expected_head_version=prior["version"])
    return publish(rpc, params, mode="bytes.")


def test_threshold_observation_never_grants_action_and_exact_grant_is_consumed_once(artifacts, monkeypatch):
    project = artifacts.project()["id"]
    first = json_source(artifacts, project, {"count": 5, "cosmetic": "one"})
    config = definition(project, first)
    config["specification"]["predicate"] = {"kind": "threshold", "version": 1, "field": "count", "operator": "gte", "value": 10}
    config["specification"]["condition_action"] = {"purpose": "memory_review", "workflow_ref": None,
        "source_refs": [{key: first[key] for key in ("artifact_id", "version", "sha256")}]}
    active = update(artifacts, create(artifacts, config), "active")
    now = active["next_due"]
    assert tick(artifacts, monkeypatch, now) == 1
    latest = json_source(artifacts, project, {"count": 12}, prior=first, command="cross")
    assert tick(artifacts, monkeypatch, now + 60) == 1
    view = get(artifacts, active)
    actions = [item for item in view["intents"] if item["kind"] == "action"]
    assert len(actions) == 1 and actions[0]["state"] == "awaiting_authorization"
    grant = decoded(artifacts.call("runtime.schedule.grant", command_id="grant", project_id=project,
        schedule_id="watch", expected_revision=view["revision"], expires_at=now + 3600, max_age_seconds=30, max_fires=1))
    assert grant["external_actions"] is False
    latest = json_source(artifacts, project, {"count": 1}, prior=latest, command="down")
    assert tick(artifacts, monkeypatch, now + 120) == 1
    latest = json_source(artifacts, project, {"count": 15}, prior=latest, command="up")
    assert tick(artifacts, monkeypatch, now + 180) == 1
    actions = [item for item in get(artifacts, active)["intents"] if item["kind"] == "action"]
    assert sum(item["state"] == "completed" for item in actions) == 1
    db = artifacts.agents["a"]._session_db
    with db._runtime_read() as conn:
        occurrence = conn.execute("SELECT * FROM durable_occurrences ORDER BY due_at DESC LIMIT 1").fetchone()
    receipt = db.read_runtime_command(occurrence["session_id"], occurrence["command_id"])["result"]
    assert receipt["action_review"]["source_refs"][0]["version"] == first["version"]
    assert receipt["action_review"]["memory_ingested"] is False and receipt["action_review"]["skill_promoted"] is False
    latest = json_source(artifacts, project, {"count": 1}, prior=latest, command="down-again")
    tick(artifacts, monkeypatch, now + 240)
    json_source(artifacts, project, {"count": 20}, prior=latest, command="up-again")
    tick(artifacts, monkeypatch, now + 300)
    actions = [item for item in get(artifacts, active)["intents"] if item["kind"] == "action"]
    assert sum(item["state"] == "completed" for item in actions) == 1
    assert sum(item["state"] == "awaiting_authorization" for item in actions) == 2


def test_atomic_admission_restart_and_dead_claim_never_replays(artifacts, monkeypatch):
    from cron import durable_runtime
    from hermes_state_schedules import ScheduleRegistry
    project = artifacts.project()["id"]
    first = source(artifacts, project, "safe")
    active = update(artifacts, create(artifacts, definition(project, first)), "active")
    now = active["next_due"]
    db = artifacts.agents["a"]._session_db
    real_fence = ScheduleRegistry._no_unresolved
    def fail_admission(*_args):
        raise RuntimeError("synthetic admission failure")
    monkeypatch.setattr(ScheduleRegistry, "_no_unresolved", staticmethod(fail_admission))
    assert tick(artifacts, monkeypatch, now) == 0
    with db._runtime_read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM durable_occurrences").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM runtime_commands WHERE command_id LIKE 'occ_%'").fetchone()[0] == 0
    assert get(artifacts, active)["remaining_checks"] == active["remaining_checks"]
    monkeypatch.setattr(ScheduleRegistry, "_no_unresolved", staticmethod(real_fence))
    execute = durable_runtime.execute_occurrence
    monkeypatch.setattr(durable_runtime, "execute_occurrence", lambda *_args: False)
    assert tick(artifacts, monkeypatch, now) == 0
    pending = get(artifacts, active)
    assert pending["occurrences"][0]["state"] == "accepted"
    monkeypatch.setattr(durable_runtime, "execute_occurrence", execute)
    # Tick reopens the actual on-disk DB and starts only the accepted command.
    assert tick(artifacts, monkeypatch, now) == 1
    assert get(artifacts, active)["remaining_checks"] == active["remaining_checks"] - 1
    monkeypatch.setattr(durable_runtime, "execute_occurrence", lambda *_args: False)
    tick(artifacts, monkeypatch, now + 60)
    with db._runtime_read() as conn:
        occurrence = dict(conn.execute("SELECT * FROM durable_occurrences WHERE state='accepted'").fetchone())
    assert db.try_acquire_session_turn_lease(occurrence["session_id"], "crashed-owner", ttl_seconds=30)
    lease = db.get_session_turn_lease(occurrence["session_id"])
    assert db.claim_runtime_command(occurrence["session_id"], occurrence["command_id"], holder="crashed-owner", generation=lease["generation"])
    db.release_session_turn_lease(occurrence["session_id"], "crashed-owner", generation=lease["generation"])
    monkeypatch.setattr(durable_runtime, "execute_occurrence", execute)
    assert tick(artifacts, monkeypatch, now + 120) == 0
    unknown = get(artifacts, active)
    assert unknown["occurrences"][0]["state"] == "outcome_unknown"
    assert tick(artifacts, monkeypatch, now + 180) == 0
    assert len(get(artifacts, active)["occurrences"]) == 2
    paused = update(artifacts, unknown, "paused", command="pause-unknown")
    denied(artifacts.call("runtime.schedule.update", command_id="unsafe-resume", project_id=project, schedule_id="watch",
        state="active", expected_revision=paused["revision"]), "schedule_unresolved")


def test_background_review_pins_approved_workflow_and_sources_without_selfpromotion(artifacts, monkeypatch):
    from tests.tui_gateway.test_workflows_rpc import create_workflow, baselines, evaluate, decision
    project = artifacts.project()["id"]
    row = create_workflow(artifacts, project)
    cases = baselines(artifacts, project)
    evaluated = evaluate(artifacts, row, cases)
    approved = decision(artifacts, evaluated["workflow"])["workflow"]
    ref = cases[0]["generalist_ref"]
    config = definition(project, ref, schedule_id="skill-review")
    config.update(kind="review", specification={"purpose": "skill_review", "source_refs": [ref],
        "workflow_ref": {key: approved[key] for key in ("workflow_id", "version", "sha256")}})
    active = update(artifacts, create(artifacts, config), "active")
    now = active["next_due"]
    assert tick(artifacts, monkeypatch, now) == 1
    view = get(artifacts, active)
    assert view["health"] == "healthy" and view["occurrences"][0]["result"]["workflow_ref"]["version"] == 1
    db = artifacts.agents["a"]._session_db
    with db._runtime_read() as conn:
        occurrence = conn.execute("SELECT * FROM durable_occurrences ORDER BY due_at DESC LIMIT 1").fetchone()
    receipt = db.read_runtime_command(occurrence["session_id"], occurrence["command_id"])["result"]
    assert receipt["source_refs"] == [ref] and receipt["skill_promoted"] is False and receipt["memory_ingested"] is False
    assert receipt["semantic_review"] == "human_review_required"
    # An unrelated mutable active pointer is never consulted by the review.
    second = create_workflow(artifacts, project, version=2, predecessor={key: approved[key] for key in ("workflow_id", "version", "sha256")},
                             template="# Greeting\nDifferent ${input.name}\n")
    assert second["state"] == "draft"
    assert tick(artifacts, monkeypatch, now + 60) == 1
    decision(artifacts, approved, action="revoke", command="revoke-workflow")
    assert tick(artifacts, monkeypatch, now + 120) == 1
    assert get(artifacts, active)["last_error"] == "workflow_not_approved"


def test_pause_cancels_unclaimed_admission_and_expiry_never_runs_source(artifacts, monkeypatch):
    from cron import durable_runtime
    project = artifacts.project()["id"]
    first = source(artifacts, project, "safe")
    active = update(artifacts, create(artifacts, definition(project, first)), "active")
    now = active["next_due"]
    original = durable_runtime.execute_occurrence
    monkeypatch.setattr(durable_runtime, "execute_occurrence", lambda *_args: False)
    tick(artifacts, monkeypatch, now)
    pending = get(artifacts, active)
    paused = update(artifacts, pending, "paused", command="pause-before-claim")
    assert paused["occurrences"][0]["state"] == "cancelled"
    monkeypatch.setattr(durable_runtime, "execute_occurrence", original)
    active = update(artifacts, paused, "active", command="resume-after-cancel")
    assert tick(artifacts, monkeypatch, now) == 0
    assert tick(artifacts, monkeypatch, now + 60) == 1
    assert len(get(artifacts, active)["occurrences"]) == 2
    assert tick(artifacts, monkeypatch, active["definition"]["expires_at"] + 1) == 0
    assert get(artifacts, active)["last_error"] == "schedule_expired"


def test_failed_intent_transaction_does_not_advance_monitor_baseline(artifacts, monkeypatch):
    project = artifacts.project()["id"]
    first = source(artifacts, project, "old")
    active = update(artifacts, create(artifacts, definition(project, first)), "active")
    now = active["next_due"]
    assert tick(artifacts, monkeypatch, now) == 1
    source(artifacts, project, "new", prior=first, command="changed")
    db = artifacts.agents["a"]._session_db
    db._execute_write(lambda conn: conn.execute("CREATE TRIGGER fail_monitor_intent BEFORE INSERT ON durable_monitor_intents "
        "BEGIN SELECT RAISE(ABORT,'fixture intent failure'); END"))
    assert tick(artifacts, monkeypatch, now + 60) == 1
    failed = get(artifacts, active)
    assert failed["health"] == "unhealthy" and failed["intents"] == []
    with db._runtime_read() as conn:
        baseline_refs = json.loads(conn.execute("SELECT source_refs_json FROM durable_monitor_state").fetchone()[0])
        assert baseline_refs[0]["version"] == first["version"]
    db._execute_write(lambda conn: conn.execute("DROP TRIGGER fail_monitor_intent"))
    assert tick(artifacts, monkeypatch, now + 120) == 1
    assert len(get(artifacts, active)["intents"]) == 1
