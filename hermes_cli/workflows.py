"""Manual workflow authoring, exact human promotion, export and pinned execution.

This is an owned RPC service, not a model tool or scheduler. CLI validation can
inspect local definitions but cannot mint approval or runtime authority.
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid

from agent.artifact_commands import assert_artifact_dispatch
from agent.project_context import authorize_project, project_access
from agent.result_artifacts import artifact_actor
from agent.workflow_runtime import assert_workflow_control, execute_workflow
from hermes_state_workflows import WorkflowRegistry, canonical, digest, require

DECISIONS = frozenset({"approve", "deprecate", "revoke", "rollback", "authorize_export"})


def resolve_executable(context, db, *, project_id, workflow_id, version, sha256):
    """Shared scheduler/manual admission: exact approved version, never a moving head."""
    from agent.workflow_contract import WorkflowVersion
    row = WorkflowRegistry(context, db).get(project_id, workflow_id, version)
    require(row["sha256"] == sha256, "workflow_digest_mismatch", "Executable version digest differs")
    require(row["state"] == "approved", "workflow_not_approved", "Only an approved nonrevoked version can execute")
    authorize_project(context, project_id, "write")
    definition = WorkflowVersion.from_record(json.loads(row["definition_json"]))
    if definition.to_record()["template_ref"]:
        WorkflowRegistry(context, db).template(project_id, definition.to_record()["template_ref"])
    return definition


def _decision_scope(registry, request):
    require(request["action"] in DECISIONS, "workflow_decision_invalid", "Unknown workflow decision")
    row = registry.get(request["project_id"], request["workflow_id"], request["version"])
    require(row["sha256"] == request["sha256"] and row["revision"] == request["expected_revision"]
            and row["head_revision"] == request["expected_head_revision"],
            "workflow_revision_conflict", "Decision does not name current version, lifecycle and pointer")
    action = request["action"]
    if action == "approve":
        require(row["state"] == "tested" and row["evaluation_ref"] is not None,
                "workflow_evaluation_required", "Promotion requires reproducibly tested content")
        evidence = registry.evaluation(row["project_id"], row["workflow_id"], row["version"], row["evaluation_ref"])
        require(evidence["passed"] and evidence["workflow_sha256"] == row["sha256"],
                "workflow_evaluation_required", "Evaluation differs from canonical content")
    elif action == "rollback":
        require(row["state"] == "approved" and row["active_version"] is not None and row["active_version"] != row["version"],
                "workflow_rollback_invalid", "Rollback requires a different approved predecessor")
        current = registry.get(row["project_id"], row["workflow_id"], row["active_version"])
        seen = set()
        while current["version"] != row["version"]:
            require(current["version"] not in seen and len(seen) < 100, "workflow_rollback_invalid", "Predecessor history is invalid")
            seen.add(current["version"])
            previous = json.loads(current["definition_json"])["predecessor"]
            require(previous is not None, "workflow_rollback_invalid", "Target is not an approved predecessor")
            current = registry.get(row["project_id"], row["workflow_id"], previous["version"])
            require(current["sha256"] == previous["sha256"], "workflow_digest_mismatch", "Predecessor content changed")
    elif action == "authorize_export":
        authorize_project(registry.context, row["project_id"], "share")
        require(row["state"] == "approved" and isinstance(request.get("recipient"), str)
                and 0 < len(request["recipient"]) <= 256 and request["recipient"].strip() == request["recipient"]
                and not any(ord(c) < 32 for c in request["recipient"]),
                "workflow_share_grant_required", "Sharing requires an exact destination and approved version")
    else:
        allowed = {"deprecate": {"approved"}, "revoke": {"draft", "tested", "approved", "deprecated"}}
        require(row["state"] in allowed[action], "workflow_transition_invalid", "Lifecycle transition is not allowed")
    require(action == "authorize_export" or request.get("recipient") is None,
            "workflow_decision_invalid", "Recipient belongs only to an explicit sharing decision")
    return row, {**request, "evaluation_ref": row["evaluation_ref"], "state": row["state"]}


def prepare_decision(run, request):
    assert_workflow_control(run, {"runtime.workflow.decision.prepare", "runtime.workflow.decision.commit"})
    registry = WorkflowRegistry(run.context, run.db)
    row, scope = _decision_scope(registry, request)
    actor = artifact_actor(run.context)
    binding = {"session_id": run.session_id, "run_id": run.run_id, "holder": run.holder, "generation": run.generation,
        "action_digest": digest(scope), "input_digest": row["sha256"],
        "target_ref": "workflow:" + digest({"project": row["project_id"], "workflow": row["workflow_id"], "recipient": request.get("recipient")}),
        "policy_version": str(run.context.policy.policy_version), "policy_digest": run.context.policy.digest,
        "input_revision": str(row["revision"]), "artifact_revision": str(row["version"])}
    approval_id = "workflow-decision-" + digest({"run_id": run.run_id, "scope": scope})
    approval = run.db.request_effect_approval(actor=actor, **binding, approval_id=approval_id, expires_at=run.deadline_at)
    return {"approval_id": approval["approval_id"], "approval_digest": approval["approval_digest"],
            "expires_at": approval["expires_at"], "scope_json": canonical(scope), "binding": binding, "workflow": row}


def commit_decision(run, request, approval_id, approval_digest):
    assert_workflow_control(run, {"runtime.workflow.decision.commit"})
    prepared = prepare_decision(run, request)
    require((approval_id, approval_digest) == (prepared["approval_id"], prepared["approval_digest"]),
            "approval_mismatch", "Exact human decision approval required")
    registry, actor = WorkflowRegistry(run.context, run.db), artifact_actor(run.context)
    row, scope = prepared["workflow"], json.loads(prepared["scope_json"])
    with registry.access.guard(row["project_id"], actor, "share" if request["action"] == "authorize_export" else "write"):
        approval = run.db.get_effect_approval(approval_id, actor)
        if approval["status"] == "pending":
            run.db.resolve_effect_approval(approval_id, actor, holder=run.holder, generation=run.generation,
                                          approval_digest=approval_digest, choice="once")
        def write(conn):
            registry._fence(conn, run)
            key = registry.key(row["project_id"], row["workflow_id"])
            current = registry._row(conn, key, row["version"], owner=True)
            actual = registry._public(conn, current)
            require(actual["revision"] == row["revision"] and actual["head_revision"] == row["head_revision"],
                    "workflow_revision_conflict", "Lifecycle or active pointer changed during review")
            require(conn.execute("SELECT COUNT(*) FROM workflow_decisions").fetchone()[0] < 65536,
                    "workflow_capacity", "Decision audit capacity reached")
            run.db._consume_effect_approval_on_conn(conn, approval_id, actor, prepared["binding"], approval_id)
            action = request["action"]
            if action in {"approve", "rollback"}:
                conn.execute("INSERT INTO workflow_heads VALUES(?,?,?) ON CONFLICT(workflow_key) DO UPDATE SET version=excluded.version,revision=excluded.revision",
                             (key, row["version"], row["head_revision"] + 1))
            states = {"approve": "approved", "deprecate": "deprecated", "revoke": "revoked"}
            if action in states:
                conn.execute("UPDATE workflow_versions SET state=?,revision=revision+1 WHERE workflow_key=? AND version=?",
                             (states[action], key, row["version"]))
            if action in {"deprecate", "revoke"} and row["active_version"] == row["version"]:
                # Keep the pointer for rollback ancestry; admission also checks state.
                conn.execute("UPDATE workflow_heads SET revision=revision+1 WHERE workflow_key=?", (key,))
            if action == "revoke":
                for saved in conn.execute("SELECT workflow_run_id,record_json FROM workflow_runs WHERE workflow_key=? AND version=?", (key, row["version"])).fetchall():
                    record = json.loads(saved["record_json"])
                    if record["state"] == "prepared":
                        record["state"] = "paused_revoked"
                        conn.execute("UPDATE workflow_runs SET record_json=?,updated_at=? WHERE workflow_run_id=?",
                                     (canonical(record), time.time(), saved["workflow_run_id"]))
            decision = {"approval_id": approval_id, "approval_digest": approval_digest, "scope": scope,
                        "authority": "owned_human_rpc", "recorded_at": time.time(), "external_send_performed": False}
            conn.execute("INSERT INTO workflow_decisions VALUES(?,?,?,?,?)", (approval_id, key, row["version"], canonical(decision), time.time()))
            return {"workflow": registry._public(conn, registry._row(conn, key, row["version"])), "decision_json": canonical(decision)}
        return run.db._execute_write(write)


def export_workflow(context, db, *, project_id, workflow_id, version, recipient, approval_id):
    registry = WorkflowRegistry(context, db)
    row = registry.get(project_id, workflow_id, version)
    authorize_project(context, project_id, "share")
    with registry.access.guard(project_id, registry.actor, "share"), db._runtime_read() as conn:
        row = registry._public(conn, registry._row(conn, registry.key(project_id, workflow_id), version))
        saved = conn.execute("SELECT record_json FROM workflow_decisions WHERE approval_id=? AND workflow_key=? AND version=?",
                            (approval_id, registry.key(project_id, workflow_id), version)).fetchone()
        require(saved is not None, "workflow_share_grant_required", "Private procedure export requires an exact human publication grant")
        scope = json.loads(saved[0])["scope"]
        require(scope["action"] == "authorize_export" and scope["recipient"] == recipient and scope["sha256"] == row["sha256"]
                and row["state"] == "approved", "workflow_share_grant_required", "Sharing grant does not match exact recipient/content")
        return {"recipient": recipient, "export_json": canonical({"workflow": json.loads(row["definition_json"]),
                "sha256": row["sha256"], "evaluation_ref": row["evaluation_ref"],
                "template": registry.template(project_id, json.loads(row["definition_json"])["template_ref"])
                             if json.loads(row["definition_json"])["template_ref"] else None}),
                "external_send_performed": False}


def prepare_workflow_run(run, request):
    from hermes_cli.artifact_store import prepare_artifact
    assert_workflow_control(run, {"runtime.workflow.run.prepare", "runtime.workflow.run.publish"})
    registry, actor = WorkflowRegistry(run.context, run.db), artifact_actor(run.context)
    definition = resolve_executable(run.context, run.db, **{key: request[key] for key in ("project_id", "workflow_id", "version", "sha256")})
    mission = run.db.get_mission(run.session_id, actor, access=registry.access)
    require(mission is not None and mission["project_id"] == request["project_id"] and mission["mission_id"] == request["mission_id"]
            and mission["revision"] == request["mission_revision"] and mission["state"] in {"ready", "working"},
            "workflow_mission_mismatch", "Workflow requires its exact ready mission revision")
    require(mission["deadline"] is None or mission["deadline"] > time.time(), "workflow_mission_expired", "Mission deadline expired")
    if run.budget is not None:
        require(mission["budget_ref"] in {None, run.budget.root_id}, "budget_parent_required", "Workflow cannot replace its mission's budget root")
    require(not run.db.list_effects(run.session_id, actor, unresolved_only=True),
            "effect_reconciliation_required", "Reconcile existing effects before preparing a workflow")
    admitted_at = run.db.read_runtime_run_accepted_at(run.session_id, run.run_id)
    pin = {"workflow_id": request["workflow_id"], "version": request["version"], "sha256": request["sha256"],
        "project_id": request["project_id"], "parameters_sha256": digest(request["parameters"]),
        "parameters_json": canonical(request["parameters"]), "mission_id": mission["mission_id"], "mission_revision": mission["revision"],
        "template_ref": definition.to_record()["template_ref"], "environment_manifest": definition.to_record()["environment_manifest"],
        "budget_ref": run.budget.root_id if run.budget else mission["budget_ref"], "admitted_at": admitted_at}
    run_id = "workflow-run-" + digest({"run_id": run.run_id, "workflow": request["workflow_id"]})
    with registry.access.guard(request["project_id"], actor, "write"):
        def claim(conn):
            registry._fence(conn, run)
            current = registry._row(conn, registry.key(request["project_id"], request["workflow_id"]), request["version"])
            require(current["state"] == "approved" and current["sha256"] == request["sha256"],
                    "workflow_not_approved", "Workflow was revoked or changed before admission")
            old = conn.execute("SELECT * FROM workflow_runs WHERE workflow_run_id=?", (run_id,)).fetchone()
            if old:
                record = json.loads(old["record_json"])
                require(json.loads(old["owner_json"]) == actor and record["pin"] == pin and record["state"] == "prepared",
                        "workflow_resume_mismatch", "Resume cannot switch canonical recipe, inputs, mission or template")
                return record
            require(conn.execute("SELECT COUNT(*) FROM workflow_runs").fetchone()[0] < 65536,
                    "workflow_capacity", "Run history capacity reached")
            record = {"workflow_run_id": run_id, "pin": pin, "state": "prepared", "output_sha256": None, "artifact_refs": []}
            conn.execute("INSERT INTO workflow_runs VALUES(?,?,?,?,?,?,?,?,?)", (run_id, registry.key(request["project_id"], request["workflow_id"]),
                request["version"], run.session_id, run.run_id, canonical(actor), canonical(record), time.time(), time.time()))
            return record
        saved = run.db._execute_write(claim)
        produced = execute_workflow(run, definition, request["parameters"], admitted_at=admitted_at)
        require(saved["output_sha256"] in {None, produced["output_sha256"]}, "workflow_resume_mismatch", "Recomputed output differs from the original prepared run")
        proposals = []
        for index, output in enumerate(produced["outputs"]):
            proposals.append(prepare_artifact(run, project_id=request["project_id"], request_id=f"{run_id}:{index}",
                content_bytes=output["content_bytes"], mime=output["mime"],
                derived_from=[{"artifact_id": ref["artifact_id"], "version": ref["version"]} for ref in produced["source_refs"]]))
        manifest = {"schema_version": 1, "workflow_run_id": run_id, "pin": pin,
            "outputs": [{"name": output["name"], "step_id": output["step_id"],
                **{key: proposal.public_record()[key] for key in ("artifact_id", "version", "sha256", "mime")}}
                for output, proposal in zip(produced["outputs"], proposals)],
            "output_sha256": produced["output_sha256"], "external_effects": "none", "publication_atomic": False}
        proposals.append(prepare_artifact(run, project_id=request["project_id"], request_id=f"{run_id}:manifest",
            content_bytes=(canonical(manifest) + "\n").encode(), mime="application/json"))
        def save(conn):
            registry._fence(conn, run)
            saved["output_sha256"] = produced["output_sha256"]
            conn.execute("UPDATE workflow_runs SET record_json=?,updated_at=? WHERE workflow_run_id=?", (canonical(saved), time.time(), run_id))
        run.db._execute_write(save)
    return saved, tuple(proposals)


def publish_workflow_run(run, request, approvals):
    from hermes_cli.artifact_store import publish_artifact
    assert_workflow_control(run, {"runtime.workflow.run.publish"})
    saved, proposals = prepare_workflow_run(run, request)
    require(approvals == [{key: proposal.public_record()[key] for key in ("approval_id", "approval_digest")} for proposal in proposals],
            "approval_mismatch", "All exact ordered workflow outputs require human approval")
    registry, actor = WorkflowRegistry(run.context, run.db), artifact_actor(run.context)
    with registry.access.guard(request["project_id"], actor, "write"):
        records = []
        for proposal in proposals:
            decision = run.db.get_effect_approval(proposal.approval_id, actor)
            if decision["status"] == "pending":
                run.db.resolve_effect_approval(proposal.approval_id, actor, holder=run.holder, generation=run.generation,
                    approval_digest=proposal.approval_digest, choice="once")
            records.append(publish_artifact(run, proposal))
        refs = [{"artifact_id": row["artifact_id"], "version": row["version"], "digest": row["sha256"]} for row in records]
        def finish(conn):
            registry._fence(conn, run)
            saved.update(state="published", artifact_refs=refs)
            current, mission = run.db._mission_row_on_conn(conn, run.session_id, actor, holder=run.holder,
                generation=run.generation, expected_revision=request["mission_revision"])
            run.db._update_mission_on_conn(conn, run.session_id, actor, run.holder, run.generation, current["revision"],
                {"artifact_refs": mission["artifact_refs"] + [ref for ref in refs if ref not in mission["artifact_refs"]]}, registry.access)
            conn.execute("UPDATE workflow_runs SET record_json=?,updated_at=? WHERE workflow_run_id=?",
                         (canonical(saved), time.time(), saved["workflow_run_id"]))
        run.db._execute_write(finish)
    return {"workflow_run_id": saved["workflow_run_id"], "state": "published", "outputs": records[:-1],
            "manifest": records[-1], "publication_atomic": False, "mission_completed": False}


def run_history(context, db, project_id, workflow_id, version):
    registry = WorkflowRegistry(context, db)
    registry.get(project_id, workflow_id, version)
    with registry.access.guard(project_id, registry.actor, "read"), db._runtime_read() as conn:
        rows = conn.execute("SELECT record_json,owner_json,run_id,session_id FROM workflow_runs WHERE workflow_key=? AND version=? ORDER BY created_at DESC LIMIT 100",
                            (registry.key(project_id, workflow_id), version)).fetchall()
        results = []
        for row in rows:
            if json.loads(row["owner_json"]) != registry.actor:
                continue
            record = json.loads(row["record_json"])
            control = conn.execute("SELECT status FROM runtime_commands WHERE run_id=? AND session_id=? LIMIT 1",
                                   (row["run_id"], row["session_id"])).fetchone()
            record["control_status"] = control[0] if control else "unavailable"
            if record["state"] == "prepared" and record["control_status"] in {"cancelled", "blocked", "failed"}:
                record["state"] = record["control_status"]
            results.append(record)
        return results


def main(argv=None):
    import argparse
    from pathlib import Path
    from agent.workflow_contract import WorkflowVersion
    parser = argparse.ArgumentParser(description="Validate immutable workflow syntax; owned runtime.workflow RPCs perform evaluation and execution")
    parser.add_argument("definition")
    args = parser.parse_args(argv)
    with Path(args.definition).open("rb") as handle:
        data = handle.read(262145)
    if len(data) > 262144:
        parser.error("workflow exceeds 256 KiB")
    definition = WorkflowVersion.from_record(json.loads(data))
    print(canonical({"status": "syntax_validated", "sha256": definition.digest, "executed": False, "approved": False}))


if __name__ == "__main__":
    main()
