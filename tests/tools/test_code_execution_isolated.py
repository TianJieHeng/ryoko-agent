"""Identity+broker+SQLite budget+registry dispatch reaches the real OS executor."""
import json
import time
from types import SimpleNamespace

import httpx
import pytest
from openai import OpenAI

from agent.agent_identity import resolve_agent_context
from agent.budget_account import BudgetRuntime, parse_budget_policy
from agent.identity_lifecycle import agent_runtime_scope
from agent.runtime_commands import RuntimeRun, bind_runtime_run, reset_runtime_run, invoke_runtime_operation
from hermes_state import SessionDB
from tools.registry import registry

pytestmark = pytest.mark.platforms("linux")


def policy():
    return {"schema_version": 1, "mode": "tokens",
            "limits": {"tokens": 10000, "attempts": 10, "cost_micros": None,
                       "wall_ms": 20000, "provider_slots": 1, "executor_slots": 1},
            "deadline_seconds": 30, "request_timeout_ms": 5000,
            "routes": [{"model": "fixture", "base_url": "https://fixture.invalid/v1",
                        "max_input_tokens": 100, "max_output_tokens": 10, "input_overhead_tokens": 10,
                        "output_token_parameter": "max_tokens", "input_cost_micros_per_million": None,
                        "output_cost_micros_per_million": None, "bounds_verified": True}]}


@pytest.fixture
def live(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    raw = {"runtime_budget": policy(), "terminal": {"backend": "local"},
           "code_execution": {"timeout": 3}, "agent_identity": {
        "schema_version": 1, "principal_id": "owner", "profile_id": "fixture",
        "primary_agent_id": "primary", "active_agent_id": "primary",
        "agents": {"primary": {"policy_version": 1, "role": "primary", "memory_backend": "personal_mcp",
                               "allowed_tools": ["execute_code"]}}}}
    (tmp_path / "config.yaml").write_text(json.dumps(raw))
    context = resolve_agent_context(raw, session_id="session", profile_home=tmp_path)
    db = SessionDB(tmp_path / "state.db")
    db.create_session("session", source="cli")
    db.claim_session_agent_identity("session", context.identity.to_record())
    actor = {"principal_id": "owner", "profile_id": "fixture", "agent_id": "primary"}
    command = {"schema_version": 1, "command_id": "command", "idempotency_key": "command",
               "expected_revision": None, "operation": "submit", "payload": {"text": "fixture"},
               "identity_binding": actor}
    receipt = db.submit_runtime_command("session", actor=actor, command=command)
    assert db.acquire_session_turn_lease("session", "owner", wait_seconds=0)
    generation = db.get_session_turn_lease("session")["generation"]
    assert db.claim_runtime_command("session", "command", holder="owner", generation=generation)
    client = OpenAI(api_key="synthetic-only", base_url="https://fixture.invalid/v1",
                    http_client=httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(500))))
    agent = SimpleNamespace(api_mode="chat_completions", provider="openai", client=client,
                            runtime_context=context, _interrupt_requested=False)
    budget_policy = parse_budget_policy(raw)
    deadline = time.time() + 30
    account = db.create_budget_account("session", actor, receipt["run_id"], budget_policy.limits,
                                       deadline=deadline, holder="owner", generation=generation,
                                       policy_snapshot=budget_policy.record)
    budget = BudgetRuntime(db, receipt["run_id"], account["root_id"], budget_policy,
                           actor, "owner", generation, deadline, agent)
    run = RuntimeRun(agent, db, "session", "command", receipt["run_id"], "owner", generation, context,
                     budget=budget)
    import tools.code_execution_tool  # noqa: F401
    with agent_runtime_scope(context):
        token = bind_runtime_run(run)
        try:
            yield SimpleNamespace(run=run, budget=budget, home=tmp_path, raw=raw)
        finally:
            reset_runtime_run(token, run)
    client.close()
    db.close()


def test_real_registry_path_reserves_before_launch_and_stages(live, monkeypatch):
    from tools import code_execution_isolated as adapter
    actual = adapter.execute_isolated_python
    seen = []
    def inspect(*args, **kwargs):
        status = live.budget.status()
        assert status["reserved"]["executor_slots"] == 1
        assert 0 < status["reserved"]["wall_ms"] <= 5000
        assert kwargs["wall_seconds"] <= 5
        seen.append(True)
        return actual(*args, **kwargs)
    monkeypatch.setattr(adapter, "execute_isolated_python", inspect)
    result = json.loads(invoke_runtime_operation("tool", lambda: registry.dispatch("execute_code", {
        "code": "import socket\ntry:\n socket.socket()\nexcept OSError:\n print('network denied')\nopen('result.txt','w').write('verified')"}), name="execute_code"))
    assert result["status"] == "completed", result
    assert result["isolated"] is True
    assert result["stdout"] == "network denied\n"
    assert result["outputs"][0]["path"] == "result.txt"
    assert seen == [True]
    assert live.budget.status()["reserved"]["executor_slots"] == 0
    assert live.budget.status()["consumed"]["wall_ms"] > 0


