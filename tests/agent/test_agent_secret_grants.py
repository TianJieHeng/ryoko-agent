"""Real profile scopes intersect credentials with immutable per-agent grants."""

from pathlib import Path

import pytest

from agent import secret_scope as ss
from agent.agent_identity import resolve_agent_context
from agent.identity_lifecycle import agent_runtime_scope
from agent.runtime_context import bind_agent_context


def _context(home, agent_id):
    config = {"agent_identity": {
        "schema_version": 1, "principal_id": "owner", "profile_id": "profile",
        "primary_agent_id": "keeper", "active_agent_id": agent_id,
        "personal_secret_refs": ["PERSONAL_VAULT"],
        "agents": {
            "keeper": {"policy_version": 1, "role": "primary", "memory_backend": "personal_mcp",
                       "secret_refs": ["PERSONAL_VAULT", "SHARED_API_KEY", "MISSING_API_KEY"]},
            "worker": {"policy_version": 1, "role": "specialist", "memory_backend": "builtin",
                       "secret_refs": ["SHARED_API_KEY", "MISSING_API_KEY"]},
        },
    }}
    return resolve_agent_context(config, profile_home=home, session_id="scope_test")


def test_identity_alternation_and_profile_alternation_never_borrow_ambient(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    homes = [tmp_path / "a", tmp_path / "b"]
    for home in homes:
        home.mkdir()
        (home / ".env").write_text(
            f"PERSONAL_VAULT=personal-{home.name}\nSHARED_API_KEY=shared-{home.name}\n"
            "UNLISTED_BLOB=ungranted\nTERMINAL_SSH_PASSWORD=ungranted-terminal\n",
            encoding="utf-8",
        )
    monkeypatch.setenv("HERMES_HOME", str(homes[0]))
    monkeypatch.setenv("MISSING_API_KEY", "ambient-canary")
    monkeypatch.setenv("PERSONAL_VAULT", "ambient-personal-canary")
    ss.set_multiplex_active(False)
    for home in (homes[0], homes[1], homes[0]):
        primary, specialist = (_context(home, identity) for identity in ("keeper", "worker"))
        with agent_runtime_scope(primary):
            assert ss.get_secret("PERSONAL_VAULT") == f"personal-{home.name}"
            with agent_runtime_scope(specialist):
                assert ss.get_secret("PERSONAL_VAULT") is None
                assert ss.get_secret("UNLISTED_BLOB") is None
                assert ss.get_secret("TERMINAL_SSH_PASSWORD") is None
                assert ss.get_secret("MISSING_API_KEY") is None
                assert ss.get_secret("MISSING_API_KEY", "ambient-canary") is None
                assert ss.get_secret("PERSONAL_VAULT", "ambient-canary") is None
                assert ss.get_secret_str("MISSING_API_KEY", "ambient-canary") == ""
                assert ss.get_secret_str("PERSONAL_VAULT", "ambient-canary") == ""
                assert dict(ss.current_secret_scope()) == {"SHARED_API_KEY": f"shared-{home.name}"}
            assert ss.get_secret("PERSONAL_VAULT") == f"personal-{home.name}"
            assert ss.get_secret("MISSING_API_KEY") is None
    # Opt-in identity enforcement must leave legacy single-profile fallback intact.
    token = ss.set_secret_scope({}, profile_home=str(homes[0]))
    try:
        assert ss.get_secret("MISSING_API_KEY") == "ambient-canary"
    finally:
        ss.reset_secret_scope(token)


def test_identity_rejects_missing_unstamped_foreign_and_refreshed_foreign_scopes(tmp_path, monkeypatch):
    home = tmp_path / "profile"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(home))
    context = _context(home, "worker")
    with bind_agent_context(context):
        for mapping, stamped_home in ((None, None), ({"SHARED_API_KEY": "canary"}, None),
                                      ({"SHARED_API_KEY": "canary"}, str(tmp_path / "foreign"))):
            token = ss.set_secret_scope(mapping, profile_home=stamped_home)
            try:
                with pytest.raises(ss.AgentSecretScopeError):
                    ss.get_secret("SHARED_API_KEY")
                with pytest.raises(ss.AgentSecretScopeError):
                    ss.current_secret_scope()
            finally:
                ss.reset_secret_scope(token)
        token = ss.set_secret_scope({"SHARED_API_KEY": "own"}, profile_home=str(home))
        try:
            with pytest.raises(ss.AgentSecretScopeError):
                ss.refresh_installed_secret_scope(tmp_path / "foreign")
            assert ss.get_secret("SHARED_API_KEY") == "own"
        finally:
            ss.reset_secret_scope(token)
