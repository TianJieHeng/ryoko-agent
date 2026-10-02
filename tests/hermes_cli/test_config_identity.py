"""Inspection uses effective config and never exposes secret values or starts providers."""
import argparse
import json
from types import SimpleNamespace

from hermes_cli.config_identity import inspect_identity
from hermes_cli.subcommands.config import build_config_parser
from agent.agent_identity import resolve_agent_context
from hermes_state import SessionDB


def test_config_identity_command_resolves_redacted_policy(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    cfg = {"agent_identity": {
        "schema_version": 1, "principal_id": "owner", "profile_id": "profile",
        "primary_agent_id": "primary", "active_agent_id": "primary",
        "agents": {"primary": {"policy_version": 1, "role": "primary", "memory_backend": "personal_mcp",
                                "secret_refs": ["PRIVATE_TOKEN"]}},
        "personal_secret_refs": ["PRIVATE_TOKEN"],
    }, "model": {"api_key": "fixture-secret-value"}}
    (tmp_path / "config.yaml").write_text(json.dumps(cfg))
    (tmp_path / ".env").write_text("PRIVATE_TOKEN=fixture-secret-value\n")
    parser = argparse.ArgumentParser()
    from hermes_cli.config import config_command
    build_config_parser(parser.add_subparsers(), cmd_config=config_command)
    args = parser.parse_args(["config", "identity", "--agent", "primary"])
    args.func(args)
    output = capsys.readouterr().out
    assert "fixture-secret-value" not in output
    assert "PRIVATE_TOKEN" not in output
    assert json.loads(output)["policy"]["memory_backend"] == "personal_mcp"
    assert not (tmp_path / "state.db").exists()
    db = SessionDB(tmp_path / "state.db")
    context = resolve_agent_context(cfg, session_id="session", profile_home=tmp_path)
    db.create_session("session", source="cli", model_config={"agent_identity": context.identity.to_record()})
    db.close()
    assert inspect_identity(session_id="session")["status"] == "configured"


def test_legacy_inspection_does_not_create_runtime_state(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    assert inspect_identity()["status"] == "legacy"
    assert not (tmp_path / "state.db").exists()
