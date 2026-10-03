"""Owner-scoped canonical conversations, separate from legacy attachable sessions.

Authority is a trusted AgentContext, never a principal/profile supplied over RPC.
Metadata receipts survive process restarts. Transcript reads never execute work.
"""
from __future__ import annotations

import base64
import codecs
import hashlib
import json
import re
import time

from hermes_state_runtime import RuntimeStoreError, _identifier, _json, _require, _version

CONVERSATION_MAX_PAGE = 100
CONVERSATION_TEXT_CHARS = 16384
CONVERSATION_TEXT_BYTES = 262144
CONVERSATION_MAX_LINEAGE = 1000
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _conversation_owner(context):
    from agent.runtime_context import AgentContext
    _require(isinstance(context, AgentContext) and context.identity.lifecycle == "stable",
             "identity_required", "A stable trusted owner is required")
    context.validate_profile_home()
    identity = context.identity.to_record()
    keys = ("principal_id", "profile_id", "agent_id", "profile_home_digest")
    return hashlib.sha256(_json({key: identity[key] for key in keys}).encode()).hexdigest()


def _conversation_title(value):
    _require(isinstance(value, str) and len(value) <= 200, "invalid_command", "Invalid title")
    return _CONTROL_CHARS.sub("", value).strip()


def _conversation_cursor(scope, values):
    return base64.urlsafe_b64encode(_json([scope, *values]).encode()).decode().rstrip("=")


def _conversation_decode_cursor(value, scope, size):
    if value is None:
        return None
    try:
        if not isinstance(value, str) or len(value) > 2048:
            raise ValueError
        data = json.loads(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)))
        if not isinstance(data, list) or len(data) != size + 1 or data[0] != scope:
            raise ValueError
        return data[1:]
    except (ValueError, TypeError, UnicodeError) as exc:
        raise RuntimeStoreError("invalid_cursor", "Cursor does not belong to this query") from exc


def _conversation_summary(row):
    return {"conversation_id": row["conversation_id"], "title": row["display_title"] if row["display_title"] is not None else row["title"] or "",
            "archived": bool(row["archived"]), "revision": row["revision"],
            "created_at": row["created_at"], "updated_at": row["updated_at"], "source": "web"}


_CONVERSATION_SELECT = ("SELECT c.*,s.title,s.archived,s.model_config FROM runtime_conversations c "
                        "JOIN sessions s ON s.id=c.conversation_id ")


