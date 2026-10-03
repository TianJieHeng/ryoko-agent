"""Human policy → real cron → canonical BE06 outbox → owned transport and receipts."""
import json
import time

from tests.tui_gateway.test_artifact_rpc import artifacts, denied, result  # noqa: F401
from tests.tui_gateway.test_schedules_rpc import definition, create, update, get, source, tick, decoded

import pytest
pytestmark = pytest.mark.platforms("linux")


def notice_view(rpc, row):
    return decoded(rpc.call("runtime.monitor.notifications", project_id=row["project_id"], schedule_id=row["schedule_id"]))


def setup(rpc, *, digest_seconds=0, max_deliveries=10):
    project = rpc.project()["id"]
    first = source(rpc, project, "old")
    record = definition(project, first)
    record["specification"]["notify_policy"] = "local_runtime"
    row = create(rpc, record)
    config = {"kind": "local_runtime", "timezone": "Etc/UTC", "quiet_hours": None,
        "digest_seconds": digest_seconds, "max_deliveries": max_deliveries, "expires_at": record["expires_at"]}
    receipt = decoded(rpc.call("runtime.monitor.policy.set", command_id="policy", project_id=project,
        schedule_id="watch", expected_revision=row["revision"], policy_json=json.dumps(config)))
    row = update(rpc, get(rpc, row), "active")
    assert receipt["destination"]["session_id"] == rpc.agents["a"].session_id
    return row, first, config


def frames(rpc):
    captured = []
    rpc.peers["a"].write = lambda frame: captured.append(frame) or True
    return captured


def notification_frames(captured):
    return [frame["params"]["payload"] for frame in captured if frame.get("params", {}).get("type") == "runtime.monitor.available"]


def test_real_tick_owned_event_receipt_dedup_dismiss_and_restart(artifacts, monkeypatch):
    row, first, _ = setup(artifacts)
    captured = frames(artifacts)
    now = row["next_due"]
    assert tick(artifacts, monkeypatch, now) == 1
    second = source(artifacts, row["project_id"], "meaningful", prior=first, command="change")
    assert tick(artifacts, monkeypatch, now + 60) == 1
    events = notification_frames(captured)
    assert len(events) == 1
    event = events[0]
    payload = json.loads(event["notification_json"])
    assert payload["items"][0]["source_refs"][0]["version"] == second["version"]
    assert payload["items"][0]["previous_source_refs"][0]["version"] == first["version"]
    view = notice_view(artifacts, row)
    assert view["notices"][0]["delivery"]["state"] == "awaiting_ack"
    denied(artifacts.call("runtime.delivery.ack", "b", delivery_id=event["delivery_id"], attempt_token=event["attempt_token"],
        sha256=event["sha256"], text_received=True, artifact_received=True))
    receipt = result(artifacts.call("runtime.delivery.ack", delivery_id=event["delivery_id"], attempt_token=event["attempt_token"],
        sha256=event["sha256"], text_received=True, artifact_received=True))
    assert receipt["state"] == "delivered" and receipt["acknowledgment_level"] == "client_received"
    dismissed = decoded(artifacts.call("runtime.monitor.dismiss", command_id="dismiss", project_id=row["project_id"], schedule_id="watch",
        expected_revision=get(artifacts, row)["revision"], intent_id=view["notices"][0]["intent_id"]))
    assert dismissed["notices"][0]["dismissed_at"] == now + 60
    from hermes_state import SessionDB
    artifacts.agents["a"]._session_db.close()
    artifacts.agents["a"]._session_db = SessionDB(artifacts.homes["a"] / "state.db")
    source(artifacts, row["project_id"], "  meaningful\n", prior=second, command="cosmetic")
    tick(artifacts, monkeypatch, now + 120)
    assert len(notification_frames(captured)) == 1
    assert notice_view(artifacts, row)["notices_total"] == 1
    assert notice_view(artifacts, row)["notices"][0]["state"] == "dismissed"


