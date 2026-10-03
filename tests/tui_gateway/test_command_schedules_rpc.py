"""BE07 scheduled prompts use real journal/queue transactions, never a second loop."""
from copy import deepcopy
import json
import time

import pytest

from tests.tui_gateway.test_artifact_rpc import artifacts, result, denied  # noqa: F401
from tests.tui_gateway.test_schedules_rpc import create, update, get, tick, decoded

pytestmark = pytest.mark.platforms("linux")


def definition(rpc, *, schedule_id="prompt", overlap="queue", missed="run_once", max_fires=10):
    project = rpc.project()["id"]
    now = time.time()
    return {"schema_version": 1, "schedule_id": schedule_id, "version": 1, "project_id": project,
        "timezone": "America/New_York", "trigger": {"kind": "interval", "anchor": now + 60, "seconds": 60},
        "policy": {"missed_run": missed, "grace_seconds": 5, "overlap": overlap},
        "budget": {"max_checks": max_fires, "max_bytes": 10000, "deadline_seconds": 300},
        "expires_at": now + 86400, "kind": "command", "specification": {
            "prompt": "Summarize my selected project", "session_id": rpc.agents["a"].session_id,
            "authority_description": "Prepare a private project summary, without external communications"}}


def run_now(rpc, record, command="manual", **extra):
    return rpc.call("runtime.schedule.run_now", project_id=record["project_id"], schedule_id=record["schedule_id"],
        expected_revision=record["revision"], command_id=command, **extra)


def test_real_tick_and_manual_duplicate_share_canonical_queue_and_exact_intent(artifacts, monkeypatch):
    config = definition(artifacts)
    paused = create(artifacts, config)
    active = update(artifacts, paused, "active")
    manual = decoded(run_now(artifacts, active))
    assert manual["command_receipt"]["command_id"] == manual["command_id"] == "manual"
    assert manual["dispatch_performed"] is False
    assert decoded(run_now(artifacts, active)) == manual
    stale = dict(active, revision=active["revision"] + 1)
    denied(run_now(artifacts, stale), "idempotency_conflict")
    db = artifacts.agents["a"]._session_db
    record = db.read_runtime_command(manual["session_id"], manual["command_id"])
    assert record["command"]["operation"] == "submit" and record["command"]["payload"] == {"text": config["specification"]["prompt"]}
    assert tick(artifacts, monkeypatch, active["next_due"], actual=False) == 1
    assert tick(artifacts, monkeypatch, active["next_due"], actual=False) == 0
    view = get(artifacts, active)
    assert view["remaining_checks"] == config["budget"]["max_checks"] - 2
    assert len(view["occurrences"]) == 2
    with db._runtime_read() as conn:
        queued = conn.execute("SELECT * FROM runtime_admission_queue").fetchall()
    assert len(queued) == 2 and all(row["workload"] == "background" for row in queued)
    assert {row["command_id"] for row in queued} == {row["command_id"] for row in view["occurrences"]}


