"""One grant-fenced local Markdown producer on the existing cron occurrence.

This authority can execute a pinned finite renderer and retain private result
bytes. It is deliberately not artifact-control, inference, or publication
permission. Human publication uses a new ordinary control and approval.
"""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field, replace
import json
import threading
import time

from agent.result_artifacts import artifact_actor, result_artifact_descriptor, publish_result_artifact
from agent.project_context import project_access
from cron.durable_contract import canonical, digest, require
from cron.durable_workflow_contract import grant_target, resolve_workflow

_CURRENT = ContextVar("scheduled_workflow_production", default=None)


@dataclass(frozen=True)
class ScheduledWorkflowRun:
    registry: object
    occurrence_json: str
    definition_json: str
    workflow_json: str
    parameters_json: str
    workflow_run_id: str
    holder: str
    generation: int
    descriptors_json: str = "[]"
    budget: object = None
    dispatch_blocked: threading.Event = field(default_factory=threading.Event, compare=False)

    @property
    def context(self):
        return self.registry.context

    @property
    def db(self):
        return self.registry.db

    @property
    def session_id(self):
        return json.loads(self.occurrence_json)["session_id"]

    @property
    def command_id(self):
        return json.loads(self.occurrence_json)["command_id"]

    @property
    def run_id(self):
        return json.loads(self.occurrence_json)["run_id"]

    @property
    def output_limit(self):
        return json.loads(self.definition_json)["budget"]["max_bytes"]

    @property
    def deadline_at(self):
        return json.loads(self.occurrence_json)["deadline_at"]


def _check_on_conn(registry, occurrence, definition, holder, generation, conn):
    """All mutable schedule/grant/workflow/claim gates share the writer fence."""
    now = time.time()
    registry.db._runtime_fence_on_conn(conn, occurrence["session_id"], holder, generation)
    row = registry._row(conn, occurrence["schedule_key"])
    owner = json.loads(row["owner_binding_json"])
    require(all(owner[field] == registry.context.identity.to_record()[field] for field in
            ("principal_id", "profile_id", "agent_id", "policy_digest", "profile_home_digest", "lifecycle")),
            "Scheduled grant belongs to another policy or owner", "identity_mismatch")
    require(row["state"] == "active" and row["version"] == occurrence["version"], "Scheduled production stopped", "schedule_stopped")
    require(json.loads(registry._version(conn, row)["definition_json"]) == definition,
            "Scheduled definition differs from the canonical pin", "schedule_digest_mismatch")
    current = conn.execute("SELECT * FROM durable_occurrences WHERE occurrence_id=?", (occurrence["occurrence_id"],)).fetchone()
    require(current is not None and all(current[key] == occurrence[key] for key in
            ("session_id", "command_id", "run_id", "schedule_key", "version", "deadline_at", "accepted_at"))
            and current["state"] == "claimed" and current["holder"] == holder and current["generation"] == generation,
            "Scheduled occurrence lost its exact claim", "stale_owner")
    registry.db._effect_run_on_conn(conn, occurrence["session_id"], registry.actor, occurrence["run_id"], holder, generation, dispatch=True)
    require(now < occurrence["deadline_at"] and now < definition["expires_at"], "Original scheduled deadline expired", "schedule_expired")
    intent = conn.execute("SELECT * FROM durable_monitor_intents WHERE occurrence_id=? AND kind='workflow'", (occurrence["occurrence_id"],)).fetchone()
    require(intent is not None and intent["state"] == "authorized", "No live admitted workflow grant", "schedule_grant_required")
    record = json.loads(intent["record_json"])
    grant = conn.execute("SELECT * FROM durable_condition_grants WHERE grant_id=?", (record["grant_id"],)).fetchone()
    require(grant is not None and grant["state"] == "active" and grant["expires_at"] > now
            and grant["schedule_key"] == occurrence["schedule_key"] and grant["version"] == occurrence["version"]
            and grant["target_digest"] == record["target_digest"] == grant_target(definition)
            and now - occurrence["accepted_at"] <= grant["max_age_seconds"],
            "Exact workflow grant expired, changed or was revoked", "schedule_grant_required")
    from hermes_state_workflows import WorkflowRegistry
    ref = definition["specification"]["workflow_ref"]
    workflows = WorkflowRegistry(registry.context, registry.db)
    workflow = workflows._row(conn, workflows.key(definition["project_id"], ref["workflow_id"]), ref["version"])
    require(workflow["state"] == "approved" and workflow["sha256"] == ref["sha256"],
            "Pinned workflow is no longer approved", "workflow_not_approved")
    return record


