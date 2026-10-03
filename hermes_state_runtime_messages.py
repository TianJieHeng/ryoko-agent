"""Explicit command/message identity links, committed with transcript writes.

The message payload carries no authority. A link requires the thread's accepted
submission or claimed RuntimeRun and is checked again inside the writer txn.
Copies keep their UID and therefore their original attribution across compaction.
"""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

from hermes_state_runtime import _json, _require

RUNTIME_COMMAND_KEY = "_runtime_command_id"
RUNTIME_INPUT_KEY = "_runtime_command_input"
RUNTIME_MESSAGE_PAGE = 100


def bound_command(db, command_id=None):
    from agent.runtime_commands import _PENDING, _RUN
    run = _RUN.get()
    pending = _PENDING.get()
    owner = pending if pending is not None else run
    if owner is None or (command_id is not None and owner.command_id != command_id):
        return None
    agent = owner.agent
    owner_db = getattr(agent, "_session_db", None)
    if owner_db is None or Path(owner_db.db_path).resolve() != Path(db.db_path).resolve():
        return None
    return owner


def record_links(db, conn, session_id, messages, *, new_rows=None):
    """Called only within a transcript transaction, after insert/row-addressed repair."""
    for msg in messages:
        command_id = msg.get(RUNTIME_COMMAND_KEY)
        if command_id is None:
            continue
        owner = bound_command(db, command_id)
        _require(owner is not None, "message_link_authority", "Message link requires its bound command")
        sid = db._runtime_session_on_conn(conn, session_id)
        _require(sid == db._runtime_session_on_conn(conn, owner.session_id),
                 "identity_mismatch", "Message link belongs to another conversation")
        command = db._runtime_command_on_conn(conn, sid, command_id)
        is_input = msg.get(RUNTIME_INPUT_KEY) is True
        role = msg.get("role")
        _require(command is not None and command["run_id"] == owner.run_id
                 and json.loads(command["command_json"])["operation"] == "submit",
                 "message_link_authority", "Message link requires a submit command")
        _require((is_input and role == "user") or (not is_input and role in {"assistant", "tool"}),
                 "message_link_authority", "Invalid command message role")
        if hasattr(owner, "holder"):
            db._runtime_fence_on_conn(conn, sid, owner.holder, owner.generation)
            _require(command["status"] == "claimed" and command["claimed_holder"] == owner.holder
                     and command["claimed_generation"] == owner.generation,
                     "stale_owner", "Message link requires the claimed writer")
        else:
            _require(is_input and command["status"] == "accepted", "stale_owner", "Submission is no longer accepted")
        row = conn.execute("SELECT id,message_uid,role FROM messages WHERE id=? AND session_id=?",
                           (msg.get("_row_id"), session_id)).fetchone()
        _require(row is not None and row["message_uid"] and row["role"] == role,
                 "message_link_authority", "Message link requires a committed row identity")
        old = conn.execute("SELECT command_id,kind FROM runtime_command_messages WHERE session_id=? AND message_uid=?",
                           (sid, row["message_uid"])).fetchone()
        kind = "input" if is_input else "output"
        if not is_input and old is None:
            # A legacy row or a replayed clone is not this run's output. Only a
            # fresh occurrence at its first writer transaction establishes provenance.
            if (new_rows is not None and not any(msg is item for item in new_rows)) or conn.execute(
                    "SELECT 1 FROM messages WHERE message_uid=? AND id<? LIMIT 1",
                    (row["message_uid"], row["id"])).fetchone() is not None:
                continue
        # Never reattribute a replayed row to a later command, even with identical text.
        _require(old is None or (old["command_id"] == command_id and old["kind"] == kind),
                 "message_link_conflict", "Message already belongs to another command")
        if is_input:
            prior_input = conn.execute("SELECT message_uid FROM runtime_command_messages WHERE session_id=? AND command_id=? AND kind='input'",
                                       (sid, command_id)).fetchone()
            _require(prior_input is None or prior_input["message_uid"] == row["message_uid"],
                     "message_link_conflict", "Command already has its committed input")
        conn.execute("INSERT INTO runtime_command_messages(session_id,command_id,message_uid,role,kind,first_row_id) "
                     "VALUES(?,?,?,?,?,?) ON CONFLICT(session_id,message_uid) DO NOTHING", (sid, command_id, row["message_uid"], role, kind, row["id"]))