def test_snooze_digest_retains_all_versions_and_checks_continue_through_expiry(artifacts, monkeypatch):
    row, first, _ = setup(artifacts, digest_seconds=120)
    captured = frames(artifacts)
    now = row["next_due"]
    decoded(artifacts.call("runtime.monitor.snooze", command_id="snooze", project_id=row["project_id"], schedule_id="watch",
        expected_revision=row["revision"], until_at=now + 240))
    tick(artifacts, monkeypatch, now)
    second = source(artifacts, row["project_id"], "second", prior=first, command="second")
    tick(artifacts, monkeypatch, now + 60)
    third = source(artifacts, row["project_id"], "third", prior=second, command="third")
    tick(artifacts, monkeypatch, now + 120)
    assert notice_view(artifacts, row)["pending_total"] == 2
    assert notice_view(artifacts, row)["notices"][0]["hold_reason"] == "snoozed"
    assert get(artifacts, row)["occurrences_total"] == 3
    tick(artifacts, monkeypatch, now + 180)
    assert not notification_frames(captured)
    tick(artifacts, monkeypatch, now + 240)
    events = notification_frames(captured)
    assert len(events) == 1
    payload = json.loads(events[0]["notification_json"])
    assert payload["kind"] == "digest"
    assert [item["source_refs"][0]["version"] for item in payload["items"]] == [second["version"], third["version"]]
    assert get(artifacts, row)["occurrences_total"] == 5


def test_unknown_digest_explicit_bounded_retry_does_not_rerun_check_or_read_new_source(artifacts, monkeypatch):
    row, first, _ = setup(artifacts, digest_seconds=60)
    captured = frames(artifacts)
    now = row["next_due"]
    tick(artifacts, monkeypatch, now)
    second = source(artifacts, row["project_id"], "second", prior=first, command="second")
    tick(artifacts, monkeypatch, now + 60)
    artifacts.peers["a"].write = lambda frame: captured.append(frame) or False
    tick(artifacts, monkeypatch, now + 120)
    event = notification_frames(captured)[0]
    view = notice_view(artifacts, row)
    assert view["notices"][0]["delivery"]["state"] == "outcome_unknown"
    tick(artifacts, monkeypatch, now + 180)
    assert len(notification_frames(captured)) == 1
    source(artifacts, row["project_id"], "newer unseen", prior=second, command="newer")
    before = get(artifacts, row)["occurrences_total"]
    artifacts.peers["a"].write = lambda frame: captured.append(frame) or True
    receipt = result(artifacts.call("runtime.delivery.retry", delivery_id=event["delivery_id"]))
    assert receipt["attempt_count"] == 2 and receipt["state"] == "awaiting_ack"
    assert notification_frames(captured)[-1]["notification_json"] == event["notification_json"]
    assert get(artifacts, row)["occurrences_total"] == before
    monkeypatch.setattr(time, "time", lambda: now + 240)
    result(artifacts.call("runtime.delivery.retry", delivery_id=event["delivery_id"]))
    monkeypatch.setattr(time, "time", lambda: now + 300)
    exhausted = result(artifacts.call("runtime.delivery.retry", delivery_id=event["delivery_id"]))
    assert exhausted["state"] == "dead_letter" and exhausted["attempt_count"] == 3


def test_observation_never_grants_send_policy_version_change_preserves_pending(artifacts, monkeypatch):
    project = artifacts.project()["id"]
    first = source(artifacts, project, "old")
    config = definition(project, first); config["specification"]["notify_policy"] = "local_runtime"
    row = update(artifacts, create(artifacts, config), "active")
    now = row["next_due"]; captured = frames(artifacts)
    tick(artifacts, monkeypatch, now)
    source(artifacts, project, "change", prior=first, command="change")
    tick(artifacts, monkeypatch, now + 60)
    view = notice_view(artifacts, row)
    assert view["notices"][0]["state"] == "awaiting_policy" and not notification_frames(captured)
    policy = {"kind": "local_runtime", "timezone": "Etc/UTC", "quiet_hours": None,
        "digest_seconds": 0, "max_deliveries": 1, "expires_at": config["expires_at"]}
    decoded(artifacts.call("runtime.monitor.policy.set", command_id="policy-later", project_id=project,
        schedule_id="watch", expected_revision=get(artifacts, row)["revision"], policy_json=json.dumps(policy)))
    tick(artifacts, monkeypatch, now + 120)
    assert not notification_frames(captured) and notice_view(artifacts, row)["pending_total"] == 1


