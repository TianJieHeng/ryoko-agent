"""Reviewed workflow knowledge installations on the authoritative SessionDB.

Delivery changes only a named specialist's future-session pin. It neither moves
the workflow's global head nor supplies execution, tool or personal-memory grants.
"""
from __future__ import annotations

import json
from pathlib import Path
import time

from agent.result_artifacts import artifact_actor
from agent.agent_identity import _digest as configuration_digest
from agent.workflow_runtime import assert_workflow_control
from hermes_state_workflows import WorkflowRegistry, canonical, digest, require

DELIVERY_METHODS = {"runtime.workflow.delivery.prepare", "runtime.workflow.delivery.commit"}
MAX_SPECIALIST_WORKFLOWS = 32
MAX_SPECIALIST_WORKFLOW_BYTES = 32768


def delivery_key(context, project_id, workflow_id, specialist_id):
    return digest({"principal_id": context.identity.principal_id, "profile_id": context.identity.profile_id,
        "profile_home_digest": context.identity.profile_home_digest, "project_id": project_id,
        "workflow_id": workflow_id, "specialist_id": specialist_id})


def assert_delivery_store(context, db):
    from tools.capability_broker import require_live_policy
    require(require_live_policy(require_run=False) == context
            and Path(db.db_path).resolve().parent == Path(context.profile_home),
            "identity_mismatch", "Workflow delivery belongs to another harness or profile")


def checked_delivery(conn, context, db, row):
    """A persisted pin must still name the exact consumed installation decision."""
    record = json.loads(row["record_json"])
    pin, scope, owner = record["delivery"], record["scope"], record["owner"]
    fields = ("project_id", "workflow_id", "version", "sha256", "specialist_id", "action")
    key = delivery_key(context, pin["project_id"], pin["workflow_id"], pin["specialist_id"])
    require(all(pin[name] == scope[name] for name in fields)
            and all(pin[name] == row[name] for name in ("delivery_id", "project_id", "specialist_id", "version", "delivery_revision"))
            and row["delivery_key"] == key and row["head_revision"] == pin["delivery_revision"]
            and scope["expected_delivery_revision"] + 1 == pin["delivery_revision"]
            and pin["previous_delivery_id"] == scope["previous_delivery_id"]
            and pin["approval_id"] == pin["delivery_id"] and pin["activation"] == "next_session"
            and pin["execution_authority"] is False and pin["personal_memory_shared"] is False
            and scope["profile_home_digest"] == context.identity.profile_home_digest
            and all(owner[name] == getattr(context.identity, name) == row[name] for name in ("principal_id", "profile_id")),
            "workflow_delivery_changed", "Installed workflow pin differs from its reviewed scope")
    approval = db._effect_approval_on_conn(conn, pin["approval_id"], owner)
    binding = json.loads(approval["binding_json"])
    require(approval["status"] == "consumed" and approval["consumer_id"] == pin["delivery_id"]
            and approval["approval_digest"] == pin["approval_digest"] and binding["action_digest"] == digest(scope)
            and binding["input_digest"] == pin["sha256"] and binding["target_ref"] == "workflow-delivery:" + key,
            "workflow_delivery_changed", "Installation has no matching consumed human approval")
    return record


