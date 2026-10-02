"""Both real RPC transports recheck trusted grants and durable owner fences."""
import contextvars
import json
import os
import socket
import subprocess
import threading
import time
from types import SimpleNamespace

import httpx
import openai
import pytest

from agent.agent_identity import resolve_agent_context
from agent.identity_lifecycle import agent_runtime_scope
from agent.runtime_commands import RuntimeRun, bind_runtime_run, reset_runtime_run
from hermes_state import SessionDB
from tools.code_execution_rpc import _rpc_poll_loop, _rpc_server_loop


@pytest.fixture
def owner(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    raw = {"agent_identity": {"schema_version": 1, "principal_id": "fixture", "profile_id": "fixture",
        "primary_agent_id": "primary", "active_agent_id": "primary", "agents": {"primary": {
            "policy_version": 1, "role": "primary", "memory_backend": "personal_mcp", "allowed_tools": ["todo_list"]}}}}
    (tmp_path / "config.yaml").write_text(json.dumps(raw))
    ctx = resolve_agent_context(raw, session_id="session", profile_home=tmp_path)
    db = SessionDB(tmp_path / "state.db")
    db.create_session("session", source="cli")
    db.claim_session_agent_identity("session", ctx.identity.to_record())
    actor = {"principal_id": "fixture", "profile_id": "fixture", "agent_id": "primary"}
    command = {"schema_version": 1, "command_id": "fixture", "idempotency_key": "fixture", "expected_revision": None,
               "operation": "submit", "payload": {"text": "fixture"}, "identity_binding": actor}
    receipt = db.submit_runtime_command("session", actor, command)
    assert db.try_acquire_session_turn_lease("session", "owner", ttl_seconds=60)
    generation = db.get_session_turn_lease("session")["generation"]
    assert db.claim_runtime_command("session", "fixture", holder="owner", generation=generation)
    client = openai.OpenAI(api_key="fixture", base_url="https://fixture.invalid/v1",
                          http_client=httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(500))))
    agent = SimpleNamespace(runtime_context=ctx, api_mode="chat_completions", provider="openai", client=client)
    run = RuntimeRun(agent, db, "session", "fixture", receipt["run_id"], "owner", generation, ctx)
    with agent_runtime_scope(ctx):
        token = bind_runtime_run(run)
        try:
            yield SimpleNamespace(db=db, raw=raw, home=tmp_path)
        finally:
            reset_runtime_run(token, run)
    client.close()
    db.close()


@pytest.mark.parametrize("transport", ["socket", "file"])
@pytest.mark.parametrize("denial", ["grant", "revoked", "stale"])
def test_valid_rpc_token_cannot_bypass_live_tool_scope(owner, monkeypatch, transport, denial):
    if denial == "revoked":
        owner.raw["agent_identity"]["agents"]["primary"]["allowed_tools"] = []
        (owner.home / "config.yaml").write_text(json.dumps(owner.raw))
    elif denial == "stale":
        owner.db.release_session_turn_lease("session", "owner")
        assert owner.db.try_acquire_session_turn_lease("session", "successor", ttl_seconds=60)
    tool = "terminal" if denial == "grant" else "todo_list"
    request = {"token": "fixture-token", "seq": 1, "tool": tool, "args": {}}
    calls, log, counter = [], [], [0]
    dispatch = lambda name, args, **kw: calls.append((name, args)) or '{}'
    stop = threading.Event()
    allowed = frozenset({"terminal", "todo_list"})
    if transport == "socket":
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        thread = threading.Thread(target=contextvars.copy_context().run, args=(
            _rpc_server_loop, listener, "owner", log, counter, 5, allowed, stop, "fixture-token", dispatch))
        thread.start()
        try:
            with socket.create_connection(listener.getsockname(), timeout=3) as peer:
                peer.sendall((json.dumps(request) + '\n').encode())
                with peer.makefile("rb") as stream:
                    result = json.loads(stream.readline())
        finally:
            stop.set()
            listener.close()
            thread.join(5)
            assert not thread.is_alive()
    else:
        from pm.shell import bash
        shell = bash()
        class Shell:
            def execute(self, command, cwd=None, timeout=None, stdin_data=None):
                proc = subprocess.run([shell, "-c", command], cwd=cwd, timeout=timeout,
                    env=dict(os.environ), input=stdin_data or "", capture_output=True, text=True)
                assert proc.returncode == 0, proc.stderr
                return {"output": proc.stdout, "returncode": proc.returncode}
        monkeypatch.setattr("model_tools.handle_function_call", dispatch)
        folder = owner.home / "rpc"
        folder.mkdir()
        (folder / "req_000001").write_text(json.dumps(request))
        thread = threading.Thread(target=contextvars.copy_context().run, args=(
            _rpc_poll_loop, Shell(), str(folder), "owner", log, counter, 5, allowed, stop, "fixture-token"))
        thread.start()
        try:
            response = folder / "res_000001"
            until = time.monotonic() + 5
            while not response.exists() and time.monotonic() < until:
                stop.wait(0.02)
            result = json.loads(response.read_text())
        finally:
            stop.set()
            thread.join(5)
            assert not thread.is_alive()
    assert result.get("error")
    assert calls == [] and counter == [0] and log == []
