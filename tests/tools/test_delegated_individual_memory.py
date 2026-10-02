"""Real child construction and inline dispatch use child-owned stores."""
import asyncio
import json
from types import SimpleNamespace

import httpx
from openai import OpenAI
import pytest

from agent.identity_lifecycle import agent_runtime_scope
from tests.tools.test_session_search_owner import config, execution

pytestmark = pytest.mark.platforms("linux")


@pytest.mark.parametrize("budget_enabled", [False, True])
def test_real_child_constructor_memory_dispatch_and_resume_isolate_parent(tmp_path, monkeypatch, budget_enabled):
    from agent.inline_tool_executors import INLINE_TOOL_EXECUTORS, InlineToolContext
    from agent.tool_executor import _run_agent_tool_execution_middleware
    from agent.background_review import build_cache_parity_fork
    from hermes_state import SessionDB
    from run_agent import AIAgent
    from tools.delegate_tool import _build_child_agent
    from tests.agent.test_budget_runtime import active, policy
    raw = config()
    if budget_enabled:
        raw["runtime_budget"] = policy()
        raw["runtime_budget"]["routes"][0].update(model="gpt-4.1", base_url="https://fixture.invalid/v1")
    for policy in [*raw["agent_identity"]["agents"].values(), raw["agent_identity"]["child_policy"]]:
        policy["secret_refs"] = ["OPENAI_API_KEY"]
        policy["recipient_plan"] = {"schema_version": 1, "envelope": "declared", "grants": [
            {"recipient_id": "fixture", "purpose": purpose, "endpoint": "https://fixture.invalid/v1",
             "transport": "httpx"} for purpose in ("main_model", "aux_model")]}
    (tmp_path / "config.yaml").write_text(json.dumps(raw))
    (tmp_path / ".env").write_text("OPENAI_API_KEY=fixture-key\n")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    calls = []

    def client(**kwargs):
        kwargs["http_client"] = httpx.Client(transport=httpx.MockTransport(
            lambda request: calls.append(request) or httpx.Response(500)))
        return OpenAI(**kwargs)

    monkeypatch.setattr("agent.process_bootstrap.OpenAI", client)
    db = SessionDB(tmp_path / "state.db")
    options = dict(model="gpt-4.1", api_key="fixture-key", base_url="https://fixture.invalid/v1",
                   provider="openai", api_mode="chat_completions", max_iterations=1,
                   quiet_mode=True, skip_context_files=True,
                   enabled_toolsets=["memory", "session_search", "delegation"], session_db=db)
    parent = AIAgent(**options, session_id="parent")
    child = resumed = None
    try:
        with active(parent) if budget_enabled else agent_runtime_scope(parent.runtime_context):
            parent._memory_store.write_record("PARENT_PRIVATE")
            parent_namespace = parent._memory_store.namespace_id
            child = _build_child_agent(0, "Separate research", None, None, None, 1, 1, parent)
        assert child.runtime_context.policy.role == "child"
        assert "memory" in child.valid_tool_names
        with agent_runtime_scope(child.runtime_context):
            assert child._memory_store.namespace_id != parent_namespace
        assert child.skip_background_review
        with pytest.raises(ValueError, match="isolated per-agent memory"):
            build_cache_parity_fork(child, max_iterations=1)
        with active(child) if budget_enabled else execution(child._session_db, child.runtime_context, child):
            assert child._memory_store.recall() == []
            managed = _run_agent_tool_execution_middleware(
                child, function_name="memory", function_args={"action": "add", "content": "CHILD_PRIVATE"},
                effective_task_id=child.session_id, tool_call_id="memory-call",
                execute=lambda args: INLINE_TOOL_EXECUTORS["memory"](
                    child, args, InlineToolContext(child.session_id, "memory-call")))
            assert json.loads(managed.result)["success"]
            assert "PARENT_PRIVATE" not in json.dumps(child._memory_store.recall())
            with pytest.raises((ValueError, PermissionError)):
                parent._memory_store.recall()
            if budget_enabled:
                run = child._active_runtime_run
                after_write = run.budget.status()
                assert after_write["consumed"]["wall_ms"] > 0
                assert after_write["reserved"]["executor_slots"] == 0
                child._session_db.append_message(child.session_id, "user", "CHILD_SESSION_HISTORY")
                def recall(arguments):
                    return _run_agent_tool_execution_middleware(
                        child, function_name="session_search", function_args=arguments,
                        effective_task_id=child.session_id, tool_call_id="search-call",
                        execute=lambda args: INLINE_TOOL_EXECUTORS["session_search"](
                            child, args, InlineToolContext(child.session_id, "search-call"))).result
                found = recall({"session_id": child.session_id, "detail": "full"})
                assert "CHILD_SESSION_HISTORY" in found
                assert "PARENT_PRIVATE" not in found
                assert json.loads(recall({"query": "history", "detail": "summary"}))[
                    "error"] == "session_search_mode_unsupported"
                status = run.budget.status()
                assert status["reserved"]["executor_slots"] == status["reserved"]["provider_slots"] == 0
                assert status["consumed"]["attempts"] == status["consumed"]["tokens"] == 0
        with agent_runtime_scope(parent.runtime_context):
            resumed = AIAgent(**options, session_id=child.session_id,
                              parent_session_id=parent.session_id, side_agent=True)
        assert resumed.runtime_context.identity.agent_id == child.runtime_context.identity.agent_id
        with agent_runtime_scope(resumed.runtime_context):
            assert "CHILD_PRIVATE" in json.dumps(resumed._memory_store.recall())
        with agent_runtime_scope(parent.runtime_context):
            assert "CHILD_PRIVATE" not in json.dumps(parent._memory_store.recall())
        assert calls == []
    finally:
        for agent in (resumed, child, parent):
            if agent is not None:
                agent.close()
        db.close()


def test_strict_slash_surfaces_cannot_read_global_pending_memory(tmp_path, monkeypatch, capsys):
    from gateway.slash_commands import GatewaySlashCommandsMixin
    from hermes_cli.cli_commands_mixin import CLICommandsMixin
    from hermes_cli.write_approval_commands import handle_pending_subcommand
    from tools import write_approval as wa
    from tui_gateway import server
    (tmp_path / "config.yaml").write_text(json.dumps(config()))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    def forbidden(*args, **kwargs):
        pytest.fail("Strict review must not open the shared pending queue")

    monkeypatch.setattr(wa, "list_pending", forbidden)
    for args in ([], ["pending"], ["approve", "all"], ["reject", "all"], ["approval", "on"]):
        assert "unsupported" in handle_pending_subcommand(wa.MEMORY, args, set_mode_fn=forbidden)
    CLICommandsMixin._handle_memory_command(SimpleNamespace(), "/memory pending")
    captured = capsys.readouterr()
    assert "unsupported" in captured.out + captured.err
    event = SimpleNamespace(get_command_args=lambda: "pending")
    assert "unsupported" in asyncio.run(GatewaySlashCommandsMixin._handle_memory_command(SimpleNamespace(), event))
    assert "unsupported" in json.dumps(server._cmd_memory("id", {}, None, "memory", "pending"))
