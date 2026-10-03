"""Off-session cron resolves frozen copied identities, never mutable defaults."""
from contextlib import contextmanager
from copy import deepcopy
import json
import time
from types import SimpleNamespace

import pytest

from tests.tui_gateway.test_artifact_rpc import artifacts, result  # noqa: F401
from tests.tui_gateway.test_schedules_rpc import create, update, get, decoded, source, definition as monitor_definition
from tests.tui_gateway.test_workflow_delivery_rpc import configured, start_specialist
from tests.tui_gateway.test_monitor_notifications_rpc import notification_frames

pytestmark = pytest.mark.platforms("linux")


@contextmanager
def off_session(home):
    from agent.runtime_context import bind_agent_context, current_agent_context
    from cron.scheduler_provider import _profile_cron_scope
    with bind_agent_context(None), _profile_cron_scope(home):
        assert current_agent_context() is None
        yield


def managed_copy(rt, label="a"):
    project = configured(rt, label)
    template = result(rt.call("runtime.agent.get", label, agent_id="researcher"))["agent"]
    copied = result(rt.call("runtime.agent.create", label, copy_from_agent_id="researcher",
        config={**template["config"], "instructions": "Frozen schedule instructions"}))["agent"]
    assert copied["agent_id"] not in json.loads((rt.homes[label] / "config.yaml").read_text())["agent_identity"]["agents"]
    result(rt.call("runtime.project.grants.set", label, project_id=project["id"], expected_revision=project["revision"],
        grants=project["grants"] + [{"principal_id": "owner", "agent_id": copied["agent_id"], "permissions": ["read", "write"]}]))
    agent, startup = start_specialist(rt, "copied-session", label=label, name=copied["agent_id"])
    from tui_gateway import server
    ui_id = "live-copied-" + label
    server._sessions[ui_id] = server._sessions.pop("live-copied-session")
    def call(method, _label="a", **params):
        return server.dispatch({"jsonrpc": "2.0", "id": "managed-schedule", "method": method, "params": {
            "schema_version": 1, "session_id": ui_id, **params}}, transport=rt.peers[label])
    return SimpleNamespace(call=call, agent=agent, startup=startup, copied=copied, project=project["id"], home=rt.homes[label])


def command_definition(rpc):
    now = time.time()
    return {"schema_version": 1, "schedule_id": "copied-work", "version": 1, "project_id": rpc.project,
        "timezone": "Etc/UTC", "trigger": {"kind": "interval", "anchor": now + 60, "seconds": 60},
        "policy": {"missed_run": "run_once", "grace_seconds": 120, "overlap": "queue"},
        "budget": {"max_checks": 10, "max_bytes": 10000, "deadline_seconds": 300}, "expires_at": now + 3600,
        "kind": "command", "specification": {"prompt": "Prepare a private summary", "session_id": rpc.agent.session_id,
            "authority_description": "Private summary only"}}


def tick(home, now, monkeypatch):
    from cron.durable_runtime import tick_durable_schedules
    monkeypatch.setattr(time, "time", lambda: now)
    with off_session(home):
        return tick_durable_schedules()


