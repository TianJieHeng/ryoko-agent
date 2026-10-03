"""Apply exact approved-example templates without copying their example bodies.

The existing artifact_templates catalog is authoritative. A template's explicit
structure is Markdown with declared ${slot.name} placeholders; bare labels are
headings. Inputs are literal, never recursive executable expressions.
"""
from __future__ import annotations

import hashlib
import json
import re

from agent.result_artifacts import artifact_actor, read_project_artifact
from agent.project_context import project_access
from hermes_state_runtime import RuntimeStoreError

_TEMPLATE_FIELDS = ("template_id", "version", "project_id", "baseline_ref", "structure", "style",
                    "assets", "slots", "exclusions", "parent_version")
_SLOT = re.compile(r"\$\{slot\.([A-Za-z_][A-Za-z0-9_-]{0,63})\}")
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_-]{0,63}")
MAX_RENDER_BYTES = 65536


def require(condition, code, message):
    if not condition:
        raise RuntimeStoreError(code, message)


def template_digest(row):
    material = {key: row[key] for key in _TEMPLATE_FIELDS}
    return hashlib.sha256(json.dumps(material, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def pin_template(row):
    return {"store": "artifact_templates", "template_id": row["template_id"],
            "version": row["version"], "sha256": template_digest(row)}


def resolve_template(context, db, project_id, ref):
    from hermes_cli.project_sources import get_template
    require(isinstance(ref, dict) and set(ref) == {"store", "template_id", "version", "sha256"}
            and ref["store"] == "artifact_templates", "template_reference_invalid", "Exact canonical template pin required")
    row = get_template(context, db, ref["template_id"], version=ref["version"])
    require(row["project_id"] == project_id, "identity_mismatch", "Template belongs to another project")
    require(pin_template(row) == ref, "template_digest_mismatch", "Template pin differs from immutable saved bytes")
    actor, access = artifact_actor(context), project_access(context)
    for source in [row["baseline_ref"], *row["assets"]]:
        version = db.read_artifact_version(source["artifact_id"], source["version"], actor, access=access)
        require(version["project_id"] == project_id and version["metadata"]["approval_status"] == "approved"
                and version["metadata"]["validation"]["status"] == "passed",
                "template_source_unapproved", "Template sources must be approved validated versions in this project")
        read_project_artifact(context, db, project_id, source["artifact_id"], source["version"])
    return {key: row[key] for key in _TEMPLATE_FIELDS}


def render_template(row, slot_values):
    """Finite renderer; semantic style prose is disclosed rather than claimed applied."""
    require(type(slot_values) is dict and len(slot_values) <= 64, "template_slots_invalid", "Bounded explicit slot values required")
    names = [slot["name"] for slot in row["slots"]]
    require(all(_NAME.fullmatch(name) for name in names) and len(set(names)) == len(names),
            "template_slots_invalid", "Template slot names must be unique binding identifiers")
    require(set(slot_values) <= set(names) and all(type(value) is str for value in slot_values.values()),
            "template_slots_invalid", "Only declared literal string slot values are supported")
    require(all(not slot["required"] or bool(slot_values.get(slot["name"], "").strip()) for slot in row["slots"]),
            "template_slot_missing", "Required template slot is missing")
    require(sum(len(value.encode()) for value in slot_values.values()) <= MAX_RENDER_BYTES,
            "template_output_bound", "Template inputs exceed byte bound")
    style = row["style"]
    level = style.get("heading_level", "2")
    spacing = style.get("section_spacing", "blank_line")
    require(level in {"1", "2", "3", "4", "5", "6"} and spacing in {"blank_line", "rule"},
            "template_style_invalid", "Supported formatting is heading_level 1–6 and section_spacing blank_line or rule")
    used, fragments, total = set(), [], 0
    require(bool(row["structure"]), "template_structure_invalid", "Reusable Markdown structure is empty")
    for fragment in row["structure"]:
        require(isinstance(fragment, str) and bool(fragment.strip()), "template_structure_invalid", "Structure fragments must be nonempty")
        parts, start, fragment_bytes = [], 0, 0
        def append(part):
            nonlocal fragment_bytes
            fragment_bytes += len(part.encode())
            require(total + fragment_bytes <= MAX_RENDER_BYTES, "template_output_bound", "Template output exceeds byte bound")
            parts.append(part)
        for match in _SLOT.finditer(fragment):
            require(match[1] in names, "template_slot_unknown", "Structure references an undeclared slot")
            used.add(match[1])
            append(fragment[start:match.start()])
            append(slot_values.get(match[1], ""))
            start = match.end()
        append(fragment[start:])
        # Reject malformed template expressions in the authored structure, not
        # strings supplied by the user. Slot values stay literal and nonrecursive.
        require("${" not in _SLOT.sub("", fragment), "template_structure_invalid", "Unsupported template expression")
        content = "".join(parts).strip()
        if "\n" not in content and not content.startswith("#"):
            content = "#" * int(level) + " " + content
        total += len(content.encode())
        require(total <= MAX_RENDER_BYTES, "template_output_bound", "Template output exceeds byte bound")
        fragments.append(content)
    require(set(slot_values) <= used, "template_slot_unused", "Supplied slot values must appear in the explicit structure")
    separator = "\n\n---\n\n" if spacing == "rule" else "\n\n"
    content = separator.join(fragments) + "\n"
    require(len(content.encode()) <= MAX_RENDER_BYTES, "template_output_bound", "Template output exceeds byte bound")
    require(all(not excluded or excluded.casefold() not in content.casefold() for excluded in row["exclusions"]),
            "template_exclusion_violation", "Output contains an explicitly excluded incidental value")
    return content, sorted(set(style) - {"heading_level", "section_spacing"})


def preview_template(context, db, *, project_id, template_ref, slot_values, locked_sections=()):
    from hermes_cli.artifact_store import _locks
    from hermes_cli.artifact_formats import validate_artifact
    row = resolve_template(context, db, project_id, template_ref)
    content, advisory = render_template(row, slot_values)
    validation = validate_artifact(content.encode(), "text/markdown")
    locks = _locks(content, locked_sections, [])
    return {"template_ref": pin_template(row), "project_id": project_id, "content": content,
            "sha256": validation["sha256"], "size": len(content.encode()), "mime": "text/markdown",
            "locked_sections": locks, "advisory_style_keys": advisory,
            "applied_style_keys": sorted(set(row["style"]) & {"heading_level", "section_spacing"}),
            "assets_mode": "lineage_only",
            "baseline_copied": False, "assets": row["assets"], "exclusions_checked": True,
            "publication_state": "preview_only", "preview_mode": "plain_text"}


def prepare_template(run, *, project_id, template_ref, slot_values, locked_sections, request_id, approval_id=None):
    from hermes_cli.artifact_store import prepare_markdown
    preview = preview_template(run.context, run.db, project_id=project_id, template_ref=template_ref,
                               slot_values=slot_values, locked_sections=locked_sections)
    row = resolve_template(run.context, run.db, project_id, template_ref)
    source = f'template:{row["template_id"]}:{row["version"]}:{template_ref["sha256"]}'
    proposal = prepare_markdown(run, project_id=project_id, request_id=request_id, content=preview["content"],
        locked_sections=locked_sections, derived_from=[row["baseline_ref"], *row["assets"]],
        provenance={"kind": "template", "source_ref": source}, approval_id=approval_id)
    return proposal, preview
