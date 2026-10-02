"""Live owner-filtered view of the existing session store for strict local recall.

No index or transcript copy is created. Lineage is navigation only and never
widens the authenticated owner. Legacy unbound callers retain their old view.
"""
from pathlib import Path
import sqlite3
import time

from hermes_state_common import escape_like
from hermes_state_search import session_owner_predicate
from tools.capability_broker import CapabilityDenied, require_live_policy


def local_recall_context(tool="session_search"):
    context = require_live_policy(require_run=False)
    if context is not None and (context.policy.memory_backend != "builtin" or context.policy.role == "primary"):
        raise CapabilityDenied("memory_backend_unsupported",
            "Local session recall is unavailable for the personal MCP backend; use its explicitly granted tools")
    if context is not None and not context.policy.allows_tool(tool):
        raise CapabilityDenied("agent_policy_denied", "The current agent has no grant for this memory operation")
    return context


class OwnedSessionSearch:
    def __init__(self, db, context):
        self._db, self._context = db, context
        self._owner = {key: getattr(context.identity, key) for key in (
            "principal_id", "profile_id", "agent_id", "profile_home_digest", "lifecycle")}
        self._check()

    def _check(self):
        if local_recall_context() != self._context:
            raise CapabilityDenied("session_owner_changed", "Session recall owner changed")
        if Path(self._db.db_path).resolve() != Path(self._context.profile_home, "state.db").resolve():
            raise CapabilityDenied("session_profile_denied", "Session recall cannot use another profile store")

    def _scope(self, alias="s"):
        self._check()
        return session_owner_predicate(self._owner, alias)

    def _allowed(self, session_id):
        predicate, params = self._scope()
        return bool(self._db._read_one(f"SELECT 1 FROM sessions s WHERE s.id = ? AND {predicate}",
                                      [session_id, *params]))

    def get_session(self, session_id):
        predicate, params = self._scope()
        row = self._db._read_one(
            f"SELECT s.id, s.title, s.source, s.model, s.started_at, s.end_reason, s.parent_session_id "
            f"FROM sessions s WHERE s.id = ? AND {predicate}", [session_id, *params])
        if row is None:
            return None
        result = dict(row)
        if result["parent_session_id"] and not self._allowed(result["parent_session_id"]):
            result["parent_session_id"] = None
        return result

    def get_messages(self, session_id):
        if not self._allowed(session_id):
            return []
        return self._db.get_messages(session_id)

    def get_messages_around(self, session_id, around_message_id, window=5):
        if not self._allowed(session_id):
            return {"window": [], "messages_before": 0, "messages_after": 0}
        return self._db.get_messages_around(session_id, around_message_id, window=window)

    def get_anchored_view(self, session_id, around_message_id, window=5, bookend=3):
        if not self._allowed(session_id):
            return {"window": [], "messages_before": 0, "messages_after": 0,
                    "bookend_start": [], "bookend_end": []}
        return self._db.get_anchored_view(session_id, around_message_id, window=window, bookend=bookend)

    def get_message_storage_state(self, message_id):
        predicate, params = self._scope()
        row = self._db._read_one(
            f"SELECT m.session_id, m.active, m.compacted FROM messages m "
            f"JOIN sessions s ON s.id = m.session_id WHERE m.id = ? AND {predicate}",
            [message_id, *params])
        return dict(row) if row else None

    def resolve_session_by_title(self, title):
        predicate, params = self._scope()
        row = self._db._read_one(
            f"SELECT s.id FROM sessions s WHERE {predicate} "
            "AND (s.title = ? OR s.title LIKE ? ESCAPE '\\') "
            "ORDER BY s.started_at DESC, s.id DESC LIMIT 1", [*params, title, f"{escape_like(title)} #%"])
        return row["id"] if row else None

    def search_messages(self, **kwargs):
        self._check()
        # The shared SQL filter reaches FTS, CJK, LIKE and deferred-index fallback
        # before LIMIT and before any matching content or neighbor is hydrated.
        return self._db.search_messages(**kwargs, owner_filter=self._owner)

    def fts_rebuild_status(self):
        self._check()
        # Global corpus counts include other private owners. Do not expose them.
        return None

    def list_recent_sessions_bounded(self, *, limit=20, exclude_sources=None, timeout_seconds=3.0):
        predicate, params = self._scope()
        if exclude_sources:
            predicate += f" AND s.source NOT IN ({','.join('?' for _ in exclude_sources)})"
            params.extend(exclude_sources)
        # No cross-owner ancestry expansion. Metadata candidates are scoped before
        # reading the short preview; output remains bounded even in a large store.
        sql = (
            f"SELECT s.id, s.title, s.source, s.started_at, s.last_activity_at AS last_active, s.message_count "
            f"FROM sessions s WHERE {predicate} AND s.archived = 0 AND s.hidden = 0 "
            "ORDER BY COALESCE(s.last_activity_at, s.started_at) DESC, s.id DESC LIMIT ?")
        deadline = time.monotonic() + min(max(float(timeout_seconds), 0), 3.0)
        with self._db._read_ctx() as conn:
            conn.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
            try:
                rows = conn.execute(sql, [*params, min(max(int(limit), 1), 25)]).fetchall()
            except sqlite3.OperationalError as exc:
                if "interrupt" in str(exc).lower():
                    raise TimeoutError("Owned session browse exceeded its deadline") from exc
                raise
            finally:
                conn.set_progress_handler(None, 0)
        result = []
        for row in rows:
            item = dict(row)
            preview = self._db._read_one(
                "SELECT substr(content, 1, 200) AS preview FROM messages WHERE session_id = ? "
                "AND active = 1 AND role = 'user' AND COALESCE(display_kind, '') <> 'hidden' ORDER BY id LIMIT 1",
                (item["id"],))
            item["preview"] = preview["preview"] if preview else ""
            result.append(item)
        return result