def test_pause_revoke_budget_and_foreign_transport_block_new_delivery(artifacts, monkeypatch):
    row, first, _ = setup(artifacts, max_deliveries=1)
    captured = frames(artifacts); now = row["next_due"]
    tick(artifacts, monkeypatch, now)
    second = source(artifacts, row["project_id"], "second", prior=first, command="second")
    tick(artifacts, monkeypatch, now + 60)
    event = notification_frames(captured)[0]
    denied(artifacts.call("runtime.monitor.notifications", via=artifacts.peers["b"], project_id=row["project_id"], schedule_id="watch"))
    denied(artifacts.call("runtime.delivery.retry", via=artifacts.peers["b"], delivery_id=event["delivery_id"]))
    source(artifacts, row["project_id"], "third", prior=second, command="third")
    tick(artifacts, monkeypatch, now + 120)
    view = notice_view(artifacts, row)
    assert view["remaining_deliveries"] == 0 and view["pending_total"] == 2
    assert view["notices"][0]["hold_reason"] == "notification_budget_exhausted"
    paused = update(artifacts, get(artifacts, row), "paused", command="pause")
    denied(artifacts.call("runtime.delivery.retry", delivery_id=event["delivery_id"]), "schedule_paused")
    revoked = update(artifacts, paused, "revoked", command="revoke")
    tick(artifacts, monkeypatch, now + 180)
    assert len(notification_frames(captured)) == 1
    assert notice_view(artifacts, row)["pending_total"] == 2
    assert result(artifacts.call("runtime.delivery.status", delivery_id=event["delivery_id"]))["state"] == "awaiting_ack"


def test_pending_offline_digest_dismissal_regroups_without_dropping_other_change(artifacts, monkeypatch):
    from tui_gateway import server
    row, first, _ = setup(artifacts, digest_seconds=60)
    now = row["next_due"]; captured = frames(artifacts)
    tick(artifacts, monkeypatch, now)
    second = source(artifacts, row["project_id"], "second", prior=first, command="second")
    tick(artifacts, monkeypatch, now + 60)
    source(artifacts, row["project_id"], "third", prior=second, command="third")
    # A connected read/control session is not a background transport while detached.
    server._sessions["live-a"]["transport"] = None
    tick(artifacts, monkeypatch, now + 120)
    server._sessions["live-a"]["transport"] = artifacts.peers["a"]
    view = notice_view(artifacts, row)
    assert len({item["delivery_id"] for item in view["notices"]}) == 1
    old_id = view["notices"][0]["delivery_id"]
    decoded(artifacts.call("runtime.monitor.dismiss", command_id="dismiss-one", project_id=row["project_id"], schedule_id="watch",
        expected_revision=get(artifacts, row)["revision"], intent_id=view["notices"][1]["intent_id"]))
    tick(artifacts, monkeypatch, now + 180)
    event = notification_frames(captured)[0]
    assert event["delivery_id"] != old_id
    assert len(json.loads(event["notification_json"])["items"]) == 1
    assert result(artifacts.call("runtime.delivery.status", delivery_id=old_id))["last_error"] == "notification_dismissed"