def test_direct_entry_has_same_boundary_and_no_kernel_fallback(live, monkeypatch):
    from tools.code_execution_tool import execute_code
    monkeypatch.setattr("tools.code_kernel.execute_in_session_kernel",
                        lambda *a, **k: pytest.fail("unsafe kernel fallback"))
    result = json.loads(execute_code("print(2 + 3)"))
    assert result["status"] == "completed", result
    assert result["stdout"] == "5\n"
    assert live.budget.status()["reserved"]["executor_slots"] == 0


def test_missing_enforcement_and_remote_mode_fail_closed(live, monkeypatch):
    from tools.environments.isolated_python import IsolationUnavailable
    from tools import code_execution_isolated as adapter
    def unavailable(*args, **kwargs):
        raise IsolationUnavailable("fixture unavailable")
    monkeypatch.setattr(adapter, "execute_isolated_python", unavailable)
    result = json.loads(registry.dispatch("execute_code", {"code": "print('never')"}))
    assert result["error"] == "executor_enforcement_unavailable"
    assert live.budget.status()["reserved"]["executor_slots"] == 0
    monkeypatch.setattr("tools.terminal_tool._get_env_config", lambda: {"env_type": "ssh"})
    result = json.loads(registry.dispatch("execute_code", {"code": "print('never')"}))
    assert result["error"] == "executor_not_certified"


def test_unacknowledged_termination_retains_budget_slot(live, monkeypatch):
    from tools.environments.isolated_python import IsolationTerminationUncertain
    def uncertain(*args, **kwargs):
        raise IsolationTerminationUncertain("fixture uncertain")
    monkeypatch.setattr("tools.code_execution_isolated.execute_isolated_python", uncertain)
    result = json.loads(registry.dispatch("execute_code", {"code": "print('fixture')"}))
    assert result["status"] == "outcome_uncertain"
    assert live.budget.status()["reserved"]["executor_slots"] == 1
    assert live.budget.status()["unknown_usage"] is True


def test_revoked_or_unbound_identity_cannot_execute(live):
    live.raw["agent_identity"]["agents"]["primary"]["allowed_tools"] = []
    (live.home / "config.yaml").write_text(json.dumps(live.raw))
    result = json.loads(registry.dispatch("execute_code", {"code": "print('never')"}))
    assert result["status"] == "denied"
    assert live.budget.status()["consumed"]["wall_ms"] == 0


def test_actual_agent_tools_receive_isolated_schema(tmp_path, monkeypatch):
    from unittest.mock import MagicMock
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    raw = {"agent_identity": {
        "schema_version": 1, "principal_id": "owner", "profile_id": "fixture",
        "primary_agent_id": "primary", "active_agent_id": "primary",
        "agents": {"primary": {"policy_version": 1, "role": "primary", "memory_backend": "personal_mcp",
            "secret_refs": ["OPENAI_API_KEY"], "allowed_tools": ["execute_code"],
            "recipient_plan": {"schema_version": 1, "envelope": "declared", "grants": [
                {"recipient_id": "fixture", "purpose": "main_model", "endpoint": "https://fixture.invalid/v1",
                 "transport": "httpx"}]}}}}}
    (tmp_path / "config.yaml").write_text(json.dumps(raw))
    (tmp_path / ".env").write_text("OPENAI_API_KEY=fixture-key\n")
    monkeypatch.setattr("agent.process_bootstrap.OpenAI", MagicMock())
    # Requirement discovery is unrelated to this schema consumer and may probe
    # optional services; the actual selection/rewrite/publication path stays real.
    monkeypatch.setattr("model_tools.check_toolset_requirements", lambda *a, **k: {})
    from run_agent import AIAgent
    db = SessionDB(tmp_path / "state.db")
    agent = None
    try:
        agent = AIAgent(model="fixture", provider="openai", api_key="fixture-key",
                        base_url="https://fixture.invalid/v1", session_id="schema-session", session_db=db,
                        quiet_mode=True, skip_context_files=True, skip_memory=True,
                        enabled_toolsets=["code_execution"])
        definition = next(item["function"] for item in agent.tools if item["function"]["name"] == "execute_code")
        assert "stateless Python" not in registry.get_schema("execute_code")["description"]
        assert "stateless Python" in definition["description"]
        assert "No network" in definition["description"]
        assert "hermes_tools import" not in definition["description"]
        assert set(definition["parameters"]["properties"]) == {"code", "reset"}
    finally:
        if agent is not None:
            agent.close()
        db.close()