def test_off_session_copied_schedule_frozen_restart_and_profile_a_b_a(artifacts, monkeypatch):
    from agent.conversation_identity import recorded_owner_scope
    from agent.identity_lifecycle import agent_runtime_scope
    from agent.individual_memory_scope import IndividualMemoryScope
    from hermes_state import SessionDB
    from tools.individual_memory_store import IndividualMemoryStore
    from tools.capability_broker import require_live_policy
    peers = {label: managed_copy(artifacts, label) for label in ("a", "b")}
    rows = {label: update(peer, create(peer, command_definition(peer)), "active") for label, peer in peers.items()}
    pinned = {label: deepcopy(peer.startup) for label, peer in peers.items()}
    namespaces = {}
    for label, peer in peers.items():
        with agent_runtime_scope(peer.agent.runtime_context):
            store = IndividualMemoryStore(peer.agent.runtime_context)
            store.load_from_disk()
            store.add("memory", "Private schedule memory " + label)
            namespaces[label] = store.namespace_id
        result(artifacts.call("runtime.agent.update", label, agent_id=peer.copied["agent_id"], expected_revision=peer.copied["revision"],
            config={**peer.copied["config"], "instructions": "Only future sessions get these instructions"}))
    for label in ("a", "b", "a"):
        peer = peers[label]
        with off_session(peer.home):
            context = recorded_owner_scope(peer.agent._session_db, owner_binding=peer.agent.runtime_context.identity.to_record(),
                session_id="transient-check", profile_home=peer.home)
        with agent_runtime_scope(context):
            assert require_live_policy(require_run=False) == context
            assert IndividualMemoryScope.from_context(context).namespace_id == namespaces[label]
            store = IndividualMemoryStore(context)
            store.load_from_disk()
            assert any("Private schedule memory " + label in item for item in store.memory_entries)
        now = get(peer, rows[label])["next_due"]
        assert tick(peer.home, now, monkeypatch) == 1
        assert peer.startup == pinned[label]
    assert namespaces["a"] != namespaces["b"]
    peer = peers["a"]
    path = peer.agent._session_db.db_path
    peer.agent._session_db.close()
    reopened = SessionDB(path)
    peer.agent._session_db = reopened
    artifacts.agents["a"]._session_db = reopened
    assert tick(peer.home, get(peer, rows["a"])["next_due"], monkeypatch) == 1
    with reopened._runtime_read() as conn:
        queue = conn.execute("SELECT * FROM runtime_admission_queue WHERE session_id=?", (peer.agent.session_id,)).fetchall()
        assert len(queue) == 3 and all(row["workload"] == "background" for row in queue)
    assert get(peer, rows["a"])["remaining_checks"] == 7


@pytest.mark.parametrize("change", [{"memory_allowed": False}, {"project_grants": []}, "archive"])
def test_revocation_blocks_before_admission_and_regrant_never_revives_pin(artifacts, monkeypatch, change):
    from agent.conversation_identity import recorded_owner_scope
    from tools.capability_broker import CapabilityDenied
    peer = managed_copy(artifacts)
    row = update(peer, create(peer, command_definition(peer)), "active")
    original, snapshot = peer.agent.runtime_context, deepcopy(peer.startup)
    params = {"agent_id": peer.copied["agent_id"], "expected_revision": peer.copied["revision"]}
    if change == "archive":
        result(artifacts.call("runtime.agent.archive", **params))
    else:
        narrowed = result(artifacts.call("runtime.agent.update", **params, config={**peer.copied["config"], **change}))["agent"]
        result(artifacts.call("runtime.agent.update", agent_id=peer.copied["agent_id"], expected_revision=narrowed["revision"], config=peer.copied["config"]))
    with off_session(peer.home), pytest.raises(CapabilityDenied, match="narrowed or archived"):
        recorded_owner_scope(peer.agent._session_db, owner_binding=original.identity.to_record(), session_id="after-revoke", profile_home=peer.home)
    assert tick(peer.home, row["next_due"], monkeypatch) == 0
    with peer.agent._session_db._runtime_read() as conn:
        saved = conn.execute("SELECT * FROM durable_schedules WHERE schedule_id='copied-work'").fetchone()
        assert saved["state"] == "paused" and saved["remaining_checks"] == 10
        assert conn.execute("SELECT COUNT(*) FROM runtime_admission_queue").fetchone()[0] == 0
    assert original == peer.agent.runtime_context and snapshot == peer.startup


def test_copied_monitor_occurrences_and_notice_outbox_use_frozen_scoped_owner(artifacts, monkeypatch):
    from hermes_state import SessionDB
    peer = managed_copy(artifacts)
    initial = source(peer, peer.project, "old")
    definition = monitor_definition(peer.project, initial)
    definition["specification"]["notify_policy"] = "local_runtime"
    row = create(peer, definition)
    policy = {"kind": "local_runtime", "timezone": "Etc/UTC", "quiet_hours": None, "digest_seconds": 0,
              "max_deliveries": 10, "expires_at": definition["expires_at"]}
    decoded(peer.call("runtime.monitor.policy.set", command_id="policy", project_id=peer.project, schedule_id="watch",
        expected_revision=row["revision"], policy_json=json.dumps(policy)))
    row = update(peer, get(peer, row), "active")
    captured = []
    artifacts.peers["a"].write = lambda frame: captured.append(frame) or True
    now = row["next_due"]
    assert tick(peer.home, now, monkeypatch) == 1
    changed = source(peer, peer.project, "changed", prior=initial, command="changed")
    # Reopening only the canonical store cannot replace the selected snapshot.
    path = peer.agent._session_db.db_path
    peer.agent._session_db.close()
    peer.agent._session_db = SessionDB(path)
    artifacts.agents["a"]._session_db = peer.agent._session_db
    assert tick(peer.home, now + 60, monkeypatch) == 1
    events = notification_frames(captured)
    assert len(events) == 1
    assert json.loads(events[0]["notification_json"])["items"][0]["source_refs"][0]["version"] == changed["version"]
    view = decoded(peer.call("runtime.monitor.notifications", project_id=peer.project, schedule_id="watch"))
    assert view["destination"]["agent_id"] == peer.copied["agent_id"]
    assert view["destination"]["session_id"] == peer.agent.session_id
    assert view["notices"][0]["delivery"]["state"] == "awaiting_ack"
    original = result(artifacts.call("runtime.agent.get", agent_id=peer.copied["agent_id"]))["agent"]
    result(artifacts.call("runtime.agent.archive", agent_id=peer.copied["agent_id"], expected_revision=original["revision"]))
    assert tick(peer.home, now + 120, monkeypatch) == 0
    assert len(notification_frames(captured)) == 1


