"""Real composition/scoping paths bind authority before constructing clients."""
import json
from types import SimpleNamespace

import pytest

from agent.agent_identity import IdentityPolicyError, resolve_agent_context
from agent.identity_lifecycle import agent_runtime_scope, identity_construction, bound_agent_lifecycle
from agent.runtime_context import current_agent_context
from agent.secret_scope import get_secret
from hermes_constants import get_hermes_home
from hermes_state import SessionDB


def config(active="assistant"):
    return {"agent_identity": {
        "schema_version": 1, "principal_id": "fixture_owner", "profile_id": "fixture_profile",
        "primary_agent_id": "primary", "active_agent_id": active,
        "personal_mcp_servers": ["private_memory"], "personal_secret_refs": ["PRIVATE_TOKEN"],
        "agents": {
            "primary": {"policy_version": 1, "role": "primary", "memory_backend": "personal_mcp",
                        "secret_refs": ["PRIVATE_TOKEN", "PROVIDER_TOKEN"], "allowed_tools": ["todo"]},
            "assistant": {"policy_version": 1, "role": "specialist", "memory_backend": "builtin",
                          "secret_refs": ["PROVIDER_TOKEN"], "allowed_tools": ["todo"]},
        },
        "child_policy": {"policy_version": 1, "role": "child", "memory_backend": "builtin",
                         "secret_refs": ["PROVIDER_TOKEN"], "allowed_tools": ["todo"]},
    }}


def write_config(home, data):
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text(json.dumps(data))
    (home / ".env").write_text("PROVIDER_TOKEN=provider-fixture\nPRIVATE_TOKEN=private-fixture\n")


@identity_construction
def construct(agent, session_id=None, session_db=None, parent_session_id=None,
              side_agent=False, skip_memory=False, skip_background_review=False, platform=None):
    agent.session_id = session_id
    agent._session_init_model_config = {}
    agent.seen_context = current_agent_context()
    agent.seen_secret = get_secret("PRIVATE_TOKEN")
    agent.memory_skipped = skip_memory
    agent.review_skipped = skip_background_review


def test_scope_switch_restores_home_and_secret_authority(tmp_path, monkeypatch):
    a, b = tmp_path / "a", tmp_path / "b"
    write_config(a, config("primary")); write_config(b, config())
    monkeypatch.setenv("HERMES_HOME", str(a))
    monkeypatch.setenv("MISSING_TOKEN", "ambient-must-not-leak")
    primary = resolve_agent_context(config("primary"), session_id="session_a", profile_home=a)
    specialist = resolve_agent_context(config(), session_id="session_b", profile_home=b)
    with agent_runtime_scope(primary):
        assert get_secret("PRIVATE_TOKEN") == "private-fixture"
        with agent_runtime_scope(specialist):
            assert get_hermes_home() == b
            assert get_secret("PRIVATE_TOKEN") is None
            assert get_secret("PROVIDER_TOKEN") == "provider-fixture"
        assert current_agent_context() is primary
        assert get_hermes_home() == a
        assert get_secret("PRIVATE_TOKEN") == "private-fixture"
    assert current_agent_context() is None


def test_construct_persists_binding_before_body_and_rejects_legacy_history(tmp_path, monkeypatch):
    write_config(tmp_path, config()); monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    db = SessionDB(tmp_path / "state.db")
    db.create_session("fresh", source="tui")
    agent = SimpleNamespace()
    construct(agent, session_id="fresh", session_db=db)
    assert agent.seen_context.identity.agent_id == "assistant"
    assert agent.seen_secret is None
    assert agent.memory_skipped and agent.review_skipped
    stored = db.get_session_model_config_value("fresh", "agent_identity")
    assert stored == agent.runtime_context.identity.to_record()
    resumed = SimpleNamespace()
    construct(resumed, session_id="fresh", session_db=db)
    assert resumed.runtime_context.identity == agent.runtime_context.identity
    db.create_session("legacy", source="cli")
    db.append_message("legacy", "user", "old private conversation")
    with pytest.raises(IdentityPolicyError):
        construct(SimpleNamespace(), session_id="legacy", session_db=db)
    db.close()


