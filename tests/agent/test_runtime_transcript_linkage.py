"""Explicit transcript provenance uses real loop/SQLite transactions, never text matching."""
import json

import pytest

from agent.identity_lifecycle import agent_runtime_scope
from agent.runtime_commands import bind_submitted_command
from hermes_state import SessionDB
from tests.agent.test_runtime_commands import (  # noqa: F401
    real_agent, _envelope, _response, _submit, _run_submitted,
)


def test_real_loop_links_user_assistant_and_tools_and_duplicate_stays_read_only(real_agent):
    agent, db = real_agent, real_agent._session_db
    receipt = _submit(agent, _envelope())
    accepted = db.read_runtime_command_receipt("session", "command")
    assert accepted["accepted_input"] == {"state": "accepted", "message_id": None}
    assert accepted["messages"] == []
    agent.client._fixture_create.side_effect = [_response(True), _response()]
    result = _run_submitted(agent, receipt)
    assert result["final_response"] == "recorded answer"
    committed = db.read_runtime_command_receipt("session", "command")
    links = committed["messages"]
    assert [link["role"] for link in links] == ["user", "assistant", "tool", "assistant"]
    assert [link["kind"] for link in links] == ["input", "output", "output", "output"]
    assert committed["accepted_input"] == {"state": "committed", "message_id": links[0]["message_id"]}
    with db._read_ctx() as conn:
        rows = conn.execute("SELECT message_uid,role FROM messages WHERE session_id='session' AND role IN ('user','assistant','tool') ORDER BY id").fetchall()
    assert [(x["message_id"], x["role"]) for x in links] == [(x["message_uid"], x["role"]) for x in rows]
    assert "recorded answer" not in json.dumps(committed)
    assert "recorded work" not in json.dumps(committed)
    _run_submitted(agent, receipt)
    assert db.read_runtime_command_receipt("session", "command") == committed
    assert agent.client._fixture_create.call_count == 2
    first = db.read_runtime_command_receipt("session", "command", message_limit=2)
    second = db.read_runtime_command_receipt("session", "command", message_limit=2, message_cursor=first["next_message_cursor"])
    assert first["messages"] + second["messages"] == links
    assert first["messages_has_more"] and not second["messages_has_more"]


def test_equal_text_new_commands_keep_distinct_input_identity_and_old_output_provenance(real_agent):
    agent, db = real_agent, real_agent._session_db
    first = _submit(agent, _envelope("first"))
    result = _run_submitted(agent, first)
    before = db.read_runtime_command_receipt("session", "first")["messages"]
    second = _submit(agent, _envelope("second"))
    with bind_submitted_command(agent, second):
        agent.run_conversation("hello", conversation_history=result["messages"])
    after = db.read_runtime_command_receipt("session", "second")["messages"]
    assert [x["role"] for x in before] == ["user", "assistant"]
    assert [x["role"] for x in after] == ["user", "assistant"]
    assert {x["message_id"] for x in before}.isdisjoint(x["message_id"] for x in after)
    assert db.read_runtime_command_receipt("session", "first")["messages"] == before
    requests = [json.loads(call.args[0].content) for call in agent.client._fixture_create.call_args_list]
    assert requests[0]["messages"][0] == requests[1]["messages"][0]
    assert all("_runtime_command" not in json.dumps(request) for request in requests)


