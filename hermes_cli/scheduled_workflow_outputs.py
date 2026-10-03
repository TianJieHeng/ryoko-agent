"""Inspect retained scheduled bytes and prepare a fresh human publication approval.

Review never reruns a workflow, copies an old approval, or adopts its generation.
Only completed production may become a new project-artifact proposal.
"""
from __future__ import annotations

import json

from agent.project_context import project_access
from agent.result_artifacts import artifact_actor, descriptor_digest, read_result_artifact
from cron.durable_contract import digest, require
from hermes_state_schedules import ScheduleRegistry


def _stored(context, db, project_id, schedule_id, occurrence_id, output_index):
    registry = ScheduleRegistry(context, db)
    actor, access = registry.actor, registry.access
    with access.guard(project_id, actor, "read"), db._runtime_read() as conn:
        schedule = registry._row(conn, registry.key(schedule_id))
        require(schedule["project_id"] == project_id, "Draft project differs", "identity_mismatch")
        occurrence = conn.execute("SELECT * FROM durable_occurrences WHERE occurrence_id=? AND schedule_key=?",
                                 (occurrence_id, schedule["schedule_key"])).fetchone()
        require(occurrence is not None, "Owned occurrence not found", "schedule_not_found")
        version = conn.execute("SELECT * FROM durable_schedule_versions WHERE schedule_key=? AND version=?",
                               (schedule["schedule_key"], occurrence["version"])).fetchone()
        definition = json.loads(version["definition_json"])
        require(definition["kind"] == "workflow_draft" and digest(definition) == version["sha256"],
                "Exact scheduled workflow definition required", "schedule_digest_mismatch")
        row = conn.execute("SELECT * FROM workflow_runs WHERE session_id=? AND run_id=?",
                           (occurrence["session_id"], occurrence["run_id"])).fetchone()
        require(row is not None and json.loads(row["owner_json"]) == actor, "Owned draft not found", "schedule_output_missing")
        saved = json.loads(row["record_json"])
        require(saved["pin"]["occurrence_id"] == occurrence_id and saved["pin"]["schedule_sha256"] == version["sha256"],
                "Draft does not match its occurrence", "schedule_input_mismatch")
        descriptors = saved["output_descriptors"]
        require(type(output_index) is int and 0 <= output_index < len(descriptors), "Scheduled output not found", "schedule_output_missing")
        descriptor = descriptors[output_index]
        effect = conn.execute("SELECT * FROM runtime_effects WHERE session_id=? AND run_id=? AND operation_type='artifact_publish' "
            "AND state='confirmed' AND input_digest=? AND target_ref=?", (occurrence["session_id"], occurrence["run_id"],
            descriptor_digest(descriptor), f"artifact:{descriptor['artifact_id']}:{descriptor['version']}")).fetchone()
        require(effect is not None and json.loads(effect["input_ref_json"]) == descriptor
                and all(effect[key] == actor[key] for key in actor), "Draft bytes have no exact confirmed receipt", "artifact_not_confirmed")
        occurrence = dict(occurrence)
    data = read_result_artifact(context, descriptor)
    return definition, occurrence, saved, descriptor, data


def read_scheduled_output(context, db, *, project_id, schedule_id, occurrence_id, output_index, offset=0, limit=65536):
    from hermes_cli.artifact_store import _chunk
    with project_access(context).guard(project_id, artifact_actor(context), "read"):
        _definition, occurrence, saved, descriptor, data = _stored(context, db, project_id, schedule_id, occurrence_id, output_index)
        return {**_chunk(project_id, descriptor, data, offset, limit), "draft_only": True,
                "occurrence_id": occurrence_id, "occurrence_state": occurrence["state"],
                "output_index": output_index, "workflow_run_id": saved["workflow_run_id"]}


def prepare_scheduled_output(run, *, project_id, schedule_id, occurrence_id, output_index, expected_sha256):
    from agent.workflow_runtime import assert_workflow_control
    from hermes_cli.artifact_store import prepare_artifact
    from hermes_cli.workflows import resolve_executable
    assert_workflow_control(run, {"runtime.schedule.output.prepare", "runtime.schedule.output.publish"})
    with project_access(run.context).guard(project_id, artifact_actor(run.context), "write"):
        definition, occurrence, saved, descriptor, data = _stored(run.context, run.db, project_id, schedule_id, occurrence_id, output_index)
        require(occurrence["state"] == "completed" and saved["state"] == "draft_ready",
                "Only completed production can be prepared; reconcile uncertain work first", "schedule_output_unresolved")
        require(descriptor["sha256"] == expected_sha256, "Review must name the exact inspected bytes", "schedule_input_mismatch")
        # Revocation stops new publications; historical confirmed bytes stay inspectable.
        resolve_executable(run.context, run.db, project_id=project_id, **definition["specification"]["workflow_ref"])
        request_id = "scheduled-review:" + digest({"occurrence_id": occurrence_id, "output_index": output_index})
        return prepare_artifact(run, project_id=project_id, request_id=request_id, content_bytes=data, mime=descriptor["mime"],
            derived_from=[{"artifact_id": ref["artifact_id"], "version": ref["version"]} for ref in saved["pin"]["source_refs"]],
            provenance={"kind": "generated", "source_ref": "schedule:" + occurrence_id})


def publish_scheduled_output(run, *, approval_id, approval_digest, **request):
    from agent.workflow_runtime import assert_workflow_control
    from hermes_cli.artifact_store import publish_artifact
    assert_workflow_control(run, {"runtime.schedule.output.publish"})
    proposal = prepare_scheduled_output(run, **request)
    require(approval_id == proposal.approval_id and approval_digest == proposal.approval_digest,
            "Exact fresh human publication approval required", "approval_mismatch")
    actor = artifact_actor(run.context)
    decision = run.db.get_effect_approval(approval_id, actor)
    if decision["status"] == "pending":
        run.db.resolve_effect_approval(approval_id, actor, holder=run.holder, generation=run.generation,
                                      approval_digest=approval_digest, choice="once")
    return publish_artifact(run, proposal)