def test_recorded_resolver_rejects_wrong_store_and_existing_foreign_session(artifacts):
    from agent.conversation_identity import recorded_owner_scope
    from hermes_state_runtime import RuntimeStoreError
    peers = {label: managed_copy(artifacts, label) for label in ("a", "b")}
    a, b = peers["a"], peers["b"]
    with off_session(b.home), pytest.raises(RuntimeStoreError):
        recorded_owner_scope(a.agent._session_db, owner_binding=a.agent.runtime_context.identity.to_record(), session_id="wrong", profile_home=b.home)
    with off_session(a.home), pytest.raises(RuntimeStoreError, match="another frozen identity"):
        recorded_owner_scope(a.agent._session_db, owner_binding=a.agent.runtime_context.identity.to_record(),
            session_id=artifacts.agents["a"].session_id, profile_home=a.home)


def setup_monitor(peer, *, digest_seconds=0):
    initial = source(peer, peer.project, "old")
    definition = monitor_definition(peer.project, initial)
    definition["specification"]["notify_policy"] = "local_runtime"
    row = create(peer, definition)
    policy = {"kind": "local_runtime", "timezone": "Etc/UTC", "quiet_hours": None, "digest_seconds": digest_seconds,
              "max_deliveries": 10, "expires_at": definition["expires_at"]}
    decoded(peer.call("runtime.monitor.policy.set", command_id="policy", project_id=peer.project, schedule_id="watch",
        expected_revision=row["revision"], policy_json=json.dumps(policy)))
    return update(peer, get(peer, row), "active"), initial


def test_notification_destinations_remain_profile_scoped_a_b_a(artifacts, monkeypatch):
    peers = {label: managed_copy(artifacts, label) for label in ("a", "b")}
    monitors = {label: setup_monitor(peer) for label, peer in peers.items()}
    captured = {label: [] for label in peers}
    changed = {}
    for label, peer in peers.items():
        artifacts.peers[label].write = lambda frame, label=label: captured[label].append(frame) or True
        row, first = monitors[label]
        assert tick(peer.home, row["next_due"], monkeypatch) == 1
        changed[label] = source(peer, peer.project, "changed " + label, prior=first, command="change")
    for label in ("a", "b", "a"):
        peer = peers[label]
        assert tick(peer.home, get(peer, monitors[label][0])["next_due"], monkeypatch) == 1
        events = notification_frames(captured[label])
        assert len(events) == 1
        refs = json.loads(events[0]["notification_json"])["items"][0]["source_refs"]
        assert refs[0]["artifact_id"] == changed[label]["artifact_id"]
        view = decoded(peer.call("runtime.monitor.notifications", project_id=peer.project, schedule_id="watch"))
        assert view["destination"]["agent_id"] == peer.copied["agent_id"]