def test_overlap_skip_and_queue_and_pause_never_cancel_claimed_work(artifacts, monkeypatch):
    config = definition(artifacts, overlap="skip")
    active = update(artifacts, create(artifacts, config), "active")
    first = decoded(run_now(artifacts, active))
    assert tick(artifacts, monkeypatch, active["next_due"], actual=False) == 0
    view = get(artifacts, active)
    assert view["remaining_checks"] == 9
    assert view["occurrences"][0]["state"] == "skipped" and view["occurrences"][0]["detail"]["reason"] == "overlap"
    from agent.admission import AdmissionQueue
    db = artifacts.agents["a"]._session_db
    queue = AdmissionQueue(db)
    assert queue.reserve_next("normal-consumer", {first["session_id"]})["command_id"] == first["command_id"]
    assert db.try_acquire_session_turn_lease(first["session_id"], "active-worker", ttl_seconds=120)
    generation = db.get_session_turn_lease(first["session_id"])["generation"]
    assert db.claim_runtime_command(first["session_id"], first["command_id"], holder="active-worker", generation=generation)
    artifacts.agents["a"]._active_runtime_run = object()
    stopped = update(artifacts, view, "paused", command="stop-schedule")
    assert stopped["scheduler_pause_cancels_running"] is False
    assert db.read_runtime_snapshot(first["session_id"])["state"]["run_id"] == first["command_receipt"]["run_id"]
    assert db.read_runtime_command(first["session_id"], first["command_id"])["status"] == "claimed"
    assert tick(artifacts, monkeypatch, active["next_due"] + 60, actual=False) == 0
    assert decoded(artifacts.call("runtime.schedule.update", project_id=stopped["project_id"], schedule_id=stopped["schedule_id"],
        expected_revision=stopped["revision"], state="revoked", command_id="revoke"))["state"] == "revoked"
    assert db.read_runtime_command(first["session_id"], first["command_id"])["status"] == "claimed"


@pytest.mark.parametrize("missed,expected", [("skip", 0), ("run_once", 1)])
def test_restart_missed_policy_accounts_once_without_catchup_storm(artifacts, monkeypatch, missed, expected):
    active = update(artifacts, create(artifacts, definition(artifacts, missed=missed)), "active")
    now = active["next_due"] + 20 * 60 + 10
    assert tick(artifacts, monkeypatch, now, actual=False) == expected
    assert tick(artifacts, monkeypatch, now, actual=False) == 0
    view = get(artifacts, active)
    assert view["remaining_checks"] == 10 - expected
    assert view["next_due"] > now
    assert sum(item["command_id"] is not None for item in view["occurrences"]) == expected


def test_atomic_admission_failure_retry_and_crash_after_claim_never_replay(artifacts, monkeypatch):
    from agent.admission import AdmissionQueue
    from hermes_state_command_schedules import CommandScheduleRegistry
    active = update(artifacts, create(artifacts, definition(artifacts)), "active")
    db = artifacts.agents["a"]._session_db
    original = CommandScheduleRegistry._activation
    calls = []
    def fail_after_prepare(self, conn, row, definition, now):
        calls.append(1)
        original(self, conn, row, definition, now)
        if len(calls) == 2:
            raise RuntimeError("crash before admission commit")
    monkeypatch.setattr(CommandScheduleRegistry, "_activation", fail_after_prepare)
    assert tick(artifacts, monkeypatch, active["next_due"], actual=False) == 0
    view = get(artifacts, active)
    assert view["remaining_checks"] == 10 and view["occurrences"] == []
    with db._runtime_read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM runtime_admission_queue").fetchone()[0] == 0
    monkeypatch.setattr(CommandScheduleRegistry, "_activation", original)
    assert tick(artifacts, monkeypatch, active["next_due"], actual=False) == 1
    first = get(artifacts, active)["occurrences"][0]
    queue = AdmissionQueue(db)
    job = queue.reserve_next("crashed-before-claim", {first["session_id"]})
    assert job["command_id"] == first["command_id"]
    # A fresh queue owner recovers exactly the accepted command after launch lease expiry.
    monkeypatch.setattr(time, "time", lambda: active["next_due"] + 31)
    second = AdmissionQueue(db).reserve_next("restart", {first["session_id"]})
    assert second["command_id"] == first["command_id"]
    assert db.try_acquire_session_turn_lease(first["session_id"], "crashed-after-claim", ttl_seconds=5)
    generation = db.get_session_turn_lease(first["session_id"])["generation"]
    assert db.claim_runtime_command(first["session_id"], first["command_id"], holder="crashed-after-claim", generation=generation)
    assert tick(artifacts, monkeypatch, active["next_due"] + 60, actual=False) == 0
    view = get(artifacts, active)
    assert view["state"] == "paused" and view["occurrences"][0]["state"] == "outcome_unknown"
    assert AdmissionQueue(db).reserve_next("no-replay", {first["session_id"]}) is None
    assert view["remaining_checks"] == 9