def append_input(db, session_id, command_id, message):
    """Persist the accepted user input once, including restart before claim.

    The prior row is selected by its command/UID link, never by mutable text.
    """
    def write(conn):
        sid = db._runtime_session_on_conn(conn, session_id)
        from hermes_state_compression import _CHAIN_STEP_SQL
        chain = [sid]
        for _ in range(1000):
            child = conn.execute(_CHAIN_STEP_SQL, (chain[-1],)).fetchone()
            if child is None:
                break
            _require(child["id"] not in chain, "lineage_invalid", "Compression lineage contains a cycle")
            chain.append(child["id"])
        else:
            _require(False, "lineage_limit", "Compression lineage exceeds the supported bound")
        marks = ','.join('?' for _ in chain)
        previous = conn.execute("SELECT m.* FROM runtime_command_messages l JOIN messages m "
            "ON m.message_uid=l.message_uid WHERE l.session_id=? AND l.command_id=? AND l.kind='input' "
            f"AND m.session_id IN ({marks}) AND (m.active=1 OR m.compacted=1) ORDER BY m.active DESC,m.id DESC LIMIT 1",
            (sid, command_id, *chain)).fetchone()
        if previous is not None:
            # Recheck authority even when only adopting the already-committed occurrence.
            copy = {**message, "_row_id": previous["id"], RUNTIME_COMMAND_KEY: command_id, RUNTIME_INPUT_KEY: True}
            record_links(db, conn, previous["session_id"], [copy])
            restored = db._row_to_message_dict(previous, warn_context="runtime input", summary_flag=True)
            restored["_row_id"] = previous["id"]
            restored[RUNTIME_COMMAND_KEY], restored[RUNTIME_INPUT_KEY] = command_id, True
            return restored
        db._check_transcript_write_guards(conn, session_id, None)
        message[RUNTIME_COMMAND_KEY], message[RUNTIME_INPUT_KEY] = command_id, True
        count, tools = db._insert_message_rows(conn, session_id, [message])
        record_links(db, conn, session_id, [message])
        db._bump_session_counters(conn, session_id, count, tools, unit=False)
        return dict(message)
    return db._execute_transcript_write(write, [message], patience_s=db._TRANSCRIPT_WRITE_PATIENCE_S)


def read_links(conn, sid, command, *, cursor=None, limit=100):
    _require(type(limit) is int and 1 <= limit <= RUNTIME_MESSAGE_PAGE, "invalid_command", "Invalid message page size")
    command_id = command["command_id"] if command is not None else ""
    scope = hashlib.sha256(_json([sid, command_id]).encode()).hexdigest()
    after = 0
    if cursor is not None:
        try:
            value = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
            if len(value) != 2 or value[0] != scope or type(value[1]) is not int or value[1] < 0:
                raise ValueError
            after = value[1]
        except (ValueError, TypeError, UnicodeError, KeyError, IndexError):
            _require(False, "invalid_cursor", "Cursor does not belong to this command")
    rows = conn.execute("SELECT message_uid,role,kind,first_row_id FROM runtime_command_messages "
                        "WHERE session_id=? AND command_id=? AND first_row_id>? ORDER BY first_row_id LIMIT ?",
                        (sid, command_id, after, limit + 1)).fetchall()
    more, rows = len(rows) > limit, rows[:limit]
    input_row = conn.execute("SELECT message_uid FROM runtime_command_messages WHERE session_id=? AND command_id=? AND kind='input'",
                             (sid, command_id)).fetchone()
    accepted_input = None
    if command is not None and json.loads(command["command_json"])["operation"] == "submit":
        accepted_input = {"state": "committed" if input_row is not None else "accepted",
                          "message_id": input_row["message_uid"] if input_row is not None else None}
    return {"accepted_input": accepted_input,
            "messages": [{"message_id": r["message_uid"], "role": r["role"], "kind": r["kind"], "committed": True} for r in rows],
            "next_message_cursor": base64.urlsafe_b64encode(_json([scope, rows[-1]["first_row_id"]]).encode()).decode().rstrip("=") if more else None,
            "messages_has_more": more}
