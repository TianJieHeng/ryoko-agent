"""BE02 bounded command journal, fenced transitions and restart-safe projections.

SQLite owns acceptance and execution admission. This module never invokes a model,
tool or callback: recovery consumes recorded facts, never re-executes a claim.
The existing compression-root turn lease remains the sole execution owner.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import time
import uuid
from collections.abc import Mapping

RUNTIME_SCHEMA_VERSION = 1
MAX_RUNTIME_PAYLOAD_BYTES = 256 * 1024
MAX_RUNTIME_PAGE = 500
_OPERATIONS = frozenset({"submit", "steer", "cancel", "approval"})
_FINISH_STATES = frozenset({"completed", "failed", "blocked", "cancelled"})
_RECORDED_TYPES = frozenset({"runtime.output", "runtime.state", "approval.requested",
                             "approval.resolved", "effect.recorded", "model.started", "model.completed", "model.failed",
                             "tool.started", "tool.completed", "tool.failed"})
_EVENT_TYPES = _RECORDED_TYPES | {"command.accepted", "command.claimed", "checkpoint.published"} | {
    f"command.{status}" for status in _FINISH_STATES}
_CORRELATIONS = ("mission_id", "run_id", "operation_id", "effect_id", "delivery_id", "approval_id")


class RuntimeStoreError(ValueError):
    """Stable transport-independent failure code; no rejected command is dispatched."""
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def _require(condition, code, message):
    if not condition:
        raise RuntimeStoreError(code, message)


def _version(value):
    _require(type(value) is int and value == RUNTIME_SCHEMA_VERSION,
             "unsupported_schema", "Unsupported runtime schema version")


def _identifier(value, name):
    _require(isinstance(value, str) and 0 < len(value) <= 256 and value.strip() == value,
             "invalid_command", f"{name} must be a nonempty bounded identifier")
    return value


def _json(value):
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, RecursionError) as exc:
        raise RuntimeStoreError("invalid_command", "Runtime data must be finite JSON") from exc
    _require(len(encoded.encode("utf-8")) <= MAX_RUNTIME_PAYLOAD_BYTES,
             "payload_too_large", "Runtime data exceeds the inline bound; use artifact references")
    return encoded


def _revision(actual, expected):
    _require(expected is None or (type(expected) is int and expected >= 0),
             "invalid_command", "expected_revision must be a nonnegative integer")
    _require(expected is None or actual == expected, "revision_conflict", "Runtime revision changed")


def _empty_projection():
    return {"state": {"status": "idle", "run_id": None, "last_command_id": None, "last_operation": None},
            "outstanding_requests": [], "artifacts": [], "unresolved_effects": []}


def _validated_command(actor, command):
    _require(isinstance(actor, Mapping) and isinstance(command, Mapping), "invalid_command", "Expected mappings")
    _version(command.get("schema_version"))
    for key in ("principal_id", "profile_id", "agent_id"):
        _identifier(actor.get(key), key)
    for key in ("command_id", "idempotency_key"):
        _identifier(command.get(key), key)
    _require(isinstance(command.get("operation"), str) and command["operation"] in _OPERATIONS, "invalid_command", "Unknown runtime operation")
    _require(isinstance(command.get("payload"), Mapping), "invalid_command", "payload must be an object")
    _require(set(command) <= {"schema_version", "command_id", "idempotency_key", "expected_revision",
                              "identity_binding", "operation", "payload"},
             "invalid_command", "Unsupported command fields")
    binding = command.get("identity_binding", actor)
    _require(isinstance(binding, Mapping) and all(binding.get(key) == actor[key]
             for key in ("principal_id", "profile_id", "agent_id")),
             "identity_mismatch", "Command identity differs from authenticated actor")
    expected = command.get("expected_revision")
    _require(expected is None or (type(expected) is int and expected >= 0),
             "invalid_command", "expected_revision must be a nonnegative integer")
    encoded = _json(dict(command))
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    return encoded, digest


def _checkpoint_metadata(checkpoint):
    _require(isinstance(checkpoint, Mapping), "invalid_command", "checkpoint must be an object")
    _version(checkpoint.get("schema_version"))
    metadata = dict(checkpoint)
    for field in ("config_version", "policy_version", "runtime_version", "prompt_projection_version"):
        _identifier(metadata.get(field), field)
    specifications = {
        "artifacts": ({"artifact_id", "version"}, {}),
        "unresolved_effects": ({"effect_id", "status"}, {"status": {"pending", "outcome_uncertain"}}),
        "outstanding_requests": ({"request_id", "kind", "status"},
                                 {"kind": {"approval", "input"}, "status": {"pending"}}),
    }
    _require(set(metadata) <= set(specifications) | {"schema_version", "config_version", "policy_version",
             "runtime_version", "prompt_projection_version"}, "invalid_command", "Unsupported checkpoint fields")
    for name, (fields, enums) in specifications.items():
        rows = metadata.setdefault(name, [])
        _require(isinstance(rows, list) and len(rows) <= 100, "invalid_command", "Checkpoint reference bound exceeded")
        for row in rows:
            _require(isinstance(row, dict) and set(row) == fields, "invalid_command", "Invalid checkpoint reference")
            for field in fields:
                _identifier(row[field], field)
                _require(field not in enums or row[field] in enums[field], "invalid_command", "Invalid reference state")
    _json(metadata)
    return metadata


def _project_runtime_operation(projection, event):
    """Track recorded invocation outcomes, not external-effect success or exactly-once delivery."""
    kind, _, phase = event["type"].partition(".")
    if kind not in {"model", "tool"}:
        return
    operation_id = _identifier(event["operation_id"], "operation_id")
    unresolved = {item["effect_id"]: item for item in projection["unresolved_effects"]}
    result = event["payload"].get("result")
    uncertain = phase == "failed" or (isinstance(result, dict) and (
        result.get("outcome_uncertain") is True or result.get("output_omitted") is True
        or result.get("outcome") == "outcome_uncertain"))
    if phase == "completed" and not uncertain:
        # This removes only the missing-invocation-output marker. An effect receipt,
        # delivery confirmation and safe retry remain separate BE06 responsibilities.
        unresolved.pop(operation_id, None)
    else:
        unresolved[operation_id] = {"effect_id": operation_id,
                                   "status": "outcome_uncertain" if uncertain else "pending"}
    _require(len(unresolved) <= 100, "unresolved_limit", "Reconcile outstanding operations before dispatch")
    projection["unresolved_effects"] = list(unresolved.values())


class SessionRuntimeMixin:
    """Additive SessionDB API; every authoritative mutation uses its existing writer transaction."""

    def _runtime_session_on_conn(self, conn, session_id):
        _identifier(session_id, "session_id")
        _require(conn.execute("SELECT 1 FROM sessions WHERE id=?", (session_id,)).fetchone() is not None,
                 "session_not_found", "Runtime session does not exist")
        return self._session_turn_lease_key_on_conn(conn, session_id)

    def _runtime_state_on_conn(self, conn, session_id, *, create=False):
        row = conn.execute("SELECT * FROM runtime_state WHERE session_id=?", (session_id,)).fetchone()
        if row is None and create:
            conn.execute("INSERT INTO runtime_state(session_id,epoch,snapshot_json) VALUES(?,?,?)",
                         (session_id, uuid.uuid4().hex, _json(_empty_projection())))
            row = conn.execute("SELECT * FROM runtime_state WHERE session_id=?", (session_id,)).fetchone()
        if row is not None:
            _version(row["schema_version"])
        return row

    def _runtime_fence_on_conn(self, conn, session_id, holder, generation):
        _require(type(generation) is int and generation > 0 and isinstance(holder, str) and bool(holder),
                 "stale_owner", "A live owner generation is required")
        row = conn.execute("SELECT holder,generation,expires_at FROM session_turn_leases WHERE conversation_id=?",
                           (session_id,)).fetchone()
        _require(row is not None and row["holder"] == holder and row["generation"] == generation
                 and row["expires_at"] > time.time(), "stale_owner", "Session turn lease lost or expired")

    @contextmanager
    def _runtime_read(self):
        # A read pool checkout alone is not a snapshot: SAVEPOINT pins the revision, event
        # page and projection together without committing a fallback connection's caller.
        with self._read_ctx() as conn:
            conn.execute("SAVEPOINT runtime_read")
            try:
                yield conn
            finally:
                conn.execute("RELEASE runtime_read")

    def _runtime_snapshot_on_conn(self, conn, session_id):
        row = self._runtime_state_on_conn(conn, session_id)
        projection = json.loads(row["snapshot_json"]) if row else _empty_projection()
        return {"schema_version": RUNTIME_SCHEMA_VERSION, "session_id": session_id,
                "revision": row["revision"] if row else 0, **projection,
                "last_cursor": f'{row["epoch"]}:{row["revision"]}' if row else "legacy:0",
                "compatibility_status": "native" if row else "legacy"}

    def _append_runtime_event_on_conn(self, conn, session_id, event_type, payload, generation, **correlations):
        _require(isinstance(event_type, str) and event_type in _EVENT_TYPES, "invalid_command", "Unknown runtime event type")
        _require(set(correlations) <= set(_CORRELATIONS), "invalid_command", "Unknown correlation fields")
        for key, value in correlations.items():
            if value is not None:
                _identifier(value, key)
        payload_json = _json(payload)
        row = self._runtime_state_on_conn(conn, session_id, create=True)
        seq = row["revision"] + 1
        event = {"event_id": uuid.uuid4().hex, "session_id": session_id, "seq": seq,
                 "cursor": f'{row["epoch"]}:{seq}', "generation": generation, "schema_version": 1,
                 "occurred_at": time.time(), "type": event_type, "payload": payload,
                 **{key: correlations.get(key) for key in _CORRELATIONS}}
        conn.execute("INSERT INTO runtime_events(session_id,seq,event_id,schema_version,generation,type,occurred_at,"
                     "mission_id,run_id,operation_id,effect_id,delivery_id,approval_id,payload_json) "
                     "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (session_id, seq, event["event_id"], 1, generation,
                     event_type, event["occurred_at"], *(event[key] for key in _CORRELATIONS), payload_json))
        projection = json.loads(row["snapshot_json"])
        _project_runtime_operation(projection, event)
        if event_type.startswith("command."):
            command = self._runtime_command_on_conn(conn, session_id, payload["command_id"])
            operation = payload.get("operation") if command is None else json.loads(command["command_json"])["operation"]
            state = projection["state"]
            # Acknowledging a steer/cancel is not completion of the active run. Control
            # outcomes live in their command records; the snapshot describes the submit.
            # Likewise queued submit acceptance must not hide a currently claimed run.
            status = event_type.removeprefix("command.")
            pending_behind_owner = status == "accepted" and state["status"] == "claimed"
            other_run_finished = status in _FINISH_STATES and state["last_command_id"] != payload["command_id"]
            if operation == "submit" and not pending_behind_owner and not other_run_finished:
                state.update(status=status, last_command_id=payload["command_id"],
                             last_operation=operation, run_id=event["run_id"])
        conn.execute("UPDATE runtime_state SET revision=?,snapshot_json=? WHERE session_id=?",
                     (seq, _json(projection), session_id))
        return event

    def submit_runtime_command(self, session_id, actor, command):
        """Durably accept once per authenticated principal/session/key; never dispatch here."""
        command_json, digest = _validated_command(actor, command)
        def write(conn):
            sid = self._runtime_session_on_conn(conn, session_id)
            state = self._runtime_state_on_conn(conn, sid, create=True)
            _require(state["principal_id"] is None or all(state[key] == actor[key]
                     for key in ("principal_id", "profile_id", "agent_id")),
                     "identity_mismatch", "Runtime session belongs to a different actor")
            duplicates = conn.execute("SELECT * FROM runtime_commands WHERE session_id=? AND principal_id=? "
                "AND (idempotency_key=? OR command_id=?)", (sid, actor["principal_id"],
                command["idempotency_key"], command["command_id"])).fetchall()
            if duplicates:
                _require(len(duplicates) == 1 and duplicates[0]["digest"] == digest,
                         "idempotency_conflict", "Command key was already used for different content")
                _version(duplicates[0]["schema_version"])
                return json.loads(duplicates[0]["receipt_json"])
            _revision(state["revision"], command.get("expected_revision"))
            run_id = uuid.uuid4().hex if command["operation"] == "submit" else json.loads(state["snapshot_json"])["state"]["run_id"]
            generation = conn.execute("SELECT turn_owner_generation FROM sessions WHERE id=?", (sid,)).fetchone()[0]
            event = self._append_runtime_event_on_conn(conn, sid, "command.accepted",
                {"command_id": command["command_id"], "operation": command["operation"]}, generation, run_id=run_id)
            receipt = {"schema_version": 1, "command_id": command["command_id"], "status": "accepted",
                       "durable_revision": event["seq"], "run_id": run_id}
            conn.execute("INSERT INTO runtime_commands(session_id,principal_id,command_id,idempotency_key,"
                "schema_version,digest,command_json,receipt_json,accepted_revision,run_id) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (sid, actor["principal_id"], command["command_id"], command["idempotency_key"], 1, digest,
                 command_json, _json(receipt), event["seq"], run_id))
            conn.execute("UPDATE runtime_state SET principal_id=?,profile_id=?,agent_id=? WHERE session_id=?",
                         (actor["principal_id"], actor["profile_id"], actor["agent_id"], sid))
            return receipt
        return self._execute_write(write)

    def _runtime_command_on_conn(self, conn, sid, command_id):
        # A journal is bound to exactly one authenticated principal on first acceptance.
        _identifier(command_id, "command_id")
        row = conn.execute("SELECT * FROM runtime_commands WHERE session_id=? AND command_id=?",
                           (sid, command_id)).fetchone()
        if row:
            _version(row["schema_version"])
        return row

    def read_runtime_command(self, session_id, command_id):
        with self._runtime_read() as conn:
            sid = self._runtime_session_on_conn(conn, session_id)
            row = self._runtime_command_on_conn(conn, sid, command_id)
            return None if row is None else {"command": json.loads(row["command_json"]),
                "receipt": json.loads(row["receipt_json"]), "status": row["status"],
                "claimed_holder": row["claimed_holder"], "claimed_generation": row["claimed_generation"],
                "result": json.loads(row["result_json"]) if row["result_json"] is not None else None}

    def claim_runtime_command(self, session_id, command_id, *, holder, generation):
        """One durable admission; a crashed claim is unresolved, never implicitly retried."""
        def write(conn):
            sid = self._runtime_session_on_conn(conn, session_id)
            self._runtime_fence_on_conn(conn, sid, holder, generation)
            row = self._runtime_command_on_conn(conn, sid, command_id)
            _require(row is not None, "command_not_found", "Runtime command does not exist")
            if row["status"] != "accepted":
                return False
            conn.execute("UPDATE runtime_commands SET status='claimed',claimed_holder=?,claimed_generation=? "
                         "WHERE session_id=? AND command_id=?", (holder, generation, sid, command_id))
            self._append_runtime_event_on_conn(conn, sid, "command.claimed", {"command_id": command_id},
                                               generation, run_id=row["run_id"])
            return True
        return self._execute_write(write)

    def finish_runtime_command(self, session_id, command_id, *, holder, generation, status="completed", result=None):
        _require(isinstance(status, str) and status in _FINISH_STATES, "invalid_command", "Unsupported completion status")
        _require(result is None or isinstance(result, dict), "invalid_command", "result must be an object")
        result_json = _json(result or {})
        def write(conn):
            sid = self._runtime_session_on_conn(conn, session_id)
            self._runtime_fence_on_conn(conn, sid, holder, generation)
            row = self._runtime_command_on_conn(conn, sid, command_id)
            _require(row is not None, "command_not_found", "Runtime command does not exist")
            _require(row["status"] == "claimed" and row["claimed_holder"] == holder
                     and row["claimed_generation"] == generation, "claim_conflict", "Command is not owned by this claim")
            conn.execute("UPDATE runtime_commands SET status=?,result_json=? WHERE session_id=? AND command_id=?",
                         (status, result_json, sid, command_id))
            return self._append_runtime_event_on_conn(conn, sid, f"command.{status}",
                {"command_id": command_id, "result": json.loads(result_json)}, generation, run_id=row["run_id"])
        return self._execute_write(write)

    def append_runtime_event(self, session_id, event_type, payload, *, holder, generation,
                             schema_version=1, expected_revision=None, **correlations):
        _version(schema_version)
        _require(isinstance(event_type, str) and event_type in _RECORDED_TYPES, "invalid_command", "Use transactional command/checkpoint operations")
        _require(isinstance(payload, dict), "invalid_command", "Event payload must be an object")
        _json(payload)
        def write(conn):
            sid = self._runtime_session_on_conn(conn, session_id)
            self._runtime_fence_on_conn(conn, sid, holder, generation)
            row = self._runtime_state_on_conn(conn, sid, create=True)
            _revision(row["revision"], expected_revision)
            return self._append_runtime_event_on_conn(conn, sid, event_type, payload, generation, **correlations)
        return self._execute_write(write)

    def read_runtime_snapshot(self, session_id):
        with self._runtime_read() as conn:
            return self._runtime_snapshot_on_conn(conn, self._runtime_session_on_conn(conn, session_id))

    def replay_runtime_events(self, session_id, cursor=None, limit=100):
        _require(type(limit) is int and 1 <= limit <= MAX_RUNTIME_PAGE, "invalid_command", "Invalid replay page size")
        with self._runtime_read() as conn:
            sid = self._runtime_session_on_conn(conn, session_id)
            state = self._runtime_state_on_conn(conn, sid)
            snapshot = self._runtime_snapshot_on_conn(conn, sid)
            epoch = state["epoch"] if state else "legacy"
            floor, revision = (state["cursor_floor"], state["revision"]) if state else (0, 0)
            parsed_epoch, seq = epoch, 0
            try:
                if cursor is not None:
                    parsed_epoch, value = cursor.rsplit(":", 1)
                    seq = int(value)
            except (AttributeError, ValueError):
                parsed_epoch, seq = "invalid", -1
            if parsed_epoch != epoch or not floor <= seq <= revision:
                return {"status": "snapshot_required", "events": [], "snapshot": snapshot,
                        "last_cursor": snapshot["last_cursor"], "has_more": False}
            rows = conn.execute("SELECT * FROM runtime_events WHERE session_id=? AND seq>? ORDER BY seq LIMIT ?",
                                (sid, seq, limit)).fetchall()
            # Retention is explicit, but a missing row from repair or an older writer must
            # also fail closed rather than presenting an apparently empty successful replay.
            contiguous = all(row["seq"] == seq + index + 1 for index, row in enumerate(rows))
            if not contiguous or (seq < revision and not rows):
                return {"status": "snapshot_required", "events": [], "snapshot": snapshot,
                        "last_cursor": snapshot["last_cursor"], "has_more": False}
            events = []
            for row in rows:
                _version(row["schema_version"])
                event = dict(row)
                event["payload"] = json.loads(event.pop("payload_json"))
                event["cursor"] = f'{epoch}:{event["seq"]}'
                events.append(event)
            last_seq = events[-1]["seq"] if events else seq
            return {"status": "ok", "events": events, "snapshot": None,
                    "last_cursor": f"{epoch}:{last_seq}", "has_more": last_seq < revision}

    def publish_runtime_checkpoint(self, session_id, checkpoint, *, holder, generation, expected_revision, included_seq):
        metadata = _checkpoint_metadata(checkpoint)
        _require(type(included_seq) is int and included_seq >= 0, "invalid_command", "Invalid checkpoint watermark")
        _require(type(expected_revision) is int, "invalid_command", "Checkpoint requires revision CAS")
        def write(conn):
            sid = self._runtime_session_on_conn(conn, session_id)
            self._runtime_fence_on_conn(conn, sid, holder, generation)
            row = self._runtime_state_on_conn(conn, sid, create=True)
            _revision(row["revision"], expected_revision)
            _require(included_seq == row["revision"], "revision_conflict", "Checkpoint source watermark changed")
            checkpoint_id = uuid.uuid4().hex
            projection = json.loads(row["snapshot_json"])
            for key in ("outstanding_requests", "artifacts", "unresolved_effects"):
                projection[key] = metadata[key]
            conn.execute("UPDATE runtime_state SET snapshot_json=? WHERE session_id=?", (_json(projection), sid))
            event = self._append_runtime_event_on_conn(conn, sid, "checkpoint.published",
                {"checkpoint_id": checkpoint_id, "included_seq": included_seq}, generation)
            saved = {**metadata, "snapshot": projection}
            conn.execute("INSERT INTO runtime_checkpoints(session_id,checkpoint_id,schema_version,included_seq,"
                "published_seq,generation,checkpoint_json) VALUES(?,?,?,?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET "
                "checkpoint_id=excluded.checkpoint_id,schema_version=excluded.schema_version,included_seq=excluded.included_seq,"
                "published_seq=excluded.published_seq,generation=excluded.generation,checkpoint_json=excluded.checkpoint_json",
                (sid, checkpoint_id, 1, included_seq, event["seq"], generation, _json(saved)))
            return {"schema_version": 1, "checkpoint_id": checkpoint_id, "included_seq": included_seq,
                    "published_seq": event["seq"], "revision": event["seq"]}
        return self._execute_write(write)

    def prune_runtime_events(self, session_id, *, through_seq, holder, generation, limit=MAX_RUNTIME_PAGE):
        """Explicit bounded retention, only behind a committed checkpoint; dedup receipts survive."""
        _require(type(through_seq) is int and through_seq >= 0 and type(limit) is int
                 and 1 <= limit <= MAX_RUNTIME_PAGE, "invalid_command", "Invalid retention bound")
        def write(conn):
            sid = self._runtime_session_on_conn(conn, session_id)
            self._runtime_fence_on_conn(conn, sid, holder, generation)
            checkpoint = conn.execute("SELECT included_seq,schema_version FROM runtime_checkpoints WHERE session_id=?", (sid,)).fetchone()
            _require(checkpoint is not None and through_seq <= checkpoint["included_seq"],
                     "checkpoint_required", "Retention requires a checkpoint covering the removed events")
            _version(checkpoint["schema_version"])
            rows = conn.execute("SELECT seq FROM runtime_events WHERE session_id=? AND seq<=? ORDER BY seq LIMIT ?",
                                (sid, through_seq, limit)).fetchall()
            if not rows:
                return 0
            floor = rows[-1]["seq"]
            conn.execute("DELETE FROM runtime_events WHERE session_id=? AND seq<=?", (sid, floor))
            conn.execute("UPDATE runtime_state SET cursor_floor=MAX(cursor_floor,?) WHERE session_id=?", (floor, sid))
            return len(rows)
        return self._execute_write(write)
