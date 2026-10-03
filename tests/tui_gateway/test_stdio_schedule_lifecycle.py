"""Real stdio entry maintenance -> cron lock -> durable schedule queue; no provider."""
import json
import threading
import time
from types import SimpleNamespace

import pytest

from tests.tui_gateway.test_artifact_rpc import artifacts, result  # noqa: F401
from tests.tui_gateway.test_command_schedules_rpc import definition
from tests.tui_gateway.test_schedules_rpc import create, update, get

pytestmark = pytest.mark.platforms("linux")


def configure(artifacts, monkeypatch, enabled=True):
    from cron import scheduler, scheduler_ownership
    from tui_gateway import runtime_schedule_tick
    home = artifacts.homes["a"]
    path = home / "config.yaml"
    config = json.loads(path.read_text())
    config["cron"] = {"stdio_scheduler": {"enabled": enabled, "interval_seconds": 1}}
    path.write_text(json.dumps(config))
    monkeypatch.setattr("hermes_constants.get_process_hermes_home", lambda: home)
    monkeypatch.setattr(scheduler_ownership, "live_gateway_ticking", lambda _home: None)
    monkeypatch.setattr(scheduler, "_should_yield_tick_to_fresh_gateway", lambda: None)
    monkeypatch.setattr(scheduler, "_maybe_reap_dead_owners", lambda: None)
    monkeypatch.setattr(scheduler, "_maybe_run_worktree_maintenance", lambda: None)
    monkeypatch.setattr(scheduler, "_sweep_mcp_orphans", lambda: None)
    monkeypatch.setattr("cron.bot_chat_delivery.drain_in_background", lambda: None)
    runtime_schedule_tick._next_tick.clear()
    runtime_schedule_tick._states.clear()
    return home


@pytest.mark.parametrize("setting", [None, {}, {"enabled": False}, {"enabled": "true"}, {"enabled": 1}])
def test_disabled_stdio_and_nonstdio_never_tick(artifacts, monkeypatch, setting):
    from tui_gateway import runtime_schedule_tick
    config = definition(artifacts)
    active = update(artifacts, create(artifacts, config), "active")
    home = configure(artifacts, monkeypatch, enabled=False)
    path = home / "config.yaml"
    configured = json.loads(path.read_text())
    configured["cron"] = {} if setting is None else {"stdio_scheduler": setting}
    path.write_text(json.dumps(configured))
    monkeypatch.setattr(time, "time", lambda: active["next_due"])
    assert runtime_schedule_tick.tick_if_owned(SimpleNamespace(_stdio_is_rpc_channel=True)) == 0
    configure(artifacts, monkeypatch, enabled=True)
    assert runtime_schedule_tick.tick_if_owned(SimpleNamespace(_stdio_is_rpc_channel=False)) == 0
    assert get(artifacts, active)["occurrences"] == []


