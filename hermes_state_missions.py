"""BE09 bounded mission authority on the existing SessionDB writer/root lease.

Legacy goals are imported once under a verified owner. Receipts remain immutable;
intent revisions, execution, human acceptance and delivery are separate facts.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import time
import uuid

from agent.mission_contract import (MISSION_STATES, MissionContract, VerificationReceipt, artifact_ref,
    bounded_json, criterion_digest, digest, identifier, items, require, text)
from hermes_state_effects import _actor

MAX_MISSIONS = 16384
MAX_MISSION_RECEIPTS = 262144
MAX_RECEIPTS_PER_MISSION = 1000
MAX_MISSION_TURNS = 1000
_ACTOR_KEYS = ("principal_id", "profile_id", "agent_id")
_CONTRACT_FIELDS = set(MissionContract.__dataclass_fields__)
_MUTABLE_FIELDS = _CONTRACT_FIELDS - {"project_id", "budget_ref"} | {
    "state", "next_step", "blockers", "artifact_refs", "effect_refs", "delivery_refs", "last_verdict", "last_reason",
    "paused_reason", "recovery_choices"}
_TRANSITIONS = {
    "ready": MISSION_STATES - {"completed"},
    "working": MISSION_STATES,
    "waiting_for_user": MISSION_STATES - {"completed"},
    "waiting_for_source": MISSION_STATES - {"completed"},
    "ready_to_review": MISSION_STATES,
    "completed": frozenset({"completed", "ready", "paused", "cancelled"}),
    "partially_completed": MISSION_STATES - {"completed"},
    "paused": MISSION_STATES - {"completed"},
    "cancelled": frozenset({"cancelled", "ready", "paused"}),
    "failed": frozenset({"failed", "ready", "paused", "cancelled"}),
}


def _plan_ready(contract):
    return contract["risk"] == contract["uncertainty"] == "low" or bool(contract["plan_steps"]) and any(step.get("checkpoint") for step in contract["plan_steps"])


def _initial(contract, mission_id, session_id, actor, now):
    return {**contract, "schema_version": 1, "mission_id": mission_id, "session_id": session_id, **actor,
        "revision": 1, "state": "ready" if contract["acceptance"] and _plan_ready(contract) else "waiting_for_user", "execution_status": "not_started", "acceptance_status": "not_requested",
        "delivery_status": "not_requested", "next_step": "", "blockers": [], "artifact_refs": [], "effect_refs": [],
        "delivery_refs": [], "turns_used": 0, "consecutive_no_progress": 0, "verification_rounds": 0,
        "last_run_id": None, "last_verification_digest": None, "goal_decision": None, "last_verdict": None, "last_reason": None,
        "paused_reason": None, "recovery_choices": [], "progress_evidence": {}, "missed_steer": [],
        "created_at": now, "updated_at": now, "legacy_imported": False}


def _runtime_fields(record):
    require(record["state"] in MISSION_STATES, "Unknown mission state")
    text(record["next_step"])
    for name in ("blockers", "recovery_choices"):
        for item in items(record[name]):
            text(item, 1024)
    for ref in items(record["artifact_refs"]):
        artifact_ref(ref)
    for key in ("effect_refs", "delivery_refs"):
        for ref in items(record[key]):
            require(isinstance(ref, dict) and set(ref) <= ({"effect_id", "state"} if key == "effect_refs" else {"delivery_id", "state"}), "Invalid effect/delivery reference")
            identifier(ref.get("effect_id" if key == "effect_refs" else "delivery_id"))
    for name in ("last_verdict", "last_reason", "paused_reason"):
        text(record[name], optional=True)
    bounded_json(record)


class SessionMissionsMixin:
    def _mission_owner_on_conn(self, conn, session_id, actor, *, holder=None, generation=None):
        sid = self._runtime_session_on_conn(conn, session_id)
        state = self._runtime_state_on_conn(conn, sid)
        config = json.loads(conn.execute("SELECT COALESCE(model_config,'{}') FROM sessions WHERE id=?", (sid,)).fetchone()[0])
        binding = config.get("agent_identity")
        if state is not None and state["principal_id"] is not None:
            require(all(state[key] == actor[key] for key in _ACTOR_KEYS), "Mission belongs to another actor", "identity_mismatch")
        else:
            require(isinstance(binding, dict) and all(binding.get(key) == actor[key] for key in _ACTOR_KEYS),
                    "Mission requires an explicitly bound session owner", "identity_mismatch")
        if binding is not None:
            require(all(binding.get(key) == actor[key] for key in _ACTOR_KEYS), "Session identity differs", "identity_mismatch")
        if holder is not None:
            self._runtime_fence_on_conn(conn, sid, holder, generation)
        return sid

    @contextmanager
    def _mission_guard(self, session_id, actor, access, permission, *, project_id=None):
        # Read just the project key first; no state write may precede the project lock.
        with self._runtime_read() as conn:
            sid = self._mission_owner_on_conn(conn, session_id, actor)
            row = conn.execute("SELECT project_id FROM runtime_missions WHERE session_id=?", (sid,)).fetchone()
            if row:
                project_id = row[0]
        if project_id is None:
            yield
        else:
            self._artifact_access(actor, project_id, access, permission)
            with access.guard(project_id, actor, permission):
                yield

    def _mission_row_on_conn(self, conn, session_id, actor, *, holder=None, generation=None, expected_revision=None):
        sid = self._mission_owner_on_conn(conn, session_id, actor, holder=holder, generation=generation)
        row = conn.execute("SELECT * FROM runtime_missions WHERE session_id=?", (sid,)).fetchone()
        require(row is not None, "Mission does not exist", "mission_not_found")
        require(all(row[key] == actor[key] for key in _ACTOR_KEYS), "Mission belongs to another actor", "identity_mismatch")
        require(expected_revision is None or type(expected_revision) is int and expected_revision == row["revision"],
                "Mission revision changed", "revision_conflict")
        record = json.loads(row["record_json"])
        require(record.get("schema_version") == 1, "Unsupported mission record schema", "unsupported_schema")
        return row, record

    @staticmethod
    def _mission_budget_on_conn(conn, sid, actor, requested=None):
        row = conn.execute("SELECT * FROM budget_accounts WHERE session_id=? ORDER BY created_at,account_id LIMIT 1", (sid,)).fetchone()
        if row is None:
            require(requested is None, "Mission cannot invent a budget root", "budget_not_found")
            return None, None
        require(all(row[key] == actor[key] for key in _ACTOR_KEYS), "Budget belongs to another actor", "identity_mismatch")
        require(requested is None or requested == row["root_id"], "Mission must retain its first budget root", "budget_parent_required")
        root = conn.execute("SELECT deadline FROM budget_accounts WHERE account_id=?", (row["root_id"],)).fetchone()
        require(root is not None, "Budget root missing", "budget_not_found")
        return row["root_id"], root[0]

    def _mission_event_on_conn(self, conn, record, generation):
        state = self._runtime_state_on_conn(conn, record["session_id"], create=True)
        if state["principal_id"] is None:
            conn.execute("UPDATE runtime_state SET principal_id=?,profile_id=?,agent_id=? WHERE session_id=?",
                (*(record[key] for key in _ACTOR_KEYS), record["session_id"]))
        self._append_runtime_event_on_conn(conn, record["session_id"], "runtime.state",
            {"mission_state": record["state"], "mission_revision": record["revision"]}, generation,
            mission_id=record["mission_id"])

    def _mission_save_on_conn(self, conn, record, generation):
        _runtime_fields(record)
        conn.execute("UPDATE runtime_missions SET revision=?,generation=?,record_json=?,updated_at=? WHERE mission_id=?",
            (record["revision"], generation, bounded_json(record), record["updated_at"], record["mission_id"]))
        self._mission_event_on_conn(conn, record, generation)
        return record

    def _create_mission_on_conn(self, conn, session_id, actor, holder, generation, contract, mission_id=None, access=None):
        sid = self._mission_owner_on_conn(conn, session_id, actor, holder=holder, generation=generation)
        require(conn.execute("SELECT 1 FROM runtime_missions WHERE session_id=?", (sid,)).fetchone() is None,
                "Mission already exists; use revision CAS", "mission_exists")
        require(conn.execute("SELECT COUNT(*) FROM runtime_missions").fetchone()[0] < MAX_MISSIONS,
                "Mission capacity reached", "mission_capacity")
        contract = MissionContract.from_dict(contract).to_dict()
        root, deadline = self._mission_budget_on_conn(conn, sid, actor, contract["budget_ref"])
        contract["budget_ref"] = root
        if deadline is not None:
            contract["deadline"] = min(contract["deadline"], deadline) if contract["deadline"] is not None else deadline
        record = _initial(contract, identifier(mission_id) if mission_id else uuid.uuid4().hex, sid, actor, time.time())
        self._mission_refs_on_conn(conn, record, actor, access)
        conn.execute("INSERT INTO runtime_missions(session_id,mission_id,principal_id,profile_id,agent_id,project_id,revision,generation,record_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (sid, record["mission_id"], *(actor[key] for key in _ACTOR_KEYS), contract["project_id"], 1, generation,
             bounded_json(record), record["created_at"], record["updated_at"]))
        self._mission_event_on_conn(conn, record, generation)
        return record

    def create_mission(self, session_id, actor, *, holder, generation, contract, mission_id=None, access=None):
        actor = _actor(actor)
        contract = contract.to_dict() if isinstance(contract, MissionContract) else MissionContract.from_dict(contract).to_dict()
        with self._mission_guard(session_id, actor, access, "write", project_id=contract["project_id"]):
            return self._execute_write(lambda conn: self._create_mission_on_conn(conn, session_id, actor, holder, generation, contract, mission_id, access))

    def _mission_live_refs_on_conn(self, conn, record, actor):
        refs = {item["effect_id"] for item in record["effect_refs"]}
        params = (record["session_id"], *actor.values(), record["created_at"])
        where = "session_id=? AND principal_id=? AND profile_id=? AND agent_id=? AND (created_at>=? OR state IN ('dispatched','outcome_unknown','reconciliation_required')"
        if refs:
            where += " OR effect_id IN (" + ",".join("?" for _ in refs) + ")"
            params += tuple(sorted(refs))
        where += ")"
        count = conn.execute("SELECT COUNT(*) FROM runtime_effects WHERE " + where, params).fetchone()[0]
        record["effect_refs"] = [{"effect_id": row[0], "state": row[1]} for row in conn.execute("SELECT effect_id,state FROM runtime_effects WHERE " + where + " ORDER BY created_at DESC,effect_id LIMIT 100", params)]
        record.update(effect_refs_total=count, effect_refs_truncated=count > 100)
        where = "authority='runtime.v1' AND json_extract(metadata_json,'$.destination.session_id')=? AND json_extract(metadata_json,'$.run_id') IN (SELECT run_id FROM runtime_mission_turns WHERE mission_id=?)"
        params = (record["session_id"], record["mission_id"])
        count = conn.execute("SELECT COUNT(*) FROM delivery_obligations WHERE " + where, params).fetchone()[0]
        record["delivery_refs"] = [{"delivery_id": row[0], "state": row[1]} for row in conn.execute("SELECT obligation_id,state FROM delivery_obligations WHERE " + where + " ORDER BY created_at DESC,obligation_id LIMIT 100", params)]
        record.update(delivery_refs_total=count, delivery_refs_truncated=count > 100)
        states = {row["state"] for row in record["delivery_refs"]}
        record["delivery_status"] = "not_requested" if not states else next(iter(states)) if len(states) == 1 and not count > 100 else "mixed"
        return record

    def get_mission(self, session_id, actor, *, access=None):
        actor = _actor(actor)
        with self._mission_guard(session_id, actor, access, "read"):
            with self._runtime_read() as conn:
                sid = self._mission_owner_on_conn(conn, session_id, actor)
                if conn.execute("SELECT 1 FROM runtime_missions WHERE session_id=?", (sid,)).fetchone() is None:
                    return None
                _, record = self._mission_row_on_conn(conn, session_id, actor)
                self._mission_live_refs_on_conn(conn, record, actor)
                record["verification_current"] = self._mission_verified_on_conn(conn, record, actor, access)
                return record

    def list_missions(self, actor, *, limit=100, access=None):
        actor = _actor(actor)
        require(type(limit) is int and 1 <= limit <= 100, "Mission page exceeds bound")
        with self._runtime_read() as conn:
            sessions = [row[0] for row in conn.execute("SELECT session_id FROM runtime_missions WHERE principal_id=? AND profile_id=? AND agent_id=? ORDER BY updated_at DESC,mission_id LIMIT ?", (*actor.values(), limit))]
        return [self.get_mission(sid, actor, access=access) for sid in sessions]

    def _mission_refs_on_conn(self, conn, record, actor, access):
        refs = record["artifact_refs"] + [ref for criterion in record["acceptance"] for ref in criterion["artifact_refs"]]
        refs += [item["artifact_ref"] for item in record["deliverables"] if item.get("artifact_ref")]
        require(len({(ref["artifact_id"], ref["version"], ref["digest"]) for ref in refs}) <= 100,
                "Distinct retained mission artifacts exceed bound", "mission_capacity")
        for ref in refs:
            row = self._artifact_row_on_conn(conn, ref["artifact_id"], ref["version"], actor, access)
            require(row["project_id"] == record["project_id"] and json.loads(row["descriptor_json"])["sha256"] == ref["digest"],
                    "Artifact reference differs from retained project/version digest", "mission_artifact_mismatch")
        for ref in record["effect_refs"]:
            row = self._effect_on_conn(conn, ref["effect_id"], actor)
            require(row["session_id"] == record["session_id"], "Effect belongs to another session", "identity_mismatch")
        for ref in record["delivery_refs"]:
            row = conn.execute("SELECT metadata_json FROM delivery_obligations WHERE obligation_id=? AND authority='runtime.v1'", (ref["delivery_id"],)).fetchone()
            require(row is not None, "Delivery does not exist", "delivery_not_found")
            metadata = json.loads(row[0])
            require(metadata.get("destination", {}).get("session_id") == record["session_id"], "Delivery belongs to another session", "identity_mismatch")

    def _invalidate_mission_approvals_on_conn(self, conn, record, old, actor, generation, inputs, targets, steps):
        for value in items(inputs):
            digest(value)
        for value in items(targets):
            text(value, 1024)
        for value in items(steps):
            identifier(value)
        old_steps = {step["step_id"]: step for step in old["plan_steps"]}
        require(set(steps) <= set(old_steps), "Changed step must name a declared prior plan step")
        approval_ids = {aid for step in steps for aid in old_steps[step].get("approval_ids", [])}
        inputs = set(inputs) | {d for step in steps for d in old_steps[step].get("input_digests", [])}
        targets = set(targets) | {r for step in steps for r in old_steps[step].get("target_refs", [])}
        if not inputs and not targets and not approval_ids:
            return
        rows = conn.execute("SELECT * FROM runtime_effect_approvals WHERE session_id=? AND principal_id=? AND profile_id=? AND agent_id=? AND status IN ('pending','approved')", (record["session_id"], *actor.values())).fetchall()
        for row in rows:
            binding = json.loads(row["binding_json"])
            matches = [reason for ok, reason in ((row["approval_id"] in approval_ids, "plan_step_changed"),
                (binding["input_digest"] in inputs, "input_changed"), (binding["target_ref"] in targets, "target_changed")) if ok]
            if not matches:
                continue
            conn.execute("UPDATE runtime_effect_approvals SET status='invalidated',resolved_at=? WHERE approval_id=?", (time.time(), row["approval_id"]))
            conn.execute("INSERT INTO runtime_mission_approval_invalidations VALUES(?,?,?,?,?)", (row["approval_id"], record["mission_id"], record["revision"], matches[0], time.time()))
            self._append_runtime_event_on_conn(conn, record["session_id"], "approval.resolved", {"status": "invalidated", "invalidation_reason": matches[0], "mission_revision": record["revision"]}, generation, approval_id=row["approval_id"], run_id=row["run_id"], mission_id=record["mission_id"])

    def _update_mission_on_conn(self, conn, session_id, actor, holder, generation, expected_revision, changes, access,
                                changed_inputs=(), changed_targets=(), changed_plan_steps=()):
        _, old = self._mission_row_on_conn(conn, session_id, actor, holder=holder, generation=generation, expected_revision=expected_revision)
        require(isinstance(changes, dict) and set(changes) <= _MUTABLE_FIELDS, "Unknown or authoritative mission field")
        record = {**old, **changes}
        contract = MissionContract.from_dict({key: record[key] for key in _CONTRACT_FIELDS}).to_dict()
        require(contract["max_turns"] <= old["max_turns"] and contract["no_progress_limit"] <= old["no_progress_limit"],
                "Mission revision cannot reset or enlarge continuation ceilings", "budget_limit_enlarged")
        require(old["deadline"] is None or contract["deadline"] is not None and contract["deadline"] <= old["deadline"],
                "Mission revision cannot extend its deadline", "budget_limit_enlarged")
        record.update(contract)
        root, deadline = self._mission_budget_on_conn(conn, record["session_id"], actor, old["budget_ref"])
        record["budget_ref"] = root
        if deadline is not None:
            record["deadline"] = min(record["deadline"], deadline) if record["deadline"] is not None else deadline
        if record["state"] in {"ready", "working"}:
            require(record["acceptance"], "Declare acceptance criteria before execution", "mission_criteria_required")
            require(_plan_ready(record), "Risk or uncertainty requires an explicit plan and checkpoint", "mission_plan_required")
            require(record["turns_used"] < record["max_turns"] and record["consecutive_no_progress"] < record["no_progress_limit"]
                and (record["deadline"] is None or record["deadline"] > time.time()), "Mission continuation ceiling exhausted", "mission_continuation_denied")
            require(conn.execute("SELECT 1 FROM runtime_effects WHERE session_id=? AND state IN ('dispatched','outcome_unknown','reconciliation_required') LIMIT 1", (record["session_id"],)).fetchone() is None,
                    "Resolve uncertain effects before resuming", "effect_reconciliation_required")
        require(record["state"] in _TRANSITIONS[old["state"]], "Illegal mission transition", "mission_transition")
        require(record["state"] != "completed" or old["state"] == "completed", "Completion requires verification finalization", "mission_verification_required")
        if record["state"] == "ready_to_review":
            require(self._mission_verified_on_conn(conn, record, actor, access), "Review requires current deterministic evidence", "mission_verification_required")
            require(conn.execute("SELECT 1 FROM runtime_effects WHERE session_id=? AND state IN ('dispatched','outcome_unknown','reconciliation_required') LIMIT 1", (record["session_id"],)).fetchone() is None,
                    "Unresolved effects prevent verified completion", "effect_reconciliation_required")
            record["acceptance_status"] = "pending"
        if any(key in changes for key in ("outcome", "acceptance", "deliverables", "scope_ref", "dependencies", "plan_steps")):
            require(record["state"] != "completed", "Revised intent must reopen before completion", "mission_transition")
            record["acceptance_status"] = "not_requested"
        record["revision"] += 1
        record["updated_at"] = time.time()
        self._mission_refs_on_conn(conn, record, actor, access)
        old_steps = {item["step_id"]: item for item in old["plan_steps"]}
        new_steps = {item["step_id"]: item for item in record["plan_steps"]}
        automatic_steps = {key for key, value in old_steps.items() if new_steps.get(key) != value}
        changed_plan_steps = sorted(set(changed_plan_steps) | automatic_steps)
        old_inputs = {item.get("digest") for item in old["dependencies"] if item.get("digest")}
        new_inputs = {item.get("digest") for item in record["dependencies"] if item.get("digest")}
        changed_inputs = sorted(set(changed_inputs) | (old_inputs - new_inputs))
        self._invalidate_mission_approvals_on_conn(conn, record, old, actor, generation,
            list(changed_inputs), list(changed_targets), list(changed_plan_steps))
        irreversible = [row[0] for row in conn.execute("SELECT effect_id FROM runtime_effects WHERE session_id=? AND principal_id=? AND profile_id=? AND agent_id=? AND state IN ('dispatched','confirmed','outcome_unknown','reconciliation_required') ORDER BY created_at LIMIT 101", (record["session_id"], *actor.values()))]
        if changed_inputs or changed_targets or changed_plan_steps:
            require(len(irreversible) <= 100 and len(record["missed_steer"]) < 100, "Steer audit capacity exceeded", "mission_capacity")
            if irreversible:
                record["missed_steer"] = record["missed_steer"] + [{"revision": record["revision"], "effect_ids": irreversible, "reason": "effect_already_dispatched"}]
        if record["state"] in {"cancelled", "paused"}:
            self._mission_live_refs_on_conn(conn, record, actor)
        return self._mission_save_on_conn(conn, record, generation)

    def update_mission(self, session_id, actor, *, holder, generation, expected_revision, changes,
                       changed_inputs=(), changed_targets=(), changed_plan_steps=(), access=None):
        actor = _actor(actor)
        with self._mission_guard(session_id, actor, access, "write"):
            return self._execute_write(lambda conn: self._update_mission_on_conn(conn, session_id, actor, holder, generation,
                expected_revision, changes, access, changed_inputs, changed_targets, changed_plan_steps))

    def _receipt_on_conn(self, conn, record, receipt, actor, access):
        receipt = receipt.to_dict() if isinstance(receipt, VerificationReceipt) else VerificationReceipt.from_dict(receipt).to_dict()
        criterion = next((item for item in record["acceptance"] if item["criterion_id"] == receipt["criterion_id"]), None)
        require(criterion is not None and criterion_digest(criterion) == receipt["criterion_digest"]
                and criterion["artifact_refs"] == receipt["artifact_refs"],
                "Verification no longer binds the requested criterion/version", "mission_verification_stale")
        require(receipt["observed_at"] <= time.time() + 1, "Verification cannot be from the future")
        self._mission_refs_on_conn(conn, record, actor, access)
        receipt["receipt_id"] = receipt["receipt_id"] or hashlib.sha256(bounded_json(receipt).encode()).hexdigest()
        encoded = bounded_json(receipt)
        previous = conn.execute("SELECT * FROM runtime_mission_verifications WHERE receipt_id=?", (receipt["receipt_id"],)).fetchone()
        if previous:
            require(previous["mission_id"] == record["mission_id"] and previous["receipt_json"] == encoded,
                    "Receipt IDs name immutable evidence", "idempotency_conflict")
            return receipt
        require(conn.execute("SELECT COUNT(*) FROM runtime_mission_verifications").fetchone()[0] < MAX_MISSION_RECEIPTS
            and conn.execute("SELECT COUNT(*) FROM runtime_mission_verifications WHERE mission_id=?", (record["mission_id"],)).fetchone()[0] < MAX_RECEIPTS_PER_MISSION,
            "Verification history capacity reached", "mission_capacity")
        conn.execute("INSERT INTO runtime_mission_verifications VALUES(?,?,?,?,?,?,?)", (receipt["receipt_id"], record["mission_id"], record["revision"],
            receipt["criterion_id"], receipt["criterion_digest"], encoded, time.time()))
        return receipt

    def append_verification_receipt(self, session_id, actor, *, holder, generation, expected_revision, receipt, access=None):
        actor = _actor(actor)
        with self._mission_guard(session_id, actor, access, "write"):
            def write(conn):
                _, record = self._mission_row_on_conn(conn, session_id, actor, holder=holder, generation=generation, expected_revision=expected_revision)
                return self._receipt_on_conn(conn, record, receipt, actor, access)
            return self._execute_write(write)

    def list_verification_receipts(self, session_id, actor, *, limit=100, access=None):
        actor = _actor(actor)
        require(type(limit) is int and 1 <= limit <= 100, "Receipt page exceeds bound")
        with self._mission_guard(session_id, actor, access, "read"):
            with self._runtime_read() as conn:
                _, record = self._mission_row_on_conn(conn, session_id, actor)
                return [json.loads(row[0]) for row in conn.execute("SELECT receipt_json FROM runtime_mission_verifications WHERE mission_id=? ORDER BY created_at DESC,receipt_id LIMIT ?", (record["mission_id"], limit))]

    def _mission_verified_on_conn(self, conn, record, actor, access):
        self._mission_refs_on_conn(conn, record, actor, access)
        deliverables = [item for item in record["deliverables"] if item.get("required", True)]
        covered = set()
        if not deliverables:
            return False
        required = [item for item in record["acceptance"] if item["required"] and item["kind"] != "user_acceptance"]
        if not required:
            return False
        for deliverable in record["deliverables"]:
            if deliverable.get("required", True) and not deliverable.get("artifact_ref"):
                return False
        for criterion in required:
            row = conn.execute("SELECT receipt_json FROM runtime_mission_verifications WHERE mission_id=? AND criterion_id=? AND criterion_digest=? ORDER BY created_at DESC,receipt_id DESC LIMIT 1",
                (record["mission_id"], criterion["criterion_id"], criterion_digest(criterion))).fetchone()
            if row is None:
                return False
            receipt = json.loads(row[0])
            if receipt["result"] != "pass" or receipt["verifier"] != "deterministic_artifact_v1" or not receipt["artifact_refs"]:
                return False
            if criterion["kind"] == "test_execution" and not self._mission_test_receipt_current_on_conn(conn, record, criterion, receipt, actor, access):
                return False
            for ref in receipt["artifact_refs"]:
                covered.add((ref["artifact_id"], ref["version"], ref["digest"]))
                artifact = self._artifact_row_on_conn(conn, ref["artifact_id"], ref["version"], actor, access)
                current = self._artifact_result_on_conn(conn, artifact)
                if criterion["parameters"].get("require_current_head", True) and current["head_version"] not in (None, ref["version"]):
                    return False
                if criterion["parameters"].get("require_current_dependencies", True) and current["derived_validity"] != "current":
                    return False
            from agent.mission_verifier import dependency_is_current
            if not all(dependency_is_current(self, conn, dep, actor, access, criterion=criterion)
                       for dep in receipt["details"].get("dependencies", [])):
                return False
        return all((item["artifact_ref"]["artifact_id"], item["artifact_ref"]["version"], item["artifact_ref"]["digest"]) in covered for item in deliverables)

    @staticmethod
    def _mission_evidence(evidence):
        require(isinstance(evidence, dict) and set(evidence) <= {"guardrails", "no_progress", "budget_blocked", "cancelled",
            "pending_steer_count", "effect_refs", "recovery_choices", "verification_digest"}, "Invalid progress evidence")
        for flag in ("no_progress", "budget_blocked", "cancelled"):
            require(type(evidence.get(flag, False)) is bool, "Invalid progress flag")
        count = evidence.get("pending_steer_count", 0)
        require(type(count) is int and 0 <= count <= 100, "Pending steer exceeds bound")
        for row in items(evidence.get("guardrails", []), 16):
            require(isinstance(row, dict) and set(row) <= {"code", "action", "tool_name", "count", "signature_digest"}, "Invalid guardrail evidence")
            for key in ("code", "action", "tool_name"):
                if key in row:
                    identifier(row[key])
            if "count" in row:
                require(type(row["count"]) is int and 0 <= row["count"] <= 10000, "Invalid guardrail count")
            if "signature_digest" in row:
                digest(row["signature_digest"])
        if evidence.get("verification_digest") is not None:
            digest(evidence["verification_digest"])
        bounded_json(evidence, 16384)
        return evidence

    def finalize_mission_turn(self, session_id, actor, *, holder, generation, expected_revision, run_id,
                               receipts, evidence, decision, state, access=None):
        actor = _actor(actor)
        items(receipts)
        evidence = self._mission_evidence(evidence)
        require(isinstance(decision, dict) and set(decision) <= {"status", "should_continue", "continuation_prompt", "verdict", "reason", "message"}, "Invalid goal decision")
        require(type(decision.get("should_continue")) is bool and state in MISSION_STATES, "Invalid goal decision state")
        bounded_json(decision, 16384)
        fingerprint = hashlib.sha256(bounded_json(dict(run_id=run_id, receipts=receipts, evidence=evidence, decision=decision, state=state)).encode()).hexdigest()
        with self._mission_guard(session_id, actor, access, "write"):
            def write(conn):
                _, record = self._mission_row_on_conn(conn, session_id, actor, holder=holder, generation=generation)
                previous = conn.execute("SELECT * FROM runtime_mission_turns WHERE mission_id=? AND run_id=?", (record["mission_id"], run_id)).fetchone()
                if previous:
                    require(previous["finalization_digest"] == fingerprint, "Finalized run cannot change its outcome", "idempotency_conflict")
                    return record
                require(type(expected_revision) is int and record["revision"] == expected_revision, "Mission revision changed", "revision_conflict")
                self._effect_run_on_conn(conn, record["session_id"], actor, run_id, holder, generation)
                command = conn.execute("SELECT * FROM runtime_commands WHERE session_id=? AND run_id=? AND status='claimed' AND claimed_holder=? AND claimed_generation=? AND json_extract(command_json,'$.operation')='submit'", (record["session_id"], run_id, holder, generation)).fetchone()
                require(command is not None, "Mission turn must be the claimed model run", "claim_conflict")
                require(conn.execute("SELECT COUNT(*) FROM runtime_mission_turns WHERE mission_id=?", (record["mission_id"],)).fetchone()[0] < MAX_MISSION_TURNS,
                        "Mission turn history capacity reached", "mission_capacity")
                for receipt in receipts:
                    self._receipt_on_conn(conn, record, receipt, actor, access)
                verified = self._mission_verified_on_conn(conn, record, actor, access)
                actual_state, actual_decision = state, dict(decision)
                cancelled = evidence.get("cancelled") or conn.execute("SELECT 1 FROM runtime_commands WHERE session_id=? AND run_id=? AND json_extract(command_json,'$.operation')='cancel'", (record["session_id"], run_id)).fetchone() is not None
                if cancelled:
                    actual_state = "cancelled"
                    actual_decision.update(status="paused", should_continue=False, continuation_prompt=None,
                        verdict="blocked", reason="Mission cancelled; already-dispatched effects remain recorded", message="Mission cancelled")
                late_steer = conn.execute("SELECT 1 FROM runtime_commands WHERE session_id=? AND run_id=? AND json_extract(command_json,'$.operation')='steer' AND (json_extract(result_json,'$.outcome')='steer_not_queued' OR json_extract(result_json,'$.missed_steer')=1) LIMIT 1", (record["session_id"], run_id)).fetchone() is not None
                if late_steer or evidence.get("pending_steer_count"):
                    actual_state = actual_state if actual_state in {"cancelled", "paused"} else "partially_completed" if record["artifact_refs"] else "waiting_for_user"
                    actual_decision.update(status="paused", should_continue=False, continuation_prompt=None,
                        verdict="blocked", reason="A steer arrived after execution; review retained effects and remaining work",
                        message="Late steer retained for review; completed effects were not undone")
                unknown = conn.execute("SELECT 1 FROM runtime_effects WHERE session_id=? AND state IN ('dispatched','outcome_unknown','reconciliation_required') LIMIT 1", (record["session_id"],)).fetchone() is not None
                require(actual_state not in {"completed", "ready_to_review"} or not unknown,
                        "Unresolved effects prevent verified completion", "effect_reconciliation_required")
                if actual_state in {"completed", "ready_to_review"}:
                    require(verified, "Required criteria lack current deterministic receipts", "mission_verification_required")
                require(actual_state in _TRANSITIONS[record["state"]], "Illegal mission transition", "mission_transition")
                if actual_state == "completed":
                    require(record["policy"] == "direct" and not any(item["kind"] == "user_acceptance" and item["required"] for item in record["acceptance"]),
                            "Reviewed work requires human acceptance", "mission_acceptance_required")
                root, deadline = self._mission_budget_on_conn(conn, record["session_id"], actor, record["budget_ref"])
                record["budget_ref"] = root
                if deadline is not None:
                    record["deadline"] = min(record["deadline"], deadline) if record["deadline"] is not None else deadline
                record["turns_used"] += 1
                record["consecutive_no_progress"] = record["consecutive_no_progress"] + 1 if evidence.get("no_progress") else 0
                record["verification_rounds"] += 1
                exhausted = record["turns_used"] >= record["max_turns"] or record["consecutive_no_progress"] >= record["no_progress_limit"] or evidence.get("budget_blocked") or record["deadline"] is not None and record["deadline"] <= time.time()
                cancelled = evidence.get("cancelled") or conn.execute("SELECT 1 FROM runtime_commands WHERE session_id=? AND run_id=? AND json_extract(command_json,'$.operation')='cancel'", (record["session_id"], run_id)).fetchone() is not None
                require(not actual_decision["should_continue"] or actual_state in {"ready", "working"} and not exhausted and not cancelled,
                        "Mission cannot continue after a stop or exhausted ceiling", "mission_continuation_denied")
                record.update(state=actual_state, last_run_id=run_id, goal_decision=actual_decision, progress_evidence=evidence,
                    last_verdict=actual_decision.get("verdict"), last_reason=actual_decision.get("reason"), last_verification_digest=evidence.get("verification_digest"),
                    recovery_choices=evidence.get("recovery_choices", []), effect_refs=evidence.get("effect_refs", record["effect_refs"]))
                record["execution_status"] = "cancelled" if cancelled else "completed" if actual_state in {"completed", "ready_to_review"} else "failed" if actual_state == "failed" else "stopped" if not actual_decision["should_continue"] else "running"
                record["acceptance_status"] = "pending" if actual_state == "ready_to_review" else record["acceptance_status"]
                record["next_step"] = actual_decision.get("reason") or ""
                if not actual_decision["should_continue"] and actual_state not in {"completed", "ready_to_review"}:
                    record["blockers"] = [actual_decision.get("reason") or actual_state]
                if late_steer or evidence.get("pending_steer_count"):
                    effect_ids = [row[0] for row in conn.execute("SELECT effect_id FROM runtime_effects WHERE session_id=? AND run_id=? AND state IN ('dispatched','confirmed','outcome_unknown','reconciliation_required') LIMIT 101", (record["session_id"], run_id))]
                    require(len(effect_ids) <= 100 and len(record["missed_steer"]) < 100, "Steer audit capacity reached", "mission_capacity")
                    record["missed_steer"].append({"revision": record["revision"] + 1, "run_id": run_id, "effect_ids": effect_ids, "reason": "effect_already_dispatched" if effect_ids else "turn_already_finalizing"})
                refs = record["artifact_refs"] + [item["artifact_ref"] for item in record["deliverables"] if item.get("artifact_ref")]
                refs += [ref for criterion in record["acceptance"] for ref in criterion["artifact_refs"]]
                record["artifact_refs"] = list({(ref["artifact_id"], ref["version"], ref["digest"]): ref for ref in refs}.values())
                record["revision"] += 1
                record["updated_at"] = time.time()
                self._mission_refs_on_conn(conn, record, actor, access)
                self._mission_save_on_conn(conn, record, generation)
                conn.execute("INSERT INTO runtime_mission_turns(mission_id,run_id,revision,finalization_digest,decision_json,created_at) VALUES(?,?,?,?,?,?)", (record["mission_id"], run_id, record["revision"], fingerprint, bounded_json(actual_decision), time.time()))
                return record
            return self._execute_write(write)

    def _settle_mission_delivery_on_conn(self, conn, session_id, actor, run_id, generation):
        """Close the steer/final-result race inside either existing writer transaction."""
        actor = _actor(actor)
        sid = self._mission_owner_on_conn(conn, session_id, actor)
        row = conn.execute("SELECT record_json FROM runtime_missions WHERE session_id=?", (sid,)).fetchone()
        if row is None:
            return
        record = json.loads(row[0])
        if record["last_run_id"] != run_id:
            return
        if any(item.get("run_id") == run_id for item in record["missed_steer"]):
            return record
        missed = conn.execute("SELECT 1 FROM runtime_commands WHERE session_id=? AND run_id=? AND json_extract(command_json,'$.operation')='steer' AND (json_extract(result_json,'$.outcome')='steer_not_queued' OR json_extract(result_json,'$.missed_steer')=1) LIMIT 1", (sid, run_id)).fetchone()
        if missed is None:
            return record
        refs = [row[0] for row in conn.execute("SELECT effect_id FROM runtime_effects WHERE session_id=? AND run_id=? AND state IN ('dispatched','confirmed','outcome_unknown','reconciliation_required') ORDER BY created_at LIMIT 101", (sid, run_id))]
        require(len(refs) <= 100 and len(record["missed_steer"]) < 100, "Steer audit capacity reached", "mission_capacity")
        record["revision"] += 1
        record["updated_at"] = time.time()
        record["missed_steer"].append({"revision": record["revision"], "run_id": run_id, "effect_ids": refs,
            "reason": "effect_already_dispatched" if refs else "turn_already_finalizing"})
        if record["state"] not in {"cancelled", "paused"}:
            record["state"] = "partially_completed" if record["artifact_refs"] else "waiting_for_user"
        reason = "A steer arrived after execution; review retained effects and remaining work"
        record["acceptance_status"] = "not_requested"
        record["next_step"], record["blockers"] = reason, [reason]
        decision = {"status": "paused", "should_continue": False, "continuation_prompt": None,
            "verdict": "blocked", "reason": reason, "message": "Late steer retained; completed effects were not undone"}
        record["goal_decision"] = decision
        self._mission_live_refs_on_conn(conn, record, actor)
        self._mission_save_on_conn(conn, record, generation)
        conn.execute("UPDATE runtime_mission_turns SET revision=?,decision_json=? WHERE mission_id=? AND run_id=?",
            (record["revision"], bounded_json(decision), record["mission_id"], run_id))
        return record

    def consume_mission_decision(self, session_id, actor, *, run_id=None, access=None):
        actor = _actor(actor)
        with self._mission_guard(session_id, actor, access, "read"):
            def write(conn):
                _, record = self._mission_row_on_conn(conn, session_id, actor)
                row = conn.execute("SELECT * FROM runtime_mission_turns WHERE mission_id=? AND run_id=?", (record["mission_id"], run_id or record["last_run_id"])).fetchone()
                if row is None or row["consumed_at"] is not None or row["revision"] != record["revision"]:
                    return None
                command = conn.execute("SELECT status FROM runtime_commands WHERE session_id=? AND run_id=? AND json_extract(command_json,'$.operation')='submit'", (record["session_id"], row["run_id"])).fetchone()
                if command is None or command[0] not in {"completed", "failed", "blocked", "cancelled"}:
                    return None
                conn.execute("UPDATE runtime_mission_turns SET consumed_at=? WHERE mission_id=? AND run_id=?", (time.time(), record["mission_id"], row["run_id"]))
                return json.loads(row["decision_json"])
            return self._execute_write(write)

    def accept_mission(self, session_id, actor, *, holder, generation, expected_revision, access=None):
        from agent.mission_controls import assert_mission_user_control
        actor = _actor(actor)
        assert_mission_user_control(session_id, actor, holder, generation)
        with self._mission_guard(session_id, actor, access, "write"):
            def write(conn):
                _, record = self._mission_row_on_conn(conn, session_id, actor, holder=holder, generation=generation, expected_revision=expected_revision)
                require(record["state"] == "ready_to_review" and self._mission_verified_on_conn(conn, record, actor, access),
                        "Only currently verified review-ready work can be accepted", "mission_verification_required")
                require(conn.execute("SELECT 1 FROM runtime_effects WHERE session_id=? AND state IN ('dispatched','outcome_unknown','reconciliation_required') LIMIT 1", (record["session_id"],)).fetchone() is None,
                        "Unresolved effects prevent completion acceptance", "effect_reconciliation_required")
                record.update(state="completed", acceptance_status="accepted", revision=record["revision"] + 1, updated_at=time.time())
                return self._mission_save_on_conn(conn, record, generation)
            return self._execute_write(write)

    def migrate_legacy_mission(self, session_id, actor, *, holder, generation, access=None):
        actor = _actor(actor)
        with self._mission_guard(session_id, actor, access, "write"):
            def write(conn):
                sid = self._mission_owner_on_conn(conn, session_id, actor, holder=holder, generation=generation)
                prior = conn.execute("SELECT record_json FROM runtime_missions WHERE session_id=?", (sid,)).fetchone()
                if prior:
                    return json.loads(prior[0])
                raw = conn.execute("SELECT value FROM state_meta WHERE key=?", ("goal:" + session_id,)).fetchone()
                if raw is None and sid != session_id:
                    raw = conn.execute("SELECT value FROM state_meta WHERE key=?", ("goal:" + sid,)).fetchone()
                if raw is None:
                    return None
                try:
                    legacy = json.loads(raw[0])
                except (ValueError, TypeError) as exc:
                    raise ValueError("Legacy goal is malformed; original retained") from exc
                require(isinstance(legacy, dict), "Legacy goal must be an object")
                bounded_json(legacy, 65536)
                maximum = legacy.get("max_turns", 20)
                used = legacy.get("turns_used", 0)
                require(type(maximum) is int and 1 <= maximum <= 100 and type(used) is int and 0 <= used <= 10000, "Legacy goal counters exceed migration bound")
                contract = dict(outcome=legacy.get("goal", ""), max_turns=maximum, legacy_contract=legacy.get("contract") or {},
                    subgoals=legacy.get("subgoals") or [], gates=legacy.get("gates") or [])
                record = self._create_mission_on_conn(conn, sid, actor, holder, generation, contract)
                record.update(legacy_imported=True, turns_used=used, state="cancelled" if legacy.get("status") == "cleared" else "paused",
                    paused_reason="Legacy goal needs explicit criteria and fresh verification", next_step="Declare acceptance criteria before resuming",
                    last_verdict=legacy.get("last_verdict"), last_reason=legacy.get("last_reason"))
                # Legacy 'done' is historical model judgment, never new deterministic proof.
                self._mission_save_on_conn(conn, record, generation)
                return record
            return self._execute_write(write)
