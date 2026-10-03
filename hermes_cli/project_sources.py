"""Explicit project captures and reusable templates over the authoritative catalog.

Capture imports never fetch a URL or invent an extraction result. The original
is an immutable catalog version; filing and extraction attempts cannot erase it.
"""
from __future__ import annotations

import math
from pathlib import Path
import time
from urllib.parse import urlsplit
import uuid


class ProjectSourceError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def source_authority(context, db):
    """Use the bound profile's store and live project grants on every operation."""
    from agent.project_context import project_access
    from agent.result_artifacts import artifact_actor
    if Path(db.db_path).resolve().parent != Path(context.profile_home).resolve():
        raise ProjectSourceError("identity_mismatch", "Project sources require the owning profile store")
    return artifact_actor(context), project_access(context)


def _timestamp(value):
    value = time.time() if value is None else value
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= time.time() + 300:
        raise ProjectSourceError("invalid_source", "Acquisition time must be a finite observed UTC timestamp")
    return float(value)


def _source_url(value):
    if value is None:
        return None
    if not isinstance(value, str) or not 0 < len(value) <= 2048 or value != value.strip() or any(ord(char) < 32 for char in value):
        raise ProjectSourceError("invalid_source_url", "Source URL must be bounded metadata")
    try:
        parsed = urlsplit(value)
        valid = parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username and not parsed.password
        parsed.port
    except ValueError as exc:
        raise ProjectSourceError("invalid_source_url", "Source URL must be a credential-free HTTP URL") from exc
    if not valid:
        raise ProjectSourceError("invalid_source_url", "Source URL must be a credential-free HTTP URL")
    return value


def create_capture(context, db, *, project_id, original_ref, source_url=None, acquired_at=None,
                   annotation="", suggested_project_id=None, capture_id=None):
    actor, access = source_authority(context, db)
    return db.create_capture(actor, capture_id=capture_id or "capture_" + uuid.uuid4().hex,
        project_id=project_id, original_ref=original_ref, source_url=_source_url(source_url),
        acquired_at=_timestamp(acquired_at), annotation=annotation,
        suggested_project_id=suggested_project_id, access=access)


def get_capture(context, db, capture_id):
    actor, access = source_authority(context, db)
    return db.get_capture(capture_id, actor, access=access)


def list_captures(context, db, project_id, *, limit=100):
    actor, access = source_authority(context, db)
    return db.list_captures(project_id, actor, access=access, limit=limit)


def record_capture_extraction(context, db, capture_id, *, status, extracted_ref=None, failure_code=None):
    """Record a supplied extractor result, not a claim that this service ran one."""
    actor, access = source_authority(context, db)
    return db.record_capture_extraction(capture_id, actor, status=status, extracted_ref=extracted_ref,
                                        failure_code=failure_code, access=access)


def file_capture(context, db, capture_id, *, filed_project_id, expected_revision):
    actor, access = source_authority(context, db)
    return db.file_capture(capture_id, actor, filed_project_id=filed_project_id,
                           expected_revision=expected_revision, access=access)


def read_capture(context, db, capture_id, *, offset=0, limit=65536):
    from hermes_cli.artifact_store import read_artifact
    capture = get_capture(context, db, capture_id)
    original = capture["original_ref"]
    return read_artifact(context, db, capture["project_id"], original["artifact_id"],
                         version=original["version"], offset=offset, limit=limit)


def create_template(context, db, *, project_id, baseline_ref, structure, style, assets, slots,
                    exclusions, template_id=None, version=1, parent_version=None):
    """Only explicit reusable components are stored; no incidental text is copied."""
    actor, access = source_authority(context, db)
    from hermes_cli.template_application import template_digest
    row = db.create_template(actor, template_id=template_id or "template_" + uuid.uuid4().hex,
        version=version, project_id=project_id, baseline_ref=baseline_ref, structure=structure,
        style=style, assets=assets, slots=slots, exclusions=exclusions,
        parent_version=parent_version, access=access)
    return {**row, "sha256": template_digest(row)}


def get_template(context, db, template_id, *, version=None):
    actor, access = source_authority(context, db)
    from hermes_cli.template_application import template_digest
    row = db.get_template(template_id, actor, version=version, access=access)
    return {**row, "sha256": template_digest(row)}


def list_templates(context, db, project_id, *, limit=100):
    actor, access = source_authority(context, db)
    from hermes_cli.template_application import template_digest
    return [{**row, "sha256": template_digest(row)}
            for row in db.list_templates(project_id, actor, limit=limit, access=access)]


def propose_capture_duplicates(context, db, project_id, *, limit=100):
    """Digest matches propose review only; dates, annotations and originals stay distinct."""
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ProjectSourceError("invalid_source", "Duplicate review must be bounded to one hundred captures")
    actor, access = source_authority(context, db)
    captures = db.list_captures(project_id, actor, access=access, limit=min(limit + 1, 100))
    groups = {}
    for capture in captures[:limit]:
        ref = capture["original_ref"]
        artifact = db.read_artifact_version(ref["artifact_id"], ref["version"], actor, access=access)
        if artifact["project_id"] != capture["project_id"]:
            raise ProjectSourceError("identity_mismatch", "Capture source no longer belongs to its project")
        digest = artifact["descriptor"]["sha256"]
        groups.setdefault(digest, []).append(capture["capture_id"])
    return {"groups": [{"sha256": digest, "capture_ids": ids} for digest, ids in groups.items() if len(ids) > 1],
            "scanned": min(len(captures), limit), "truncated": len(captures) > limit or len(captures) == 100,
            "complete": False, "consolidation_performed": False}
