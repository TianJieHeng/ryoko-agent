"""Opted-in stdio cron lifecycle on the existing admission maintenance handle.

There is no new timer or execution loop. The ordinary cron tick retains its
profile .tick.lock, retirement/ESTOP gates, immutable occurrences and durable
command queue. Status reports observations rather than interpreting config as
proof of a live scheduler. No paths, PIDs, credentials or exception text leave
this module through its public status projection.
"""
from __future__ import annotations

import threading
import time

_next_tick: dict[str, float] = {}
_states: dict[str, dict] = {}
_state_lock = threading.Lock()


def _state(key):
    return _states.setdefault(key, {"last_maintenance_at": None, "last_maintenance_mono": None,
        "last_tick_started_at": None, "last_tick_completed_at": None,
        "last_tick_succeeded_at": None, "last_tick_succeeded_mono": None,
        "tick_lock_held": False, "state": "awaiting_maintenance", "last_error_code": None})


def _configuration(home):
    from cron.scheduler_provider import _profile_cron_scope
    from hermes_cli.config import load_config_readonly
    with _profile_cron_scope(home):
        cron = load_config_readonly().get("cron") or {}
        setting = cron.get("stdio_scheduler") if isinstance(cron, dict) else None
        if not isinstance(setting, dict) or setting.get("enabled") is not True:
            return False, None, None
        interval = setting.get("interval_seconds", 60)
        if type(interval) is not int or not 1 <= interval <= 300:
            return True, None, "invalid_config"
        return True, interval, None


def scheduler_status(server, profile_home):
    """Read-only, bounded proof for the already-authenticated session profile.

    The caller must perform the ordinary live transport/profile authorization.
    A callback handle alone is insufficient: maintenance and a successful real
    locked tick must both have recent observations. State is never inferred from
    a heartbeat file left by a dead process.
    """
    from hermes_constants import get_process_hermes_home, hermes_home_key
    from cron.scheduler_ownership import live_gateway_ticking
    from tui_gateway import prompt_admission
    key = hermes_home_key(profile_home)
    enabled, interval, config_error = _configuration(profile_home)
    same_home = key == hermes_home_key(get_process_hermes_home())
    stdio = bool(server._stdio_is_rpc_channel and same_home)
    other_owner = live_gateway_ticking(profile_home) is not None
    with _state_lock:
        record = dict(_states.get(key) or {"last_maintenance_at": None, "last_maintenance_mono": None,
            "last_tick_started_at": None, "last_tick_completed_at": None,
            "last_tick_succeeded_at": None, "last_tick_succeeded_mono": None,
            "tick_lock_held": False, "state": "awaiting_maintenance", "last_error_code": None})
    handle = prompt_admission._HANDLE
    stopping = bool(prompt_admission._STOPPING)
    started = bool(prompt_admission._STARTED and handle is not None and not handle.cancelled)
    mono = time.monotonic()
    live = bool(stdio and started and not stopping and record["last_maintenance_mono"] is not None
                and 0 <= mono - record["last_maintenance_mono"] <= 5)
    recent_tick = bool(interval and record["last_tick_succeeded_mono"] is not None
                       and 0 <= mono - record["last_tick_succeeded_mono"] <= 2 * interval + 5)
    state = ("stopping" if stopping else "unsupported_surface" if not stdio else "disabled" if not enabled
        else "error" if config_error else "standby_other_owner" if other_owner
        else "awaiting_maintenance" if not live else record["state"])
    qualified = bool(enabled and live and recent_tick and not other_owner
        and state in {"healthy", "waiting", "ticking"} and not config_error)
    return {"schema_version": 1, "authority": "runtime_cron", "enabled": enabled,
        "surface": "stdio" if stdio else "other", "state": state,
        "maintenance_started": started, "maintenance_live": live, "recurring_admission_ready": qualified,
        "tick_lock_held": bool(stdio and record["tick_lock_held"]), "other_gateway_owner_live": other_owner,
        "poll_interval_seconds": interval, "last_maintenance_at": record["last_maintenance_at"],
        "last_tick_started_at": record["last_tick_started_at"], "last_tick_completed_at": record["last_tick_completed_at"],
        "last_tick_succeeded_at": record["last_tick_succeeded_at"],
        "last_error_code": config_error or record["last_error_code"],
        "execution_requires_live_owned_session": True, "scheduler_pause_cancels_running": False,
        "dispatch_performed": False}


def tick_if_owned(server):
    """Called while prompt_admission._LOCK excludes teardown/overlapping pumps."""
    if not server._stdio_is_rpc_channel:
        return 0
    from hermes_constants import get_process_hermes_home, hermes_home_key
    from cron.scheduler_provider import _profile_cron_scope
    from cron.scheduler_ownership import live_gateway_ticking
    home = get_process_hermes_home()
    key = hermes_home_key(home)
    with _state_lock:
        _state(key).update(last_maintenance_at=time.time(), last_maintenance_mono=time.monotonic())
    enabled, interval, error = _configuration(home)
    if not enabled or error:
        _next_tick.pop(key, None)
        with _state_lock:
            _state(key).update(state="error" if error else "disabled", last_error_code=error)
        return 0
    if live_gateway_ticking(home) is not None:
        _next_tick.pop(key, None)
        with _state_lock:
            _state(key).update(state="standby_other_owner", last_error_code=None)
        return 0
    now = time.monotonic()
    if now < _next_tick.get(key, 0):
        return 0
    # Re-anchor after suspend/restart; the durable missed_run policy alone
    # determines catch-up. Multiple processes still share the existing tick lock.
    _next_tick[key] = now + interval
    observed = {"acquired": False, "blocked": False}
    def observe(phase):
        with _state_lock:
            record = _state(key)
            if phase == "acquired":
                observed["acquired"] = True
            elif phase in {"paused", "draining", "retired"}:
                observed["blocked"] = True
            changes = {
                "acquired": {"tick_lock_held": True, "state": "ticking"},
                "released": {"tick_lock_held": False},
                "contended": {"state": "lock_busy", "tick_lock_held": False},
                "paused": {"state": "paused"},
                "draining": {"state": "draining"},
                "retired": {"state": "retired"},
            }
            record.update(changes[phase])
    with _profile_cron_scope(home):
        from cron.scheduler import tick
        from cron.jobs import record_ticker_error, record_ticker_heartbeat, clear_ticker_error
        with _state_lock:
            _state(key).update(last_tick_started_at=time.time(), last_error_code=None)
        try:
            result = tick(verbose=False, sync=False, _observer=observe)
        except BaseException as failure:
            with _state_lock:
                _state(key).update(state="error", last_error_code="tick_failed", tick_lock_held=False,
                                   last_tick_completed_at=time.time())
            record_ticker_error(f"{type(failure).__name__}: {failure}")
            record_ticker_heartbeat(success=False)
            raise
        successful = observed["acquired"] and not observed["blocked"]
        with _state_lock:
            record = _state(key)
            record["last_tick_completed_at"] = time.time()
            if successful:
                record.update(state="healthy", last_tick_succeeded_at=time.time(),
                              last_tick_succeeded_mono=time.monotonic())
        record_ticker_heartbeat(success=successful)
        if successful:
            clear_ticker_error()
        return result
