"""BE06 durable effect intent and exact one-use approval records.

The inherited SessionDB writer and turn lease are the only transaction/ownership
boundary. These records survive transcript deletion and never authorize replay of
an uncertain external action. Adapters persist references/digests, not credentials
or arbitrary provider responses; invocation completion is not an effect receipt.
"""
from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import math
import re
import time
import uuid

from hermes_state_runtime import RuntimeStoreError, _identifier

EFFECT_SCHEMA_VERSION = 1
EFFECT_STATES = frozenset({"prepared", "dispatched", "confirmed", "failed", "outcome_unknown", "reconciliation_required"})
MAX_EFFECT_RECORDS = 65536
MAX_APPROVAL_RECORDS = 65536
MAX_EVIDENCE_RECORDS = 262144
MAX_EFFECT_EVIDENCE = 128
_ACTOR_KEYS = ("principal_id", "profile_id", "agent_id")
_BINDING_KEYS = ("session_id", "run_id", "holder", "generation", "action_digest", "input_digest", "target_ref",
                 "policy_version", "policy_digest", "input_revision", "artifact_revision")
_EVIDENCE_KEYS = frozenset({"kind", "reference", "sha256", "observed_at", "reason", "receipt_id", "artifact_id",
                            "version", "state", "size", "mime"})
_INPUT_REF_KEYS = frozenset({"artifact_id", "version", "locator", "sha256", "size", "mime", "producing_run"})
_TRANSITIONS = {
    "prepared": frozenset({"failed"}),
    "dispatched": frozenset({"confirmed", "failed", "outcome_unknown", "reconciliation_required"}),
    "outcome_unknown": frozenset({"confirmed", "failed", "reconciliation_required"}),
    "reconciliation_required": frozenset({"confirmed", "failed", "outcome_unknown"}),
    "confirmed": frozenset(), "failed": frozenset(),
}


class EffectStoreError(RuntimeStoreError):
    """A rejected effect/approval transition never permits dispatch."""


def _check(condition, code, message):
    if not condition:
        raise EffectStoreError(code, message)


def _json(value, *, maximum=16384):
    def validate(item):
        if isinstance(item, dict):
            _check(all(isinstance(key, str) for key in item), "invalid_effect", "Object keys must be strings")
            for child in item.values():
                validate(child)
        elif isinstance(item, list):
            for child in item:
                validate(child)
        else:
            _check(item is None or type(item) in (str, int, float, bool), "invalid_effect", "Expected finite JSON")
    try:
        validate(value)
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError, RecursionError) as exc:
        raise EffectStoreError("invalid_effect", "Expected bounded finite JSON") from exc
    _check(len(encoded.encode()) <= maximum, "effect_payload_limit", "Use bounded digest/reference records")
    return encoded


def effect_digest(value):
    """Canonical JSON digest for adapter-owned operation-specific input normalization."""
    return hashlib.sha256(_json(value, maximum=65536).encode()).hexdigest()


def _digest(value, name):
    _check(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None,
           "invalid_effect", f"{name} must be a SHA-256 digest")
    return value


def _actor(value):
    _check(isinstance(value, Mapping), "identity_mismatch", "Trusted actor required")
    return {key: _identifier(value.get(key), key) for key in _ACTOR_KEYS}


def _deadline(value):
    _check(type(value) in (int, float) and math.isfinite(value) and 0 < value <= 253402300799,
           "invalid_effect", "Expected finite UTC expiry")
    return float(value)


def _reference(value, name):
    _check(isinstance(value, str) and 0 < len(value.encode()) <= 1024 and value.strip() == value
           and not any(ord(char) < 32 for char in value), "invalid_effect", f"{name} must be a bounded reference")
    return value


def _metadata(value, allowed):
    if value is None:
        return None
    _check(isinstance(value, dict) and set(value) <= allowed and bool(value),
           "invalid_effect", "Only typed receipt or artifact reference fields may be persisted")
    for key, item in value.items():
        _check(type(item) in (str, int, float, bool) or item is None,
               "invalid_effect", "Receipt references cannot contain private nested payloads")
        if key == "sha256":
            _digest(item, key)
    return _json(value, maximum=8192)