def test_expired_exhausted_and_global_paused_authority_prevents_admission(artifacts, monkeypatch):
    from hermes_state_runtime_controls import RuntimeOwnerControls
    from agent.result_artifacts import artifact_actor
    config = definition(artifacts, max_fires=1)
    active = update(artifacts, create(artifacts, config), "active")
    db = artifacts.agents["a"]._session_db
    controls = RuntimeOwnerControls(db, artifact_actor(artifacts.agents["a"].runtime_context))
    controls.set_paused(True, operation_id="pause-all", expected_revision=0)
    assert tick(artifacts, monkeypatch, active["next_due"], actual=False) == 0
    assert get(artifacts, active)["next_due"] == active["next_due"]
    denied(run_now(artifacts, active), "owner_paused")
    controls.set_paused(False, operation_id="resume-all", expected_revision=1)
    assert tick(artifacts, monkeypatch, active["next_due"], actual=False) == 1
    denied(run_now(artifacts, get(artifacts, active)), "schedule_expired")
    assert tick(artifacts, monkeypatch, config["expires_at"] + 1, actual=False) == 0
    assert get(artifacts, active)["remaining_checks"] == 0


def test_import_is_stable_paused_cutover_requires_no_old_claims_and_exact_receipt(artifacts, monkeypatch):
    from cron.durable_contract import digest
    config = definition(artifacts, schedule_id="import_" + digest("old-task")[:32])
    declaration = {"authority": "dots_runner", "source_id": "old-task", "source_state": "paused", "unresolved_occurrences": [],
        "occurrences": [{"source_occurrence_id": "old-run", "due_at": time.time() - 60, "state": "completed"}]}
    request = dict(command_id="import", definition_json=json.dumps(config), import_json=json.dumps(declaration))
    paused = decoded(artifacts.call("runtime.schedule.import", **request))
    assert decoded(artifacts.call("runtime.schedule.import", **request)) == paused
    assert paused["foreign_cutover_verified"] is False and paused["occurrences"][0]["detail"]["legacy_occurrence_id"] == "old-run"
    denied(artifacts.call("runtime.schedule.update", project_id=config["project_id"], schedule_id=config["schedule_id"],
        expected_revision=paused["revision"], state="active", command_id="bad-resume"), "schedule_cutover_blocked")
    params = dict(project_id=config["project_id"], schedule_id=config["schedule_id"], expected_revision=paused["revision"],
        command_id="cutover", source_id="old-task", retirement_receipt="old-admission-frozen:42", unresolved_occurrences=["old-active"])
    denied(artifacts.call("runtime.schedule.cutover", **params), "schedule_cutover_blocked")
    params["unresolved_occurrences"] = []
    retired = decoded(artifacts.call("runtime.schedule.cutover", **params))
    assert retired["foreign_cutover_verified"] is False
    assert decoded(artifacts.call("runtime.schedule.cutover", **params)) == retired
    active = update(artifacts, retired, "active")
    assert tick(artifacts, monkeypatch, active["next_due"], actual=False) == 1
    assert get(artifacts, active)["occurrences_total"] == 2
    declaration["occurrences"][0]["state"] = "failed"
    denied(artifacts.call("runtime.schedule.import", **dict(request, command_id="conflicting-import", import_json=json.dumps(declaration))), "schedule_immutable")


def test_wrong_profile_target_and_changed_control_intent_fail_closed(artifacts):
    config = definition(artifacts)
    config["specification"]["session_id"] = "stored-session"
    denied(artifacts.call("runtime.schedule.create", command_id="wrong-target", definition_json=json.dumps(config)), "identity_mismatch")
    config["specification"]["session_id"] = artifacts.agents["a"].session_id
    paused = create(artifacts, config)
    denied(artifacts.call("runtime.schedule.get", "b", project_id=config["project_id"], schedule_id=config["schedule_id"]))
    active = update(artifacts, paused, "active")
    denied(artifacts.call("runtime.schedule.update", project_id=active["project_id"], schedule_id=active["schedule_id"],
        expected_revision=paused["revision"], state="paused", command_id="activate"), "idempotency_conflict")