def test_accepted_input_survives_restart_and_claim_without_duplicate_row(real_agent):
    from tui_gateway import server
    agent, original = real_agent, real_agent._session_db
    receipt = _submit(agent, _envelope())
    session = {"agent": agent, "session_key": "session", "profile_home": agent.runtime_context.profile_home}
    with agent_runtime_scope(agent.runtime_context), bind_submitted_command(agent, receipt):
        server._persist_submit_user_row(session, "hello", None)
    before = original.read_runtime_command_receipt("session", "command")
    assert before["status"] == "accepted" and before["accepted_input"]["state"] == "committed"
    uid = before["accepted_input"]["message_id"]
    reopened = SessionDB(original.db_path)
    agent._session_db = reopened
    try:
        session.pop("_submit_user_row")
        with agent_runtime_scope(agent.runtime_context), bind_submitted_command(agent, receipt):
            server._persist_submit_user_row(session, "hello", None)
            assert session["_submit_user_row"]["message_uid"] == uid
            server._adopt_submit_user_row(session, agent, "hello", "hello")
            agent.run_conversation("hello")
        committed = reopened.read_runtime_command_receipt("session", "command")
        assert committed["accepted_input"]["message_id"] == uid
        with reopened._read_ctx() as conn:
            assert conn.execute("SELECT COUNT(*) FROM messages WHERE role='user' AND message_uid=?", (uid,)).fetchone()[0] == 1
        assert agent.client._fixture_create.call_count == 1
    finally:
        agent._session_db = original
        reopened.close()


def test_link_failure_rolls_back_input_and_row_markers(real_agent, monkeypatch):
    from hermes_state_runtime_messages import append_input
    from hermes_state_runtime import RuntimeStoreError
    import hermes_state_runtime_messages as linkage
    agent, db = real_agent, real_agent._session_db
    receipt = _submit(agent, _envelope())
    message = {"role": "user", "content": "hello"}
    record = linkage.record_links
    def fail_after_link(*args, **kwargs):
        record(*args, **kwargs)
        raise RuntimeError("forced transaction rollback")
    with agent_runtime_scope(agent.runtime_context), bind_submitted_command(agent, receipt):
        with monkeypatch.context() as patch:
            patch.setattr(linkage, "record_links", fail_after_link)
            with pytest.raises(RuntimeError, match="forced transaction"):
                append_input(db, "session", "command", message)
        assert "_row_id" not in message
        assert db.read_runtime_command_receipt("session", "command")["messages"] == []
        assert db.get_messages("session") == []
        saved = append_input(db, "session", "command", message)
        assert db.read_runtime_command_receipt("session", "command")["accepted_input"]["message_id"] == saved["message_uid"]
    with pytest.raises(RuntimeStoreError) as denied:
        append_input(db, "session", "command", {"role": "user", "content": "hello"})
    assert denied.value.code == "message_link_authority"


def test_unanswered_accepted_input_is_not_reattributed_when_next_turn_merges_users(real_agent):
    from hermes_state_runtime_messages import append_input
    agent, db = real_agent, real_agent._session_db
    first = _submit(agent, _envelope("first", text="Earlier unanswered input"))
    with agent_runtime_scope(agent.runtime_context), bind_submitted_command(agent, first):
        initial = append_input(db, "session", "first", {"role": "user", "content": "Earlier unanswered input"})
    history = db.get_messages_as_conversation("session", include_row_ids=True)
    second = _submit(agent, _envelope("second", text="Follow up"))
    with bind_submitted_command(agent, second):
        result = agent.run_conversation("Follow up", conversation_history=history)
    assert result["final_response"] == "recorded answer"
    first_link = db.read_runtime_command_receipt("session", "first")["accepted_input"]
    second_link = db.read_runtime_command_receipt("session", "second")["accepted_input"]
    assert first_link["message_id"] == initial["message_uid"]
    assert second_link["state"] == "committed"
    assert first_link["message_id"] != second_link["message_id"]


def test_deleted_committed_input_cannot_be_recreated_by_retry(real_agent):
    from hermes_state_runtime_messages import append_input
    from hermes_state_runtime import RuntimeStoreError
    agent, db = real_agent, real_agent._session_db
    receipt = _submit(agent, _envelope())
    with agent_runtime_scope(agent.runtime_context), bind_submitted_command(agent, receipt):
        saved = append_input(db, "session", "command", {"role": "user", "content": "hello"})
        db._execute_write(lambda conn: conn.execute("DELETE FROM messages WHERE id=?", (saved["_row_id"],)))
        with pytest.raises(RuntimeStoreError) as denied:
            append_input(db, "session", "command", {"role": "user", "content": "hello"})
        assert denied.value.code == "message_link_conflict"
    assert db.get_messages("session") == []
    assert db.read_runtime_command_receipt("session", "command")["accepted_input"]["message_id"] == saved["message_uid"]


