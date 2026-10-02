"""Typed project evidence and bounded resume context, never personal-memory recall.

Evidence records describe source authority and freshness. An assertion, score,
source label or historical approval never becomes permission or proof by itself.
"""
from __future__ import annotations

import time
import uuid

from hermes_cli.project_sources import ProjectSourceError, _timestamp, source_authority

_MAX_CONTEXT_BYTES = 256 * 1024


def create_evidence_anchor(context, db, *, project_id, kind, source_ref, source_version,
                           range_ref=None, captured_at=None, authority="source_claim", validity="unverified",
                           fresh_until=None, annotation="", anchor_id=None):
    actor, access = source_authority(context, db)
    return db.create_evidence_anchor(actor, anchor_id=anchor_id or "anchor_" + uuid.uuid4().hex,
        project_id=project_id, kind=kind, source_ref=source_ref, source_version=source_version,
        range_ref=range_ref, captured_at=_timestamp(captured_at), authority=authority,
        validity=validity, fresh_until=fresh_until, annotation=annotation, access=access)


def get_evidence_anchor(context, db, anchor_id):
    actor, access = source_authority(context, db)
    return db.get_evidence_anchor(anchor_id, actor, access=access)


def list_evidence_anchors(context, db, project_id, *, limit=100):
    actor, access = source_authority(context, db)
    return db.list_evidence_anchors(project_id, actor, access=access, limit=limit)


def _limit(value, maximum=100):
    if type(value) is not int or not 1 <= value <= maximum:
        raise ProjectSourceError("invalid_resume", "Resume limits must be positive bounded integers")
    return value


def _excerpt(value, maximum=2048):
    return value[:maximum], len(value) > maximum


def _blocker(kind, reference, code):
    return {"kind": kind, "reference": reference, "code": code}


def _resume_missions(context, db, access, project_id, references, limit):
    from agent.result_artifacts import artifact_actor
    actor, missions, blockers = artifact_actor(context), [], []
    for reference in references[:limit]:
        access.assert_access(project_id, actor, "read")
        sid, run_id = reference["session_id"], reference["run_id"]
        stored = db.get_session_model_config_value(sid, "agent_identity")
        if not isinstance(stored, dict) or any(stored.get(key) != value for key, value in actor.items()):
            blockers.append(_blocker("mission", run_id, "mission_scope_unavailable"))
            continue
        snapshot = db.read_runtime_snapshot(sid)
        if snapshot["state"].get("run_id") != run_id:
            blockers.append(_blocker("mission", run_id, "mission_reference_stale"))
            continue
        missions.append({"session_id": snapshot["session_id"], "run_id": run_id,
                         "revision": snapshot["revision"], "status": snapshot["state"]["status"]})
        if snapshot["state"]["status"] in {"blocked", "failed", "cancelled"}:
            blockers.append(_blocker("mission", run_id, "mission_" + snapshot["state"]["status"]))
        if snapshot["outstanding_requests"]:
            blockers.append(_blocker("mission", run_id, "outstanding_requests"))
        if snapshot["unresolved_effects"] or snapshot.get("unresolved_invocations"):
            blockers.append(_blocker("mission", run_id, "unresolved_outcomes"))
    return missions, blockers


def _resume_artifacts(db, actor, access, project_id, references, limit):
    artifacts, blockers = [], []
    for reference in references[:limit]:
        artifact_id = reference["artifact_id"]
        try:
            row = db.get_artifact_head(artifact_id, actor, access=access)
            if row["project_id"] != project_id:
                raise ProjectSourceError("identity_mismatch", "Artifact belongs to another project")
        except (ValueError, PermissionError):
            blockers.append(_blocker("artifact", artifact_id, "artifact_unavailable"))
            continue
        descriptor = row["descriptor"]
        artifacts.append({"artifact_id": artifact_id, "version": row["version"],
                          "sha256": descriptor["sha256"], "mime": descriptor["mime"], "size": descriptor["size"],
                          "filed_version": reference["version"], "derived_validity": row["derived_validity"]})
        if row["version"] != reference["version"]:
            blockers.append(_blocker("artifact", artifact_id, "canonical_filing_stale"))
        if row["derived_validity"] != "current":
            blockers.append(_blocker("artifact", artifact_id, "derivative_not_current"))
    return artifacts, blockers