def test_pending_notice_cannot_admit_after_narrow_then_regrant(artifacts, monkeypatch):
    from cron.monitor_notification_delivery import tick_monitor_notifications
    peer = managed_copy(artifacts)
    row, first = setup_monitor(peer, digest_seconds=120)
    captured = []
    artifacts.peers["a"].write = lambda frame: captured.append(frame) or True
    now = row["next_due"]
    assert tick(peer.home, now, monkeypatch) == 1
    source(peer, peer.project, "change", prior=first, command="change")
    assert tick(peer.home, now + 60, monkeypatch) == 1
    assert notification_frames(captured) == []
    initial = result(artifacts.call("runtime.agent.get", agent_id=peer.copied["agent_id"]))["agent"]
    narrowed = result(artifacts.call("runtime.agent.update", agent_id=peer.copied["agent_id"], expected_revision=initial["revision"],
        config={**initial["config"], "memory_allowed": False}))["agent"]
    result(artifacts.call("runtime.agent.update", agent_id=peer.copied["agent_id"], expected_revision=narrowed["revision"], config=initial["config"]))
    monkeypatch.setattr(time, "time", lambda: now + 240)
    with off_session(peer.home):
        tick_monitor_notifications(peer.agent._session_db, peer.home)
    assert notification_frames(captured) == []
    with peer.agent._session_db._runtime_read() as conn:
        assert conn.execute("SELECT state,delivery_id FROM durable_monitor_notices").fetchone()[:] == ("pending", None)
        assert conn.execute("SELECT COUNT(*) FROM durable_monitor_batches").fetchone()[0] == 0


def test_revocation_between_resolution_and_queue_commit_cannot_debit_schedule(artifacts, monkeypatch):
    from agent.admission import AdmissionQueue
    peer = managed_copy(artifacts)
    row = update(peer, create(peer, command_definition(peer)), "active")
    original = AdmissionQueue.submit_bound
    revoked = []
    def revoke_before_writer(queue, *args, **kwargs):
        result(artifacts.call("runtime.agent.update", agent_id=peer.copied["agent_id"], expected_revision=peer.copied["revision"],
            config={**peer.copied["config"], "memory_allowed": False}))
        revoked.append(True)
        return original(queue, *args, **kwargs)
    monkeypatch.setattr(AdmissionQueue, "submit_bound", revoke_before_writer)
    assert tick(peer.home, row["next_due"], monkeypatch) == 0
    assert revoked == [True]
    with peer.agent._session_db._runtime_read() as conn:
        saved = conn.execute("SELECT state,remaining_checks FROM durable_schedules WHERE schedule_id='copied-work'").fetchone()
        assert tuple(saved) == ("paused", 10)
        assert conn.execute("SELECT COUNT(*) FROM durable_command_occurrences").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM runtime_admission_queue").fetchone()[0] == 0


def test_notice_policy_uses_its_own_frozen_session_after_catalog_expands(artifacts, monkeypatch):
    from tui_gateway import server
    peer = managed_copy(artifacts)
    first = source(peer, peer.project, "old")
    definition = monitor_definition(peer.project, first)
    definition["specification"]["notify_policy"] = "local_runtime"
    row = create(peer, definition)
    template = result(artifacts.call("runtime.agent.get", agent_id="researcher"))["agent"]
    result(artifacts.call("runtime.agent.create", copy_from_agent_id="researcher", config=template["config"]))
    notice_agent, _ = start_specialist(artifacts, "notice-session", name=peer.copied["agent_id"])
    assert notice_agent.runtime_context.config_digest != peer.agent.runtime_context.config_digest
    def call(method, _label="a", **params):
        return server.dispatch({"jsonrpc": "2.0", "id": "notice-policy", "method": method, "params": {
            "schema_version": 1, "session_id": "live-notice-session", **params}}, transport=artifacts.peers["a"])
    policy_peer = SimpleNamespace(call=call)
    policy = {"kind": "local_runtime", "timezone": "Etc/UTC", "quiet_hours": None, "digest_seconds": 0,
              "max_deliveries": 10, "expires_at": definition["expires_at"]}
    decoded(call("runtime.monitor.policy.set", command_id="new-policy", project_id=peer.project, schedule_id="watch",
        expected_revision=row["revision"], policy_json=json.dumps(policy)))
    row = update(policy_peer, get(policy_peer, row), "active")
    captured = []
    artifacts.peers["a"].write = lambda frame: captured.append(frame) or True
    now = row["next_due"]
    assert tick(peer.home, now, monkeypatch) == 1
    source(peer, peer.project, "changed", prior=first, command="change")
    assert tick(peer.home, now + 60, monkeypatch) == 1
    assert len(notification_frames(captured)) == 1
    view = decoded(call("runtime.monitor.notifications", project_id=peer.project, schedule_id="watch"))
    assert view["destination"]["session_id"] == "notice-session"