def test_restart_adopts_expanded_preclaim_row_by_command_uid_not_text(real_agent):
    from tui_gateway import server
    agent, db = real_agent, real_agent._session_db
    receipt = _submit(agent, _envelope())
    session = {"agent": agent, "session_key": "session", "profile_home": agent.runtime_context.profile_home}
    expanded = "hello\nExpanded attachment text"
    with agent_runtime_scope(agent.runtime_context), bind_submitted_command(agent, receipt):
        server._persist_submit_user_row(session, "hello", None)
        server._adopt_submit_user_row(session, agent, expanded, "hello")
        uid = agent._pending_cli_user_message["message_uid"]
        agent._pending_cli_user_message = None  # interrupted before claim
        server._persist_submit_user_row(session, "hello", None)
        assert session["_submit_user_row"]["content"] == expanded
        server._adopt_submit_user_row(session, agent, expanded, "hello")
        assert agent._pending_cli_user_message["message_uid"] == uid
        result = agent.run_conversation(expanded, persist_user_message=expanded)
    assert result["final_response"] == "recorded answer"
    assert db.read_runtime_command_receipt("session", "command")["accepted_input"]["message_id"] == uid
    with db._read_ctx() as conn:
        assert conn.execute("SELECT COUNT(*) FROM messages WHERE role='user'").fetchone()[0] == 1


def test_stale_claim_cannot_commit_transcript_link_or_output(real_agent):
    from agent.runtime_commands import claim_turn_command, read_command_state, bind_runtime_run, reset_runtime_run
    from agent.turn_facade_lease import admit_durable_turn_lease
    from hermes_state_runtime import RuntimeStoreError
    agent, db = real_agent, real_agent._session_db
    _submit(agent, _envelope())
    with agent_runtime_scope(agent.runtime_context):
        admission = admit_durable_turn_lease(agent, session_id="session", relay_turn_id="link-owner",
            task_context={"session_id": "session", "platform": "cli"}, conversation_history=[])
        run = claim_turn_command(agent, read_command_state(agent, "command"), admission.lease)
        token = bind_runtime_run(run)
        admission.lease.release()
        assert db.try_acquire_session_turn_lease("session", "replacement")
        try:
            with pytest.raises(RuntimeStoreError) as denied:
                db.append_messages_batch("session", [{"role": "assistant", "content": "Stale output",
                                                     "_runtime_command_id": "command"}])
            assert denied.value.code == "stale_owner"
            assert db.get_messages("session") == []
            assert db.read_runtime_command_receipt("session", "command")["messages"] == []
        finally:
            reset_runtime_run(token, run)
            db.release_session_turn_lease("session", "replacement")


def test_accepted_link_authority_is_pinned_before_mutable_agent_session_changes(real_agent):
    from hermes_state_runtime_messages import append_input
    from hermes_state_runtime import RuntimeStoreError
    agent, db = real_agent, real_agent._session_db
    receipt = _submit(agent, _envelope())
    db.create_session("unrelated", source="cli")
    with agent_runtime_scope(agent.runtime_context), bind_submitted_command(agent, receipt):
        agent.session_id = "unrelated"
        try:
            with pytest.raises(RuntimeStoreError) as denied:
                append_input(db, "unrelated", "command", {"role": "user", "content": "hello"})
            assert denied.value.code == "identity_mismatch"
            assert db.get_messages("unrelated") == []
        finally:
            agent.session_id = "session"