class WorkflowDeliveryRegistry:
    def __init__(self, context, db):
        from agent.agent_configuration import AgentConfigurationRegistry
        assert_delivery_store(context, db)
        self.context, self.db = context, db
        self.agents = AgentConfigurationRegistry(context, db)
        self.workflows = WorkflowRegistry(context, db)
        self.actor = artifact_actor(context)

    def _target(self, conn, specialist_id, project_id):
        target = self.agents.get_on_conn(conn, specialist_id)
        require(target["role"] == "specialist" and target["memory_backend"] == "builtin"
                and not target["archived"] and specialist_id != self.actor["agent_id"],
                "workflow_delivery_target_invalid", "An available named stable specialist is required")
        require(project_id in target["config"]["project_grants"],
                "workflow_delivery_project_denied", "Target specialist has no configured project grant")
        return {key: target[key] for key in ("agent_id", "role", "memory_backend", "config", "revision", "archived")}

    def _target_access(self, project_id, specialist_id):
        # The caller holds the source share guard; reuse that same project
        # transaction so target revocation cannot race the SessionDB commit.
        from agent.project_context import _project_transaction
        from hermes_cli import projects_db
        with _project_transaction(self.context) as conn:
            require(projects_db.project_permission(conn, project_id, self.actor["principal_id"], specialist_id, "read"),
                    "workflow_delivery_project_denied", "Target specialist's live project read grant is missing")

    def _head(self, conn, key):
        row = conn.execute("SELECT d.*,h.revision AS head_revision FROM workflow_delivery_heads h JOIN workflow_deliveries d "
                           "ON d.delivery_id=h.delivery_id WHERE h.delivery_key=?", (key,)).fetchone()
        return checked_delivery(conn, self.context, self.db, row)["delivery"] if row else None

    def _scope(self, conn, request):
        project, specialist = request["project_id"], request["specialist_id"]
        target = self._target(conn, specialist, project)
        key = self.workflows.key(project, request["workflow_id"])
        source = self.workflows._row(conn, key, request["version"], owner=True)
        row = self.workflows._public(conn, source)
        require(row["sha256"] == request["sha256"], "workflow_digest_mismatch", "Delivery requires exact immutable bytes")
        require(row["state"] == "approved" and row["evaluation_ref"] is not None,
                "workflow_not_approved", "Only an evaluated approved workflow may be installed")
        current = self._head(conn, delivery_key(self.context, project, row["workflow_id"], specialist))
        revision = current["delivery_revision"] if current else 0
        require(type(request["expected_delivery_revision"]) is int and request["expected_delivery_revision"] == revision,
                "workflow_delivery_revision_conflict", "Specialist installation changed; review its current pin")
        require(request["action"] in {"deliver", "rollback"}, "workflow_delivery_invalid", "Unknown delivery action")
        if request["action"] == "rollback":
            self._rollback(conn, key, row, current, specialist)
        else:
            require(current is None or row["version"] > current["version"], "workflow_delivery_invalid",
                    "Delivery requires a newer version; restoring an earlier version requires rollback")
        installed = conn.execute("SELECT v.definition_json,d.delivery_key FROM workflow_delivery_heads h "
            "JOIN workflow_deliveries d ON d.delivery_id=h.delivery_id JOIN workflow_versions v "
            "ON v.workflow_key=d.workflow_key AND v.version=d.version WHERE d.principal_id=? "
            "AND d.profile_id=? AND d.specialist_id=?", (self.actor["principal_id"], self.actor["profile_id"], specialist)).fetchall()
        replacing = delivery_key(self.context, project, row["workflow_id"], specialist)
        # Bound the complete, escaped startup snapshot before offering approval;
        # never silently truncate instructional knowledge at session construction.
        total = len(canonical(row["definition_json"]).encode()) + sum(len(canonical(item["definition_json"]).encode())
            for item in installed if item["delivery_key"] != replacing)
        require(total <= MAX_SPECIALIST_WORKFLOW_BYTES and (current is not None or len(installed) < MAX_SPECIALIST_WORKFLOWS),
                "workflow_delivery_capacity", "Reviewed knowledge exceeds the specialist startup budget")
        scope = {**request, "workflow_revision": row["revision"], "evaluation_ref": row["evaluation_ref"],
            "target_revision": target["revision"], "target_sha256": configuration_digest(target), "specialist_name": target["config"]["name"],
            "previous_delivery_id": current["delivery_id"] if current else None,
            "profile_home_digest": self.context.identity.profile_home_digest,
            "activation": "next_session", "execution_authority": False, "personal_memory_shared": False}
        return row, current, scope

    def _rollback(self, conn, workflow_key, row, current, specialist_id):
        require(current is not None and row["version"] < current["version"],
                "workflow_delivery_rollback_invalid", "Rollback requires an earlier installed version")
        key = delivery_key(self.context, row["project_id"], row["workflow_id"], specialist_id)
        prior = conn.execute("SELECT 1 FROM workflow_deliveries WHERE delivery_key=? AND version=?",
                             (key, row["version"])).fetchone()
        require(prior is not None, "workflow_delivery_rollback_invalid", "Target was never delivered to this specialist")
        cursor, seen = self.workflows._row(conn, workflow_key, current["version"], owner=True), set()
        while cursor["version"] != row["version"]:
            require(cursor["version"] not in seen and len(seen) < 100,
                    "workflow_delivery_rollback_invalid", "Workflow predecessor history is invalid")
            seen.add(cursor["version"])
            predecessor = json.loads(cursor["definition_json"])["predecessor"]
            require(predecessor is not None, "workflow_delivery_rollback_invalid", "Target is not an earlier workflow ancestor")
            cursor = self.workflows._row(conn, workflow_key, predecessor["version"], owner=True)
            require(cursor["sha256"] == predecessor["sha256"], "workflow_digest_mismatch", "Workflow ancestor bytes changed")

    def prepare(self, run, request):
        assert_workflow_control(run, DELIVERY_METHODS)
        with self.workflows.access.guard(request["project_id"], self.actor, "share"):
            self._target_access(request["project_id"], request["specialist_id"])
            with self.db._runtime_read() as conn:
                row, current, scope = self._scope(conn, request)
            binding = {"session_id": run.session_id, "run_id": run.run_id, "holder": run.holder,
                "generation": run.generation, "action_digest": digest(scope), "input_digest": row["sha256"],
                "target_ref": "workflow-delivery:" + delivery_key(self.context, row["project_id"], row["workflow_id"], request["specialist_id"]),
                "policy_version": str(self.context.policy.policy_version), "policy_digest": self.context.policy.digest,
                "input_revision": str(row["revision"]), "artifact_revision": str(request["expected_delivery_revision"])}
            approval_id = "workflow-delivery-" + digest({"run_id": run.run_id, "scope": scope})
            approval = self.db.request_effect_approval(actor=self.actor, **binding, approval_id=approval_id, expires_at=run.deadline_at)
            return {"approval_id": approval["approval_id"], "approval_digest": approval["approval_digest"],
                "expires_at": approval["expires_at"], "scope_json": canonical(scope), "binding": binding,
                "workflow": row, "current_delivery": current}

    def commit(self, run, request, approval_id, approval_digest):
        assert_workflow_control(run, {"runtime.workflow.delivery.commit"})
        prepared = self.prepare(run, request)
        require((approval_id, approval_digest) == (prepared["approval_id"], prepared["approval_digest"]),
                "approval_mismatch", "Exact reviewed specialist delivery approval required")
        with self.workflows.access.guard(request["project_id"], self.actor, "share"):
            self._target_access(request["project_id"], request["specialist_id"])
            approval = self.db.get_effect_approval(approval_id, self.actor)
            if approval["status"] == "pending":
                self.db.resolve_effect_approval(approval_id, self.actor, holder=run.holder, generation=run.generation,
                                               approval_digest=approval_digest, choice="once")

            def write(conn):
                self.workflows._fence(conn, run)
                row, current, scope = self._scope(conn, request)
                require(canonical(scope) == prepared["scope_json"], "workflow_delivery_revision_conflict",
                        "Workflow or specialist changed during review")
                key = delivery_key(self.context, row["project_id"], row["workflow_id"], request["specialist_id"])
                if current is None:
                    count = conn.execute("SELECT COUNT(*) FROM workflow_delivery_heads h JOIN workflow_deliveries d "
                        "ON d.delivery_id=h.delivery_id WHERE d.principal_id=? AND d.profile_id=? AND d.specialist_id=?",
                        (self.actor["principal_id"], self.actor["profile_id"], request["specialist_id"])).fetchone()[0]
                    require(count < MAX_SPECIALIST_WORKFLOWS, "workflow_delivery_capacity", "Specialist workflow capacity reached")
                require(conn.execute("SELECT COUNT(*) FROM workflow_deliveries").fetchone()[0] < 65536,
                        "workflow_delivery_capacity", "Delivery audit capacity reached")
                self.db._consume_effect_approval_on_conn(conn, approval_id, self.actor, prepared["binding"], approval_id)
                delivery = {key: request[key] for key in ("project_id", "workflow_id", "version", "sha256", "specialist_id", "action")}
                delivery.update(delivery_id=approval_id, delivery_revision=request["expected_delivery_revision"] + 1,
                    approval_id=approval_id, approval_digest=approval_digest,
                    previous_delivery_id=current["delivery_id"] if current else None, activation="next_session",
                    execution_authority=False, personal_memory_shared=False, recorded_at=time.time())
                record = {"delivery": delivery, "scope": scope, "owner": self.actor}
                conn.execute("INSERT INTO workflow_deliveries VALUES(?,?,?,?,?,?,?,?,?,?,?)", (approval_id, key,
                    self.actor["principal_id"], self.actor["profile_id"], request["specialist_id"], row["project_id"],
                    self.workflows.key(row["project_id"], row["workflow_id"]), row["version"], delivery["delivery_revision"],
                    canonical(record), delivery["recorded_at"]))
                conn.execute("INSERT INTO workflow_delivery_heads VALUES(?,?,?) ON CONFLICT(delivery_key) "
                    "DO UPDATE SET delivery_id=excluded.delivery_id,revision=excluded.revision",
                    (key, approval_id, delivery["delivery_revision"]))
                return delivery

            return self.db._execute_write(write)

    def list(self, project_id, specialist_id):
        with self.workflows.access.guard(project_id, self.actor, "read"), self.db._runtime_read() as conn:
            self._target(conn, specialist_id, project_id)
            rows = conn.execute("SELECT d.*,h.revision AS head_revision FROM workflow_delivery_heads h JOIN workflow_deliveries d "
                "ON d.delivery_id=h.delivery_id WHERE d.principal_id=? AND d.profile_id=? AND d.specialist_id=? "
                "AND d.project_id=? ORDER BY d.workflow_key", (self.actor["principal_id"], self.actor["profile_id"],
                specialist_id, project_id)).fetchall()
            return [checked_delivery(conn, self.context, self.db, row)["delivery"] for row in rows]