def test_live_policy_revocation_and_pause_claim_race_preserve_offline_notice(artifacts, monkeypatch):
    from tui_gateway import server
    from hermes_state_delivery import SessionDeliveryMixin
    row, first, _ = setup(artifacts)
    now = row["next_due"]; captured = frames(artifacts)
    tick(artifacts, monkeypatch, now)
    source(artifacts, row["project_id"], "changed", prior=first, command="changed")
    original = SessionDeliveryMixin.claim_runtime_delivery
    def pause_before_claim(db, sid, actor, did):
        db._execute_write(lambda conn: conn.execute("UPDATE durable_schedules SET state='paused',revision=revision+1"))
        return original(db, sid, actor, did)
    monkeypatch.setattr(SessionDeliveryMixin, "claim_runtime_delivery", pause_before_claim)
    tick(artifacts, monkeypatch, now + 60)
    assert not notification_frames(captured)
    view = notice_view(artifacts, row)
    notice = view["notices"][0]
    assert notice["delivery"]["state"] == "pending" and notice["hold_reason"] == "schedule_paused"
    monkeypatch.setattr(SessionDeliveryMixin, "claim_runtime_delivery", original)
    update(artifacts, get(artifacts, row), "active", command="resume")
    # Administrator revokes the immutable live project grant in this synthetic home.
    path = artifacts.homes["a"] / "config.yaml"
    config = json.loads(path.read_text())
    config["agent_identity"]["agents"]["ryoko"]["project_grants"] = []
    path.write_text(json.dumps(config))
    tick(artifacts, monkeypatch, now + 120)
    assert not notification_frames(captured)
    db = artifacts.agents["a"]._session_db
    with db._runtime_read() as conn:
        delivery = conn.execute("SELECT state,attempts FROM delivery_obligations WHERE obligation_id=?", (notice["delivery_id"],)).fetchone()
        assert delivery["state"] == "pending" and delivery["attempts"] == 0
        assert conn.execute("SELECT COUNT(*) FROM durable_monitor_notices").fetchone()[0] == 1


def test_expired_notification_grant_and_outage_do_not_clear_snoozed_evidence(artifacts, monkeypatch):
    row, first, config = setup(artifacts)
    now = row["next_due"]; captured = frames(artifacts)
    config["expires_at"] = now + 180
    decoded(artifacts.call("runtime.monitor.policy.set", command_id="short-policy", project_id=row["project_id"], schedule_id="watch",
        expected_revision=row["revision"], policy_json=json.dumps(config)))
    decoded(artifacts.call("runtime.monitor.snooze", command_id="until-expiry", project_id=row["project_id"], schedule_id="watch",
        expected_revision=get(artifacts, row)["revision"], until_at=now + 180))
    tick(artifacts, monkeypatch, now)
    second = source(artifacts, row["project_id"], "changed", prior=first, command="changed")
    tick(artifacts, monkeypatch, now + 60)
    db = artifacts.agents["a"]._session_db
    with db._runtime_read() as conn:
        descriptor = json.loads(conn.execute("SELECT descriptor_json FROM runtime_artifact_versions WHERE artifact_id=? AND version=?",
            (second["artifact_id"], second["version"])).fetchone()[0])
    (artifacts.homes["a"] / descriptor["locator"]).unlink()
    tick(artifacts, monkeypatch, now + 120)
    assert get(artifacts, row)["health"] == "unhealthy"
    tick(artifacts, monkeypatch, now + 180)
    view = notice_view(artifacts, row)
    assert view["pending_total"] == 1 and view["notices"][0]["hold_reason"] == "notification_expired"
    assert not notification_frames(captured)