def test_actual_stdio_entry_starts_recurring_admission_without_provider(artifacts, monkeypatch):
    from tui_gateway import entry, prompt_admission, server
    from agent.periodic_scheduler import PeriodicScheduler
    config = definition(artifacts, max_fires=2)
    active = update(artifacts, create(artifacts, config), "active")
    configure(artifacts, monkeypatch)
    monkeypatch.setattr(time, "time", lambda: active["next_due"])
    db = artifacts.agents["a"]._session_db
    assert not hasattr(artifacts.agents["a"], "client")
    server._sessions["live-a"]["transport"] = server._detached_ws_transport
    monkeypatch.setattr(server, "_run_prompt_submit", lambda *a, **kw: pytest.fail("Detached fixture must not launch inference"))
    monkeypatch.setattr(server, "_get_db", lambda: db)
    monkeypatch.setattr(prompt_admission, "_STARTED", False)
    monkeypatch.setattr(prompt_admission, "_STOPPING", False)
    monkeypatch.setattr(prompt_admission, "_HANDLE", None)
    # Dedicated real periodic scheduler prevents test interference. The production
    # callback, lifecycle start, cron ticker, lock, DB and RPC path are unchanged.
    scheduler = PeriodicScheduler()
    monkeypatch.setattr("agent.periodic_scheduler.schedule", scheduler.schedule)
    for name in ("install_tui_message_injector", "_start_backend_heartbeat_refresher", "_schedule_startup_orphan_sweep", "_ensure_skin_watcher"):
        monkeypatch.setattr(server, name, lambda: None)
    for name in ("_close_rpc_stdin_on_exec", "_install_sidecar_publisher", "ensure_mcp_discovery_started"):
        monkeypatch.setattr(entry, name, lambda: None)
    monkeypatch.setattr("agent.ssl_verify.install_truststore", lambda: None)
    monkeypatch.setattr("hermes_cli.observability.shared_metrics_process.begin_process", lambda *_: None)
    monkeypatch.setattr("hermes_cli.observability.shared_metrics_disabled.set_process_surface", lambda *_: None)
    monkeypatch.setattr("hermes_cli.model_switch_providers.prewarm_picker_cache_async", lambda: None)
    monkeypatch.setattr(entry, "handle_spurious_eof", lambda *_: False)
    emitted = []
    monkeypatch.setattr(entry, "_write_or_exit", lambda message, *_: emitted.append(message))
    class Input:
        def readline(self):
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                with db._runtime_read() as conn:
                    if conn.execute("SELECT COUNT(*) FROM runtime_admission_queue").fetchone()[0] == 1:
                        return ""
                time.sleep(0.02)
            pytest.fail("Stdio maintenance never admitted the recurring occurrence")
    monkeypatch.setattr(entry.sys, "stdin", Input())
    try:
        entry.main()
        # Status passes through the actual owned RPC while the maintenance
        # handle is live. Prevent the detached fixture from executing inference.
        with prompt_admission._LOCK:
            server._sessions["live-a"]["transport"] = artifacts.peers["a"]
            proof = result(artifacts.call("runtime.schedule.scheduler.status"))
            assert proof["recurring_admission_ready"] and proof["maintenance_live"]
            assert proof["last_tick_succeeded_at"] is not None
            assert proof["state"] == "healthy" and not proof["tick_lock_held"]
            server._sessions["live-a"]["transport"] = server._detached_ws_transport
        prompt_admission._HANDLE.cancel(wait=5)
        server._sessions["live-a"]["transport"] = artifacts.peers["a"]
        assert emitted[0]["params"]["type"] == "gateway.ready"
        view = get(artifacts, active)
        assert len(view["occurrences"]) == 1 and view["remaining_checks"] == 1
        first = view["occurrences"][0]
        assert first["state"] == "accepted"
        with db._runtime_read() as conn:
            job = conn.execute("SELECT * FROM runtime_admission_queue").fetchone()
            assert job["command_id"] == first["command_id"] and job["workload"] == "background"
        # A second maintenance pass at this clock instant cannot duplicate it.
        server._sessions["live-a"]["transport"] = server._detached_ws_transport
        prompt_admission._maintenance(server)
        server._sessions["live-a"]["transport"] = artifacts.peers["a"]
        assert len(get(artifacts, active)["occurrences"]) == 1
        # A subsequent real trigger uses the same owner and a different durable
        # occurrence. Only test clocks advance; the consumer derives no due time.
        from tui_gateway import runtime_schedule_tick
        runtime_schedule_tick._next_tick.clear()
        monkeypatch.setattr(time, "time", lambda: active["next_due"] + 60)
        server._sessions["live-a"]["transport"] = server._detached_ws_transport
        prompt_admission._maintenance(server)
        server._sessions["live-a"]["transport"] = artifacts.peers["a"]
        final = get(artifacts, active)
        assert len(final["occurrences"]) == 2 and final["remaining_checks"] == 0
        assert len({row["command_id"] for row in final["occurrences"]}) == 2
    finally:
        if prompt_admission._HANDLE:
            prompt_admission._HANDLE.cancel(wait=5)


def test_existing_tick_lock_and_other_live_owner_prevent_admission(artifacts, monkeypatch):
    from cron import scheduler, scheduler_ownership
    from cron.scheduler_provider import _profile_cron_scope
    from tui_gateway import runtime_schedule_tick
    active = update(artifacts, create(artifacts, definition(artifacts)), "active")
    home = configure(artifacts, monkeypatch)
    monkeypatch.setattr(time, "time", lambda: active["next_due"])
    fake_server = SimpleNamespace(_stdio_is_rpc_channel=True)
    with _profile_cron_scope(home):
        directory, path = scheduler._get_lock_paths()
        scheduler._ensure_cron_dir(directory)
        lock = scheduler._acquire_tick_lock(path)
        assert lock is not None
        try:
            assert runtime_schedule_tick.tick_if_owned(fake_server) == 0
        finally:
            scheduler._release_tick_lock(lock)
    assert get(artifacts, active)["occurrences"] == []
    runtime_schedule_tick._next_tick.clear()
    monkeypatch.setattr(scheduler_ownership, "live_gateway_ticking", lambda _home: {"pid": 999})
    assert runtime_schedule_tick.tick_if_owned(fake_server) == 0
    assert get(artifacts, active)["occurrences"] == []


