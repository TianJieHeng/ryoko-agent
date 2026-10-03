"""BE11 version/evaluation/run records on the authoritative SessionDB writer.

Canonical executable JSON never changes. Lifecycle and active pointers are CAS
metadata; revocation keeps runs and effect records available for reconciliation.
"""
from __future__ import annotations

import hashlib
import json
import time

from agent.result_artifacts import artifact_actor
from agent.project_context import project_access
from hermes_state_runtime import RuntimeStoreError




def require(condition, code, message):
    if not condition:
        raise RuntimeStoreError(code, message)


def canonical(value):
    from agent.workflow_contract import canonical_json
    return canonical_json(value)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class WorkflowRegistry:
    def __init__(self, context, db):
        self.context, self.db = context, db
        self.actor = artifact_actor(context)
        self.access = project_access(context)

    def key(self, project_id, workflow_id):
        return digest({"principal_id": self.actor["principal_id"], "profile_id": self.actor["profile_id"],
                       "project_id": project_id, "workflow_id": workflow_id})

    def _fence(self, conn, run):
        require(run.db is self.db and run.context == self.context, "identity_mismatch", "Workflow owner changed")
        self.db._mission_owner_on_conn(conn, run.session_id, self.actor, holder=run.holder, generation=run.generation)

    def _row(self, conn, key, version, *, owner=False):
        row = conn.execute("SELECT * FROM workflow_versions WHERE workflow_key=? AND version=?", (key, version)).fetchone()
        require(row is not None, "workflow_not_found", "Exact workflow version does not exist")
        if owner:
            require(json.loads(row["owner_json"]) == self.actor, "identity_mismatch", "Only this version's owning agent can change it")
        require(hashlib.sha256(row["definition_json"].encode()).hexdigest() == row["sha256"],
                "workflow_digest_mismatch", "Canonical workflow bytes changed")
        return row

    @staticmethod
    def _public(conn, row):
        head = conn.execute("SELECT version,revision FROM workflow_heads WHERE workflow_key=?", (row["workflow_key"],)).fetchone()
        definition = json.loads(row["definition_json"])
        return {"workflow_id": definition["workflow_id"], "version": row["version"], "project_id": row["project_id"],
                "sha256": row["sha256"], "definition_json": row["definition_json"], "state": row["state"],
                "revision": row["revision"], "evaluation_ref": row["evaluation_ref"],
                "active_version": head[0] if head else None, "head_revision": head[1] if head else 0}

    def get(self, project_id, workflow_id, version):
        with self.access.guard(project_id, self.actor, "read"), self.db._runtime_read() as conn:
            return self._public(conn, self._row(conn, self.key(project_id, workflow_id), version))

    def list(self, project_id):
        with self.access.guard(project_id, self.actor, "read"), self.db._runtime_read() as conn:
            rows = conn.execute("SELECT * FROM workflow_versions WHERE project_id=? ORDER BY created_at DESC LIMIT 100", (project_id,))
            # Principal/profile namespace is required in addition to project ACL.
            return [self._public(conn, row) for row in rows if all(json.loads(row["owner_json"])[key] == self.actor[key]
                for key in ("principal_id", "profile_id"))]

    def create(self, run, definition):
        from agent.workflow_contract import WorkflowVersion
        from agent.workflow_runtime import assert_workflow_control, verify_provenance
        assert_workflow_control(run, {"runtime.workflow.create"})
        require(isinstance(definition, WorkflowVersion), "invalid_workflow", "Typed workflow required")
        record, project = definition.to_record(), definition.project_id
        verify_provenance(run, record)
        if record["template_ref"] and record["template_ref"].get("store") == "artifact_templates":
            self.template(project, record["template_ref"])
        key = self.key(project, definition.workflow_id)
        with self.access.guard(project, self.actor, "write"):
            def write(conn):
                self._fence(conn, run)
                require(conn.execute("SELECT COUNT(*) FROM workflow_versions").fetchone()[0] < 16384,
                        "workflow_capacity", "Workflow registry capacity reached")
                require(conn.execute("SELECT 1 FROM workflow_versions WHERE workflow_key=? AND version=?", (key, definition.version)).fetchone() is None,
                        "workflow_immutable", "Editing requires a new immutable version")
                prior = record["predecessor"]
                if prior:
                    old = self._row(conn, key, prior["version"], owner=True)
                    require(prior["workflow_id"] == definition.workflow_id and prior["sha256"] == old["sha256"]
                            and prior["version"] < definition.version, "workflow_predecessor_mismatch", "Exact earlier predecessor required")
                else:
                    require(conn.execute("SELECT 1 FROM workflow_versions WHERE workflow_key=?", (key,)).fetchone() is None,
                            "workflow_predecessor_required", "Later versions must retain their exact predecessor")
                if record["template_ref"]:
                    self._template_on_conn(conn, project, record["template_ref"])
                conn.execute("INSERT INTO workflow_versions VALUES(?,?,?,?,?,?,?,1,NULL,?)", (key, definition.version,
                    project, canonical(self.actor), canonical(record), definition.digest, "draft", time.time()))
                return self._public(conn, self._row(conn, key, definition.version))
            return self.db._execute_write(write)

    def create_template(self, run, template):
        from agent.workflow_runtime import assert_workflow_control
        assert_workflow_control(run, {"runtime.workflow.template.create"})
        record, project = template.to_record(), template.project_id
        key = self.key(project, template.template_id)
        with self.access.guard(project, self.actor, "write"):
            def write(conn):
                self._fence(conn, run)
                require(conn.execute("SELECT COUNT(*) FROM workflow_templates").fetchone()[0] < 16384,
                        "workflow_capacity", "Template capacity reached")
                require(conn.execute("SELECT 1 FROM workflow_templates WHERE template_key=? AND version=?", (key, template.version)).fetchone() is None,
                        "workflow_immutable", "Template edit requires a new version")
                if record["predecessor"]:
                    predecessor = self._template_on_conn(conn, project, record["predecessor"])
                    require(json.loads(predecessor["owner_json"]) == self.actor and record["predecessor"]["template_id"] == template.template_id
                            and record["predecessor"]["version"] < template.version, "identity_mismatch", "Template predecessor owner differs")
                else:
                    require(conn.execute("SELECT 1 FROM workflow_templates WHERE template_key=?", (key,)).fetchone() is None,
                            "workflow_predecessor_required", "Template successor requires predecessor")
                conn.execute("INSERT INTO workflow_templates VALUES(?,?,?,?,?,?,?)", (key, template.version, project,
                    canonical(self.actor), canonical(record), template.digest, time.time()))
                return {"template_id": template.template_id, "version": template.version, "project_id": project,
                        "sha256": template.digest, "definition_json": canonical(record)}
            return self.db._execute_write(write)

    def _template_on_conn(self, conn, project_id, ref):
        if ref.get("store") == "artifact_templates":
            from hermes_cli.template_application import template_digest
            row = conn.execute("SELECT * FROM artifact_templates WHERE template_id=? AND version=?",
                               (ref["template_id"], ref["version"])).fetchone()
            require(row is not None and row["project_id"] == project_id
                    and row["principal_id"] == self.actor["principal_id"] and row["profile_id"] == self.actor["profile_id"],
                    "workflow_template_mismatch", "Canonical template belongs to another project or owner scope")
            record = self.db._template_result_on_conn(conn, row, self.actor, self.access)
            require(template_digest(record) == ref["sha256"], "workflow_template_mismatch", "Canonical template digest differs")
            return {"definition_json": canonical({key: value for key, value in record.items() if key != "owner_actor"}),
                    "owner_json": canonical(record["owner_actor"]), "sha256": ref["sha256"]}
        row = conn.execute("SELECT * FROM workflow_templates WHERE template_key=? AND version=?",
                           (self.key(project_id, ref["template_id"]), ref["version"])).fetchone()
        require(row is not None and row["sha256"] == ref["sha256"] and hashlib.sha256(row["definition_json"].encode()).hexdigest() == ref["sha256"],
                "workflow_template_mismatch", "Exact template bytes are unavailable")
        return row

    def template(self, project_id, ref):
        if ref.get("store") == "artifact_templates":
            from hermes_cli.template_application import resolve_template
            return resolve_template(self.context, self.db, project_id, ref)
        with self.access.guard(project_id, self.actor, "read"), self.db._runtime_read() as conn:
            return json.loads(self._template_on_conn(conn, project_id, ref)["definition_json"])

    def evaluation(self, project_id, workflow_id, version, evaluation_id):
        with self.access.guard(project_id, self.actor, "read"), self.db._runtime_read() as conn:
            key = self.key(project_id, workflow_id)
            self._row(conn, key, version)
            row = conn.execute("SELECT record_json FROM workflow_evaluations WHERE evaluation_id=? AND workflow_key=? AND version=?",
                               (evaluation_id, key, version)).fetchone()
            require(row is not None, "workflow_evaluation_missing", "Exact evaluation is unavailable")
            return json.loads(row[0])

    def save_evaluation(self, run, row, evidence):
        from agent.workflow_runtime import assert_workflow_control
        assert_workflow_control(run, {"runtime.workflow.evaluate"})
        project = row["project_id"]
        with self.access.guard(project, self.actor, "write"):
            def write(conn):
                self._fence(conn, run)
                key = self.key(project, row["workflow_id"])
                current = self._row(conn, key, row["version"], owner=True)
                require(current["state"] in {"draft", "tested"} and current["revision"] == row["revision"],
                        "workflow_revision_conflict", "Evaluation no longer matches the draft")
                require(conn.execute("SELECT COUNT(*) FROM workflow_evaluations").fetchone()[0] < 65536,
                        "workflow_capacity", "Evaluation capacity reached")
                # A case cannot move from held-out to tuning (or vice versa), even
                # after editing the workflow. We record declarations, not claims
                # that an external teacher never saw the held-out content.
                splits = {case["input_sha256"]: case["split"] for case in evidence["cases"]}
                for previous in conn.execute("SELECT record_json FROM workflow_evaluations WHERE workflow_key=?", (key,)):
                    for case in json.loads(previous[0])["cases"]:
                        require(case["input_sha256"] not in splits or splits[case["input_sha256"]] == case["split"],
                                "workflow_holdout_leakage", "Held-out and tuning inputs must remain separate")
                conn.execute("INSERT INTO workflow_evaluations VALUES(?,?,?,?,?)", (evidence["evaluation_id"], key,
                    row["version"], canonical(evidence), time.time()))
                if evidence["passed"]:
                    conn.execute("UPDATE workflow_versions SET state='tested',revision=revision+1,evaluation_ref=? WHERE workflow_key=? AND version=?",
                                 (evidence["evaluation_id"], key, row["version"]))
                return self._public(conn, self._row(conn, key, row["version"]))
            return self.db._execute_write(write)

    def save_feedback(self, run, row, evidence):
        from agent.workflow_runtime import assert_workflow_control
        assert_workflow_control(run, {"runtime.workflow.feedback"})
        with self.access.guard(row["project_id"], self.actor, "write"):
            def write(conn):
                self._fence(conn, run)
                key = self.key(row["project_id"], row["workflow_id"])
                self._row(conn, key, row["version"])
                require(conn.execute("SELECT COUNT(*) FROM workflow_evidence").fetchone()[0] < 65536,
                        "workflow_capacity", "Feedback capacity reached")
                conn.execute("INSERT INTO workflow_evidence VALUES(?,?,?,?,?)", (evidence["evidence_id"], key,
                    row["version"], canonical(evidence), time.time()))
                return evidence
            return self.db._execute_write(write)
