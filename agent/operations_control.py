"""Host-owned, previewed maintenance using the existing lease and recovery gates.

This is a local operator API, never a model tool. The host must show the complete
plan and obtain authorization for its digest before calling ``apply_repair``.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import math
from pathlib import Path
import secrets
import time

from agent.result_artifacts import artifact_actor
from hermes_state_runtime import RuntimeStoreError
from tools.capability_broker import require_live_policy

_REPAIRS = {"reconcile-effect", "retry-delivery", "revoke-lease", "rebuild-index", "restore-checkpoint"}


class OperationsError(RuntimeStoreError):
    pass


def require(condition, code):
    if not condition:
        raise OperationsError(code, code.replace("_", " "))


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode()).hexdigest()


def authority(db, context):
    require(context is not None and require_live_policy(require_run=False) == context,
            "operations_identity_required")
    require(Path(db.db_path).resolve() == Path(context.profile_home) / "state.db",
            "operations_wrong_profile")
    sid = context.identity.session_id
    require(db.get_session_model_config_value(sid, "agent_identity") == context.identity.to_record(),
            "operations_identity_mismatch")
    actor = artifact_actor(context)
    with db._runtime_read() as conn:
        canonical = db._runtime_session_on_conn(conn, sid)
        state = db._runtime_state_on_conn(conn, canonical)
        require(state is not None and all(state[key] == value for key, value in actor.items()),
                "operations_runtime_required")
    return canonical, actor


def inspect_runtime(db, context):
    sid, actor = authority(db, context)
    with db._runtime_read() as conn:
        snapshot = db._runtime_snapshot_on_conn(conn, sid)
        lease = conn.execute("SELECT generation,expires_at FROM session_turn_leases WHERE conversation_id=?",
                             (sid,)).fetchone()
        effects = conn.execute("SELECT effect_id,state,operation_type FROM runtime_effects WHERE session_id=? "
            "AND principal_id=? AND profile_id=? AND agent_id=? ORDER BY created_at DESC LIMIT 101",
            (sid, actor["principal_id"], actor["profile_id"], actor["agent_id"])).fetchall()
        deliveries = conn.execute("SELECT obligation_id,state,attempts,max_attempts,deadline_at FROM delivery_obligations "
            "WHERE session_key=? AND authority='runtime.v1' ORDER BY created_at DESC LIMIT 101", (sid,)).fetchall()
        budgets = conn.execute("SELECT account_id,state,deadline,consumed_json,reserved_json FROM budget_accounts "
            "WHERE session_id=? AND principal_id=? AND profile_id=? AND agent_id=? LIMIT 101",
            (sid, actor["principal_id"], actor["profile_id"], actor["agent_id"])).fetchall()
        checkpoint = conn.execute("SELECT checkpoint_id,included_seq,published_seq FROM runtime_checkpoints "
                                  "WHERE session_id=?", (sid,)).fetchone()
    # No model output, titles, user text, endpoint URLs, policy secrets, holders,
    # artifact locators, receipt bodies or arbitrary exception strings escape.
    return {"schema_version": 1, "actor": actor, "session_id": sid,
        "revision": snapshot["revision"], "replay_cursor": snapshot["last_cursor"],
        "state": {key: snapshot["state"].get(key) for key in ("status", "run_id", "last_command_id")},
        "ownership": {"active": lease is not None and lease["expires_at"] > time.time(),
                      "generation": lease["generation"] if lease else None},
        "waiting_reason": "unresolved_effect" if snapshot["unresolved_effects"] else (
            "input_or_approval" if snapshot["outstanding_requests"] else "none_recorded"),
        "effects": [dict(row) for row in effects[:100]],
        "deliveries": [dict(row) for row in deliveries[:100]],
        "budgets": [{"account_id": row["account_id"], "state": row["state"],
                     "deadline": row["deadline"], "consumed": json.loads(row["consumed_json"]),
                     "reserved": json.loads(row["reserved_json"])} for row in budgets[:100]],
        "truncated": any(len(rows) > 100 for rows in (effects, deliveries, budgets)),
        "memory_backend": context.policy.memory_backend,
        "connection_health": {"sqlite": "readable", "provider": "not_probed", "memory_remote": "not_probed"},
        "laya_mode": "not_certified", "checkpoint": dict(checkpoint) if checkpoint else None,
        "sensitive_ingestion_certified": False}


def qualify_checkpoint_restore(db, context):
    """Qualify a local derived-projection repair, never a history/profile rewind."""
    from agent.operations_checkpoint_recovery import qualify
    return qualify(db, context)


def _target(db, context, action, target_id):
    sid, actor = authority(db, context)

    def checkpoint():
        from agent.operations_checkpoint_recovery import reconstruct_on_conn
        require(target_id == sid, "operations_target_mismatch")
        with db._runtime_read() as conn:
            return reconstruct_on_conn(db, conn, sid, context)[1]

    def effect():
        row = db.get_effect(target_id, actor)
        require(row is not None and row["session_id"] == sid, "operations_target_mismatch")
        return {key: row[key] for key in ("effect_id", "state", "updated_at", "operation_type")}

    def delivery():
        row = db.read_runtime_delivery(sid, actor, target_id)
        return {key: row[key] for key in ("delivery_id", "state", "attempt_count", "max_attempts", "deadline_at")}

    def lease():
        require(target_id == sid, "operations_target_mismatch")
        row = db.get_session_turn_lease(sid)
        require(row is not None, "operations_no_live_lease")
        return {"generation": row["generation"], "holder_digest": digest(row["holder"])}

    def index():
        require(target_id == sid, "operations_target_mismatch")
        # FTS is profile-wide. A session-scoped operator may not silently repair
        # another actor's indexes; only bounded single-actor stores qualify.
        with db._runtime_read() as conn:
            rows = conn.execute("SELECT id,model_config FROM sessions LIMIT 101").fetchall()
            require(len(rows) <= 100, "operations_index_scope_too_large")
            for row in rows:
                identity = json.loads(row["model_config"] or "{}").get("agent_identity", {})
                require(all(identity.get(key) == value for key, value in actor.items()), "operations_index_scope_mismatch")
            count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            require(count <= 10000 and Path(db.db_path).stat().st_size <= 64 * 1024 * 1024,
                    "operations_index_scope_too_large")
        return {"session_ids": sorted(row["id"] for row in rows), "message_count": count}

    return {"restore-checkpoint": checkpoint, "reconcile-effect": effect, "retry-delivery": delivery,
            "revoke-lease": lease, "rebuild-index": index}[action]()


def preview_repair(db, context, action, target_id, *, expires_at=None):
    require(action in _REPAIRS, "operations_repair_unsupported")
    require(isinstance(target_id, str) and 0 < len(target_id) <= 256, "operations_invalid_target")
    expiry = time.time() + 300 if expires_at is None else expires_at
    require(type(expiry) in (int, float) and math.isfinite(expiry)
            and time.time() < expiry <= time.time() + 301, "operations_preview_expired")
    sid, actor = authority(db, context)
    target = _target(db, context, action, target_id)
    with db._runtime_read() as conn:
        before_revision = db._runtime_state_on_conn(conn, sid)["revision"]
    plan = {"schema_version": 1, "action": action, "actor": actor, "session_id": sid,
        "target_id": target_id, "affected_ids": target.get("session_ids", [target_id]),
        "before_revision": before_revision, "target": target,
        "invariant_checks": ["live_policy", "exact_actor", "owning_profile", "preview_cas", "mandatory_journal"],
        "expected_after": {"reconcile-effect": "evidence_only_no_redispatch",
            "retry-delivery": "same_outbox_same_attempt_budget_no_send",
            "revoke-lease": "old_generation_cannot_dispatch_remote_work_may_continue",
            "rebuild-index": "derived_indexes_rebuilt_no_source_change",
            "restore-checkpoint": "derived_projection_reconstructed_no_history_or_effect_rewind"}[action],
        "expires_at": expiry}
    return {**plan, "plan_digest": digest(plan)}


def validate_plan(db, context, plan, authorization_digest):
    require(isinstance(plan, dict) and authorization_digest == plan.get("plan_digest"),
            "operations_exact_authorization_required")
    fresh = preview_repair(db, context, plan.get("action"), plan.get("target_id"),
                           expires_at=plan.get("expires_at"))
    require(fresh == plan, "operations_preview_changed")
    return fresh


@contextmanager
def maintenance_lease(db, context):
    sid, _ = authority(db, context)
    holder = "operations:" + secrets.token_hex(16)
    require(db.try_acquire_session_turn_lease(sid, holder, ttl_seconds=60), "operations_session_busy")
    generation = db.get_session_turn_lease(sid)["generation"]
    try:
        yield holder, generation
    finally:
        db.release_session_turn_lease(sid, holder, generation=generation)


def _record(db, sid, plan, phase, holder, generation, *, outcome=None, expected_revision=None):
    payload = {"action": plan["action"], "plan_digest": plan["plan_digest"],
               "affected_ids": plan["affected_ids"], "before_revision": plan["before_revision"]}
    if outcome is not None:
        payload["outcome"] = outcome
    return db.append_runtime_event(sid, "operations.repair_" + phase, payload,
        holder=holder, generation=generation, expected_revision=expected_revision,
        operation_id=plan["plan_digest"])


def _revoke(db, context, plan):
    sid, _ = authority(db, context)
    def write(conn):
        row = db._runtime_state_on_conn(conn, sid)
        require(row["revision"] == plan["before_revision"], "operations_preview_changed")
        lease = conn.execute("SELECT * FROM session_turn_leases WHERE conversation_id=?", (sid,)).fetchone()
        require(lease is not None and lease["expires_at"] > time.time()
                and lease["generation"] == plan["target"]["generation"]
                and digest(lease["holder"]) == plan["target"]["holder_digest"], "operations_preview_changed")
        payload = {"action": plan["action"], "plan_digest": plan["plan_digest"],
                   "affected_ids": plan["affected_ids"], "before_revision": plan["before_revision"]}
        db._append_runtime_event_on_conn(conn, sid, "operations.repair_started", payload, lease["generation"],
                                        operation_id=plan["plan_digest"])
        conn.execute("DELETE FROM session_turn_leases WHERE conversation_id=? AND generation=?",
                     (sid, lease["generation"]))
        db._append_runtime_event_on_conn(conn, sid, "operations.repair_finished",
            {**payload, "outcome": "revoked_remote_state_unknown"}, lease["generation"],
            operation_id=plan["plan_digest"])
        return {"state": "revoked", "remote_work_stopped": False}
    return db._execute_write(write)


def apply_repair(db, context, plan, *, authorization_digest):
    plan = validate_plan(db, context, plan, authorization_digest)
    if plan["action"] == "revoke-lease":
        return _revoke(db, context, plan)
    sid, actor = authority(db, context)
    with maintenance_lease(db, context) as (holder, generation):
        if plan["action"] == "restore-checkpoint":
            from agent.operations_checkpoint_recovery import restore_projection
            return restore_projection(db, context, plan, holder=holder, generation=generation)
        # The first required write happens before any adapter runs. A journal
        # outage blocks the repair; after-write failure leaves a started receipt.
        require(_target(db, context, plan["action"], plan["target_id"]) == plan["target"],
                "operations_preview_changed")
        _record(db, sid, plan, "started", holder, generation, expected_revision=plan["before_revision"])
        handlers = {
            "reconcile-effect": lambda: _reconcile(db, context, plan, holder, generation),
            "retry-delivery": lambda: _retry_delivery(db, sid, actor, plan, holder, generation),
            "rebuild-index": lambda: {"indexes_rebuilt": db.rebuild_fts()},
        }
        try:
            authority(db, context)
            result = handlers[plan["action"]]()
        except Exception:
            _record(db, sid, plan, "finished", holder, generation, outcome="failed_or_unresolved")
            raise
        outcome = "no_progress" if result.get("indexes_rebuilt") == 0 else "applied"
        _record(db, sid, plan, "finished", holder, generation, outcome=outcome)
        return {"plan_digest": plan["plan_digest"], "outcome": outcome, **result}


def _reconcile(db, context, plan, holder, generation):
    from agent.effect_reconciler import reconcile_effect
    row = reconcile_effect(db, plan["target_id"], context=context, holder=holder,
                           generation=generation, deadline_at=time.time() + 30)
    return {"effect_id": row["effect_id"], "state": row["state"], "redispatched": False}


def _retry_delivery(db, sid, actor, plan, holder, generation):
    row = db.repair_runtime_delivery(sid, actor, plan["target_id"], holder=holder, generation=generation,
        expected_attempt_count=plan["target"]["attempt_count"], expected_state=plan["target"]["state"])
    return {key: row[key] for key in ("delivery_id", "state", "attempt_count", "max_attempts")}
