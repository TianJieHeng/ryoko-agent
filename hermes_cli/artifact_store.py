"""Approved Markdown revisions over the existing durable artifact catalog.

The blob, immutable version, and artifact-head CAS have distinct receipts.
Project filing is separate: this service never claims a cross-database commit.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass, field

from agent.result_artifacts import (
    MAX_ARTIFACT_BYTES, ArtifactConflict, artifact_actor, descriptor_digest,
    read_project_artifact, read_result_artifact, result_artifact_descriptor,
)
from hermes_state_runtime import RuntimeStoreError

MARKDOWN_MIME = "text/markdown"
MAX_CHUNK_BYTES = 65536
_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")
_FENCE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})")


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def _text(content):
    if not isinstance(content, str):
        raise ArtifactConflict("Markdown content must be Unicode text")
    data = content.encode("utf-8")
    if len(data) > MAX_ARTIFACT_BYTES or "\0" in content:
        raise ArtifactConflict("Markdown content exceeds the supported text bound")
    return data


def _sections(content):
    """Exact ATX heading sections outside code fences; ambiguous anchors refuse."""
    starts, offset, fence = [], 0, None
    for line in content.splitlines(keepends=True):
        stripped = line.rstrip("\r\n")
        marker = _FENCE.match(stripped)
        if marker:
            token = marker[1]
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
        elif fence is None:
            heading = _HEADING.match(stripped)
            if heading:
                starts.append((heading[2].strip(), len(heading[1]), offset))
        offset += len(line)
    sections = {}
    for index, (anchor, level, start) in enumerate(starts):
        end = next((position for _anchor, following_level, position in starts[index + 1:]
                    if following_level <= level), len(content))
        if anchor in sections:
            sections[anchor] = None
        else:
            sections[anchor] = (start, end, content[start:end])
    return sections


def _section(sections, anchor):
    if not isinstance(anchor, str) or not anchor or len(anchor) > 256 or sections.get(anchor) is None:
        raise ArtifactConflict("Locked or edited section anchor must identify one existing heading")
    return sections[anchor]


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _locks(content, requested, inherited):
    if not isinstance(requested, (list, tuple)) or len(requested) > 100:
        raise ArtifactConflict("Locked section list must be bounded")
    sections = _sections(content)
    records = {item["anchor"]: dict(item) for item in inherited}
    for anchor, record in records.items():
        if _sha(_section(sections, anchor)[2]) != record["sha256"]:
            raise ArtifactConflict("Revision changes a locked section")
    for anchor in requested:
        records[anchor] = {"anchor": anchor, "sha256": _sha(_section(sections, anchor)[2])}
    return [records[key] for key in sorted(records)]


def _refs(previous, added):
    if not isinstance(added, (list, tuple)) or len(added) > 100:
        raise ArtifactConflict("Source reference list must be bounded")
    return list(dict.fromkeys([*previous, *added]))


def _version_row(context, db, project_id, artifact_id, version):
    from agent.project_context import project_access
    actor, access = artifact_actor(context), project_access(context)
    row = db.read_artifact_version(artifact_id, version, actor, access=access)
    if row["project_id"] != project_id:
        raise ArtifactConflict("Artifact belongs to another project")
    return row


@dataclass(frozen=True)
class ArtifactProposal:
    request_id: str
    scope_json: str = field(repr=False)
    content_bytes: bytes = field(repr=False)
    approval_id: str
    approval_digest: str
    expires_at: float

    @property
    def scope(self):
        return json.loads(self.scope_json)

    def public_record(self):
        scope = self.scope
        return {"request_id": self.request_id, "project_id": scope["project_id"],
                **{key: scope["descriptor"][key] for key in ("artifact_id", "version", "sha256", "size", "mime")},
                "parent_version": scope["metadata"]["parent_version"],
                "expected_head_version": scope["expected_head_version"],
                "action_digest": _action(scope).digest, "approval_id": self.approval_id,
                "approval_digest": self.approval_digest, "expires_at": self.expires_at}


def _action(scope):
    from tools.capability_broker import project_artifact_action
    return project_artifact_action(scope)


def prepare_markdown(run, *, project_id, request_id, content, artifact_id=None,
                     parent_version=None, expected_head_version=None, locked_sections=(),
                     source_refs=(), derived_from=(), provenance=None, merge_from=None,
                     approval_id=None):
    """Reserve a version and exact durable approval; this never publishes bytes."""
    from agent.artifact_commands import assert_artifact_dispatch
    from agent.project_context import authorize_project, project_access
    from tools.capability_broker import preview_action, recover_approval_preview
    assert_artifact_dispatch(run)
    project = authorize_project(run.context, project_id, "write")
    actor, access, data = artifact_actor(run.context), project_access(run.context), _text(content)
    if not isinstance(request_id, str) or not 0 < len(request_id) <= 256:
        raise ArtifactConflict("A stable bounded request identity is required")
    identity = descriptor_digest({"actor": actor, "run_id": run.run_id, "project_id": project_id,
                                  "request_id": request_id})
    artifact_id = artifact_id or "artifact_" + identity
    approved_id = "artifact_approval_" + identity
    if approval_id is not None and approval_id != approved_id:
        raise ArtifactConflict("Approval does not belong to this artifact request")
    previous, base_sha = {}, "none"
    if parent_version is not None:
        row = _version_row(run.context, run.db, project_id, artifact_id, parent_version)
        previous, base_sha = row["metadata"], row["descriptor"]["sha256"]
        # Revalidate baseline bytes, not merely historical metadata.
        read_project_artifact(run.context, run.db, project_id, artifact_id, parent_version)
        if expected_head_version is None:
            expected_head_version = parent_version
    elif expected_head_version is not None:
        raise ArtifactConflict("A revision requires its exact immutable parent version")
    from agent.artifact_commands import ArtifactControlRun
    origin = "source" if isinstance(run, ArtifactControlRun) else "generated"
    lineage = {"kind": "revision" if parent_version is not None else origin} if provenance is None else dict(provenance)
    if merge_from is not None:
        lineage = {"kind": "revision", "source_ref": f"artifact:{artifact_id}:{merge_from}"}
    metadata = {"parent_version": parent_version, "branch_of": None,
        "derived_from": list(derived_from or previous.get("derived_from", [])),
        "locked_sections": _locks(content, locked_sections, previous.get("locked_sections", [])),
        "source_refs": _refs(previous.get("source_refs", []), source_refs), "provenance": lineage,
        "validation": {"status": "passed", "receipt_ref": "markdown-utf8-sha256:" + _sha(content)},
        "approval_status": "approved", "approval_id": approved_id}
    reservation = run.db.reserve_artifact_version(run.session_id, actor, holder=run.holder,
        generation=run.generation, run_id=run.run_id, command_id=run.command_id,
        project_id=project_id, artifact_id=artifact_id, request_id=request_id,
        parent_version=parent_version, content_sha256=_sha(content), size=len(data), mime=MARKDOWN_MIME,
        metadata=metadata, expected_head_version=expected_head_version, access=access)
    descriptor = result_artifact_descriptor(run.context, run.run_id, data, artifact_id,
                                            reservation["version"], MARKDOWN_MIME)
    scope = {"project_id": project_id, "request_id": request_id, "descriptor": descriptor,
             "metadata": metadata, "expected_head_version": expected_head_version,
             "project_revision": project["revision"], "base_sha256": base_sha}
    action = _action(scope)
    try:
        preview = recover_approval_preview(approved_id, action)
    except RuntimeStoreError as error:
        if error.code != "approval_not_found":
            raise
        expires = min(reservation["created_at"] + 300, getattr(run, "deadline_at", float("inf")))
        preview = preview_action(action, approval_id=approved_id, expires_at=expires)
    return ArtifactProposal(request_id, _json(scope), data, approved_id,
                            preview.approval_digest, preview.expires_at)


def _safe_version(row):
    metadata, descriptor = row["metadata"], row["descriptor"]
    disposition = row["disposition"]
    if disposition in {"committed", "head"}:
        disposition = "canonical"
    return {"project_id": row["project_id"],
        **{key: descriptor[key] for key in ("artifact_id", "version", "sha256", "size", "mime")},
        "parent_version": metadata["parent_version"], "disposition": disposition,
        "head_version": row.get("head_version"), "validation_status": metadata["validation"]["status"],
        "approval_status": metadata["approval_status"]}


def publish_markdown(run, proposal):
    """Consume one exact approval with effect admission, then commit head or branch."""
    from agent.artifact_commands import assert_artifact_dispatch
    from agent.project_context import project_access
    from tools.capability_broker import invoke_effect_dispatch, recover_approval_preview
    assert_artifact_dispatch(run)
    if not isinstance(proposal, ArtifactProposal):
        raise ArtifactConflict("An exact prepared Markdown proposal is required")
    scope, actor = proposal.scope, artifact_actor(run.context)
    if proposal.request_id != scope["request_id"]:
        raise ArtifactConflict("Artifact proposal request identity changed")
    action = _action(scope)
    preview = recover_approval_preview(proposal.approval_id, action)
    if preview.approval_digest != proposal.approval_digest:
        raise ArtifactConflict("Artifact proposal approval digest changed")
    identity = descriptor_digest({"actor": actor, "run_id": run.run_id, "request_id": proposal.request_id})
    invoke_effect_dispatch("project_artifact_publish", run=run, input_ref=scope["descriptor"],
        payload=proposal.content_bytes, operation_id="project-artifact-" + identity,
        intent_key="project-artifact-" + identity, action=action, approval=preview)
    assert_artifact_dispatch(run)
    row = run.db.register_artifact_version(run.session_id, actor, holder=run.holder,
        generation=run.generation, run_id=run.run_id, command_id=run.command_id,
        descriptor=scope["descriptor"], project_id=scope["project_id"], metadata=scope["metadata"],
        expected_head_version=scope["expected_head_version"], publish_head=True, access=project_access(run.context))
    return _safe_version(row)


def _chunk(project_id, descriptor, data, offset, limit):
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= MAX_CHUNK_BYTES:
        raise ArtifactConflict("Invalid artifact chunk bounds")
    if offset > len(data):
        raise ArtifactConflict("Artifact offset exceeds its complete size")
    chunk = data[offset:offset + limit]
    return {"project_id": project_id,
            **{key: descriptor[key] for key in ("artifact_id", "version", "sha256", "size", "mime")},
            "offset": offset, "data_base64": base64.b64encode(chunk).decode("ascii"),
            "next_offset": offset + len(chunk), "eof": offset + len(chunk) == len(data),
            "preview_mode": "plain_text"}


def read_artifact(context, db, project_id, artifact_id, version=None, offset=0, limit=MAX_CHUNK_BYTES):
    """Safe plain-text chunks; the complete immutable file is digest-checked each time."""
    from agent.project_context import project_access
    if version is None:
        head = db.get_artifact_head(artifact_id, artifact_actor(context), access=project_access(context))
        if head is None:
            raise ArtifactConflict("Artifact has no canonical version")
        version = head["version"]
    row = _version_row(context, db, project_id, artifact_id, version)
    descriptor = row["descriptor"]
    data = read_project_artifact(context, db, project_id, artifact_id, version)
    return _chunk(project_id, descriptor, data, offset, limit)


def read_artifact_recovery(context, db, project_id, effect_id, offset=0, limit=MAX_CHUNK_BYTES):
    """Recover proven bytes without committing a version, head, or delivery claim."""
    from agent.project_context import project_access
    from tools.capability_broker import require_live_policy
    if require_live_policy(require_run=False) != context:
        raise ArtifactConflict("Recovery requires its live requesting context")
    actor, access = artifact_actor(context), project_access(context)
    effect = db.get_effect(effect_id, actor)
    if effect["operation_type"] != "project_artifact_publish" or effect["state"] != "confirmed":
        raise ArtifactConflict("Only an exactly confirmed project publication is recoverable")
    descriptor = effect["input_ref"]
    reservation = db.read_artifact_reservation(descriptor["artifact_id"], descriptor["version"], actor, access=access)
    if (reservation["project_id"] != project_id or reservation["run_id"] != effect["run_id"]
            or reservation["session_id"] != effect["session_id"]
            or descriptor["producing_run"] != effect["run_id"]
            or descriptor_digest(descriptor) != effect["input_digest"]
            or effect["target_ref"] != f"artifact:{descriptor['artifact_id']}:{descriptor['version']}"):
        raise ArtifactConflict("Recovery does not match the exact reserved artifact scope")
    with access.guard(project_id, actor, "read"):
        data = read_result_artifact(context, descriptor)
    state = "committed" if reservation["publication_state"] == "committed" else "published_uncommitted"
    return {**_chunk(project_id, descriptor, data, offset, limit), "publication_state": state}


def _apply_edits(content, edits):
    if not isinstance(edits, (list, tuple)) or not 0 < len(edits) <= 100:
        raise ArtifactConflict("A bounded nonempty exact section edit list is required")
    sections, replacements = _sections(content), []
    for edit in edits:
        if not isinstance(edit, dict) or set(edit) != {"anchor", "expected_sha256", "replacement"}:
            raise ArtifactConflict("Each section edit needs its anchor, old digest and exact replacement")
        start, end, original = _section(sections, edit["anchor"])
        if _sha(original) != edit["expected_sha256"]:
            raise ArtifactConflict("Section changed since the proposed edit")
        _text(edit["replacement"])
        replacements.append((start, end, edit["replacement"]))
    ordered = sorted(replacements)
    if any(left[1] > right[0] for left, right in zip(ordered, ordered[1:])):
        raise ArtifactConflict("Overlapping section edits require a new explicit proposal")
    for start, end, replacement in reversed(ordered):
        content = content[:start] + replacement + content[end:]
    return content


def prepare_markdown_edit(run, *, project_id, artifact_id, parent_version, request_id, edits,
                          expected_head_version=None, approval_id=None):
    baseline = read_project_artifact(run.context, run.db, project_id, artifact_id, parent_version).decode("utf-8")
    content = _apply_edits(baseline, edits)
    return prepare_markdown(run, project_id=project_id, request_id=request_id, content=content,
        artifact_id=artifact_id, parent_version=parent_version, expected_head_version=expected_head_version,
        approval_id=approval_id)


def prepare_markdown_merge(run, *, project_id, artifact_id, branch_version, current_head_version,
                           request_id, approved_anchors, approval_id=None):
    """Three-way merge only explicitly selected, unchanged-at-head source sections."""
    branch = _version_row(run.context, run.db, project_id, artifact_id, branch_version)
    base_version = branch["metadata"]["parent_version"]
    if base_version is None or not isinstance(approved_anchors, (list, tuple)) or not approved_anchors:
        raise ArtifactConflict("A merge requires a branch baseline and explicit section selection")
    texts = {version: read_project_artifact(run.context, run.db, project_id, artifact_id, version).decode("utf-8")
             for version in (base_version, branch_version, current_head_version)}
    base, selected, current = (_sections(texts[version]) for version in (base_version, branch_version, current_head_version))
    edits = []
    for anchor in approved_anchors:
        old, changed, live = (_section(sections, anchor)[2] for sections in (base, selected, current))
        if old == changed or old != live:
            raise ArtifactConflict("Selected merge delta is unchanged or conflicts with the current head")
        edits.append({"anchor": anchor, "expected_sha256": _sha(live), "replacement": changed})
    content = _apply_edits(texts[current_head_version], edits)
    return prepare_markdown(run, project_id=project_id, request_id=request_id, content=content,
        artifact_id=artifact_id, parent_version=current_head_version, expected_head_version=current_head_version,
        merge_from=branch_version, approval_id=approval_id)