def test_shutdown_fences_tick_before_queue_teardown(artifacts, monkeypatch):
    from cron import durable_runtime
    from tui_gateway import prompt_admission, server
    active = update(artifacts, create(artifacts, definition(artifacts)), "active")
    configure(artifacts, monkeypatch)
    monkeypatch.setattr(time, "time", lambda: active["next_due"])
    monkeypatch.setattr(prompt_admission, "_STOPPING", False)
    monkeypatch.setattr(prompt_admission, "_HANDLE", None)
    monkeypatch.setattr(server, "_stdio_is_rpc_channel", True)
    db = artifacts.agents["a"]._session_db
    monkeypatch.setattr(server, "_get_db", lambda: db)
    server._sessions["live-a"]["transport"] = server._detached_ws_transport
    entered, attempted = threading.Event(), threading.Event()
    events = []
    real_tick = durable_runtime.tick_durable_schedules

    def shutdown():
        assert entered.wait(5)
        # The handoff holds the exact existing admission lock while cron owns
        # the shared file lock. Shutdown must wait, then cancel accepted work.
        acquired = prompt_admission._LOCK.acquire(blocking=False)
        if acquired:
            prompt_admission._LOCK.release()
        events.append(("shutdown_attempt", acquired))
        attempted.set()
        prompt_admission.shutdown(server)
        events.append(("shutdown_complete", True))

    def checked_tick():
        entered.set()
        assert attempted.wait(5)
        assert not prompt_admission._STOPPING
        admitted = real_tick()
        events.append(("admitted", admitted))
        return admitted

    monkeypatch.setattr(durable_runtime, "tick_durable_schedules", checked_tick)
    worker = threading.Thread(target=shutdown, daemon=True)
    worker.start()
    try:
        prompt_admission._maintenance(server)
    finally:
        worker.join(5)
    assert not worker.is_alive()
    assert events == [("shutdown_attempt", False), ("admitted", 1), ("shutdown_complete", True)]
    assert prompt_admission._STOPPING
    with db._runtime_read() as conn:
        queued = conn.execute("SELECT * FROM runtime_admission_queue").fetchall()
        assert len(queued) == 1 and queued[0]["state"] == "cancelled"
    # A later due trigger cannot admit after shutdown has established its fence.
    monkeypatch.setattr(time, "time", lambda: active["next_due"] + 60)
    prompt_admission._maintenance(server)
    assert events[-1] == ("shutdown_complete", True)
    with db._runtime_read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM durable_command_occurrences").fetchone()[0] == 1


def test_status_requires_real_observed_maintenance_and_owned_transport(artifacts, monkeypatch):
    from agent.secret_scope import is_multiplex_active, set_multiplex_active
    from tui_gateway import prompt_admission, server
    configure(artifacts, monkeypatch)
    monkeypatch.setattr(server, "_stdio_is_rpc_channel", True)
    monkeypatch.setattr(prompt_admission, "_STARTED", False)
    monkeypatch.setattr(prompt_admission, "_HANDLE", None)
    monkeypatch.setattr(prompt_admission, "_STOPPING", False)
    before = result(artifacts.call("runtime.schedule.scheduler.status"))
    assert before["enabled"] and not before["recurring_admission_ready"]
    assert before["state"] == "awaiting_maintenance"
    assert before["last_tick_succeeded_at"] is None
    assert not before["dispatch_performed"]
    denied = artifacts.call("runtime.schedule.scheduler.status", via=artifacts.peers["b"])
    assert "error" in denied and denied["error"]["code"] == 4001
    encoded = json.dumps(before)
    assert str(artifacts.homes["a"]) not in encoded and "pid" not in encoded
    second_path = artifacts.homes["b"] / "config.yaml"
    second_config = json.loads(second_path.read_text())
    second_config["cron"] = {"stdio_scheduler": {"enabled": True, "interval_seconds": 17}}
    second_path.write_text(json.dumps(second_config))
    previous = is_multiplex_active()
    set_multiplex_active(True)
    try:
        for label, interval, surface in (("a", 1, "stdio"), ("b", 17, "other"), ("a", 1, "stdio")):
            proof = result(artifacts.call("runtime.schedule.scheduler.status", label))
            assert proof["enabled"] and proof["poll_interval_seconds"] == interval
            assert proof["surface"] == surface and not proof["recurring_admission_ready"]
        assert result(artifacts.call("runtime.schedule.scheduler.status", "b"))["state"] == "unsupported_surface"
    finally:
        set_multiplex_active(previous)