def test_run_now_request_key_is_conversation_wide_and_parallel_retries_deduplicate(artifacts):
    from concurrent.futures import ThreadPoolExecutor
    config = definition(artifacts)
    active = update(artifacts, create(artifacts, config), "active")
    with ThreadPoolExecutor(max_workers=4) as pool:
        replies = list(pool.map(lambda _: decoded(run_now(artifacts, active, command="same-request")), range(4)))
    assert all(reply["occurrence_id"] == replies[0]["occurrence_id"] for reply in replies)
    other = deepcopy(config)
    other["schedule_id"] = "second-schedule"
    second = update(artifacts, create(artifacts, other, command="create-second"), "active", command="activate-second")
    denied(run_now(artifacts, second, command="same-request"), "idempotency_conflict")
    assert get(artifacts, active)["remaining_checks"] == 9


def test_manual_key_cannot_reuse_another_runtime_control_command(artifacts):
    active = update(artifacts, create(artifacts, definition(artifacts)), "active")
    denied(run_now(artifacts, active, command="activate"), "idempotency_conflict")
    assert get(artifacts, active)["occurrences"] == []


def test_historical_one_time_import_stays_inspectable_but_cannot_authorize_work(artifacts):
    from cron.durable_contract import digest
    config = definition(artifacts, schedule_id="import_" + digest("finished-task")[:32])
    now = time.time()
    config.update(trigger={"kind": "at", "at": now - 120}, expires_at=now - 60)
    declaration = {"authority": "dots_runner", "source_id": "finished-task", "source_state": "retired", "unresolved_occurrences": [],
        "occurrences": [{"source_occurrence_id": "finished-run", "due_at": now - 120, "state": "completed"}]}
    paused = decoded(artifacts.call("runtime.schedule.import", command_id="import-past", definition_json=json.dumps(config), import_json=json.dumps(declaration)))
    assert paused["next_due"] is None and paused["occurrences"][0]["state"] == "completed"
    denied(run_now(artifacts, paused), "schedule_revision_conflict")


def test_paused_immutable_version_edit_keeps_fire_budget_and_rejects_kind_switch(artifacts):
    config = definition(artifacts, max_fires=2)
    active = update(artifacts, create(artifacts, config), "active")
    manual = decoded(run_now(artifacts, active))
    from agent.admission import AdmissionQueue
    db = artifacts.agents["a"]._session_db
    AdmissionQueue(db).reject_launch(manual["session_id"], manual["command_id"])
    view = get(artifacts, active)
    paused = update(artifacts, view, "paused", command="pause-for-edit")
    successor = deepcopy(config)
    successor["version"] = 2
    successor["budget"]["max_checks"] = 1000
    successor["specification"]["prompt"] = "A revised summary"
    edited = decoded(artifacts.call("runtime.schedule.create", command_id="edit", expected_revision=paused["revision"], definition_json=json.dumps(successor)))
    assert edited["remaining_checks"] == 1 and edited["state"] == "paused"
    assert edited["occurrences"][0]["command_id"] == manual["command_id"]


