"""Strict Dots turn preparation must retain identity before model-turn binding."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.agent_identity import resolve_agent_context
from agent.runtime_context import current_agent_context
from hermes_constants import get_hermes_home
from tests.agent.test_runtime_commands import _config
from tools.egress_policy import EgressDenied
from tui_gateway.context_reference_scope import expand_turn_references


def test_references_and_literal_email_use_owning_profile_then_restore_scope(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    homes = [tmp_path / "a", tmp_path / "b"]
    agents = []
    for index, home in enumerate(homes):
        home.mkdir()
        config = _config()
        config["agent_identity"]["profile_id"] = home.name
        (home / "config.yaml").write_text(json.dumps(config))
        (home / "note.txt").write_text("owned-by-" + home.name)
        context = resolve_agent_context(config, session_id=f"session-{index}", profile_home=home)
        agents.append(SimpleNamespace(runtime_context=context, model="fixture/model", _config_context_length=32000))
    monkeypatch.setenv("HERMES_HOME", str(homes[0]))
    for index in [0, 1, 0]:
        agent, home = agents[index], homes[index]
        result = expand_turn_references(agent, "Reviewer <reviewer@example.test>", cwd=home, model=agent.model)
        assert result.message == "Reviewer <reviewer@example.test>"
        assert not result.blocked
        result = expand_turn_references(agent, "Read @file:note.txt", cwd=home, model=agent.model)
        assert not result.blocked
        assert "owned-by-" + home.name in result.message
        assert "owned-by-" + homes[1-index].name not in result.message
        assert current_agent_context() is None
        assert get_hermes_home() == homes[0]


def test_unbound_agent_still_cannot_bypass_strict_metadata_policy(tmp_path, monkeypatch):
    (tmp_path / "config.yaml").write_text(json.dumps(_config()))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    with pytest.raises(EgressDenied, match="recipient_identity_required"):
        expand_turn_references(SimpleNamespace(), "user@example.test", cwd=tmp_path, model="fixture/model")
    assert current_agent_context() is None
