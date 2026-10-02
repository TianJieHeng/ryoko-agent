"""Final constructor schemas and inspection agree after trusted late injections."""
import json
import copy
from unittest.mock import MagicMock

import pytest


@pytest.mark.parametrize("granted", [False, True])
def test_final_injected_schema_is_grant_checked_before_frozen_view(tmp_path, monkeypatch, granted):
    from hermes_state import SessionDB
    from run_agent import AIAgent

    name = "todo_list"
    config = {"agent_identity": {
        "schema_version": 1, "principal_id": "fixture-owner", "profile_id": "fixture-profile",
        "primary_agent_id": "primary", "active_agent_id": "primary",
        "agents": {"primary": {"policy_version": 1, "role": "primary", "memory_backend": "personal_mcp",
            "secret_refs": ["OPENAI_API_KEY"], "allowed_tools": [name] if granted else [],
            "recipient_plan": {"schema_version": 1, "envelope": "declared", "grants": [
                {"recipient_id": "fixture-model", "purpose": "main_model",
                 "endpoint": "https://fixture.invalid/v1", "transport": "httpx"}]}}},
    }}
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "config.yaml").write_text(json.dumps(config))
    (tmp_path / ".env").write_text("OPENAI_API_KEY=fixture-key\n")
    monkeypatch.setattr("agent.process_bootstrap.OpenAI", MagicMock())
    monkeypatch.setattr("model_tools.check_toolset_requirements", lambda *args, **kwargs: {})

    def inject(agent):
        from tools.todo_tool import TODO_SCHEMA
        # Late exposure is still a real certified handler, not a grant that
        # silently authorizes arbitrary injected Python or plugin code.
        agent.tools = [tool for tool in agent.tools if tool["function"]["name"] != name]
        agent.tools.append({"type": "function", "function": copy.deepcopy(TODO_SCHEMA)})
        agent.valid_tool_names.add(name)

    monkeypatch.setattr("agent.agent_init._inject_context_engine_tools", inject)
    db = SessionDB(tmp_path / "state.db")
    agent = None
    try:
        agent = AIAgent(model="gpt-4.1-mini", provider="openai", api_key="fixture-key",
                        base_url="https://fixture.invalid/v1", session_id="fixture-session", session_db=db,
                        quiet_mode=True, skip_memory=True, skip_context_files=True)
        exposed = {tool["function"]["name"] for tool in agent.tools}
        assert (name in exposed) is granted
        assert exposed == agent.valid_tool_names == set(agent.tool_view.selected_tool_ids)
        assert (name in agent.tool_view.authorized_tool_ids) is granted
    finally:
        if agent is not None:
            agent.close()
        db.close()