def test_profile_bound_tick_returns_to_original_home_without_cross_admission(artifacts, monkeypatch):
    a = definition(artifacts)
    project_b = artifacts.project("b")["id"]
    b = deepcopy(a)
    b["project_id"] = project_b
    b["specification"]["session_id"] = artifacts.agents["b"].session_id
    records = {}
    for label, config in (("a", a), ("b", b)):
        paused = decoded(artifacts.call("runtime.schedule.create", label, command_id="create", definition_json=json.dumps(config)))
        records[label] = decoded(artifacts.call("runtime.schedule.update", label, project_id=config["project_id"], schedule_id=config["schedule_id"],
            command_id="activate", expected_revision=paused["revision"], state="active"))
    for label in ("a", "b", "a"):
        now = records[label]["next_due"] + (60 if label == "a" and get(artifacts, records[label])["occurrences_total"] else 0)
        assert tick(artifacts, monkeypatch, now, label=label, actual=False) == 1
    for label, expected in (("a", 2), ("b", 1)):
        view = get(artifacts, records[label], label=label)
        assert view["occurrences_total"] == expected
        with artifacts.agents[label]._session_db._runtime_read() as conn:
            assert conn.execute("SELECT COUNT(*) FROM runtime_admission_queue").fetchone()[0] == expected


def test_schedule_stop_controls_work_while_global_pause_blocks_all_new_work(artifacts):
    from hermes_state_runtime_controls import RuntimeOwnerControls
    from agent.result_artifacts import artifact_actor
    active = update(artifacts, create(artifacts, definition(artifacts)), "active")
    db = artifacts.agents["a"]._session_db
    controls = RuntimeOwnerControls(db, artifact_actor(artifacts.agents["a"].runtime_context))
    controls.set_paused(True, operation_id="pause-all", expected_revision=0)
    paused = update(artifacts, active, "paused", command="pause-schedule")
    denied(artifacts.call("runtime.schedule.update", project_id=active["project_id"], schedule_id=active["schedule_id"],
        expected_revision=paused["revision"], state="active", command_id="resume-schedule"), "owner_paused")
    cancelled = update(artifacts, paused, "revoked", command="revoke-schedule")
    assert cancelled["state"] == "revoked" and cancelled["remaining_checks"] == 10


@pytest.mark.parametrize("definition_json", ["[]", "null", "42", '{"kind":"command"}'])
def test_malformed_command_schedule_definition_has_a_safe_client_error(artifacts, definition_json):
    response = artifacts.call("runtime.schedule.create", command_id="malformed", definition_json=definition_json)
    error = denied(response)
    assert error["code"] in {4000, 4090}


def test_offline_queue_expiry_is_terminal_then_next_due_can_admit(artifacts, monkeypatch):
    active = update(artifacts, create(artifacts, definition(artifacts, overlap="skip")), "active")
    now = active["next_due"]
    assert tick(artifacts, monkeypatch, now, actual=False) == 1
    original = get(artifacts, active)["occurrences"][0]
    assert tick(artifacts, monkeypatch, now + 301, actual=False) == 1
    view = get(artifacts, active)
    first = artifacts.agents["a"]._session_db.read_runtime_command(original["session_id"], original["command_id"])
    assert first["status"] == "blocked" and first["result"]["admission_state"] == "expired"
    assert view["remaining_checks"] == 8
    assert sum(row["state"] == "accepted" for row in view["occurrences"]) == 1


def test_lost_manual_response_after_atomic_commit_replays_original_identity(artifacts, monkeypatch):
    from agent.admission import AdmissionQueue
    active = update(artifacts, create(artifacts, definition(artifacts)), "active")
    original = AdmissionQueue.submit_bound
    calls = []
    def lose_response(self, *args, **kwargs):
        value = original(self, *args, **kwargs)
        calls.append(value)
        raise RuntimeError("synthetic response loss after commit")
    monkeypatch.setattr(AdmissionQueue, "submit_bound", lose_response)
    with pytest.raises(RuntimeError, match="synthetic response loss"):
        run_now(artifacts, active, command="lost-response")
    replay = decoded(run_now(artifacts, active, command="lost-response"))
    assert replay["command_receipt"] == calls[0] and len(calls) == 1
    view = get(artifacts, active)
    assert view["occurrences_total"] == 1 and view["remaining_checks"] == 9
