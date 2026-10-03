"""Strict owner authorization precedes every legacy pre-agent activity."""
from types import SimpleNamespace

import pytest

from agent.agent_identity import resolve_agent_context, resolve_owned_agent_context, IdentityPolicyError
from agent.identity_lifecycle import agent_runtime_scope
from agent.runtime_context import current_agent_context
from agent.secret_scope import get_secret
from tests.agent.test_identity_lifecycle import config, write_config


def test_owner_binding_ignores_active_default_and_restores_profile_scope(tmp_path, monkeypatch):
    a, b = tmp_path / "a", tmp_path / "b"
    write_config(a, config("primary")); write_config(b, config("primary"))
    monkeypatch.setenv("HERMES_HOME", str(a))
    original = resolve_agent_context(config("assistant"), session_id="creator", profile_home=a)
    owner_a = resolve_owned_agent_context(config("primary"), owner_binding=original.identity.to_record(),
                                         session_id="job_a", profile_home=a)
    original_b = resolve_agent_context(config("assistant"), session_id="creator_b", profile_home=b)
    owner_b = resolve_owned_agent_context(config("primary"), owner_binding=original_b.identity.to_record(),
                                         session_id="job_b", profile_home=b)
    for context in (owner_a, owner_b, owner_a):
        with agent_runtime_scope(context):
            assert current_agent_context().identity.agent_id == "assistant"
            assert get_secret("PRIVATE_TOKEN") is None
            assert get_secret("PROVIDER_TOKEN") == "provider-fixture"
    changed = config("primary")
    changed["agent_identity"]["agents"]["assistant"]["allowed_tools"] = []
    with pytest.raises(IdentityPolicyError):
        resolve_owned_agent_context(changed, owner_binding=original.identity.to_record(), session_id="no", profile_home=a)
    with pytest.raises(IdentityPolicyError):
        resolve_owned_agent_context(config(), owner_binding=original.identity.to_record(), session_id="no", profile_home=b)


@pytest.mark.parametrize("fields", [{"script": "unsafe.py", "no_agent": True}, {"script": "unsafe.py"},
                                    {"monitor_url": "https://example.invalid"}, {"monitor_script": "unsafe.py"}])
def test_strict_cron_never_prepares_sources_or_constructs_provider_before_denial(tmp_path, monkeypatch, fields):
    from cron import scheduler
    write_config(tmp_path, config()); monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    calls = []
    monkeypatch.setattr(scheduler, "_prepare_job_prompt", lambda *args: calls.append("unsafe"))
    owner = resolve_agent_context(config(), session_id="creator", profile_home=tmp_path)
    for binding in (None, owner.identity.to_record()):
        job = {"id": "job", "prompt": "test", **fields}
        if binding:
            job["owner_binding"] = binding
        result = scheduler.run_job(job)
        assert result[0] is False and result[3].startswith("IdentityPolicyError")
    assert calls == []
