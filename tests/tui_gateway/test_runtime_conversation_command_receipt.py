"""Canonical receipt reads survive unavailable providers without reviving work."""
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

from tests.tui_gateway.test_runtime_conversations_rpc import runtime  # noqa: F401
from tests.tui_gateway.test_runtime_conversation_agents import configure_specialists, primary_controls


def _seed_command(db, sid, command_id, status="accepted"):
    binding = db.get_session_model_config_value(sid, "agent_identity")
    actor = {key: binding[key] for key in ("principal_id", "profile_id", "agent_id")}
    receipt = db.submit_runtime_command(sid, actor, {
        "schema_version": 1, "command_id": command_id, "idempotency_key": command_id,
        "expected_revision": None, "operation": "submit", "payload": {"text": "PRIVATE INPUT"},
        "identity_binding": actor,
    })
    if status != "accepted":
        assert db.try_acquire_session_turn_lease(sid, "receipt-fixture")
        generation = db.get_session_turn_lease(sid)["generation"]
        assert db.claim_runtime_command(sid, command_id, holder="receipt-fixture", generation=generation)
        if status != "claimed":
            db.finish_runtime_command(sid, command_id, holder="receipt-fixture", generation=generation,
                status=status, result={"final_response": "PRIVATE OUTPUT", "reasoning": "PRIVATE REASONING"})
        db.release_session_turn_lease(sid, "receipt-fixture")
    return receipt


def _forbidden(*_args, **_kwargs):
    raise AssertionError("Receipt inspection may not build, bind, mutate, notify or execute")


def _durable_rows(db):
    with db._runtime_read() as conn:
        tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' "
            "AND (name LIKE 'runtime_%' OR name IN ('sessions','messages','session_turn_leases','agent_configuration_sessions'))")]
        return {table: list(map(tuple, conn.execute('SELECT * FROM "' + table + '" ORDER BY rowid')))
                for table in tables}


@pytest.mark.parametrize("status", ["accepted", "claimed", "completed", "failed", "blocked", "cancelled"])
def test_receipt_reads_only_exact_owned_conversations_after_archive_and_reopen(runtime, monkeypatch, status):
    from agent.runtime_commands import _RUN
    from hermes_state import SessionDB
    from tui_gateway.contracts.runtime_v1 import RuntimeCommandReceiptResult

    rt = runtime
    configure_specialists(rt)
    primary = rt.call("create", idempotency_key="primary")["result"]["conversation"]["conversation_id"]
    specialist = rt.call("create", agent_id="researcher", idempotency_key="specialist")["result"]["conversation"]["conversation_id"]
    db = rt.stores["a"]
    expected = _seed_command(db, specialist, "same-command", status)
    other = _seed_command(db, primary, "same-command", status)
    _seed_command(db, primary, "primary-only")
    db.create_session("legacy-only", source="web")
    control = primary_controls(rt)
    target = control("get", agent_id="researcher")["result"]["agent"]
    assert control("archive", agent_id="researcher", expected_revision=target["revision"])["result"]["agent"]["archived"]
    assert rt.call("archive", conversation_id=specialist, idempotency_key="archive", expected_revision=1,
                   archived=True)["result"]["conversation"]["archived"]
    db.end_session(specialist, "done")
    rt.server._sessions.clear()
    db.close()
    rt.stores["a"] = db = SessionDB(rt.homes["a"] / "state.db")
    before = _durable_rows(db)
    config_path = rt.homes["a"] / "config.yaml"
    original_config = json.loads(config_path.read_text())

    def call(sid=specialist, command_id="same-command", **params):
        return rt.call("command.receipt", conversation_id=sid, command_id=command_id, **params)

    with monkeypatch.context() as readonly:
        for name in ("_execute_write", "_execute_transcript_write", "claim_runtime_command", "submit_runtime_command"):
            readonly.setattr(db, name, _forbidden)
        for name in ("_schedule_agent_build", "_make_agent", "_submit_runtime_prompt", "_run_prompt_submit", "_emit"):
            readonly.setattr(rt.server, name, _forbidden)
        readonly.setattr("agent.runtime_commands.submit_command", _forbidden)
        for _ in range(2):
            response = call()
            assert "result" in response, response
            result = response["result"]
            RuntimeCommandReceiptResult.model_validate(result)
            assert result["receipt"] == expected and result["status"] == status and result["found"]
            assert result["accepted_input"] == {"state": "accepted", "message_id": None}
            assert result["messages"] == [] and not result["messages_has_more"] and result["next_message_cursor"] is None
            assert "PRIVATE" not in json.dumps(result)
        assert call(primary)["result"]["receipt"] == other
        for command_id in ("never-submitted", "primary-only"):
            missing = call(command_id=command_id)["result"]
            assert not missing["found"] and missing["receipt"] is None and missing["status"] is None
            assert missing["accepted_input"] is None and missing["messages"] == []
            assert missing["durable_revision"] == result["durable_revision"]
        for sid in ("unknown", "legacy-only"):
            assert call(sid)["error"]["data"]["code"] == "session_not_found"
        assert call(via=SimpleNamespace(write=lambda _frame: True))["error"]["data"]["code"] == "runtime_transport_unsupported"
        token = _RUN.set(object())
        try:
            assert call()["error"]["data"]["code"] == "conversation_owner_required"
        finally:
            _RUN.reset(token)
        for params in ({"schema_version": True}, {"schema_version": "1"}, {"schema_version": 2},
                       {"message_limit": True}, {"message_limit": 0}, {"message_limit": 101},
                       {"message_cursor": "x" * 2049}, {"agent_id": "ryoko"}, {"profile": "b"},
                       {"session_id": "live"}, {"principal_id": "owner-b"}, {"payload": {"text": "PRIVATE"}}):
            invalid = call(**params)
            assert invalid["error"]["code"] == 4000
            assert "PRIVATE" not in json.dumps(invalid)
        assert call(message_cursor="not-a-cursor")["error"]["data"]["code"] == "invalid_cursor"
        for field in ("principal_id", "profile_id"):
            changed = json.loads(json.dumps(original_config))
            changed["agent_identity"][field] = "another-owner"
            config_path.write_text(json.dumps(changed))
            assert call()["error"]["data"]["code"] == "session_not_found"
        changed = json.loads(json.dumps(original_config))
        changed["agent_identity"]["active_agent_id"] = "researcher"
        config_path.write_text(json.dumps(changed))
        assert call(primary)["error"]["data"]["code"] == "agent_selection_denied"
        config_path.write_text(json.dumps(original_config))
        # A matching owner/profile in another home cannot borrow this store.
        (rt.homes["b"] / "config.yaml").write_text(json.dumps(original_config))
        for label in ("a", "b", "a"):
            response = rt.call("command.receipt", label, conversation_id=specialist, command_id="same-command")
            if label == "a":
                assert response["result"]["receipt"] == expected
            else:
                assert response["error"]["data"]["code"] == "session_not_found"
        own_b = rt.stores["b"]
        rt.stores["b"] = db
        try:
            assert rt.call("command.receipt", "b", conversation_id=specialist, command_id="same-command")["error"]["data"]["code"] == "identity_mismatch"
        finally:
            rt.stores["b"] = own_b
        assert not rt.server._sessions
    assert _durable_rows(db) == before
    db.patch_session_model_config(specialist, {"agent_identity": None})
    assert call()["error"]["data"]["code"] == "identity_mismatch"


