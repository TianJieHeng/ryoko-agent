"""Real two-home identity scopes and copy_context transport without ambient leaks."""

from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from dataclasses import FrozenInstanceError, replace
import json

import pytest

from agent.agent_identity import IdentityPolicyError, resolve_agent_context
from agent.runtime_context import (
    bind_agent_context,
    current_agent_context,
    reset_agent_context,
    set_agent_context,
)
from hermes_constants import reset_hermes_home_override, set_hermes_home_override


def make_context(home, *, profile="profile_a", session="session-a"):
    home.mkdir(exist_ok=True)
    raw = {
        "agent_identity": {
            "schema_version": 1,
            "principal_id": "owner",
            "profile_id": profile,
            "primary_agent_id": "ryoko",
            "active_agent_id": "ryoko",
            "agents": {
                "ryoko": {
                    "policy_version": 1,
                    "role": "primary",
                    "memory_backend": "personal_mcp",
                }
            },
        }
    }
    (home / "config.json").write_text(json.dumps(raw), encoding="utf-8")
    return resolve_agent_context(
        json.loads((home / "config.json").read_text()),
        session_id=session,
        profile_home=home,
    )


def test_two_on_disk_homes_a_b_a_and_unbound_legacy(tmp_path, monkeypatch):
    a = make_context(tmp_path / "a")
    b = make_context(tmp_path / "b", profile="profile_b", session="session-b")
    monkeypatch.setenv("HERMES_HOME", a.profile_home)
    assert current_agent_context() is None
    with bind_agent_context(a):
        assert current_agent_context() is a
        with pytest.raises(FrozenInstanceError):
            a.profile_home = b.profile_home
        home_token = set_hermes_home_override(b.profile_home)
        try:
            with pytest.raises(IdentityPolicyError, match="home"):
                current_agent_context()
            with bind_agent_context(b):
                assert current_agent_context() is b
            with pytest.raises(IdentityPolicyError, match="home"):
                current_agent_context()
        finally:
            reset_hermes_home_override(home_token)
        assert current_agent_context() is a
    assert current_agent_context() is None


def test_bind_refuses_wrong_home_without_destroying_previous_scope(
    tmp_path, monkeypatch
):
    a = make_context(tmp_path / "a")
    b = make_context(tmp_path / "b", profile="profile_b")
    monkeypatch.setenv("HERMES_HOME", a.profile_home)
    token = set_agent_context(a)
    try:
        with pytest.raises(IdentityPolicyError):
            set_agent_context(b)
        assert current_agent_context() is a
        with pytest.raises(RuntimeError):
            with bind_agent_context(None):
                assert current_agent_context() is None
                raise RuntimeError("a hook failed")
        assert current_agent_context() is a
    finally:
        reset_agent_context(token)
    assert current_agent_context() is None


def test_copy_context_transports_identity_with_the_existing_home_scope(
    tmp_path, monkeypatch
):
    a = make_context(tmp_path / "a")
    b = make_context(tmp_path / "b", profile="profile_b")
    monkeypatch.setenv("HERMES_HOME", b.profile_home)
    token = set_hermes_home_override(a.profile_home)
    try:
        with bind_agent_context(a):
            copied = copy_context()
    finally:
        reset_hermes_home_override(token)
    with ThreadPoolExecutor(max_workers=1) as pool:
        assert pool.submit(current_agent_context).result() is None
        assert pool.submit(copied.run, current_agent_context).result() is a
        assert pool.submit(current_agent_context).result() is None
    assert current_agent_context() is None


def test_context_rejects_replaced_policy_or_home(tmp_path):
    a = make_context(tmp_path / "a")
    with pytest.raises(IdentityPolicyError):
        replace(a, policy=replace(a.policy, allowed_tools=frozenset({"terminal"})))
    with pytest.raises(IdentityPolicyError):
        replace(a, profile_home=tmp_path / "another")
    with pytest.raises(IdentityPolicyError):
        replace(a, config_digest="0" * 64)
