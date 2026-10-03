"""Exact, draft-only local scheduled workflow inputs and grant targets."""
from __future__ import annotations

import re

from cron.durable_contract import canonical, digest, exact, identifier, immutable_ref, require


def validate_workflow_draft(value):
    exact(value, "workflow_ref parameters source_bindings destination")
    ref = value["workflow_ref"]
    exact(ref, "workflow_id version sha256")
    immutable_ref({"artifact_id": ref["workflow_id"], "version": ref["version"], "sha256": ref["sha256"]})
    require(isinstance(value["parameters"], dict), "Exact fixed workflow parameters required")
    canonical(value["parameters"])
    bindings = value["source_bindings"]
    require(isinstance(bindings, list) and len(bindings) <= 8, "At most eight local text source bindings are supported")
    names = set()
    for binding in bindings:
        exact(binding, "parameter artifact_id")
        name = binding["parameter"]
        require(isinstance(name, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,63}", name), "Exact source parameter required")
        identifier(binding["artifact_id"])
        require(name not in names and name not in value["parameters"], "Source parameters must be unique and separate from fixed inputs")
        names.add(name)
    exact(value["destination"], "kind project_id")
    require(value["destination"]["kind"] == "project_artifact_drafts", "Only local drafts for human review are supported")
    identifier(value["destination"]["project_id"])


def grant_target(definition):
    action = (definition["specification"] if definition["kind"] == "workflow_draft"
              else definition["specification"].get("condition_action"))
    require(action is not None and definition["kind"] in {"monitor", "workflow_draft"}, "No exact local action is declared")
    return digest({"project_id": definition["project_id"], "action": action, "schedule_sha256": digest(definition)})


def resolve_workflow(context, db, definition):
    from hermes_cli.workflows import resolve_executable
    specification = definition["specification"]
    validate_workflow_draft(specification)
    require(specification["destination"]["project_id"] == definition["project_id"], "Draft destination differs from the granted project", "identity_mismatch")
    workflow = resolve_executable(context, db, project_id=definition["project_id"], **specification["workflow_ref"])
    record = workflow.to_record()
    require(all(step["kind"] == "render_markdown" for step in record["steps"]),
            "Scheduled production currently supports bounded Markdown rendering only", "schedule_adapter_unsupported")
    properties = record["input_schema"]["properties"]
    for binding in specification["source_bindings"]:
        require(properties.get(binding["parameter"], {}).get("type") == "string",
                "Local source bindings require declared string parameters")
    return workflow


def available_grant(conn, row, definition, now):
    grant = conn.execute("SELECT * FROM durable_condition_grants WHERE schedule_key=? AND version=? "
        "AND state='active' AND remaining>0 AND expires_at>? AND target_digest=? ORDER BY created_at DESC,grant_id LIMIT 1",
        (row["schedule_key"], row["version"], now, grant_target(definition))).fetchone()
    require(grant is not None, "Exact unexpired workflow production grant required", "schedule_grant_required")
    return grant


def admit_workflow_grant(conn, row, definition, occurrence, now):
    """Debit once in the same transaction as occurrence/command acceptance."""
    grant = available_grant(conn, row, definition, now)
    conn.execute("UPDATE durable_condition_grants SET remaining=remaining-1 WHERE grant_id=?", (grant["grant_id"],))
    conn.execute("INSERT INTO durable_monitor_intents VALUES(?,?,?,'authorized',?,?)",
        ("workflow_" + occurrence, occurrence, "workflow", canonical({"grant_id": grant["grant_id"],
         "target_digest": grant["target_digest"], "draft_only": True, "external_actions": False}), now))
