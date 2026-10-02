"""Deterministic BE05 checks precede model gates and bind post-hook inputs."""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.tools.test_capability_broker import live_runtime  # noqa: F401
from tools.capability_broker import CapabilityDenied


def test_deterministic_gate_precedes_model_and_executor_then_rechecks_exact_args(live_runtime, monkeypatch):
    from agent import tool_executor as executor
    from agent.tool_executor import _ToolCallRef

    gate = Mock(return_value=SimpleNamespace(allows_execution=True))
    agent = live_runtime.run.agent
    agent._tool_guardrails = SimpleNamespace(before_call=gate)
    agent._current_turn_id = "turn"
    agent.session_id = "session"
    execute = Mock(return_value="executed")
    monkeypatch.setattr(executor, "_begin_tool_execution", lambda *a, **k: None)
    monkeypatch.setattr(executor, "_run_with_activity_heartbeat", lambda _a, _n, fn: fn())
    monkeypatch.setattr(executor, "_pre_tool_block", lambda _a, ref: (None, ref.args))
    monkeypatch.setattr(_ToolCallRef, "emit_post", lambda *a, **k: None)
    def run(name, arguments):
        state = executor._ManagedToolResult(result=None, args=arguments, middleware_trace=[], blocked=False, dispatched=False)
        return executor._dispatch_authorized_once(agent, state, _ToolCallRef(name, arguments, "task", "call", []),
            execute=execute, scope_block=None, display_index=None, begin_execution=None,
            authorization_gate=None, trusted_context=live_runtime.context)

    denied = run("opaque_tool", {"actor": "primary", "approved": True})
    assert "certified" in denied
    gate.assert_not_called()
    execute.assert_not_called()

    # Simulate a classifier mutating arguments while it evaluates. Even its
    # allow verdict cannot approve bytes other than the deterministic snapshot.
    def mutate(_name, arguments):
        arguments["action"] = "write"
        return SimpleNamespace(allows_execution=True)
    gate.side_effect = mutate
    with pytest.raises(CapabilityDenied, match="scope"):
        run("todo_list", {"action": "read"})
    execute.assert_not_called()


def test_direct_model_entry_fences_before_permission_hooks(live_runtime, monkeypatch):
    import model_tools
    hook = Mock(return_value=({}, None))
    monkeypatch.setattr(model_tools, "_pre_dispatch_guards", hook)
    live_runtime.raw["agent_identity"]["agents"]["primary"]["allowed_tools"] = []
    (live_runtime.home / "config.yaml").write_text(json.dumps(live_runtime.raw))
    result = model_tools.handle_function_call("todo_list", {"action": "read"})
    assert json.loads(result)["error"] == "policy_revoked"
    hook.assert_not_called()