def test_definitely_unsent_backoff_retries_only_delivery_and_policy_edit_holds_queued(artifacts, monkeypatch):
    from tui_gateway import server
    from agent.result_artifacts import artifact_actor
    row, first, config = setup(artifacts)
    now = row["next_due"]; captured = frames(artifacts)
    tick(artifacts, monkeypatch, now)
    second = source(artifacts, row["project_id"], "second", prior=first, command="second")
    server._sessions["live-a"]["transport"] = None
    tick(artifacts, monkeypatch, now + 60)
    server._sessions["live-a"]["transport"] = artifacts.peers["a"]
    notice = notice_view(artifacts, row)["notices"][0]
    db, agent = artifacts.agents["a"]._session_db, artifacts.agents["a"]
    actor = artifact_actor(agent.runtime_context)
    claim = db.claim_runtime_delivery(agent.session_id, actor, notice["delivery_id"])
    failed = db.finish_runtime_delivery_attempt(agent.session_id, actor, notice["delivery_id"], claim["attempt_token"],
                                               accepted=False, definitely_not_sent=True)
    assert failed["state"] == "failed" and failed["next_attempt_at"] > now + 60
    before = get(artifacts, row)["occurrences_total"]
    tick(artifacts, monkeypatch, now + 70)
    assert len(notification_frames(captured)) == 1 and get(artifacts, row)["occurrences_total"] == before
    source(artifacts, row["project_id"], "third", prior=second, command="third")
    server._sessions["live-a"]["transport"] = None
    tick(artifacts, monkeypatch, now + 120)
    server._sessions["live-a"]["transport"] = artifacts.peers["a"]
    config["digest_seconds"] = 60
    edited = decoded(artifacts.call("runtime.monitor.policy.set", command_id="edit-policy", project_id=row["project_id"], schedule_id="watch",
        expected_revision=get(artifacts, row)["revision"], policy_json=json.dumps(config)))
    assert edited["notices"][0]["hold_reason"] == "notification_policy_superseded"
    tick(artifacts, monkeypatch, now + 180)
    assert len(notification_frames(captured)) == 1
    assert notice_view(artifacts, row)["notices_total"] == 2


def test_restart_after_delivery_claim_preserves_unknown_and_same_actor_other_session_is_denied(artifacts, monkeypatch):
    from agent.agent_identity import resolve_agent_context
    from agent.result_artifacts import artifact_actor
    from hermes_state import SessionDB
    from tui_gateway import server
    from types import SimpleNamespace
    row, first, _ = setup(artifacts)
    now = row["next_due"]; captured = frames(artifacts)
    tick(artifacts, monkeypatch, now)
    source(artifacts, row["project_id"], "changed", prior=first, command="changed")
    server._sessions["live-a"]["transport"] = None
    tick(artifacts, monkeypatch, now + 60)
    server._sessions["live-a"]["transport"] = artifacts.peers["a"]
    notice = notice_view(artifacts, row)["notices"][0]
    agent, db = artifacts.agents["a"], artifacts.agents["a"]._session_db
    assert db.claim_runtime_delivery(agent.session_id, artifact_actor(agent.runtime_context), notice["delivery_id"])
    db.close(); db = SessionDB(artifacts.homes["a"] / "state.db"); agent._session_db = db
    tick(artifacts, monkeypatch, now + 120)
    assert not notification_frames(captured)
    assert notice_view(artifacts, row)["notices"][0]["delivery"]["state"] == "outcome_unknown"
    config = json.loads((artifacts.homes["a"] / "config.yaml").read_text())
    context = resolve_agent_context(config, session_id="other-owned-session", profile_home=artifacts.homes["a"])
    db.create_session(context.identity.session_id, source="tui")
    db.claim_session_agent_identity(context.identity.session_id, context.identity.to_record())
    db.submit_runtime_command(context.identity.session_id, artifact_actor(context), {"schema_version": 1,
        "command_id": "other-control", "idempotency_key": "other-control", "expected_revision": None,
        "operation": "artifact", "payload": {"mode": "test"}})
    other = SimpleNamespace(runtime_context=context, _session_db=db, session_id=context.identity.session_id)
    peer = SimpleNamespace(write=lambda frame: True)
    server._sessions["live-other"] = {"agent": other, "transport": peer, "profile_home": str(artifacts.homes["a"]),
        "session_key": other.session_id}
    response = server.dispatch({"jsonrpc": "2.0", "id": "foreign-session", "method": "runtime.monitor.notifications",
        "params": {"session_id": "live-other", "schema_version": 1, "project_id": row["project_id"], "schedule_id": "watch"}}, transport=peer)
    denied(response, "identity_mismatch")
    receipt = result(artifacts.call("runtime.delivery.retry", delivery_id=notice["delivery_id"]))
    assert receipt["attempt_count"] == 2 and len(notification_frames(captured)) == 1