def _check(registry, occurrence, definition, holder, generation):
    from agent.runtime_context import current_agent_context
    from tools.capability_broker import require_live_policy
    require(current_agent_context() == registry.context and require_live_policy(require_run=False) == registry.context,
            "Scheduled identity or policy changed", "identity_mismatch")
    with registry.access.guard(definition["project_id"], registry.actor, "write"), registry.db._runtime_read() as conn:
        return _check_on_conn(registry, occurrence, definition, holder, generation, conn)


def assert_scheduled_workflow_dispatch(run, *, definition=None, parameters=None):
    require(isinstance(run, ScheduledWorkflowRun) and _CURRENT.get() is run,
            "Dedicated claimed scheduled workflow authority required", "schedule_control_required")
    require(not run.dispatch_blocked.is_set(), "Scheduled result needs reconciliation", "effect_reconciliation_required")
    _check(run.registry, json.loads(run.occurrence_json), json.loads(run.definition_json), run.holder, run.generation)
    if definition is not None:
        require(definition.to_record() == json.loads(run.workflow_json) and parameters == json.loads(run.parameters_json),
                "Workflow dispatch differs from exact admitted inputs", "schedule_input_mismatch")
    return run


def assert_scheduled_result_dispatch(run, descriptor):
    assert_scheduled_workflow_dispatch(run)
    require(descriptor in json.loads(run.descriptors_json), "Private output is outside the produced manifest", "schedule_input_mismatch")
    with run.db._runtime_read() as conn:
        row = conn.execute("SELECT record_json FROM workflow_runs WHERE workflow_run_id=? AND run_id=? AND session_id=?",
            (run.workflow_run_id, run.run_id, run.session_id)).fetchone()
    saved = json.loads(row[0]) if row else {}
    require(saved.get("state") == "producing" and saved.get("output_descriptors") == json.loads(run.descriptors_json)
            and saved.get("pin", {}).get("parameters_sha256") == digest(json.loads(run.parameters_json)),
            "Scheduled private result manifest changed", "schedule_input_mismatch")
    return run


def _inputs(registry, definition, workflow, check):
    """Snapshot only explicitly granted current local heads, then use immutable bytes."""
    from hermes_cli.domain_media import _read
    from agent.workflow_contract import validate_parameters
    parameters = dict(definition["specification"]["parameters"])
    refs, total, seen = [], 0, set()
    def read(row):
        nonlocal total
        check()
        require(row["project_id"] == definition["project_id"], "Source project differs", "identity_mismatch")
        ref = {"artifact_id": row["artifact_id"], "version": row["version"], "sha256": row["descriptor"]["sha256"]}
        key = (ref["artifact_id"], ref["version"])
        if key not in seen:
            total += row["descriptor"]["size"]
            require(total <= definition["budget"]["max_bytes"], "Scheduled source byte budget exceeded", "schedule_budget_exhausted")
            require(len(refs) < 16, "Scheduled source manifest exceeds its bound", "schedule_budget_exhausted")
            refs.append(ref)
            seen.add(key)
        raw, mime = _read(registry.context, registry.db, definition["project_id"], ref)
        check()
        return raw, mime
    for binding in definition["specification"]["source_bindings"]:
        check()
        head = registry.db.get_artifact_head(binding["artifact_id"], registry.actor, access=registry.access)
        require(head is not None, "Scheduled source has no current head", "source_not_found")
        raw, mime = read(head)
        require(mime in {"text/markdown", "text/plain"}, "Scheduled bindings accept local text or Markdown only", "schedule_adapter_unsupported")
        parameters[binding["parameter"]] = raw.decode("utf-8")
    template_ref = workflow.to_record()["template_ref"]
    if template_ref:
        from hermes_state_workflows import WorkflowRegistry
        template = WorkflowRegistry(registry.context, registry.db).template(definition["project_id"], template_ref)
        for ref in [template["baseline_ref"], *template["assets"]]:
            check()
            read(registry.db.read_artifact_version(ref["artifact_id"], ref["version"], registry.actor, access=registry.access))
    validate_parameters(workflow.to_record()["input_schema"], parameters)
    return parameters, refs, total