def _resume_sources(db, actor, access, project_id, references, limit):
    captures, blockers = [], []
    for reference in references[:limit]:
        capture_id = reference["capture_id"]
        try:
            capture = db.get_capture(capture_id, actor, access=access)
            if capture["project_id"] != project_id and capture["filed_project_id"] != project_id:
                raise ProjectSourceError("identity_mismatch", "Capture belongs to another project")
        except (ValueError, PermissionError):
            blockers.append(_blocker("capture", capture_id, "capture_unavailable"))
            continue
        extraction = capture["extractions"][-1]["status"] if capture["extractions"] else "not_attempted"
        annotation, truncated = _excerpt(capture["annotation"])
        captures.append({"capture_id": capture_id, "original_ref": capture["original_ref"],
                         "acquired_at": capture["acquired_at"], "extraction_status": extraction,
                         "annotation": annotation, "annotation_truncated": truncated})
        if extraction in {"failed", "unavailable"}:
            blockers.append(_blocker("capture", capture_id, "extraction_" + extraction))
    return captures, blockers


def _resume_evidence(db, actor, access, project_id, limit):
    rows = db.list_evidence_anchors(project_id, actor, access=access, limit=min(limit + 1, 100))
    evidence, blockers = [], []
    now = time.time()
    for row in rows[:limit]:
        annotation, truncated = _excerpt(row["annotation"])
        item = {key: row[key] for key in ("anchor_id", "kind", "source_ref", "source_version", "range_ref",
                                         "captured_at", "authority", "validity", "effective_validity", "fresh_until")}
        item.update(annotation=annotation, annotation_truncated=truncated, execution_authority=False,
                    freshness="unspecified" if row["fresh_until"] is None else (
                        "stale" if row["fresh_until"] <= now else "within_declared_window"))
        evidence.append(item)
        if row["effective_validity"] != "current":
            blockers.append(_blocker("evidence", row["anchor_id"], "evidence_" + row["effective_validity"]))
    return evidence, blockers, len(rows) > limit or len(rows) == 100


def assemble_resume(context, db, project_id, *, mission_limit=8, artifact_limit=16,
                    source_limit=16, evidence_limit=32):
    """Join current authorized metadata with explicit bounds and no completion inference."""
    import json
    from agent.project_context import authorize_project
    limits = {"missions": _limit(mission_limit), "artifacts": _limit(artifact_limit),
              "sources": _limit(source_limit), "evidence": _limit(evidence_limit)}
    actor, access = source_authority(context, db)
    project = authorize_project(context, project_id, operation="read")
    missions, blockers = _resume_missions(context, db, access, project_id, project["active_mission_refs"], mission_limit)
    artifacts, artifact_blockers = _resume_artifacts(db, actor, access, project_id,
                                                   project["canonical_artifact_refs"], artifact_limit)
    sources, source_blockers = _resume_sources(db, actor, access, project_id, project["source_refs"], source_limit)
    evidence, evidence_blockers, evidence_truncated = _resume_evidence(db, actor, access, project_id, evidence_limit)
    blockers.extend(artifact_blockers + source_blockers + evidence_blockers)
    latest = authorize_project(context, project_id, operation="read")
    consistent = latest["revision"] == project["revision"]
    if not consistent:
        blockers.append(_blocker("project", project_id, "project_changed_during_read"))
    purpose, purpose_truncated = _excerpt(project["purpose"], 8192)
    result = {"project": {"project_id": project_id, "revision": project["revision"], "purpose": purpose,
                          "purpose_truncated": purpose_truncated},
              "missions": missions, "artifacts": artifacts, "sources": sources, "evidence": evidence,
              "blockers": blockers, "limits": limits, "complete": False, "project_revision_stable": consistent,
              "assembled_at": time.time(), "truncated": {
                  "missions": len(project["active_mission_refs"]) > mission_limit,
                  "artifacts": len(project["canonical_artifact_refs"]) > artifact_limit,
                  "sources": len(project["source_refs"]) > source_limit, "evidence": evidence_truncated}}
    if len(json.dumps(result, ensure_ascii=True).encode()) > _MAX_CONTEXT_BYTES:
        raise ProjectSourceError("resume_context_limit", "Authorized resume context exceeds its explicit byte bound")
    return result
