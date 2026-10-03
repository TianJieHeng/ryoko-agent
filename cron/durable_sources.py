"""Finite local retained-source monitors and immutable review reports.

Reading an imported artifact never certifies access to its original live service.
No code, shell, HTTP, provider or MCP adapter is selected by these records.
"""
from __future__ import annotations

import json
import math
import operator
import unicodedata

from agent.project_context import project_access
from agent.result_artifacts import artifact_actor
from cron.durable_contract import canonical, digest, require
from hermes_cli.domain_media import _read


def _projection(raw, mime, predicate):
    text = raw.decode("utf-8-sig")
    if predicate["kind"] == "normalized_text":
        require(mime in {"text/plain", "text/markdown", "application/json", "text/csv"}, "Text source required")
        import hashlib
        return hashlib.sha256(" ".join(unicodedata.normalize("NFKC", text).split()).encode()).hexdigest()
    require(mime == "application/json", "Structured predicate requires JSON")
    value = json.loads(text)
    require(isinstance(value, dict), "Structured source must be an object")
    if predicate["kind"] == "json_fields":
        require(all(field in value for field in predicate["fields"]), "Required source field is missing")
        result = {field: value[field] for field in predicate["fields"]}
        canonical(result)
        return digest(result)
    field = predicate["field"]
    number = value.get(field)
    require(type(number) in (int, float) and math.isfinite(number), "Threshold source must be a finite number")
    compare = {"gt": operator.gt, "gte": operator.ge, "lt": operator.lt, "lte": operator.le, "eq": operator.eq}
    return compare[predicate["operator"]](number, predicate["value"])


def observe_sources(context, db, definition, check):
    specification = definition["specification"]
    values, refs, total = {}, [], 0
    for artifact_id in specification["source_set"]:
        check()
        head = db.get_artifact_head(artifact_id, artifact_actor(context), access=project_access(context))
        require(head["project_id"] == definition["project_id"], "Source project differs", "identity_mismatch")
        total += head["descriptor"]["size"]
        require(total <= definition["budget"]["max_bytes"], "Monitor source budget exceeded", "schedule_budget_exhausted")
        ref = {"artifact_id": artifact_id, "version": head["version"], "sha256": head["descriptor"]["sha256"]}
        raw, mime = _read(context, db, definition["project_id"], ref)
        values[artifact_id] = _projection(raw, mime, specification["predicate"])
        refs.append(ref)
    check()
    from agent.decisions.integration import observe_core
    observe_core("DP11", {"evidence": {"source_count": len(refs), "bytes_read": total,
                                      "semantic_content_available": False},
                          "source_digest": digest(refs)})
    return {"projection": values, "source_refs": refs, "bytes_read": total,
            "source_scope": "retained_local_artifacts", "live_connection_verified": False}


def review_sources(context, db, definition, check, *, specification=None):
    specification = definition["specification"] if specification is None else specification
    refs, total, findings = specification["source_refs"], 0, []
    for ref in refs:
        check()
        row = db.read_artifact_version(ref["artifact_id"], ref["version"], artifact_actor(context), access=project_access(context))
        total += row["descriptor"]["size"]
        require(total <= definition["budget"]["max_bytes"], "Review byte budget exceeded", "schedule_budget_exhausted")
        raw, mime = _read(context, db, definition["project_id"], ref)
        findings.append({"source_ref": ref, "mime": mime, "bytes": len(raw), "immutable_bytes_checked": True})
    workflow = specification["workflow_ref"]
    if workflow is not None:
        from hermes_cli.workflows import resolve_executable
        with project_access(context).guard(definition["project_id"], artifact_actor(context), "read"):
            resolve_executable(context, db, project_id=definition["project_id"], **workflow)
    check()
    return {"purpose": specification["purpose"], "source_refs": refs, "workflow_ref": workflow,
            "findings": findings, "review_digest": digest(findings), "bytes_read": total,
            "semantic_review": "human_review_required", "memory_ingested": False, "skill_promoted": False,
            "owner_agent_id": context.identity.agent_id, "source_scope": "retained_local_artifacts"}