def produce_workflow_draft(registry, occurrence, definition, holder, generation):
    from agent.workflow_runtime import execute_workflow
    from hermes_state_workflows import WorkflowRegistry
    check = lambda: _check(registry, occurrence, definition, holder, generation)
    grant = check()
    workflow = resolve_workflow(registry.context, registry.db, definition)
    parameters, sources, bytes_read = _inputs(registry, definition, workflow, check)
    check()
    workflow_run_id = "scheduled-workflow-" + digest(occurrence["occurrence_id"])
    mission_id = "scheduled-mission-" + digest(occurrence["occurrence_id"])
    pin = {**definition["specification"]["workflow_ref"], "project_id": definition["project_id"],
        "parameters_sha256": digest(parameters), "source_refs": sources, "schedule_sha256": digest(definition),
        "template_ref": workflow.to_record()["template_ref"], "environment_manifest": workflow.to_record()["environment_manifest"],
        "destination": definition["specification"]["destination"], "grant_id": grant["grant_id"],
        "occurrence_id": occurrence["occurrence_id"], "mission_id": mission_id,
        "admitted_at": occurrence["accepted_at"], "deadline_at": occurrence["deadline_at"],
        "policy_digest": registry.context.policy.digest, "budget": definition["budget"]}
    saved = {"workflow_run_id": workflow_run_id, "pin": pin, "state": "producing", "artifact_refs": [],
             "output_sha256": None, "output_descriptors": [], "draft_only": True}
    run = ScheduledWorkflowRun(registry, canonical(occurrence), canonical(definition), canonical(workflow.to_record()),
                               canonical(parameters), workflow_run_id, holder, generation)
    def start(conn):
        _check_on_conn(registry, occurrence, definition, holder, generation, conn)
        require(conn.execute("SELECT COUNT(*) FROM workflow_runs").fetchone()[0] < 65536, "Workflow history capacity reached", "workflow_capacity")
        registry.db._create_mission_on_conn(conn, run.session_id, registry.actor, holder, generation,
            {"outcome": "Produce a pinned scheduled Markdown draft for human review", "project_id": definition["project_id"],
             "risk": "low", "uncertainty": "low", "deadline": occurrence["deadline_at"], "max_turns": 1,
             "acceptance": [{"criterion_id": "human-review", "kind": "user_acceptance"}]}, mission_id, registry.access)
        conn.execute("INSERT INTO workflow_runs VALUES(?,?,?,?,?,?,?,?,?)", (workflow_run_id,
            WorkflowRegistry(registry.context, registry.db).key(definition["project_id"], workflow.workflow_id), workflow.version,
            run.session_id, run.run_id, canonical(registry.actor), canonical(saved), time.time(), time.time()))
    with registry.access.guard(definition["project_id"], registry.actor, "write"):
        registry.db._execute_write(start)
    token = _CURRENT.set(run)
    try:
        produced = execute_workflow(run, workflow, parameters, admitted_at=occurrence["accepted_at"])
        total = sum(len(item["content_bytes"]) for item in produced["outputs"])
        require(bytes_read + total <= definition["budget"]["max_bytes"], "Scheduled input/output byte budget exceeded", "schedule_budget_exhausted")
        descriptors = [result_artifact_descriptor(run.context, run.run_id, item["content_bytes"],
            "schedule-draft-" + digest({"occurrence": occurrence["occurrence_id"], "output": index}), mime=item["mime"])
            for index, item in enumerate(produced["outputs"])]
        saved.update(output_descriptors=descriptors, output_sha256=produced["output_sha256"],
                     outputs=[{"name": item["name"], "step_id": item["step_id"]} for item in produced["outputs"]])
        def manifest(conn):
            _check_on_conn(registry, occurrence, definition, holder, generation, conn)
            conn.execute("UPDATE workflow_runs SET record_json=?,updated_at=? WHERE workflow_run_id=?",
                         (canonical(saved), time.time(), workflow_run_id))
        check()
        with registry.access.guard(definition["project_id"], registry.actor, "write"):
            registry.db._execute_write(manifest)
        run = replace(run, descriptors_json=canonical(descriptors))
        _CURRENT.set(run)
        for item, descriptor in zip(produced["outputs"], descriptors):
            assert_scheduled_result_dispatch(run, descriptor)
            publish_result_artifact(run, item["content_bytes"], descriptor["artifact_id"], mime=item["mime"])
        check()
        def finish(conn):
            _check_on_conn(registry, occurrence, definition, holder, generation, conn)
            saved["state"] = "draft_ready"
            conn.execute("UPDATE workflow_runs SET record_json=?,updated_at=? WHERE workflow_run_id=?",
                         (canonical(saved), time.time(), workflow_run_id))
            registry.db._update_mission_on_conn(conn, run.session_id, registry.actor, holder, generation, 1,
                {"state": "waiting_for_user", "next_step": "Review the retained scheduled draft bytes before preparing exact publication approval"}, registry.access)
        with registry.access.guard(definition["project_id"], registry.actor, "write"):
            registry.db._execute_write(finish)
        return {"workflow_run_id": workflow_run_id, "mission_id": mission_id, "workflow_ref": definition["specification"]["workflow_ref"],
                "parameters_sha256": pin["parameters_sha256"], "source_refs": sources, "bytes_read": bytes_read, "bytes_produced": total,
                "source_scope": "retained_local_artifacts", "live_connection_verified": False, "draft_only": True,
                "publication_state": "human_review_required", "outputs": [
                    {"output_index": index, **{key: descriptor[key] for key in ("artifact_id", "version", "sha256", "size", "mime")},
                     **saved["outputs"][index], "review_method": "runtime.schedule.output.get"}
                    for index, descriptor in enumerate(descriptors)]}
    finally:
        _CURRENT.reset(token)


