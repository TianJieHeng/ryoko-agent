# Canonical approved-example template application

The existing `artifact_templates` catalog is the saved-example authority. Its immutable pin is
`{store: "artifact_templates", template_id, version, sha256}`; the digest covers all saved reusable
fields and baseline/asset references. A later template version never changes an old pin or output.

`runtime.template.create/get/list` expose the digest. `runtime.template.preview` requires the exact
pin, project, literal `slot_values`, and optional `locked_sections`. Structure entries are explicitly
authored reusable Markdown fragments using `${slot.name}`. A bare label becomes a heading. Slots
must be declared, required slots supplied, supplied slots used. Expansion is literal/nonrecursive,
bounded to 64 KiB, and rejects excluded strings case-insensitively. Baseline bytes are validated and
retained as provenance but are never copied into the new topic.

Only `heading_level` (1–6) and `section_spacing` (`blank_line` or `rule`) are deterministic style
operations. Other style keys are reported as advisory; no semantic tone or visual fidelity claim is
made. Assets are authorized immutable provenance references (`assets_mode: lineage_only`), not
embedded media. Preview is inert plain text and does not publish or share anything.

`runtime.template.prepare` adds command/request identity and returns exact preview plus the existing
artifact approval proposal. `runtime.template.publish` revalidates the pin, content, live owner,
project grants and matching approval, then uses the canonical effect-backed artifact writer. Locks
are section digests inherited by subsequent edits. Cancel uses `runtime.artifact.cancel`; publication
is the local artifact only, not external sharing. No workflow is promoted by creating/applying a
template. Canonical workflow application must explicitly name the store and has no legacy-store
fallback. Legacy `workflow_templates` style-only pins are preserved for inspection, but execution
fails with `workflow_legacy_template_not_applicable` until an explicitly authored canonical template
pin is supplied; they no longer silently validate metadata and render unrelated step text.

Local fixture coverage is recorded in the worker validation receipt. Real provider tone generation,
embedded assets, DOCX/PDF/deck layout, and external publication are outside this Markdown slice.
