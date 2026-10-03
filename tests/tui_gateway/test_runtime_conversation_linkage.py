"""Canonical safe history recovers authoritative links without private model scaffolding."""
import json

import pytest

from tests.tui_gateway.test_runtime_conversations_rpc import runtime, config_for  # noqa: F401


def test_explicit_links_survive_compression_and_receipts_are_bounded(runtime):
    from types import SimpleNamespace
    from agent.agent_identity import resolve_agent_context
    from agent.identity_lifecycle import agent_runtime_scope
    from agent.runtime_commands import RuntimeRun, bind_runtime_run, reset_runtime_run, bind_submitted_command
    from hermes_state_runtime_messages import append_input

    sid = runtime.call("create", idempotency_key="links")["result"]["conversation"]["conversation_id"]
    db = runtime.stores["a"]
    context = resolve_agent_context(config_for("a"), session_id=sid, profile_home=runtime.homes["a"])
    agent = SimpleNamespace(runtime_context=context, _session_db=db, session_id=sid)
    actor = {key: getattr(context.identity, key) for key in ("principal_id", "profile_id", "agent_id")}
    command = {"schema_version": 1, "command_id": "one", "idempotency_key": "one", "expected_revision": None,
               "operation": "submit", "payload": {"text": "same text"}, "identity_binding": actor}
    receipt = db.submit_runtime_command(sid, actor, command)
    # An unrelated ordinary row of identical text must remain unlinked.
    db.append_message(sid, "user", "same text", message_uid="unlinked")
    with agent_runtime_scope(context), bind_submitted_command(agent, receipt):
        user = append_input(db, sid, "one", {"role": "user", "content": "same text"})
    assert db.try_acquire_session_turn_lease(sid, "writer")
    generation = db.get_session_turn_lease(sid)["generation"]
    db.claim_runtime_command(sid, "one", holder="writer", generation=generation)
    run = RuntimeRun(agent, db, sid, "one", receipt["run_id"], "writer", generation, context)
    outputs = [{"role": "assistant", "content": "Answer", "reasoning": "PRIVATE REASONING",
                "_runtime_command_id": "one"},
               {"role": "tool", "content": "PRIVATE TOOL", "_runtime_command_id": "one"}]
    token = bind_runtime_run(run)
    try:
        with agent_runtime_scope(context):
            db.append_messages_batch(sid, outputs, turn_lease_holder="writer")
    finally:
        reset_runtime_run(token, run)
    db.finish_runtime_command(sid, "one", holder="writer", generation=generation)
    db.release_session_turn_lease(sid, "writer")
    db.publish_compression_child(parent_session_id=sid, child_session_id="tip", source="web",
        messages=[user, *outputs], model_config={"agent_identity": context.identity.to_record()}, require_compression_lease=False)
    agent.session_id = "tip"
    page = runtime.call("history", conversation_id=sid)["result"]
    assert {row["message_id"]: row["command_id"] for row in page["messages"]} == {
        "unlinked": None, user["message_uid"]: "one", outputs[0]["message_uid"]: "one"}
    assert all(row["physical_session_id"] == "tip" for row in page["messages"] if row["command_id"])
    assert "PRIVATE" not in json.dumps(page)
    state = runtime.call("command.receipt", conversation_id=sid, command_id="one", message_limit=1)["result"]
    assert state["accepted_input"] == {"state": "committed", "message_id": user["message_uid"]}
    assert state["receipt"] == receipt and state["status"] == "completed"
    assert "PRIVATE" not in json.dumps(state)
    all_links = state["messages"]
    first_cursor = state["next_message_cursor"]
    while state["messages_has_more"]:
        assert len(state["messages"]) == 1
        state = runtime.call("command.receipt", conversation_id=sid, command_id="one", message_limit=1,
                             message_cursor=state["next_message_cursor"])["result"]
        all_links.extend(state["messages"])
    assert {link["message_id"] for link in all_links} == {row["message_uid"] for row in [user, *outputs]}
    for invalid in ("not-base64", "e30", "W10", "MQ"):
        rejected = runtime.call("command.receipt", conversation_id=sid, command_id="one", message_cursor=invalid)
        assert rejected["error"]["data"]["code"] == "invalid_cursor"
    other = runtime.call("create", idempotency_key="unrelated-links")["result"]["conversation"]["conversation_id"]
    for conversation_id, command_id in ((other, "one"), (sid, "another-command")):
        rejected = runtime.call("command.receipt", conversation_id=conversation_id,
                                command_id=command_id, message_cursor=first_cursor)
        assert rejected["error"]["data"]["code"] == "invalid_cursor"
    caps = runtime.call("capabilities")["result"]
    assert caps["identity"]["role"] == "primary"
    assert caps["identity"]["memory_backend"] == "personal_mcp"
    assert caps["command_message_linkage"] == "explicit"


