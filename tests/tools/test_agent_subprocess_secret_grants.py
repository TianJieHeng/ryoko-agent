"""Child-observed grants survive raw env overrides and managed restoration."""

from pathlib import Path

import pytest

from agent.agent_identity import resolve_agent_context
from agent.identity_lifecycle import agent_runtime_scope
from agent.secret_scope import AgentSecretScopeError, set_multiplex_active
from tests.tools._child_env_fixtures import child_env, observe_child  # noqa: F401
from tools.environments import local


def _context(home, agent_id="worker"):
    config = {"agent_identity": {
        "schema_version": 1, "principal_id": "owner", "profile_id": "profile",
        "primary_agent_id": "keeper", "active_agent_id": agent_id,
        "personal_secret_refs": ["PERSONAL_VAULT", "ABSENT_PERSONAL_DATA"],
        "agents": {
            "keeper": {"policy_version": 1, "role": "primary", "memory_backend": "personal_mcp",
                       "secret_refs": ["PERSONAL_VAULT"]},
            "worker": {"policy_version": 1, "role": "specialist", "memory_backend": "builtin",
                       "secret_refs": ["SHARED_API_KEY", "MISSING_API_KEY"]},
        },
    }}
    return resolve_agent_context(config, profile_home=home, session_id="child_env_test")


@pytest.mark.parametrize("multiplex", [False, True])
def test_real_child_env_intersects_every_spawn_and_restore_path(child_env, monkeypatch, multiplex):
    from hermes_cli import env_loader
    from tools.env_passthrough import register_env_passthrough, resolve_passthrough_value

    home = child_env / "hermes"
    home.mkdir()
    (home / ".env").write_text("SHARED_API_KEY=scoped-shared\nPERSONAL_VAULT=scoped-personal\n"
                               "UNLISTED_BLOB=scoped-unlisted\n", encoding="utf-8")
    managed = child_env / "managed"
    managed.mkdir()
    (managed / ".env").write_text("PERSONAL_VAULT=managed-personal\nREMOVED_PRIVATE_DATA=old-canary\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_MANAGED_DIR", str(managed))
    # Exercise the actual managed loader and its provenance registry.
    monkeypatch.setattr(env_loader, "_MANAGED_DOTENV_KEYS", set())
    monkeypatch.setenv("PERSONAL_VAULT", "initial-canary")
    monkeypatch.setenv("REMOVED_PRIVATE_DATA", "initial-canary")
    env_loader._apply_managed_env()
    (managed / ".env").write_text("PERSONAL_VAULT=managed-personal\n", encoding="utf-8")
    denied = {"PERSONAL_VAULT", "ABSENT_PERSONAL_DATA", "UNLISTED_BLOB", "AWS_ACCESS_KEY_ID", "GATEWAY_RELAY_ID",
              "TERMINAL_SSH_PASSWORD", "MISSING_API_KEY", "UNREGISTERED_API_TOKEN", "REMOVED_PRIVATE_DATA"}
    for name in denied:
        monkeypatch.setenv(name, "ambient-canary")
    monkeypatch.setenv("SHARED_API_KEY", "ambient-shared-canary")
    register_env_passthrough(["SHARED_API_KEY", "MISSING_API_KEY", "PERSONAL_VAULT"])
    base = {name: "base-canary" for name in denied | {"SHARED_API_KEY"}}
    base.update(SAFE_SETTING="base-setting", PATH="/usr/bin:/bin")
    extra = {name: "extra-canary" for name in denied | {"SHARED_API_KEY"}}
    extra.update(SAFE_SETTING="explicit-setting", _HERMES_FORCE_PERSONAL_VAULT="forced-canary")
    extra["_hermes_force_personal_vault"] = "lowercase-forced-canary"
    set_multiplex_active(multiplex)
    try:
        with agent_runtime_scope(_context(home)):
            assert resolve_passthrough_value("MISSING_API_KEY", "ambient-canary") is None
            assert resolve_passthrough_value("PERSONAL_VAULT", "ambient-canary") is None
            assert resolve_passthrough_value("TERMINAL_SSH_PASSWORD", "ambient-canary") is None
            assert resolve_passthrough_value("PATH", "explicit-safe-path") == "explicit-safe-path"
            candidates = [
                local.build_subprocess_env(base, scrub_secrets=scrub, extra=extra,
                                           strip_launch_profile=True)
                for scrub in (False, True)
            ]
            candidates.extend([
                local.served_profile_child_env(base, target_home=home, inherit_credentials=True),
                local.served_profile_child_env(base, inherit_credentials=False),
                local.hermes_subprocess_env(base_env=base, inherit_credentials=True),
                local._make_run_env(extra),
                local.restore_managed_env(dict(base)),
            ])
            for env in candidates:
                observed = observe_child(env, sorted(denied | {"SHARED_API_KEY"}))
                assert observed == {**dict.fromkeys(denied), "SHARED_API_KEY": "scoped-shared"}
                assert not any(name.upper().startswith("_HERMES_FORCE_") for name in env)
            assert all(env["SAFE_SETTING"] == "explicit-setting" for env in candidates[:2])
        with agent_runtime_scope(_context(home, "keeper")):
            for nested_identity in ("keeper", "worker", "keeper"):
                with agent_runtime_scope(_context(home, nested_identity)):
                    env = local.served_profile_child_env(base, inherit_credentials=True)
                    expected = "managed-personal" if nested_identity == "keeper" else None
                    assert observe_child(env, ["PERSONAL_VAULT"]) == {"PERSONAL_VAULT": expected}
    finally:
        set_multiplex_active(False)


def test_served_child_cannot_rehome_identity_or_supply_a_new_credential(child_env):
    home = child_env / "hermes"
    home.mkdir()
    foreign = child_env / "foreign"
    foreign.mkdir()
    (foreign / ".env").write_text("SHARED_API_KEY=foreign-canary\n", encoding="utf-8")
    with agent_runtime_scope(_context(home)):
        with pytest.raises(AgentSecretScopeError):
            local.served_profile_child_env({}, target_home=foreign, inherit_credentials=True)
        with pytest.raises(AgentSecretScopeError):
            local.build_subprocess_env({}, scrub_secrets=False, extra={"HERMES_HOME": str(foreign)})
        env = local.build_subprocess_env({}, scrub_secrets=False,
                                         extra={"SHARED_API_KEY": "supplied-but-missing", "SAFE_SETTING": "ok"})
        assert observe_child(env, ["SHARED_API_KEY", "SAFE_SETTING"]) == {"SHARED_API_KEY": None, "SAFE_SETTING": "ok"}
