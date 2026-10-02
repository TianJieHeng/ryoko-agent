"""Provider-required opaque blocks survive the actual flush, store and replay path."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from agent.chat_completion_helpers import build_assistant_message
from agent.provider_capabilities import UnsupportedProviderState
from agent.session_persistence import _db_flush_row
from agent.transports import get_transport
from hermes_state import SessionDB


class _Agent:
    verbose_logging = False
    reasoning_callback = None
    stream_delta_callback = None
    _stream_callback = None
    _extract_reasoning = staticmethod(lambda message: getattr(message, "reasoning", None))
    _strip_think_blocks = staticmethod(lambda text: text)
    _needs_thinking_reasoning_pad = staticmethod(lambda: False)
    _split_responses_tool_id = staticmethod(lambda value: (value, None))
    _deterministic_call_id = staticmethod(lambda name, args, index: f"call-{index}")
    _derive_responses_function_call_id = staticmethod(lambda value, response_item_id=None: value)


def _anthropic():
    return SimpleNamespace(content=[
        SimpleNamespace(type="thinking", thinking="first", signature="sig-α/+=\n"),
        SimpleNamespace(type="tool_use", id="toolu_first", name="inspect", input={"path": "α.txt"}),
        SimpleNamespace(type="thinking", thinking="second", signature="sig-second"),
        SimpleNamespace(type="tool_use", id="toolu_second", name="inspect", input={"path": "β.txt"}),
    ], stop_reason="tool_use", usage=None)


def _bedrock():
    return {"output": {"message": {"role": "assistant", "content": [
        {"reasoningContent": {"redactedContent": b"\x00opaque\xff"}},
        {"toolUse": {"toolUseId": "toolu_first", "name": "inspect", "input": {"path": "α.txt"}}},
        {"reasoningContent": {"reasoningText": {"text": "signed", "signature": "sig-second"}}},
        {"toolUse": {"toolUseId": "toolu_second", "name": "inspect", "input": {"path": "β.txt"}}},
    ]}}, "stopReason": "tool_use"}


@pytest.mark.parametrize("mode,raw", [("anthropic_messages", _anthropic), ("bedrock_converse", _bedrock)])
def test_native_order_and_media_survive_flush_restart_replay_and_export(tmp_path, mode, raw):
    transport = get_transport(mode)
    native = transport.normalize_response(raw())
    assistant = build_assistant_message(_Agent(), native, "tool_calls")
    media = [{"type": "text", "text": "inspect image"},
             {"type": "image_url", "image_url": {"url": "data:image/png;base64,aGVsbG8="}}]
    messages = [{"role": "user", "content": media}, assistant,
                {"role": "tool", "tool_call_id": "toolu_first", "content": "first result"},
                {"role": "tool", "tool_call_id": "toolu_second", "content": "second result"}]
    original = deepcopy(messages)
    expected_wire = transport.convert_messages(messages)
    path = tmp_path / "state.db"
    with SessionDB(path) as db:
        db.create_session("native", source="test")
        rows = [_db_flush_row(_Agent(), message, False) for message in messages]
        db.append_messages_batch("native", rows)
        assert "provider_sidecar" not in db.get_messages("native")[1]
        assert db.get_messages("native")[0]["content"] == media
        exported = db.export_session("native")
        assert json.loads(exported["messages"][1]["provider_sidecar"])["schema_version"] == 1
    with SessionDB(path) as db:
        restored = db.get_messages_as_conversation("native")
        assert restored[0]["content"] == media
        assert restored[1]["reasoning_details"] == original[1]["reasoning_details"]
        assert restored[1]["tool_calls"] == original[1]["tool_calls"]
        assert transport.convert_messages(restored) == expected_wire
        assert all("provider_sidecar" not in item for item in restored)
        # Repair/replace uses the same serializer, retaining its opaque payload.
        db.replace_messages("native", restored)
        assert transport.convert_messages(db.get_messages_as_conversation("native")) == expected_wire
    with SessionDB(tmp_path / "import.db") as target:
        assert target.import_sessions([exported])["imported"] == 1
        assert transport.convert_messages(target.get_messages_as_conversation("native")) == expected_wire
    assert messages == original


def test_unknown_sidecar_blocks_replay_and_import_without_changing_stored_history(tmp_path):
    with SessionDB(tmp_path / "state.db") as db:
        db.create_session("future", source="test")
        db.append_message("future", "assistant", "visible answer", api_content="\x1ehermes.provider-state:legacy text")
        future = json.dumps({"schema_version": 99, "fields": {"future_private": ["opaque"]}})
        db._execute_write(lambda conn: conn.execute("UPDATE messages SET provider_sidecar = ?", (future,)))
        with pytest.raises(UnsupportedProviderState, match="version"):
            db.get_messages_as_conversation("future")
        assert db.get_messages("future")[0]["content"] == "visible answer"
        exported = db.export_session("future")
        assert exported["messages"][0]["provider_sidecar"] == future
        assert exported["messages"][0]["api_content"] == "\x1ehermes.provider-state:legacy text"
    with SessionDB(tmp_path / "import.db") as target:
        result = target.import_sessions([exported])
        assert not result["ok"]
        assert target.get_session("future") is None


def test_schema33_upgrade_preserves_unversioned_codex_columns_and_legacy_text(tmp_path):
    path = tmp_path / "old.db"
    reasoning = [{"type": "reasoning", "id": "rs_opaque", "encrypted_content": "\x00α+=="}]
    continuation = [{"type": "message", "id": "msg_continuation", "role": "assistant",
                     "content": [{"type": "output_text", "text": "old answer"}]}]
    with SessionDB(path) as db:
        db.create_session("legacy", source="test")
        db.append_message("legacy", "assistant", "old answer", codex_reasoning_items=reasoning,
                          codex_message_items=continuation, api_content="  exact old API bytes\n")
        def old_schema(conn):
            conn.execute("ALTER TABLE messages DROP COLUMN provider_sidecar")
            conn.execute("UPDATE schema_version SET version = 33")
        db._execute_write(old_schema)
    with SessionDB(path) as db:
        restored = db.get_messages_as_conversation("legacy")[0]
        assert restored["codex_reasoning_items"] == reasoning
        assert restored["codex_message_items"] == continuation
        assert restored["api_content"] == "  exact old API bytes\n"
        assert db._read_one("SELECT provider_sidecar FROM messages")[0] is None
