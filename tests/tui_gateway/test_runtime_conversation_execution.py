"""Canonical stdio binding reaches the real executable local agent surface."""
import json
from types import SimpleNamespace

import pytest

from tests.agent.test_runtime_commands import _client, _config, _envelope
from tests.tui_gateway.test_runtime_conversations_rpc import config_for, runtime  # noqa: F401


def test_canonical_stdio_build_executes_without_allowing_external_web_or_gateway(runtime, monkeypatch):
    from agent.runtime_commands import (
        RuntimeCommandError, bind_submitted_command, runtime_execution_denial,
    )

    server, db, home = runtime.server, runtime.stores["a"], runtime.homes["a"]
    config = config_for("a")
    config["agent_identity"]["agents"]["ryoko"].update(_config()["agent_identity"]["agents"]["primary"])
    (home / "config.yaml").write_text(json.dumps(config))
    (home / ".env").write_text("OPENAI_API_KEY=fixture-provider-key\n")
    monkeypatch.setenv("HERMES_IGNORE_RULES", "1")
    monkeypatch.setattr("model_tools.get_tool_definitions", lambda *a, **k: [])
    monkeypatch.setattr("model_tools.check_toolset_requirements", lambda *a, **k: {})
    monkeypatch.setattr("tools.egress_policy.build_model_client", lambda *a, **k: _client())
    monkeypatch.setattr(server, "_resolve_agent_model_runtime", lambda *a: (
        "gpt-4.1-mini", {"provider": "openai", "base_url": "https://fixture.invalid/v1",
                          "api_mode": "chat_completions", "api_key": "fixture-provider-key"}))
    conversation = runtime.call("create", idempotency_key="executable")["result"]["conversation"]
    stored_id = conversation["conversation_id"]
    binding = runtime.call("bind", conversation_id=stored_id)["result"]
    live_id = binding["session_id"]
    session = server._sessions[live_id]
    assert session["transport"] is runtime.peer
    agents = []

    def build(sid, record):
        kwargs = server._deferred_build_agent_kwargs(record, db)
        with server._profile_build_scope(str(home)):
            agent = server._make_agent(sid, record["session_key"], **kwargs)
        agents.append(agent)
        return agent

    def rpc(method, **params):
        return server.dispatch({"jsonrpc": "2.0", "id": "execution", "method": method,
            "params": {"schema_version": 1, "session_id": live_id, **params}}, transport=runtime.peer)

    try:
        agent = session["agent"] = build(live_id, session)
        assert runtime_execution_denial(agent) is None
        assert runtime.call("bind", conversation_id=stored_id)["result"]["readiness"] == "ready"
        caps = server.dispatch({"jsonrpc": "2.0", "id": "capabilities", "method": "runtime.capabilities",
            "params": {"session_id": live_id}}, transport=runtime.peer)["result"]
        assert next(item for item in caps["operations"] if item["operation"] == "submit")["executes"]
        agent._cached_system_prompt = "Fixture system prompt."
        agent._use_prompt_caching = False
        agent._disable_streaming = True
        agent.max_iterations = 4
        agent.save_trajectories = False
        agent.compression_enabled = False
        monkeypatch.setattr(agent, "_create_request_openai_client", lambda **k: agent.client)
        monkeypatch.setattr(agent, "_close_request_openai_client", lambda *a, **k: None, raising=False)
        monkeypatch.setattr(agent, "_cleanup_task_resources", lambda *a, **k: None)
        monkeypatch.setattr(agent, "_save_trajectory", lambda *a, **k: None)
        completed = []

        def worker(_rid, _sid, record, text, *, image_paths, runtime_command_receipt):
            with bind_submitted_command(record["agent"], runtime_command_receipt):
                completed.append(record["agent"].run_conversation(text))
            record["running"] = False
            return True

        monkeypatch.setattr(server, "_run_prompt_submit", worker)
        request = _envelope("local-command")
        receipt = rpc("runtime.command", **request)["result"]
        assert receipt["status"] == "accepted"
        assert completed[0]["final_response"] == "recorded answer"
        assert agent.client._fixture_create.call_count == 1
        observed = runtime.call("command.receipt", conversation_id=stored_id, command_id="local-command")["result"]
        assert observed["status"] == "completed" and observed["receipt"] == receipt
        assert rpc("runtime.command", **request)["result"] == receipt
        assert agent.client._fixture_create.call_count == 1
        # The durable creation provenance is unchanged by the live local surface.
        assert db.get_session(stored_id)["source"] == "web"
        for source in ("web", "telegram"):
            external_id = "external-" + source
            external_record = {**session, "source": source,
                               "transport": SimpleNamespace(write=lambda frame: True)}
            server._sessions[external_id] = external_record
            external = external_record["agent"] = build(external_id, external_record)
            assert runtime_execution_denial(external) == "runtime_delivery_transport_unsupported"
            with pytest.raises(RuntimeCommandError, match="runtime_delivery_transport_unsupported"):
                external.run_conversation("Never dispatch an external-delivery run")
            assert external.client._fixture_create.call_count == 0
        stranger = SimpleNamespace(write=lambda frame: True)
        assert runtime.call("bind", conversation_id=stored_id, via=stranger)["error"]["data"]["code"] == "runtime_transport_unsupported"
    finally:
        from tui_gateway import prompt_admission
        if prompt_admission._HANDLE is not None:
            prompt_admission._HANDLE.cancel(wait=2)
            prompt_admission._HANDLE = None
        prompt_admission._STARTED = False
        prompt_admission._STOPPING = False
        for record in server._sessions.values():
            server._release_active_session_slot(record)
        for agent in agents:
            agent.close()
