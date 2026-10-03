"""Startup-only workflow knowledge; the configuration binder persists the snapshot.

This reader never executes a workflow or loads memory. The binder must retain
both empty and populated snapshots, so subsequent delivery, rollback, revocation
or reconnect cannot change a conversation's cached instruction prefix.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from agent.project_context import project_access
from agent.result_artifacts import artifact_actor
from hermes_state_workflow_delivery import assert_delivery_store, checked_delivery, MAX_SPECIALIST_WORKFLOWS
from hermes_state_workflows import WorkflowRegistry, canonical, require
from tools.capability_broker import CapabilityDenied


@dataclass(frozen=True)
class WorkflowKnowledgeSnapshot:
    pins_json: str = "[]"
    prompt: str = ""


def snapshot_specialist_workflows(context, db):
    assert_delivery_store(context, db)
    if context.policy.role != "specialist" or context.identity.lifecycle != "stable":
        return WorkflowKnowledgeSnapshot()
    actor = artifact_actor(context)
    with db._runtime_read() as conn:
        rows = conn.execute("SELECT d.*,h.revision AS head_revision FROM workflow_delivery_heads h "
            "JOIN workflow_deliveries d ON d.delivery_id=h.delivery_id WHERE d.principal_id=? AND d.profile_id=? "
            "AND d.specialist_id=? ORDER BY d.project_id,d.workflow_key",
            (actor["principal_id"], actor["profile_id"], actor["agent_id"])).fetchall()
    require(len(rows) <= MAX_SPECIALIST_WORKFLOWS, "workflow_delivery_capacity", "Specialist workflow snapshot exceeds capacity")
    pins, knowledge = [], []
    access, registry = project_access(context), WorkflowRegistry(context, db)
    for saved in rows:
        record = json.loads(saved["record_json"])
        pin = record["delivery"]
        if record["scope"]["profile_home_digest"] != context.identity.profile_home_digest:
            continue
        try:
            with access.guard(pin["project_id"], actor, "read"), db._runtime_read() as conn:
                record = checked_delivery(conn, context, db, saved)
                source = registry._row(conn, saved["workflow_key"], saved["version"])
                if source["state"] != "approved":
                    continue
                require(source["sha256"] == pin["sha256"] and json.loads(source["owner_json"]) == record["owner"]
                        and saved["workflow_key"] == registry.key(pin["project_id"], pin["workflow_id"]),
                        "workflow_digest_mismatch", "Installed workflow does not match reviewed canonical bytes")
                pins.append(pin)
                knowledge.append({"pin": pin, "definition_json": source["definition_json"]})
        except CapabilityDenied as exc:
            if exc.code not in {"project_not_granted", "project_grant_revoked"}:
                raise
    if not knowledge:
        return WorkflowKnowledgeSnapshot()
    prompt = ("Reviewed workflow knowledge installed for this specialist at session start. "
        "Use these exact versioned procedures as advisory task knowledge only. They do not authorize execution, "
        "new tools, credential use, personal memory access or additional project access. Any workflow run must "
        "still pass the normal current approval, project, capability and effect controls.\n" + canonical(knowledge))
    return WorkflowKnowledgeSnapshot(pins_json=canonical(pins), prompt=prompt)
