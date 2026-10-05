"""BE02 bounded command journal, fenced transitions and restart-safe projections.

SQLite owns acceptance and execution admission. This module never invokes a model
or tool: recovery consumes recorded facts, never re-executes a claim. Optional
append guards only recheck write admission inside the journal transaction.
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
MAX_SNAPSHOT_REFERENCES = 100
_OPERATIONS = frozenset({"submit", "steer", "cancel", "approval", "artifact"})
_FINISH_STATES = frozenset({"completed", "failed", "blocked", "cancelled"})
_RECORDED_TYPES = frozenset({"runtime.output", "runtime.state", "approval.requested",
                             "decision.observed", "decision.outcome",
                             "decision.tool_plan", "decision.policy", "decision.planner_miss", "decision.bundle",
                             "operations.repair_started", "operations.repair_finished",
                             "operations.deletion_requested", "operations.deletion_finished",
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
            "outstanding_requests": [], "artifacts": [], "unresolved_effects": [], "unresolved_invocations": []}


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
                              "identity_binding", "operation", "payload", "target_run_id"},
             "invalid_command", "Unsupported command fields")
    if "target_run_id" in command:
        _identifier(command["target_run_id"], "target_run_id")
        _require(command["operation"] in {"cancel", "steer"}, "invalid_command",
                 "Only cancel and steer may target a run")
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
        "unresolved_effects": ({"effect_id", "status"}, {"status": {
            "pending", "outcome_uncertain", "prepared", "dispatched", "outcome_unknown", "reconciliation_required"}}),
        "unresolved_invocations": ({"operation_id", "status"}, {"status": {"pending", "outcome_uncertain"}}),
        "outstanding_requests": ({"request_id", "kind", "status"},
                                 {"kind": {"approval", "input"}, "status": {"pending"}}),
    }
    _require(set(metadata) <= set(specifications) | {"schema_version", "config_version", "policy_version",
             "runtime_version", "prompt_projection_version"}, "invalid_command", "Unsupported checkpoint fields")
    # Schema34 checkpoints called missing invocation outputs "effects". Retain
    # those references without promoting them into BE06 external-effect receipts.
    if "unresolved_invocations" not in metadata:
        legacy = metadata.get("unresolved_effects", [])
        if isinstance(legacy, list):
            metadata["unresolved_invocations"] = [
                {"operation_id": item.get("effect_id"), "status": item.get("status")}
                for item in legacy if isinstance(item, dict) and isinstance(item.get("status"), str)
                and item["status"] in {"pending", "outcome_uncertain"}]
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


def _normalize_invocation_projection(projection):
    if "unresolved_invocations" not in projection:
        projection["unresolved_invocations"] = [
            {"operation_id": item["effect_id"], "status": item["status"]}
            for item in projection.get("unresolved_effects", [])
            if item.get("status") in {"pending", "outcome_uncertain"}]
    return projection


def _project_runtime_operation(projection, event):
    """Track recorded invocation outcomes, not external-effect success or exactly-once delivery."""
    kind, _, phase = event["type"].partition(".")
    if kind not in {"model", "tool"}:
        return
    operation_id = _identifier(event["operation_id"], "operation_id")
    _normalize_invocation_projection(projection)
    unresolved = {item["operation_id"]: item for item in projection["unresolved_invocations"]}
    result = event["payload"].get("result")
    uncertain = phase == "failed" or (isinstance(result, dict) and (
        result.get("outcome_uncertain") is True or result.get("output_omitted") is True
        or result.get("outcome") == "outcome_uncertain"))
    if phase == "completed" and not uncertain:
        # This removes only the missing-invocation-output marker. An effect receipt,
        # delivery confirmation and safe retry remain separate BE06 responsibilities.
        unresolved.pop(operation_id, None)
    else:
        unresolved[operation_id] = {"operation_id": operation_id,
                                   "status": "outcome_uncertain" if uncertain else "pending"}
    _require(len(unresolved) <= 100, "unresolved_limit", "Reconcile outstanding operations before dispatch")
    projection["unresolved_invocations"] = list(unresolved.values())


def _project_runtime_event(projection, event, command_operation=None):
    """Canonical pure reducer shared by live append and bounded local reconstruction."""
    _project_runtime_operation(projection, event)
    if event["type"].startswith("command."):
        operation = command_operation or event["payload"].get("operation")
        state = projection["state"]
        # Control outcomes never replace the submit's state; queued acceptance
        # does not hide the current run and another run's finish cannot close it.
        status = event["type"].removeprefix("command.")
        pending_behind_owner = status == "accepted" and state["status"] == "claimed"
        other_run_finished = status in _FINISH_STATES and state["last_command_id"] != event["payload"]["command_id"]
        if operation == "submit" and not pending_behind_owner and not other_run_finished:
            state.update(status=status, last_command_id=event["payload"]["command_id"],
                         last_operation=operation, run_id=event["run_id"])


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
        projection = _normalize_invocation_projection(json.loads(row["snapshot_json"]) if row else _empty_projection())
        projection.update(self._runtime_references_on_conn(conn, session_id, row, projection))
        return {"schema_version": RUNTIME_SCHEMA_VERSION, "session_id": session_id,
                "revision": row["revision"] if row else 0, **projection,
                "last_cursor": f'{row["epoch"]}:{row["revision"]}' if row else "legacy:0",
                "compatibility_status": "native" if row else "legacy"}

    def _runtime_references_on_conn(self, conn, session_id, state, projection):
        """Safe bounded windows from authoritative rows in the caller's read transaction."""
        actor = tuple(state[key] for key in ("principal_id", "profile_id", "agent_id")) if state else (None,) * 3
        params = (session_id, *actor)
        scope = "session_id=? AND principal_id=? AND profile_id=? AND agent_id=?"
        effect_from = "runtime_effects WHERE " + scope + " AND state NOT IN ('confirmed','failed')"
        effect_count = conn.execute("SELECT COUNT(*),MIN(schema_version),MAX(schema_version) FROM " + effect_from, params).fetchone()
        _require(not effect_count[0] or effect_count[1] == effect_count[2] == 1,
                 "unsupported_schema", "Unsupported effect projection schema")
        effects = [{"effect_id": item["effect_id"], "status": item["state"]} for item in conn.execute(
            "SELECT effect_id,state FROM " + effect_from + " ORDER BY created_at,effect_id LIMIT ?",
            (*params, MAX_SNAPSHOT_REFERENCES))]
        artifact_from = "runtime_artifact_versions WHERE " + scope + " AND publication_state='committed' AND artifact_kind='runtime_result'"
        artifact_count = conn.execute("SELECT COUNT(*) FROM " + artifact_from, params).fetchone()[0]
        artifacts = [{"artifact_id": item["artifact_id"], "version": str(item["version"])} for item in conn.execute(
            "SELECT artifact_id,version FROM " + artifact_from + " ORDER BY created_at DESC,artifact_id,version DESC LIMIT ?",
            (*params, MAX_SNAPSHOT_REFERENCES))]
        now = time.time()
        approval_from = (
            "runtime_effect_approvals a JOIN session_turn_leases l ON l.conversation_id=a.session_id "
            "AND l.holder=json_extract(a.binding_json,'$.holder') AND l.generation=json_extract(a.binding_json,'$.generation') "
            "JOIN runtime_commands c ON c.session_id=a.session_id AND c.run_id=a.run_id AND c.principal_id=a.principal_id "
            "AND c.status='claimed' AND c.claimed_holder=l.holder AND c.claimed_generation=l.generation "
            "WHERE a.session_id=? AND a.principal_id=? AND a.profile_id=? AND a.agent_id=? "
            "AND a.status='pending' AND a.expires_at>? AND l.expires_at>? "
            "AND json_extract(c.command_json,'$.operation') IN ('submit','artifact') "
            "AND NOT EXISTS (SELECT 1 FROM runtime_commands stop WHERE stop.session_id=a.session_id "
            "AND stop.run_id=a.run_id AND json_extract(stop.command_json,'$.operation')='cancel')")
        approval_params = (*params, now, now)
        approval_count = conn.execute("SELECT COUNT(*),MIN(a.schema_version),MAX(a.schema_version) FROM " + approval_from,
                                      approval_params).fetchone()
        _require(not approval_count[0] or approval_count[1] == approval_count[2] == 1,
                 "unsupported_schema", "Unsupported approval projection schema")
        approvals = [{"request_id": item[0], "kind": "approval", "status": "pending"} for item in conn.execute(
            "SELECT a.approval_id FROM " + approval_from + " ORDER BY a.created_at,a.approval_id LIMIT ?",
            (*approval_params, MAX_SNAPSHOT_REFERENCES))]
        inputs = [{key: item[key] for key in ("request_id", "kind", "status")} for item in projection["outstanding_requests"]
                  if item.get("kind") == "input"]
        invocations = projection["unresolved_invocations"]
        counts = {"outstanding_requests": approval_count[0] + len(inputs), "artifacts": artifact_count,
                  "unresolved_effects": effect_count[0], "unresolved_invocations": len(invocations)}
        return {"outstanding_requests": (approvals + inputs)[:MAX_SNAPSHOT_REFERENCES], "artifacts": artifacts,
                "unresolved_effects": effects, "unresolved_invocations": invocations[:MAX_SNAPSHOT_REFERENCES],
                "reference_counts": counts, "reference_limit": MAX_SNAPSHOT_REFERENCES,
                "references_truncated": any(count > MAX_SNAPSHOT_REFERENCES for count in counts.values())}

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
        command = (self._runtime_command_on_conn(conn, session_id, payload["command_id"])
                   if event_type.startswith("command.") else None)
        operation = json.loads(command["command_json"])["operation"] if command is not None else None
        _project_runtime_event(projection, event, operation)
        conn.execute("UPDATE runtime_state SET revision=?,snapshot_json=? WHERE session_id=?",
                     (seq, _json(projection), session_id))
        return event

    def submit_runtime_command(self, session_id, actor, command, *, admission=None, budget_policy_json=None,
                               queued_cancel=False):
        """Durably accept once per authenticated principal/session/key; never dispatch here."""
        command_json, digest = _validated_command(actor, command)
        _require(not queued_cancel or (command["operation"] == "cancel" and callable(admission)),
                 "invalid_command", "Queue cancellation requires its atomic admission consumer")
        if budget_policy_json is not None:
            _require(isinstance(budget_policy_json, str) and len(budget_policy_json.encode("utf-8")) <= 16384,
                     "invalid_budget", "Accepted budget snapshot must be bounded JSON")
            from agent.budget_account import parse_budget_policy
            accepted_policy = parse_budget_policy({"runtime_budget": json.loads(budget_policy_json)})
            _require(accepted_policy is not None, "invalid_budget", "Empty budget snapshot must be represented as null")
            budget_policy_json = accepted_policy.snapshot
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
            if command["operation"] in {"submit", "artifact"}:
                from hermes_state_runtime_controls import assert_owner_running_on_conn, is_stop_only_schedule_control
                if not is_stop_only_schedule_control(command, admission):
                    assert_owner_running_on_conn(conn, actor)
            _revision(state["revision"], command.get("expected_revision"))
            run_id = uuid.uuid4().hex if command["operation"] in {"submit", "artifact"} else json.loads(state["snapshot_json"])["state"]["run_id"]
            if "target_run_id" in command:
                from hermes_state_runtime_targets import control_target_on_conn
                run_id = control_target_on_conn(self, conn, sid, command, queued_cancel=queued_cancel)
            elif queued_cancel:
                # Queue-only cancellation is a local terminal transition. Its
                # target must still be unclaimed in this same writer transaction;
                # it grants no authority to control a model run that won launch.
                pending = conn.execute("SELECT c.run_id FROM runtime_admission_queue q JOIN runtime_commands c "
                    "USING(session_id,command_id) WHERE q.session_id=? AND c.status='accepted' "
                    "AND q.state IN ('queued','running') ORDER BY q.enqueued_at DESC,q.command_id DESC LIMIT 1",
                    (sid,)).fetchone()
                _require(pending is not None, "no_active_run", "No unclaimed queue target remains")
                run_id = pending["run_id"]
            elif command["operation"] in {"steer", "cancel"}:
                active = conn.execute("SELECT 1 FROM runtime_commands WHERE session_id=? AND run_id=? AND status='claimed' AND json_extract(command_json,'$.operation')='submit'", (sid, run_id)).fetchone()
                _require(active is not None, "no_active_run", "Control target is no longer a claimed submit run")
            generation = conn.execute("SELECT turn_owner_generation FROM sessions WHERE id=?", (sid,)).fetchone()[0]
            event = self._append_runtime_event_on_conn(conn, sid, "command.accepted",
                {"command_id": command["command_id"], "operation": command["operation"]}, generation, run_id=run_id)
            receipt = {"schema_version": 1, "command_id": command["command_id"], "status": "accepted",
                       "durable_revision": event["seq"], "run_id": run_id}
            conn.execute("INSERT INTO runtime_commands(session_id,principal_id,command_id,idempotency_key,"
                "schema_version,digest,command_json,receipt_json,accepted_revision,run_id,budget_policy_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (sid, actor["principal_id"], command["command_id"], command["idempotency_key"], 1, digest,
                 command_json, _json(receipt), event["seq"], run_id, budget_policy_json))
            if admission is not None:
                admission(conn, sid, command_json, receipt)
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
                "budget_policy_json": row["budget_policy_json"],
                "claimed_holder": row["claimed_holder"], "claimed_generation": row["claimed_generation"],
                "result": json.loads(row["result_json"]) if row["result_json"] is not None else None}

    def read_runtime_command_receipt(self, session_id, command_id, *, message_cursor=None, message_limit=100):
        """Observe status and its revision in one snapshot without claiming work."""
        with self._runtime_read() as conn:
            sid = self._runtime_session_on_conn(conn, session_id)
            row = self._runtime_command_on_conn(conn, sid, command_id)
            state = self._runtime_state_on_conn(conn, sid)
            from hermes_state_runtime_messages import read_links
            links = read_links(conn, sid, row, cursor=message_cursor, limit=message_limit)
            return {"receipt": json.loads(row["receipt_json"]) if row is not None else None,
                    "status": row["status"] if row is not None else None,
                    "durable_revision": state["revision"] if state is not None else 0, **links}

    def read_runtime_run_accepted_at(self, session_id, run_id):
        """Original acceptance time; retries and queue recovery cannot reset it."""
        with self._runtime_read() as conn:
            sid = self._runtime_session_on_conn(conn, session_id)
            row = conn.execute("SELECT MIN(e.occurred_at) FROM runtime_events e JOIN runtime_commands c "
                "ON c.session_id=e.session_id AND c.accepted_revision=e.seq "
                "WHERE c.session_id=? AND c.run_id=? AND e.type='command.accepted' "
                "AND json_extract(c.command_json,'$.operation') IN ('submit','artifact')", (sid, run_id)).fetchone()
            # An absent/pruned acceptance cannot authorize a fresh deadline.
            return row[0] if row is not None else None

    def _claim_runtime_command_on_conn(self, conn, sid, command_id, *, holder, generation):
        self._runtime_fence_on_conn(conn, sid, holder, generation)
        row = self._runtime_command_on_conn(conn, sid, command_id)
        _require(row is not None, "command_not_found", "Runtime command does not exist")
        if row["status"] != "accepted":
            return False
        command = json.loads(row["command_json"])
        if "target_run_id" in command:
            from hermes_state_runtime_targets import control_target_on_conn
            control_target_on_conn(self, conn, sid, command, holder=holder, generation=generation)
        from agent.admission import assert_launch_on_conn
        assert_launch_on_conn(conn, sid, command_id)
        conn.execute("UPDATE runtime_commands SET status='claimed',claimed_holder=?,claimed_generation=? "
                     "WHERE session_id=? AND command_id=?", (holder, generation, sid, command_id))
        self._append_runtime_event_on_conn(conn, sid, "command.claimed", {"command_id": command_id},
                                           generation, run_id=row["run_id"])
        return True

    def claim_runtime_command(self, session_id, command_id, *, holder, generation):
        """One durable admission; a crashed claim is unresolved, never implicitly retried."""
        def write(conn):
            sid = self._runtime_session_on_conn(conn, session_id)
            return self._claim_runtime_command_on_conn(conn, sid, command_id,
                                                        holder=holder, generation=generation)
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
            command = json.loads(row["command_json"])
            if command["operation"] == "steer" and (result or {}).get("missed_steer"):
                state = self._runtime_state_on_conn(conn, sid)
                actor = {key: state[key] for key in ("principal_id", "profile_id", "agent_id")}
                self._settle_mission_delivery_on_conn(conn, sid, actor, row["run_id"], generation)
            return self._append_runtime_event_on_conn(conn, sid, f"command.{status}",
                {"command_id": command_id, "result": json.loads(result_json)}, generation, run_id=row["run_id"])
        return self._execute_write(write)

    def settle_inactive_runtime_control(self, session_id, actor, command_id):
        """Terminalize a stranded control; this grants no lease, dispatch or replay."""
        def write(conn):
            sid = self._mission_owner_on_conn(conn, session_id, actor)
            row = self._runtime_command_on_conn(conn, sid, command_id)
            _require(row is not None, "command_not_found", "Runtime command does not exist")
            command = json.loads(row["command_json"])
            _require(command["operation"] in {"steer", "cancel"}, "invalid_command", "Only inactive controls may settle")
            if row["status"] in _FINISH_STATES:
                return
            target = conn.execute("SELECT * FROM runtime_commands WHERE session_id=? AND run_id=? AND json_extract(command_json,'$.operation')='submit'", (sid, row["run_id"])).fetchone()
            lease = conn.execute("SELECT * FROM session_turn_leases WHERE conversation_id=?", (sid,)).fetchone()
            live = target is not None and target["status"] == "claimed" and lease is not None and lease["expires_at"] > time.time() and lease["holder"] == target["claimed_holder"] and lease["generation"] == target["claimed_generation"]
            _require(not live, "active_run", "Live controls must be settled by their current owner")
            result = {"outcome": "target_run_ended", "applied": False if row["status"] == "accepted" else None,
                      "missed_steer": command["operation"] == "steer"}
            generation = row["claimed_generation"] or (lease["generation"] if lease else 0)
            conn.execute("UPDATE runtime_commands SET status='blocked',result_json=? WHERE session_id=? AND command_id=?", (_json(result), sid, command_id))
            self._settle_mission_delivery_on_conn(conn, sid, actor, row["run_id"], generation)
            self._append_runtime_event_on_conn(conn, sid, "command.blocked", {"command_id": command_id, "result": result}, generation, run_id=row["run_id"])
        self._execute_write(write)
        return self.read_runtime_command(session_id, command_id)

    def append_runtime_event(self, session_id, event_type, payload, *, holder, generation,
                             schema_version=1, expected_revision=None, transaction_guard=None, **correlations):
        _version(schema_version)
        _require(transaction_guard is None or callable(transaction_guard), "invalid_command", "Invalid transaction guard")
        _require(isinstance(event_type, str) and event_type in _RECORDED_TYPES, "invalid_command", "Use transactional command/checkpoint operations")
        _require(isinstance(payload, dict), "invalid_command", "Event payload must be an object")
        _json(payload)
        def write(conn):
            if transaction_guard is not None:
                transaction_guard(conn, after_append=False)
            sid = self._runtime_session_on_conn(conn, session_id)
            self._runtime_fence_on_conn(conn, sid, holder, generation)
            row = self._runtime_state_on_conn(conn, sid, create=True)
            _revision(row["revision"], expected_revision)
            event = self._append_runtime_event_on_conn(conn, sid, event_type, payload, generation, **correlations)
            # Optional decision writes may have been abandoned while storage
            # waited. Reject them inside this transaction, including retries.
            if transaction_guard is not None:
                transaction_guard(conn, after_append=True)
            return event
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

    def _publish_runtime_checkpoint_on_conn(self, conn, session_id, checkpoint, *, holder, generation, expected_revision, included_seq):
        metadata = _checkpoint_metadata(checkpoint)
        _require(type(included_seq) is int and included_seq >= 0, "invalid_command", "Invalid checkpoint watermark")
        _require(type(expected_revision) is int, "invalid_command", "Checkpoint requires revision CAS")
        sid = self._runtime_session_on_conn(conn, session_id)
        self._runtime_fence_on_conn(conn, sid, holder, generation)
        row = self._runtime_state_on_conn(conn, sid, create=True)
        _revision(row["revision"], expected_revision)
        _require(included_seq == row["revision"], "revision_conflict", "Checkpoint source watermark changed")
        checkpoint_id = uuid.uuid4().hex
        projection = json.loads(row["snapshot_json"])
        for key in ("outstanding_requests", "artifacts", "unresolved_effects", "unresolved_invocations"):
            projection[key] = metadata[key]
        conn.execute("UPDATE runtime_state SET snapshot_json=? WHERE session_id=?", (_json(projection), sid))
        event = self._append_runtime_event_on_conn(conn, sid, "checkpoint.published",
            {"checkpoint_id": checkpoint_id, "included_seq": included_seq}, generation)
        saved = {**metadata, "snapshot": projection}
        prior_checkpoint = conn.execute("SELECT checkpoint_json FROM runtime_checkpoints WHERE session_id=?", (sid,)).fetchone()
        if prior_checkpoint:
            context_ref = json.loads(prior_checkpoint[0]).get("context_projection_ref")
            if context_ref is not None:
                _require(isinstance(context_ref, dict) and set(context_ref) == {"projection_id", "session_id", "schema_version", "included_seq"},
                         "unsupported_schema", "Unsupported checkpoint context reference")
                _version(context_ref["schema_version"])
                saved["context_projection_ref"] = context_ref
        conn.execute("INSERT INTO runtime_checkpoints(session_id,checkpoint_id,schema_version,included_seq,"
            "published_seq,generation,checkpoint_json) VALUES(?,?,?,?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET "
            "checkpoint_id=excluded.checkpoint_id,schema_version=excluded.schema_version,included_seq=excluded.included_seq,"
            "published_seq=excluded.published_seq,generation=excluded.generation,checkpoint_json=excluded.checkpoint_json",
            (sid, checkpoint_id, 1, included_seq, event["seq"], generation, _json(saved)))
        return {"schema_version": 1, "checkpoint_id": checkpoint_id, "included_seq": included_seq,
                "published_seq": event["seq"], "revision": event["seq"]}

    def publish_runtime_checkpoint(self, session_id, checkpoint, *, holder, generation, expected_revision, included_seq):
        return self._execute_write(lambda conn: self._publish_runtime_checkpoint_on_conn(
            conn, session_id, checkpoint, holder=holder, generation=generation,
            expected_revision=expected_revision, included_seq=included_seq))

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