@pytest.mark.parametrize("platform", ["cli", "tui", "telegram", "cron", "api_server"])
def test_real_agent_constructor_binds_before_provider_routing(tmp_path, monkeypatch, platform):
    write_config(tmp_path, config()); monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    import agent.agent_init as initialization
    from run_agent import AIAgent

    class ReachedProvider(Exception):
        pass

    def witness(agent, *_args):
        assert current_agent_context().identity.agent_id == "assistant"
        assert agent.runtime_context.policy.role == "specialist"
        assert get_secret("PRIVATE_TOKEN") is None
        raise ReachedProvider()

    monkeypatch.setattr(initialization, "_finalize_routing", witness)
    with pytest.raises(ReachedProvider):
        AIAgent(model="fixture/model", platform=platform, user_name="primary")
    assert current_agent_context() is None


def test_child_and_teardown_keep_their_own_binding(tmp_path, monkeypatch):
    write_config(tmp_path, config("primary")); monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    parent, child = SimpleNamespace(), SimpleNamespace()
    construct(parent, session_id="parent")
    with agent_runtime_scope(parent.runtime_context):
        construct(child, session_id="child", parent_session_id="parent", side_agent=True)
        assert child.runtime_context.identity.agent_id != parent.runtime_context.identity.agent_id
        assert child.seen_secret is None
        assert child.runtime_context.policy.memory_backend == "builtin"
    @bound_agent_lifecycle
    def teardown(agent):
        return current_agent_context().identity.agent_id, get_secret("PRIVATE_TOKEN")
    assert teardown(child) == (child.runtime_context.identity.agent_id, None)
    assert teardown(parent) == (parent.runtime_context.identity.agent_id, "private-fixture")


@pytest.mark.parametrize("invalid", [False, None, [], 0, {"schema_version": 999}])
def test_invalid_policy_never_falls_back_to_legacy(tmp_path, monkeypatch, invalid):
    write_config(tmp_path, {"agent_identity": invalid}); monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    with pytest.raises(IdentityPolicyError):
        construct(SimpleNamespace())


def test_explicit_credential_cannot_bypass_scope(tmp_path, monkeypatch):
    write_config(tmp_path, config()); monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from run_agent import AIAgent
    with pytest.raises(IdentityPolicyError, match="credential"):
        AIAgent(model="fixture/model", api_key="private-fixture")


def test_explicit_memory_toolset_cannot_load_profile_store_under_policy(tmp_path, monkeypatch):
    write_config(tmp_path, config()); monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from agent.agent_init import _init_memory
    context = resolve_agent_context(config(), session_id="memory_guard", profile_home=tmp_path)
    agent = SimpleNamespace(runtime_context=context, enabled_toolsets=["memory"], disabled_toolsets=[],
                            tools=[], valid_tool_names=set())
    with agent_runtime_scope(context):
        _init_memory(agent, {"memory": {"memory_enabled": True}}, True, "cli")
    assert agent._memory_store is None
    assert agent._memory_manager is None


def test_removing_policy_cannot_downgrade_a_bound_session(tmp_path, monkeypatch):
    write_config(tmp_path, config()); monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    db = SessionDB(tmp_path / "state.db")
    construct(SimpleNamespace(), session_id="bound", session_db=db)
    (tmp_path / "config.yaml").write_text("{}")
    with pytest.raises(IdentityPolicyError, match="disabled"):
        construct(SimpleNamespace(), session_id="bound", session_db=db)
    db.close()


def test_runtime_authority_property_cannot_be_replaced_by_hook():
    from run_agent import AIAgent
    instance = object.__new__(AIAgent)
    with pytest.raises(AttributeError):
        instance.runtime_context = object()


def test_denied_optional_weixin_credential_remains_unavailable(tmp_path, monkeypatch):
    write_config(tmp_path, config()); monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from tools.send_message_tool import _weixin_env_pconfig
    context = resolve_agent_context(config(), session_id="optional_secret", profile_home=tmp_path)
    with agent_runtime_scope(context):
        assert _weixin_env_pconfig() is None