def test_summary_carrier_only_user_text_is_safe_and_exactly_reassembled(runtime):
    from agent.context_compressor import SUMMARY_PREFIX, _SUMMARY_END_MARKER, _MERGED_PRIOR_CONTEXT_HEADER, _MERGED_SUMMARY_DELIMITER

    sid = runtime.call("create", idempotency_key="carriers")["result"]["conversation"]["conversation_id"]
    db = runtime.stores["a"]
    # Empty/pure handoffs still advance a bounded page; they never leak or hide
    # a later real input merely because the first candidate page is all scaffold.
    for _ in range(4):
        db.append_message(sid, "user", SUMMARY_PREFIX + "\nPRIVATE SUMMARY\n" + _SUMMARY_END_MARKER,
                          _compressed_summary=True, display_kind="hidden")
    human = ("🦊A\x00B" * 11000) + " done"
    db.append_message(sid, "user", SUMMARY_PREFIX + "\nPRIVATE SUMMARY\n" + _SUMMARY_END_MARKER + "\n\n" + human,
                      _compressed_summary=True, display_kind="hidden", message_uid="carrier")
    db.append_message(sid, "user", _MERGED_PRIOR_CONTEXT_HEADER + "\nPrevious human turn\n\n" + _MERGED_SUMMARY_DELIMITER
                      + "\nPRIVATE SUMMARY\n" + _SUMMARY_END_MARKER, _compressed_summary=True, message_uid="merged")
    db.append_message(sid, "assistant", "PRIVATE SUMMARY", _compressed_summary=True)
    db.append_message(sid, "assistant", SUMMARY_PREFIX + "\nPRIVATE LEGACY SUMMARY\n" + _SUMMARY_END_MARKER)
    db.append_message(sid, "user", SUMMARY_PREFIX + "\nPRIVATE LEGACY SUMMARY\n" + _SUMMARY_END_MARKER)
    db.append_message(sid, "user", "PRIVATE HIDDEN USER", display_kind="hidden")
    db.append_message(sid, "user", [{"type": "text", "text": SUMMARY_PREFIX + "\nPRIVATE STRUCTURED SUMMARY\n"
        + _SUMMARY_END_MARKER + "\n\nStructured human turn"}, {"type": "thinking", "text": "PRIVATE REASONING"},
        {"type": "image_url", "image_url": {"url": "PRIVATE IMAGE"}}], message_uid="structured")
    db.append_message(sid, "user", "PRIVATE MODEL ONLY", display_metadata={"model_only": True})
    cursor, chunks = None, []
    for _ in range(30):
        page = runtime.call("history", conversation_id=sid, limit=1, cursor=cursor)["result"]
        chunks.extend(page["messages"])
        assert "PRIVATE" not in json.dumps(page)
        if not page["has_more"]:
            break
        assert page["next_cursor"] != cursor
        cursor = page["next_cursor"]
    else:
        pytest.fail("Summary-only pages did not advance")
    carrier = [chunk for chunk in chunks if chunk["message_id"] == "carrier"]
    assert ''.join(chunk["text"] for chunk in carrier) == human.replace('\x00', '')
    assert carrier[0]["text_offset"] == 0 and carrier[-1]["next_text_offset"] == len(human.encode())
    assert all(a["next_text_offset"] == b["text_offset"] for a, b in zip(carrier, carrier[1:]))
    assert all(chunk["text_sanitized"] for chunk in carrier[:-1])
    assert [chunk["text"] for chunk in chunks if chunk["message_id"] == "merged"] == ["Previous human turn"]
    structured = [chunk for chunk in chunks if chunk["message_id"] == "structured"]
    assert [chunk["text"] for chunk in structured] == ["Structured human turn"]
    assert structured[0]["non_text_omitted"]
    assert {chunk["message_id"] for chunk in chunks} == {"carrier", "merged", "structured"}


def test_link_table_upgrade_preserves_transcript_and_does_not_guess_legacy_links(runtime):
    from hermes_state import SessionDB
    from hermes_state_common import SCHEMA_VERSION
    sid = runtime.call("create", idempotency_key="migration")["result"]["conversation"]["conversation_id"]
    db = runtime.stores["a"]
    db.append_message(sid, "user", "Legacy", message_uid="legacy-stable")
    def downgrade(conn):
        conn.execute("DROP TABLE runtime_command_messages")
        conn.execute("UPDATE schema_version SET version=?", (SCHEMA_VERSION - 1,))
    db._execute_write(downgrade)
    db.close()
    runtime.stores["a"] = reopened = SessionDB(runtime.homes["a"] / "state.db")
    page = runtime.call("history", conversation_id=sid)["result"]
    assert page["messages"][0]["message_id"] == "legacy-stable"
    assert page["messages"][0]["command_id"] is None
    with reopened._read_ctx() as conn:
        assert conn.execute("SELECT COUNT(*) FROM runtime_command_messages").fetchone()[0] == 0