def test_bounded_history_cursor_keeps_older_pending_changes_reachable(artifacts, monkeypatch):
    row, current, _ = setup(artifacts)
    now = row["next_due"]
    decoded(artifacts.call("runtime.monitor.snooze", command_id="snooze-long", project_id=row["project_id"], schedule_id="watch",
        expected_revision=row["revision"], until_at=now + 3600))
    tick(artifacts, monkeypatch, now)
    for index in range(1, 10):
        current = source(artifacts, row["project_id"], f"change {index}", prior=current, command=f"change-{index}")
        tick(artifacts, monkeypatch, now + index * 60)
    view = notice_view(artifacts, row)
    assert len(view["notices"]) == 8 and view["notices_total"] == view["pending_total"] == 9
    older = decoded(artifacts.call("runtime.monitor.notifications", project_id=row["project_id"], schedule_id="watch",
        cursor_json=view["next_cursor_json"]))
    assert len(older["notices"]) == 1 and older["next_cursor_json"] is None
    assert not {item["intent_id"] for item in view["notices"]} & {item["intent_id"] for item in older["notices"]}


def test_real_a_b_a_ticks_keep_identical_schedule_and_session_ids_in_owned_home(artifacts, monkeypatch):
    from tests.tui_gateway.test_artifact_rpc import publish
    from types import SimpleNamespace
    row_a, source_a, _ = setup(artifacts)
    project_b = artifacts.project("b")["id"]
    source_b = publish(artifacts, {"project_id": project_b, "command_id": "source-b", "request_id": "source-b", "content": "b old"}, label="b")
    config = definition(project_b, source_b); config["specification"]["notify_policy"] = "local_runtime"
    config["trigger"]["anchor"] = row_a["next_due"]
    rpc_b = SimpleNamespace(call=lambda method, *args, **params: artifacts.call(method, "b", **params))
    row_b = create(rpc_b, config)
    policy = {"kind": "local_runtime", "timezone": "Etc/UTC", "quiet_hours": None,
        "digest_seconds": 0, "max_deliveries": 2, "expires_at": config["expires_at"]}
    decoded(artifacts.call("runtime.monitor.policy.set", "b", command_id="policy-b", project_id=project_b, schedule_id="watch",
        expected_revision=row_b["revision"], policy_json=json.dumps(policy)))
    row_b = update(rpc_b, get(rpc_b, row_b), "active")
    assert artifacts.agents["a"].session_id == artifacts.agents["b"].session_id
    a_frames, b_frames = frames(artifacts), []
    artifacts.peers["b"].write = lambda frame: b_frames.append(frame) or True
    now = row_a["next_due"]
    tick(artifacts, monkeypatch, now)
    tick(artifacts, monkeypatch, now, label="b")
    source(artifacts, row_a["project_id"], "a new", prior=source_a, command="a-new")
    publish(artifacts, {"project_id": project_b, "command_id": "b-new", "request_id": "b-new", "content": "b new",
        "artifact_id": source_b["artifact_id"], "parent_version": source_b["version"], "expected_head_version": source_b["version"]}, label="b")
    tick(artifacts, monkeypatch, now + 60)
    assert len(notification_frames(a_frames)) == 1 and not notification_frames(b_frames)
    tick(artifacts, monkeypatch, now + 60, label="b")
    assert len(notification_frames(b_frames)) == 1
    tick(artifacts, monkeypatch, now + 120)
    assert len(notification_frames(a_frames)) == 1
    assert json.loads(notification_frames(a_frames)[0]["notification_json"])["items"][0]["source_refs"][0]["artifact_id"] == source_a["artifact_id"]
    assert json.loads(notification_frames(b_frames)[0]["notification_json"])["items"][0]["source_refs"][0]["artifact_id"] == source_b["artifact_id"]