def test_status_observes_held_lock_read_only_and_expires_without_maintenance(artifacts, monkeypatch):
    from cron import durable_runtime
    from tui_gateway import prompt_admission, runtime_schedule_tick, server
    active = update(artifacts, create(artifacts, definition(artifacts)), "active")
    configure(artifacts, monkeypatch)
    monkeypatch.setattr(server, "_stdio_is_rpc_channel", True)
    monkeypatch.setattr(prompt_admission, "_STARTED", True)
    monkeypatch.setattr(prompt_admission, "_STOPPING", False)
    monkeypatch.setattr(prompt_admission, "_HANDLE", SimpleNamespace(cancelled=False))
    monkeypatch.setattr(time, "time", lambda: active["next_due"])
    real_tick = durable_runtime.tick_durable_schedules
    observed = []
    def checked_tick():
        proof = result(artifacts.call("runtime.schedule.scheduler.status"))
        observed.append(proof)
        assert proof["tick_lock_held"] and proof["state"] == "ticking"
        assert not proof["recurring_admission_ready"]  # no prior successful tick
        return real_tick()
    monkeypatch.setattr(durable_runtime, "tick_durable_schedules", checked_tick)
    assert runtime_schedule_tick.tick_if_owned(server) == 1
    assert len(observed) == 1
    for _ in range(3):
        proof = result(artifacts.call("runtime.schedule.scheduler.status"))
        assert proof["recurring_admission_ready"] and not proof["tick_lock_held"]
    assert len(get(artifacts, active)["occurrences"]) == 1  # status never dispatches
    mono = time.monotonic()
    monkeypatch.setattr(time, "monotonic", lambda: mono + 10)
    stale = result(artifacts.call("runtime.schedule.scheduler.status"))
    assert not stale["maintenance_live"] and not stale["recurring_admission_ready"]
    assert stale["state"] == "awaiting_maintenance"
    monkeypatch.setattr(prompt_admission, "_STOPPING", True)
    stopped = result(artifacts.call("runtime.schedule.scheduler.status"))
    assert stopped["state"] == "stopping" and not stopped["recurring_admission_ready"]


def test_status_retains_contended_other_owner_and_error_evidence(artifacts, monkeypatch):
    from cron import scheduler, scheduler_ownership
    from cron.scheduler_provider import _profile_cron_scope
    from tui_gateway import prompt_admission, runtime_schedule_tick, server
    active = update(artifacts, create(artifacts, definition(artifacts)), "active")
    home = configure(artifacts, monkeypatch)
    monkeypatch.setattr(server, "_stdio_is_rpc_channel", True)
    monkeypatch.setattr(prompt_admission, "_STARTED", True)
    monkeypatch.setattr(prompt_admission, "_STOPPING", False)
    monkeypatch.setattr(prompt_admission, "_HANDLE", SimpleNamespace(cancelled=False))
    monkeypatch.setattr(time, "time", lambda: active["next_due"])
    with _profile_cron_scope(home):
        directory, path = scheduler._get_lock_paths()
        scheduler._ensure_cron_dir(directory)
        lock = scheduler._acquire_tick_lock(path)
        try:
            assert runtime_schedule_tick.tick_if_owned(server) == 0
        finally:
            scheduler._release_tick_lock(lock)
    proof = result(artifacts.call("runtime.schedule.scheduler.status"))
    assert proof["state"] == "lock_busy" and not proof["recurring_admission_ready"]
    monkeypatch.setattr(scheduler_ownership, "live_gateway_ticking", lambda _home: {"pid": 99})
    assert runtime_schedule_tick.tick_if_owned(server) == 0
    proof = result(artifacts.call("runtime.schedule.scheduler.status"))
    assert proof["state"] == "standby_other_owner" and proof["other_gateway_owner_live"]
    assert not proof["recurring_admission_ready"]
    monkeypatch.setattr(scheduler_ownership, "live_gateway_ticking", lambda _home: None)
    def failure(**_kwargs):
        raise OSError("private/path/secret-token")
    monkeypatch.setattr(scheduler, "tick", failure)
    with pytest.raises(OSError):
        runtime_schedule_tick.tick_if_owned(server)
    proof = result(artifacts.call("runtime.schedule.scheduler.status"))
    assert proof["state"] == "error" and proof["last_error_code"] == "tick_failed"
    assert "secret" not in json.dumps(proof)