class SessionConversationsMixin:
    def _conversation_owned_on_conn(self, conn, owner, conversation_id):
        _identifier(conversation_id, "conversation_id")
        row = conn.execute(_CONVERSATION_SELECT + "WHERE c.conversation_id=? AND c.owner_key=?",
                           (conversation_id, owner)).fetchone()
        _require(row is not None, "session_not_found", "Conversation not found")
        _version(row["schema_version"])
        _require(json.loads(row["model_config"] or "{}").get("agent_identity") == json.loads(row["binding_json"]),
                 "identity_mismatch", "Stored conversation identity changed")
        return row

    def _conversation_lineage_on_conn(self, conn, row):
        from hermes_state_compression import _CHAIN_STEP_SQL
        chain = [row["conversation_id"]]
        binding = json.loads(row["binding_json"])
        for _ in range(CONVERSATION_MAX_LINEAGE):
            child = conn.execute(_CHAIN_STEP_SQL, (chain[-1],)).fetchone()
            if child is None:
                return chain
            _require(child["id"] not in chain, "lineage_invalid", "Compression lineage contains a cycle")
            identity_row = conn.execute("SELECT model_config FROM sessions WHERE id=?", (child["id"],)).fetchone()
            _require(json.loads(identity_row[0] or "{}").get("agent_identity") == binding,
                     "identity_mismatch", "Compression continuation belongs to another identity")
            chain.append(child["id"])
        raise RuntimeStoreError("lineage_limit", "Compression lineage exceeds the supported bound")

    def _conversation_operation_on_conn(self, conn, owner, request):
        _version(request.get("schema_version"))
        key = _identifier(request.get("idempotency_key"), "idempotency_key")
        digest = hashlib.sha256(_json(request).encode()).hexdigest()
        row = conn.execute("SELECT request_digest,result_json FROM runtime_conversation_operations "
                           "WHERE owner_key=? AND idempotency_key=?", (owner, key)).fetchone()
        if row is not None:
            _require(row["request_digest"] == digest, "idempotency_conflict", "Operation key reused for different intent")
        return key, digest, json.loads(row["result_json"]) if row is not None else None

    def _conversation_record_operation(self, conn, owner, key, digest, operation, result):
        conn.execute("INSERT INTO runtime_conversation_operations VALUES(?,?,?,?,?,?)",
                     (owner, key, digest, operation, _json(result), time.time()))

    def read_runtime_conversation_operation(self, context, idempotency_key):
        owner = _conversation_owner(context)
        _identifier(idempotency_key, "idempotency_key")
        with self._runtime_read() as conn:
            row = conn.execute("SELECT operation,result_json FROM runtime_conversation_operations "
                               "WHERE owner_key=? AND idempotency_key=?", (owner, idempotency_key)).fetchone()
            return {"schema_version": 1, "found": row is not None, "idempotency_key": idempotency_key,
                    "operation": row["operation"] if row else None,
                    "conversation": json.loads(row["result_json"])["conversation"] if row else None}

    def create_runtime_conversation(self, context, request):
        owner = _conversation_owner(context)
        title = _conversation_title(request.get("title", ""))
        request = {**request, "operation": "create"}
        def write(conn):
            key, digest, prior = self._conversation_operation_on_conn(conn, owner, request)
            if prior is not None:
                self._conversation_owned_on_conn(conn, owner, prior["conversation"]["conversation_id"])
                return {**prior, "created": False}
            now = time.time()
            sid = context.identity.session_id
            binding = context.identity.to_record()
            conn.execute("INSERT INTO sessions(id,source,created_source,user_id,started_at,title,title_source,"
                         "model_config,profile_name) VALUES(?,?,?,?,?,?,?,?,?)",
                         (sid, "web", "web", binding["principal_id"], now, None, None,
                          _json({"agent_identity": binding}), self._own_profile_name()))
            conn.execute("INSERT INTO runtime_conversations(conversation_id,owner_key,binding_json,display_title,created_at,updated_at) "
                         "VALUES(?,?,?,?,?,?)", (sid, owner, _json(binding), title, now, now))
            result = {"schema_version": 1, "conversation": _conversation_summary(
                self._conversation_owned_on_conn(conn, owner, sid)), "created": True}
            self._conversation_record_operation(conn, owner, key, digest, request["operation"], result)
            return result
        return self._execute_write(write)

    def read_runtime_conversation(self, context, conversation_id):
        owner = _conversation_owner(context)
        with self._runtime_read() as conn:
            row = self._conversation_owned_on_conn(conn, owner, conversation_id)
            return {"conversation": _conversation_summary(row), "binding": json.loads(row["binding_json"]),
                    "lineage": self._conversation_lineage_on_conn(conn, row)}

    def list_runtime_conversations(self, context, *, limit=50, cursor=None, archived=False, query=""):
        owner = _conversation_owner(context)
        _require(type(limit) is int and 1 <= limit <= CONVERSATION_MAX_PAGE and type(archived) is bool,
                 "invalid_command", "Invalid page parameters")
        _require(isinstance(query, str) and len(query) <= 200, "invalid_command", "Invalid title query")
        scope = hashlib.sha256(_json([owner, "list", archived, query]).encode()).hexdigest()
        page = _conversation_decode_cursor(cursor, scope, 2)
        if page is not None:
            _require(type(page[0]) in (int, float) and isinstance(page[1], str), "invalid_cursor", "Invalid list cursor")
        with self._runtime_read() as conn:
            sql = _CONVERSATION_SELECT + "WHERE c.owner_key=? AND s.archived=? AND instr(lower(coalesce(c.display_title,s.title,'')),lower(?))>0 "
            args = [owner, int(archived), query]
            if page:
                sql += "AND (c.created_at < ? OR (c.created_at=? AND c.conversation_id<?)) "
                args.extend([page[0], page[0], page[1]])
            rows = conn.execute(sql + "ORDER BY c.created_at DESC,c.conversation_id DESC LIMIT ?", (*args, limit + 1)).fetchall()
            more = len(rows) > limit
            rows = rows[:limit]
            for row in rows:
                self._conversation_owned_on_conn(conn, owner, row["conversation_id"])
            return {"schema_version": 1, "conversations": [_conversation_summary(row) for row in rows],
                    "has_more": more, "next_cursor": _conversation_cursor(scope, [rows[-1]["created_at"],
                    rows[-1]["conversation_id"]]) if more else None}

    def mutate_runtime_conversation(self, context, request, *, operation):
        owner = _conversation_owner(context)
        _require(operation in {"rename", "archive"}, "invalid_command", "Invalid conversation operation")
        request = {**request, "operation": operation}
        def write(conn):
            row = self._conversation_owned_on_conn(conn, owner, request["conversation_id"])
            key, digest, prior = self._conversation_operation_on_conn(conn, owner, request)
            if prior is not None:
                return prior
            _require(type(request.get("expected_revision")) is int and request["expected_revision"] == row["revision"],
                     "revision_conflict", "Conversation metadata changed")
            lineage = self._conversation_lineage_on_conn(conn, row)
            if operation == "rename":
                title = _conversation_title(request.get("title"))
                _require(bool(title), "invalid_command", "Title cannot be blank")
                # Canonical display metadata is independent from the legacy
                # global-unique alias on each physical/compression session row.
                conn.execute("UPDATE runtime_conversations SET display_title=? WHERE conversation_id=?", (title, row["conversation_id"]))
            else:
                _require(type(request.get("archived")) is bool, "invalid_command", "Archived must be boolean")
                conn.executemany("UPDATE sessions SET archived=?,auto_archived=0 WHERE id=?",
                                 [(int(request["archived"]), sid) for sid in lineage])
            conn.execute("UPDATE runtime_conversations SET revision=revision+1,updated_at=? WHERE conversation_id=?",
                         (time.time(), row["conversation_id"]))
            result = {"schema_version": 1, "conversation": _conversation_summary(
                self._conversation_owned_on_conn(conn, owner, row["conversation_id"]))}
            self._conversation_record_operation(conn, owner, key, digest, request["operation"], result)
            return result
        return self._execute_write(write)

    def _conversation_text_on_conn(self, conn, row_id, offset):
        # JSON content's NUL sentinel requires BLOB slicing (SQLite text substr stops at NUL).
        raw_json = "CAST(substr(CAST(content AS BLOB),7) AS TEXT)"
        is_json = "substr(CAST(content AS BLOB),1,6)=x'006a736f6e3a'"
        text_expr = f"""CASE WHEN {is_json} THEN CASE WHEN json_valid({raw_json}) THEN
            (SELECT group_concat(CASE WHEN type='object' THEN CASE WHEN json_extract(value,'$.type') IN ('text','input_text','output_text')
                AND json_type(value,'$.text')='text' THEN json_extract(value,'$.text') END END, char(10))
             FROM json_each(CASE WHEN json_type({raw_json})='array' THEN {raw_json} ELSE '[]' END))
            ELSE '' END ELSE CASE WHEN typeof(content)='text' THEN content ELSE '' END END"""
        row = conn.execute(f"SELECT substr(CAST(text AS BLOB),?,?) AS text,length(CAST(text AS BLOB)) AS length,structured FROM "
                           f"(SELECT coalesce(({text_expr}),'') AS text,({is_json}) AS structured FROM messages WHERE id=?)",
                           (offset + 1, CONVERSATION_TEXT_CHARS, row_id)).fetchone()
        data = row["text"] or b""
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        raw = decoder.decode(data, final=offset + len(data) >= int(row["length"] or 0))
        consumed = len(data) - len(decoder.getstate()[0])
        clean = _CONTROL_CHARS.sub("", raw)
        return clean, offset + consumed, int(row["length"] or 0), clean != raw, bool(row["structured"])

    def read_runtime_conversation_history(self, context, conversation_id, *, limit=50, cursor=None):
        owner = _conversation_owner(context)
        _require(type(limit) is int and 1 <= limit <= CONVERSATION_MAX_PAGE, "invalid_command", "Invalid page size")
        scope = hashlib.sha256(_json([owner, "history", conversation_id]).encode()).hexdigest()
        page = _conversation_decode_cursor(cursor, scope, 3)
        if page is not None:
            _require(all(type(n) is int and n >= 0 for n in page), "invalid_cursor", "Invalid history cursor")
        with self._runtime_read() as conn:
            owned = self._conversation_owned_on_conn(conn, owner, conversation_id)
            chain = self._conversation_lineage_on_conn(conn, owned)
            marks = ','.join('?' for _ in chain)
            upper, after, offset = page or [conn.execute(f"SELECT coalesce(MAX(id),0) FROM messages WHERE session_id IN ({marks})", chain).fetchone()[0], 0, 0]
            _require(after <= upper, "invalid_cursor", "Invalid history position")
            # Copies across compaction/rotation share message_uid; the first physical id is a stable
            # ordering key, while the newest eligible representative supplies committed display text.
            sql = f"""WITH eligible AS (
                SELECT id,session_id,role,timestamp,active,
                       coalesce(nullif(message_uid,''),'row:'||id) AS uid
                FROM messages WHERE session_id IN ({marks}) AND id<=?
                    AND (active=1 OR compacted=1) AND role IN ('user','assistant')
                    AND coalesce(_compressed_summary,0)=0 AND coalesce(display_kind,'') IN ('','steer')
                    AND coalesce(json_extract(display_metadata,'$.model_only'),0)=0),
                ranked AS (SELECT *,MIN(id) OVER (PARTITION BY uid) AS first_id,
                    ROW_NUMBER() OVER (PARTITION BY uid ORDER BY active DESC,id DESC) AS rank FROM eligible)
                SELECT * FROM ranked WHERE rank=1 AND (first_id>? OR (first_id=? AND ?>0))
                ORDER BY first_id LIMIT ?"""
            rows = conn.execute(sql, (*chain, upper, after, after, offset, limit + 1)).fetchall()
            chunks, used = [], 0
            next_position = None
            for row in rows:
                position = offset if row["first_id"] == after else 0
                while True:
                    if len(chunks) >= limit:
                        next_position = [upper, after, offset]
                        break
                    text, end, total, sanitized, structured = self._conversation_text_on_conn(conn, row["id"], position)
                    size = len(text.encode("utf-8"))
                    if chunks and used + size > CONVERSATION_TEXT_BYTES:
                        next_position = [upper, after, offset]
                        break
                    complete = end >= total
                    chunks.append({"message_id": row["uid"], "physical_session_id": row["session_id"],
                                   "role": row["role"], "text": text, "text_offset": position,
                                   "text_complete": complete, "text_sanitized": sanitized,
                                   "non_text_omitted": structured, "timestamp": float(row["timestamp"]),
                                   "committed": True, "command_id": None})
                    used += size
                    after, offset = row["first_id"], 0 if complete else end
                    if complete:
                        break
                    position = end
                if next_position is not None:
                    break
            return {"schema_version": 1, "conversation_id": conversation_id, "format": "safe_transcript_v1",
                    "messages": chunks, "lineage": chain, "snapshot_max_row_id": upper,
                    "has_more": next_position is not None,
                    "next_cursor": _conversation_cursor(scope, next_position) if next_position else None}