def record_workflow_interruption(conn, occurrence, state, *, error):
    """Retain honest inspection metadata after failure/lost claim; never dispatch."""
    row = conn.execute("SELECT * FROM workflow_runs WHERE session_id=? AND run_id=?",
                       (occurrence["session_id"], occurrence["run_id"])).fetchone()
    if row is None:
        return {}
    saved = json.loads(row["record_json"])
    if saved.get("pin", {}).get("occurrence_id") != occurrence["occurrence_id"]:
        return {}
    saved.update(state=state, production_error=error)
    conn.execute("UPDATE workflow_runs SET record_json=?,updated_at=? WHERE workflow_run_id=?",
                 (canonical(saved), time.time(), row["workflow_run_id"]))
    conn.execute("UPDATE durable_monitor_intents SET state=? WHERE occurrence_id=? AND kind='workflow' AND state='authorized'",
                 (state, occurrence["occurrence_id"]))
    return {"workflow_run_id": saved["workflow_run_id"], "mission_id": saved["pin"]["mission_id"],
            "draft_only": True, "publication_state": "reconciliation_required", "outputs": [
                {"output_index": index, **{key: descriptor[key] for key in ("artifact_id", "version", "sha256", "size", "mime")},
                 **saved["outputs"][index], "review_method": "runtime.schedule.output.get"}
                for index, descriptor in enumerate(saved["output_descriptors"])]}
