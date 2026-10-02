"""Gateway writes and actual branch handlers retain private replay state."""
from copy import deepcopy
from datetime import datetime
from unittest.mock import MagicMock

import pytest


def _history():
    return [{"role": "user", "content": "hello"}, {
        "role": "assistant", "content": "answer", "reasoning_details": [
            {"type": "thinking", "thinking": "private", "signature": "signed-α"}],
        "anthropic_content_blocks": [
            {"type": "thinking", "thinking": "private", "signature": "signed-α"},
            {"type": "text", "text": "answer"}],
        "bedrock_content_blocks": [{"reasoningContent": {"text": "private", "signature": "signed-α"}},
                                   {"text": "answer"}],
        "codex_reasoning_items": [{"type": "reasoning", "id": "rs_exact", "encrypted_content": "opaque+="}],
        "codex_message_items": [{"type": "message", "id": "msg_exact", "phase": "final_answer",
                                 "role": "assistant", "content": [{"type": "output_text", "text": "answer"}]}],
    }]


def _assert_protocol(actual, expected):
    for key in ("anthropic_content_blocks", "bedrock_content_blocks", "reasoning_details",
                "codex_reasoning_items", "codex_message_items"):
        assert actual[key] == expected[key]
    assert "provider_sidecar" not in actual


def test_gateway_append_reload_and_replay_keep_private_state(tmp_path, monkeypatch):
    import hermes_state
    from gateway.config import GatewayConfig
    from gateway.session import SessionStore
    from gateway.run import _build_replay_entry
    from tui_gateway import server
    monkeypatch.setattr(hermes_state, "DEFAULT_DB_PATH", tmp_path / "state.db")
    store = SessionStore(sessions_dir=tmp_path, config=GatewayConfig())
    history = _history()
    original = deepcopy(history)
    try:
        store._db.create_session("gateway", source="test")
        for message in history:
            store.append_to_transcript("gateway", message)
        restored = store.load_transcript("gateway")
        _assert_protocol(restored[1], original[1])
        projected = _build_replay_entry("assistant", restored[1]["content"], restored[1])
        _assert_protocol(projected, original[1])
        public = server._history_to_messages(restored)
        assert all(not ({"provider_sidecar", "anthropic_content_blocks", "bedrock_content_blocks"} & set(row))
                   for row in public)
    finally:
        store._db.close()


@pytest.mark.parametrize("surface", ["gateway", "cli", "tui"])
@pytest.mark.parametrize("raw_rows", [False, True])
def test_branch_writers_preserve_live_and_raw_protocol_carriers(tmp_path, monkeypatch, surface, raw_rows):
    from hermes_state import SessionDB
    from agent.provider_capabilities import encode_protocol_sidecar
    history = _history()
    expected = deepcopy(history[1])
    if raw_rows:
        # Raw authorized exports carry the sidecar, while normal replay expands it.
        history[1]["provider_sidecar"] = encode_protocol_sidecar(history[1])
        history[1].pop("anthropic_content_blocks")
        history[1].pop("bedrock_content_blocks")
    with SessionDB(tmp_path / "state.db") as db:
        db.create_session("parent", source="test")
        if surface == "gateway":
            from gateway.slash_commands_session import _branch_row
            db.create_session("child", source="test", parent_session_id="parent")
            db.append_messages_batch("child", [_branch_row(row) for row in history])
            child = "child"
        elif surface == "cli":
            from cli import HermesCLI
            cli = MagicMock(_agent_running=False, _session_db=db, session_id="parent", model="fixture",
                            max_turns=3, reasoning_config={}, session_start=datetime.now(), agent=None,
                            conversation_history=history)
            HermesCLI._handle_branch_command(cli, "/branch protocol child")
            child = cli.session_id
            assert child != "parent"
        else:
            from tui_gateway import server
            from tui_gateway.methods_session import _BRANCH_COPY_FIELDS
            server._persist_branch(db, "child", "parent", "Protocol child", history, source="desktop",
                                   cwd=str(tmp_path), profile_name="default", model="fixture",
                                   copy_fields=_BRANCH_COPY_FIELDS)
            child = "child"
        _assert_protocol(db.get_messages_as_conversation(child)[1], expected)