def _binding(session_id, run_id, holder, generation, action_digest, input_digest, target_ref,
             policy_version, policy_digest, input_revision, artifact_revision):
    _check(type(generation) is int and generation > 0, "stale_owner", "A live owner generation is required")
    _check(type(policy_version) in (str, int) and type(policy_version) is not bool,
           "invalid_effect", "Expected an exact policy version")
    return dict(session_id=_identifier(session_id, "session_id"), run_id=_identifier(run_id, "run_id"),
                holder=_identifier(holder, "holder"), generation=generation,
                action_digest=_digest(action_digest, "action_digest"), input_digest=_digest(input_digest, "input_digest"),
                target_ref=_reference(target_ref, "target_ref"), policy_version=_identifier(str(policy_version), "policy_version"),
                policy_digest=_digest(policy_digest, "policy_digest"),
                input_revision=_reference(input_revision, "input_revision"),
                artifact_revision=_reference(artifact_revision, "artifact_revision"))


def _effect_result(row):
    _check(row["schema_version"] == EFFECT_SCHEMA_VERSION, "unsupported_schema", "Unsupported effect schema")
    result = dict(row)
    for key in ("intent_json",):
        result.pop(key)
    result["input_ref"] = json.loads(result.pop("input_ref_json"))
    result["exactly_once_external"] = False
    result["replay_permitted"] = False
    return result


def _approval_result(row):
    _check(row["schema_version"] == EFFECT_SCHEMA_VERSION, "unsupported_schema", "Unsupported approval schema")
    result = dict(row)
    result["binding"] = json.loads(result.pop("binding_json"))
    return result