def test_real_stdio_restart_reads_receipts_with_provider_construction_forbidden(runtime, tmp_path):
    rt = runtime
    sid = rt.call("create", idempotency_key="stdio-recovery")["result"]["conversation"]["conversation_id"]
    db = rt.stores["a"]
    receipts = {status: _seed_command(db, sid, status, status) for status in ("accepted", "claimed", "completed")}
    config = json.loads((rt.homes["a"] / "config.yaml").read_text())
    config.update(mcp_servers={}, model={"default": "fixture-unconfigured", "provider": "openai"})
    (rt.homes["a"] / "config.yaml").write_text(json.dumps(config))
    env = {key: value for key, value in os.environ.items()
           if not any(part in key for part in ("API_KEY", "TOKEN", "SECRET", "PASSWORD"))}
    env.update(HERMES_HOME=str(rt.homes["a"]), HERMES_RUNTIME_DIR=str(tmp_path / "runtime"),
               HOME=str(tmp_path), HERMES_TEST_ISOLATION="1")
    sentinel = tmp_path / "forbidden-receipt-effect"
    bootstrap = '''
from pathlib import Path
from tui_gateway import entry, server
from run_agent import AIAgent
from hermes_state import SessionDB

def forbidden(*args, **kwargs):
    Path(SENTINEL).write_text("forbidden effect")
    raise AssertionError("Read-only receipt reached execution or mutation")

for name in ("_schedule_agent_build", "_make_agent", "_submit_runtime_prompt", "_run_prompt_submit"):
    setattr(server, name, forbidden)
AIAgent.__init__ = forbidden

def guard(original):
    def checked(*args, **kwargs):
        if server._current_rpc_method.get() == "runtime.conversation.command.receipt":
            forbidden()
        return original(*args, **kwargs)
    return checked
for name in ("_execute_write", "_execute_transcript_write", "claim_runtime_command", "submit_runtime_command"):
    setattr(SessionDB, name, guard(getattr(SessionDB, name)))
server._emit = guard(server._emit)
entry.main()
'''.replace("SENTINEL", repr(str(sentinel)))
    repo = Path(__file__).resolve().parents[2]
    for attempt in range(2):
        if attempt:
            rt.call("archive", conversation_id=sid, idempotency_key="archive", expected_revision=1, archived=True)
            db.end_session(sid, "done")
        before = _durable_rows(db)
        with (tmp_path / f"receipt-stderr-{attempt}.log").open("w") as diagnostics:
            process = subprocess.Popen([sys.executable, "-u", "-c", bootstrap], cwd=repo, env=env,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=diagnostics, text=True)
            frames = queue.Queue()
            def read():
                for line in process.stdout:
                    frames.put(json.loads(line))
            reader = threading.Thread(target=read, daemon=True)
            reader.start()
            def receive(request_id=None):
                while True:
                    frame = frames.get(timeout=30)
                    if frame.get("params", {}).get("type") == "gateway.ready":
                        assert request_id is None
                        return frame
                    assert frame.get("method") != "event", frame
                    if frame.get("id") == request_id:
                        assert "error" not in frame, frame
                        return frame["result"]
            def call(method, request_id, **params):
                process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": request_id,
                    "method": "runtime.conversation." + method, "params": {"schema_version": 1, **params}}) + "\n")
                process.stdin.flush()
                return receive(request_id)
            try:
                receive()
                caps = call("capabilities", "caps")
                assert "runtime.conversation.command.receipt" in caps["methods"]
                for status, expected in receipts.items():
                    result = call("command.receipt", status, conversation_id=sid, command_id=status)
                    assert result["found"] and result["receipt"] == expected and result["status"] == status
                    assert "PRIVATE" not in json.dumps(result)
                assert not call("command.receipt", "missing", conversation_id=sid, command_id="missing")["found"]
            finally:
                process.kill()
                process.wait(timeout=10)
                process.stdin.close()
                reader.join(timeout=5)
                process.stdout.close()
        assert not sentinel.exists()
        assert _durable_rows(db) == before
