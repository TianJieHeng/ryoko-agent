"""Real isolated stdio producer restart, without provider setup or model execution."""
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading


def test_stdio_owner_creation_reconciles_after_abrupt_process_restart(tmp_path):
    home = tmp_path / "owner"
    home.mkdir()
    (home / "config.yaml").write_text(json.dumps({
        "agent_identity": {"schema_version": 1, "principal_id": "stdio-owner", "profile_id": "stdio-profile",
            "primary_agent_id": "ryoko", "active_agent_id": "ryoko",
            "agents": {"ryoko": {"policy_version": 1, "role": "primary", "memory_backend": "personal_mcp"}}},
        "mcp_servers": {}, "model": {"default": "fixture-unconfigured", "provider": "openai"}}), encoding="utf-8")
    env = {key: value for key, value in os.environ.items()
           if not any(part in key for part in ("API_KEY", "TOKEN", "SECRET", "PASSWORD"))}
    env.update(HERMES_HOME=str(home), HERMES_RUNTIME_DIR=str(tmp_path / "runtime"),
               HOME=str(tmp_path), HERMES_TEST_ISOLATION="1")
    repo = Path(__file__).resolve().parents[2]
    conversation = None
    for attempt in range(2):
        with (tmp_path / f"stderr-{attempt}.log").open("w", encoding="utf-8") as diagnostics:
            process = subprocess.Popen([sys.executable, "-u", "-m", "tui_gateway.entry"], cwd=repo, env=env,
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=diagnostics, text=True)
            frames = queue.Queue()
            def read():
                for line in process.stdout:
                    frames.put(json.loads(line))
            reader = threading.Thread(target=read, daemon=True)
            reader.start()
            def receive(predicate):
                while True:
                    frame = frames.get(timeout=30)
                    if predicate(frame):
                        return frame
            def call(method, request_id, **params):
                process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": request_id,
                    "method": "runtime.conversation." + method, "params": {"schema_version": 1, **params}}) + "\n")
                process.stdin.flush()
                result = receive(lambda frame: frame.get("id") == request_id)
                assert "error" not in result, result
                return result["result"]
            try:
                receive(lambda frame: frame.get("params", {}).get("type") == "gateway.ready")
                caps = call("capabilities", "capabilities")
                assert caps["identity"]["principal_id"] == "stdio-owner"
                receipt = call("operation.get", "receipt", idempotency_key="unknown-outcome")
                assert receipt["found"] == bool(attempt)
                if attempt:
                    assert receipt["conversation"] == conversation
                created = call("create", "create", idempotency_key="unknown-outcome", title="Durable")
                if conversation is None:
                    conversation = created["conversation"]
                    assert created["created"]
                else:
                    assert created["conversation"] == conversation and not created["created"]
                listed = call("list", "list")
                assert listed["conversations"] == [conversation]
                history = call("history", "history", conversation_id=conversation["conversation_id"])
                assert history["messages"] == [] and history["lineage"] == [conversation["conversation_id"]]
            finally:
                process.kill()
                process.wait(timeout=10)
                process.stdin.close()
                reader.join(timeout=5)
                process.stdout.close()