class SessionEffectsMixin:
    """No network calls, inference, separate database connections or background writer."""

    @staticmethod
    def _effect_quota_on_conn(conn, table, maximum):
        _check(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] < maximum,
               "effect_storage_limit", "Durable audit capacity reached; do not erase uncertain records")

    def _effect_run_on_conn(self, conn, session_id, actor, run_id, holder, generation, *, dispatch=False, finalizing_artifact=False):
        sid = self._runtime_session_on_conn(conn, session_id)
        self._runtime_fence_on_conn(conn, sid, holder, generation)
        row = self._runtime_state_on_conn(conn, sid)
        _check(row is not None and all(row[key] == actor[key] for key in _ACTOR_KEYS),
               "identity_mismatch", "Effect actor differs from the accepted runtime actor")
        rows = conn.execute("SELECT * FROM runtime_commands WHERE session_id=? AND run_id=? AND principal_id=?",
                            (sid, run_id, actor["principal_id"])).fetchall()
        command = next((item for item in rows if json.loads(item["command_json"])["operation"] == "submit"), None)
        _check(command is not None, "effect_run_not_found", "An accepted runtime run is required")
        if dispatch:
            _check(command["status"] == "claimed" and command["claimed_holder"] == holder
                   and command["claimed_generation"] == generation,
                   "claim_conflict", "Effect dispatch requires the live claimed runtime run")
            cancelled = conn.execute("SELECT command_json FROM runtime_commands WHERE session_id=? AND run_id=?",
                                     (sid, run_id)).fetchall()
            _check(finalizing_artifact or not any(json.loads(item[0])["operation"] == "cancel" for item in cancelled),
                   "run_cancelled", "Cancellation stops new effects; dispatched effects remain unresolved")
        return sid

    @staticmethod
    def _effect_on_conn(conn, effect_id, actor):
        _identifier(effect_id, "effect_id")
        row = conn.execute("SELECT * FROM runtime_effects WHERE effect_id=?", (effect_id,)).fetchone()
        _check(row is not None, "effect_not_found", "Effect does not exist")
        _check(all(row[key] == actor[key] for key in _ACTOR_KEYS), "identity_mismatch", "Effect belongs to another actor")
        _check(row["schema_version"] == EFFECT_SCHEMA_VERSION, "unsupported_schema", "Unsupported effect schema")
        return row

    def _effect_event_on_conn(self, conn, row, generation):
        return self._append_runtime_event_on_conn(conn, row["session_id"], "effect.recorded",
            {"state": row["state"], "operation_type": row["operation_type"]}, generation,
            run_id=row["run_id"], operation_id=row["operation_id"], effect_id=row["effect_id"])

    def _prepare_effect_on_conn(self, conn, session_id, actor, *, holder, generation, run_id, operation_id,
                               intent_key, operation_type, input_digest, target_ref, policy_digest, policy_version,
                               input_revision, artifact_revision, action_digest=None, provider_idempotency="unsupported",
                               idempotency_key=None, approval_id=None, input_ref=None):
        actor = _actor(actor)
        sid = self._effect_run_on_conn(conn, session_id, actor, run_id, holder, generation, dispatch=True,
                                       finalizing_artifact=operation_type == "artifact_publish")
        binding = _binding(sid, run_id, holder, generation, input_digest if action_digest is None else action_digest, input_digest,
                           target_ref, policy_version, policy_digest, input_revision, artifact_revision)
        for name, value in (("operation_id", operation_id), ("intent_key", intent_key), ("operation_type", operation_type)):
            _identifier(value, name)
        _check(isinstance(provider_idempotency, str) and provider_idempotency in {"supported", "unsupported"}, "invalid_effect", "Declare provider idempotency support explicitly")
        _check((provider_idempotency == "supported") == (idempotency_key is not None),
               "invalid_effect", "Only a supporting provider may receive an idempotency key")
        if idempotency_key is not None:
            _identifier(idempotency_key, "idempotency_key")
        if approval_id is not None:
            _identifier(approval_id, "approval_id")
        input_ref_json = _metadata(input_ref, _INPUT_REF_KEYS) or "null"
        # Owner changes do not turn an identical intent into a new external action.
        # The original owner remains bound in the stored scope and approval.
        intent = {key: value for key, value in binding.items() if key not in {"holder", "generation"}}
        intent.update(operation_id=operation_id, intent_key=intent_key, operation_type=operation_type,
                      provider_idempotency=provider_idempotency, idempotency_key=idempotency_key,
                      approval_id=approval_id, input_ref=json.loads(input_ref_json))
        intent_json = _json(intent)
        duplicates = conn.execute("SELECT * FROM runtime_effects WHERE session_id=? AND run_id=? "
            "AND (intent_key=? OR operation_id=?)", (sid, run_id, intent_key, operation_id)).fetchall()
        if duplicates:
            _check(len(duplicates) == 1 and duplicates[0]["intent_json"] == intent_json,
                   "idempotency_conflict", "Effect intent key or operation ID already names a different action")
            return _effect_result(duplicates[0])
        self._effect_quota_on_conn(conn, "runtime_effects", MAX_EFFECT_RECORDS)
        now, effect_id = time.time(), uuid.uuid4().hex
        columns = {"effect_id": effect_id, "schema_version": EFFECT_SCHEMA_VERSION, **actor,
                   **binding, "prepared_generation": generation, "operation_id": operation_id,
                   "intent_key": intent_key, "operation_type": operation_type, "intent_json": intent_json,
                   "intent_digest": effect_digest(intent), "provider_idempotency": provider_idempotency,
                   "idempotency_key": idempotency_key, "approval_id": approval_id, "input_ref_json": input_ref_json,
                   "state": "prepared", "created_at": now, "updated_at": now}
        conn.execute(f"INSERT INTO runtime_effects({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
                     tuple(columns.values()))
        row = self._effect_on_conn(conn, effect_id, actor)
        self._effect_event_on_conn(conn, row, generation)
        return _effect_result(row)

    def prepare_effect(self, session_id, actor, **kwargs):
        """Commit exact intent before adapter dispatch. A distinct intent_key means a new action."""
        return self._execute_write(lambda conn: self._prepare_effect_on_conn(conn, session_id, actor, **kwargs))

    def dispatch_effect(self, effect_id, actor, *, holder, generation):
        """Exactly one local admission; False never authorizes an external call or retry."""
        actor = _actor(actor)
        def write(conn):
            row = self._effect_on_conn(conn, effect_id, actor)
            self._runtime_fence_on_conn(conn, row["session_id"], holder, generation)
            if row["state"] != "prepared":
                return {**_effect_result(row), "dispatched_now": False}
            self._effect_run_on_conn(conn, row["session_id"], actor, row["run_id"], holder, generation, dispatch=True,
                                     finalizing_artifact=row["operation_type"] == "artifact_publish")
            _check(row["holder"] == holder and row["prepared_generation"] == generation,
                   "approval_mismatch", "A successor owner cannot inherit dispatch authority")
            if row["approval_id"]:
                binding = {key: row[key] for key in _BINDING_KEYS}
                self._consume_effect_approval_on_conn(conn, row["approval_id"], actor, binding, effect_id)
            now = time.time()
            conn.execute("UPDATE runtime_effects SET state='dispatched',dispatched_at=?,updated_at=? WHERE effect_id=?",
                         (now, now, effect_id))
            row = self._effect_on_conn(conn, effect_id, actor)
            self._effect_event_on_conn(conn, row, generation)
            return {**_effect_result(row), "dispatched_now": True}
        return self._execute_write(write)

    def record_effect_outcome(self, effect_id, actor, *, holder, generation, state, receipt=None, evidence=None):
        """Record adapter proof, including successor read-only reconciliation. Never resets dispatch."""
        actor = _actor(actor)
        _check(isinstance(state, str) and state in EFFECT_STATES - {"prepared", "dispatched"},
               "invalid_effect", "Invalid effect outcome")
        receipt_json, evidence_json = _metadata(receipt, _EVIDENCE_KEYS), _metadata(evidence, _EVIDENCE_KEYS)
        _check(receipt_json is not None or evidence_json is not None,
               "effect_evidence_required", "Every outcome needs a typed receipt or evidence reference")
        def write(conn):
            row = self._effect_on_conn(conn, effect_id, actor)
            self._runtime_fence_on_conn(conn, row["session_id"], holder, generation)
            _check(state in _TRANSITIONS[row["state"]], "effect_transition_conflict", "Effect outcome cannot be overwritten or replayed")
            if row["state"] == "dispatched" and (row["holder"] != holder or row["generation"] != generation):
                _check(state in {"outcome_unknown", "reconciliation_required"},
                       "effect_reconciliation_required", "A successor must record uncertainty before reconciling old dispatch")
            if row["state"] in {"outcome_unknown", "reconciliation_required"} and state in {"confirmed", "failed"}:
                _check(evidence_json is not None, "effect_evidence_required", "Reconciliation needs observed evidence")
            self._effect_quota_on_conn(conn, "runtime_effect_evidence", MAX_EVIDENCE_RECORDS)
            sequence = conn.execute("SELECT COALESCE(MAX(sequence),0)+1 FROM runtime_effect_evidence WHERE effect_id=?",
                                    (effect_id,)).fetchone()[0]
            _check(sequence <= MAX_EFFECT_EVIDENCE, "effect_storage_limit", "Effect evidence capacity reached; retain unresolved history")
            now = time.time()
            conn.execute("INSERT INTO runtime_effect_evidence(evidence_id,effect_id,sequence,generation,from_state,state,receipt_json,evidence_json,created_at) "
                         "VALUES(?,?,?,?,?,?,?,?,?)", (uuid.uuid4().hex, effect_id, sequence, generation, row["state"], state,
                                                       receipt_json, evidence_json, now))
            conn.execute("UPDATE runtime_effects SET state=?,generation=?,updated_at=? WHERE effect_id=?",
                         (state, generation, now, effect_id))
            row = self._effect_on_conn(conn, effect_id, actor)
            self._effect_event_on_conn(conn, row, generation)
            return _effect_result(row)
        return self._execute_write(write)

    def get_effect(self, effect_id, actor):
        actor = _actor(actor)
        with self._runtime_read() as conn:
            row = self._effect_on_conn(conn, effect_id, actor)
            result = _effect_result(row)
            result["evidence"] = [{**dict(item), "receipt": json.loads(item["receipt_json"]) if item["receipt_json"] else None,
                                   "evidence": json.loads(item["evidence_json"]) if item["evidence_json"] else None}
                                  for item in conn.execute("SELECT evidence_id,sequence,generation,from_state,state,receipt_json,evidence_json,created_at "
                                                           "FROM runtime_effect_evidence WHERE effect_id=? ORDER BY sequence", (effect_id,))]
            for evidence in result["evidence"]:
                evidence.pop("receipt_json")
                evidence.pop("evidence_json")
            return result

    def list_effects(self, session_id, actor, *, run_id=None, unresolved_only=False, limit=100):
        actor = _actor(actor)
        _identifier(session_id, "session_id")
        _check(type(limit) is int and 1 <= limit <= 500, "invalid_effect", "Effect page must be bounded")
        with self._runtime_read() as conn:
            if conn.execute("SELECT 1 FROM sessions WHERE id=?", (session_id,)).fetchone():
                session_id = self._runtime_session_on_conn(conn, session_id)
            sql = "SELECT * FROM runtime_effects WHERE session_id=? AND principal_id=? AND profile_id=? AND agent_id=?"
            values = [session_id, *(actor[key] for key in _ACTOR_KEYS)]
            if run_id is not None:
                sql += " AND run_id=?"
                values.append(_identifier(run_id, "run_id"))
            if unresolved_only:
                sql += " AND state NOT IN ('confirmed','failed')"
            return [_effect_result(row) for row in conn.execute(sql + " ORDER BY created_at,effect_id LIMIT ?", (*values, limit))]

    @staticmethod
    def _effect_approval_on_conn(conn, approval_id, actor):
        _identifier(approval_id, "approval_id")
        row = conn.execute("SELECT * FROM runtime_effect_approvals WHERE approval_id=?", (approval_id,)).fetchone()
        _check(row is not None, "approval_not_found", "Approval does not exist")
        _check(all(row[key] == actor[key] for key in _ACTOR_KEYS), "identity_mismatch", "Approval belongs to another actor")
        _check(row["schema_version"] == EFFECT_SCHEMA_VERSION, "unsupported_schema", "Unsupported approval schema")
        return row

    def request_effect_approval(self, session_id, actor, *, holder, generation, run_id, action_digest, input_digest,
                                target_ref, policy_version, policy_digest, input_revision, artifact_revision,
                                expires_at, approval_id=None):
        actor, expires_at = _actor(actor), _deadline(expires_at)
        approval_id = _identifier(approval_id or uuid.uuid4().hex, "approval_id")
        def write(conn):
            sid = self._effect_run_on_conn(conn, session_id, actor, run_id, holder, generation, dispatch=True)
            binding = _binding(sid, run_id, holder, generation, action_digest, input_digest, target_ref,
                               policy_version, policy_digest, input_revision, artifact_revision)
            _check(time.time() < expires_at <= time.time() + 3600,
                   "approval_expired", "Exact approval must expire within one hour")
            binding_json = _json(binding)
            approval_digest = effect_digest({"approval_id": approval_id, "actor": actor, "binding": binding, "expires_at": expires_at})
            existing = conn.execute("SELECT * FROM runtime_effect_approvals WHERE approval_id=?", (approval_id,)).fetchone()
            if existing:
                _check(existing["approval_digest"] == approval_digest, "idempotency_conflict", "Approval ID already names another scope")
                return _approval_result(existing)
            self._effect_quota_on_conn(conn, "runtime_effect_approvals", MAX_APPROVAL_RECORDS)
            now = time.time()
            conn.execute("INSERT INTO runtime_effect_approvals(approval_id,schema_version,session_id,run_id,principal_id,profile_id,agent_id,"
                         "binding_json,approval_digest,expires_at,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                         (approval_id, EFFECT_SCHEMA_VERSION, sid, run_id, *(actor[key] for key in _ACTOR_KEYS),
                          binding_json, approval_digest, expires_at, "pending", now))
            self._append_runtime_event_on_conn(conn, sid, "approval.requested", {"status": "pending", "expires_at": expires_at},
                                               generation, run_id=run_id, approval_id=approval_id)
            return _approval_result(self._effect_approval_on_conn(conn, approval_id, actor))
        return self._execute_write(write)

    def resolve_effect_approval(self, approval_id, actor, *, holder, generation, approval_digest, choice):
        """Only a trusted human surface invokes this; model-provided answers are not authority."""
        actor = _actor(actor)
        _digest(approval_digest, "approval_digest")
        _check(isinstance(choice, str) and choice in {"once", "deny"}, "invalid_effect", "Approval choices are exact once or deny")
        def write(conn):
            row = self._effect_approval_on_conn(conn, approval_id, actor)
            binding = json.loads(row["binding_json"])
            self._effect_run_on_conn(conn, row["session_id"], actor, row["run_id"], holder, generation, dispatch=True)
            _check(binding["holder"] == holder and binding["generation"] == generation
                   and row["status"] == "pending" and row["approval_digest"] == approval_digest,
                   "approval_mismatch", "Approval answer changed, was answered, or belongs to an earlier owner")
            _check(row["expires_at"] > time.time(), "approval_expired", "Approval expired")
            status = "approved" if choice == "once" else "denied"
            conn.execute("UPDATE runtime_effect_approvals SET status=?,resolved_at=? WHERE approval_id=?", (status, time.time(), approval_id))
            self._append_runtime_event_on_conn(conn, row["session_id"], "approval.resolved", {"status": status}, generation,
                                               run_id=row["run_id"], approval_id=approval_id)
            return _approval_result(self._effect_approval_on_conn(conn, approval_id, actor))
        return self._execute_write(write)

    def _consume_effect_approval_on_conn(self, conn, approval_id, actor, binding, consumer_id):
        row = self._effect_approval_on_conn(conn, approval_id, actor)
        self._effect_run_on_conn(conn, binding["session_id"], actor, binding["run_id"], binding["holder"], binding["generation"], dispatch=True)
        _check(row["status"] == "approved" and row["binding_json"] == _json(binding),
               "approval_mismatch", "Approval is not approved for this exact unconsumed action")
        _check(row["expires_at"] > time.time(), "approval_expired", "Approval expired")
        _identifier(consumer_id, "consumer_id")
        conn.execute("UPDATE runtime_effect_approvals SET status='consumed',consumed_at=?,consumer_id=? WHERE approval_id=?",
                     (time.time(), consumer_id, approval_id))
        self._append_runtime_event_on_conn(conn, row["session_id"], "approval.resolved", {"status": "consumed"}, binding["generation"],
                                           run_id=row["run_id"], approval_id=approval_id)
        return _approval_result(self._effect_approval_on_conn(conn, approval_id, actor))

    def consume_effect_approval(self, approval_id, actor, *, consumer_id, **binding):
        actor = _actor(actor)
        scope = _binding(**binding)
        return self._execute_write(lambda conn: self._consume_effect_approval_on_conn(conn, approval_id, actor, scope, consumer_id))

    def get_effect_approval(self, approval_id, actor):
        actor = _actor(actor)
        with self._runtime_read() as conn:
            return _approval_result(self._effect_approval_on_conn(conn, approval_id, actor))

    def list_effect_approvals(self, session_id, actor, *, run_id=None, limit=100):
        actor = _actor(actor)
        _check(type(limit) is int and 1 <= limit <= 500, "invalid_effect", "Approval page must be bounded")
        with self._runtime_read() as conn:
            sid = self._runtime_session_on_conn(conn, session_id)
            sql = "SELECT * FROM runtime_effect_approvals WHERE session_id=? AND principal_id=? AND profile_id=? AND agent_id=?"
            values = [sid, *(actor[key] for key in _ACTOR_KEYS)]
            if run_id is not None:
                sql += " AND run_id=?"
                values.append(_identifier(run_id, "run_id"))
            return [_approval_result(row) for row in conn.execute(sql + " ORDER BY created_at,approval_id LIMIT ?", (*values, limit))]
