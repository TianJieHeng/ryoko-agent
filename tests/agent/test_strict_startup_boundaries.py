"""Strict startup cannot provision/probe or enter an unguarded delivery route."""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import openai
import pytest

from agent.agent_identity import resolve_agent_context
from agent.identity_lifecycle import agent_runtime_scope
from agent.runtime_commands import RuntimeCommandError, prepare_turn_command, runtime_execution_denial
from hermes_state import SessionDB


@pytest.fixture
def strict(tmp_path, monkeypatch):
    raw = {"agent_identity": {"schema_version": 1, "principal_id": "owner", "profile_id": "profile",
        "primary_agent_id": "primary", "active_agent_id": "primary", "agents": {"primary": {
            "policy_version": 1, "role": "primary", "memory_backend": "personal_mcp"}}}}
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "config.yaml").write_text(json.dumps(raw))
    ctx = resolve_agent_context(raw, session_id="session", profile_home=tmp_path)
    with agent_runtime_scope(ctx):
        yield ctx, tmp_path


def test_strict_context_engine_refuses_before_plugin_factory(strict, monkeypatch):
    from agent.agent_init import _select_context_engine
    from tools.egress_policy import EgressDenied
    callback = Mock(side_effect=AssertionError("must not load plugin"))
    monkeypatch.setattr("plugins.context_engine.load_context_engine", callback)
    assert _select_context_engine({"context": {"engine": "compressor"}}) is None
    with pytest.raises(EgressDenied):
        _select_context_engine({"context": {"engine": "untrusted-plugin"}})
    callback.assert_not_called()


def test_strict_metadata_and_local_model_startup_are_offline(strict, monkeypatch):
    import agent.model_metadata as metadata
    from agent.agent_init import _resolve_context_length, _configure_ollama_num_ctx
    probes = ["_config_override_context_length", "_resolve_moa_context_length", "_resolve_provider_aware_context_length",
              "_probe_local_context_length", "fetch_model_metadata", "get_cached_context_length"]
    for name in probes:
        monkeypatch.setattr(metadata, name, Mock(side_effect=AssertionError("network-capable metadata path reached")))
    assert metadata.get_model_context_length("fixture-model", config_context_length=100000) == 100000
    assert metadata.get_model_context_length("unknown-fixture-model") == metadata.DEFAULT_FALLBACK_CONTEXT
    for name in probes:
        getattr(metadata, name).assert_not_called()
    load = Mock(side_effect=AssertionError("must not provision model"))
    agent = SimpleNamespace(model="fixture-model", provider="lmstudio", base_url="http://127.0.0.1:1234/v1",
        _ensure_lmstudio_runtime_loaded=load, quiet_mode=True)
    result = _resolve_context_length(agent, {}, agent.base_url)
    assert result[0] is None and result[2] is None
    assert "offline" in agent.runtime_metadata_status
    load.assert_not_called()
    query = Mock(side_effect=AssertionError("must not probe local server"))
    monkeypatch.setattr("agent.agent_init.query_ollama_num_ctx", query)
    _configure_ollama_num_ctx(agent, {}, None)
    assert agent._ollama_num_ctx is None
    _configure_ollama_num_ctx(agent, {"ollama_num_ctx": 100000}, 100000)
    assert agent._ollama_num_ctx == 100000
    query.assert_not_called()


@pytest.mark.parametrize("platform", ["telegram", "slack", "cron", "api", "webhook"])
def test_external_delivery_route_refused_before_command_or_provider(strict, platform):
    ctx, home = strict
    called = []
    client = openai.OpenAI(api_key="fixture", base_url="https://fixture.invalid/v1",
        http_client=httpx.Client(transport=httpx.MockTransport(lambda request: called.append(request) or httpx.Response(500))))
    db = SessionDB(home / "state.db")
    db.create_session("session", source=platform)
    db.claim_session_agent_identity("session", ctx.identity.to_record())
    agent = SimpleNamespace(runtime_context=ctx, client=client, api_mode="chat_completions", provider="openai",
                            platform=platform, session_id="session", _session_db=db)
    try:
        assert runtime_execution_denial(agent) == "runtime_delivery_transport_unsupported"
        with pytest.raises(RuntimeCommandError, match="runtime_delivery_transport_unsupported"):
            prepare_turn_command(agent, "private fixture input")
        assert db.read_runtime_snapshot("session")["revision"] == 0
        assert not called
        agent.platform = "cli"
        assert runtime_execution_denial(agent) is None
        agent.api_mode = "anthropic_messages"
        assert runtime_execution_denial(agent) == "runtime_transport_unsupported"
    finally:
        client.close()
        db.close()
