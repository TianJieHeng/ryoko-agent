import type { ArtifactPublishResult, TemplatePrepareParams, TemplatePrepareResult, TemplatePreviewParams, TemplatePreviewResult, TemplateRecord } from './gateway-contract.generated.js'
import { markdownDigest } from './runtime-markdown.js'
import { controlDigest, controlId, controlInteger, RuntimeInputError } from './runtime-research.js'

export function templateApplicationInput(sessionId: string, projectId: string, template: TemplateRecord, values: Record<string, string>, locks: string[]): TemplatePreviewParams {
  controlId(sessionId); controlId(projectId); controlId(template.template_id); controlInteger(template.version, 1); controlDigest(template.sha256)

  if (template.project_id !== projectId) { throw new RuntimeInputError('The selected template belongs to a different project') }
  const names = template.slots.map(slot => slot.name)

  if (names.length > 64 || names.some(name => !/^[A-Za-z_][A-Za-z0-9_-]{0,63}$/u.test(name)) || new Set(names).size !== names.length || Object.keys(values).some(name => !names.includes(name))) { throw new RuntimeInputError('Use only unique declared template slots') }

  if (template.slots.some(slot => slot.required && (!Object.hasOwn(values, slot.name) || typeof values[slot.name] !== 'string' || !values[slot.name].trim()))) { throw new RuntimeInputError('Supply every required slot') }

  if (Object.values(values).some(value => typeof value !== 'string') || new TextEncoder().encode(JSON.stringify(values)).length > 65536) { throw new RuntimeInputError('Slot values must be bounded literal text') }

  if (new Set(locks).size !== locks.length || locks.some(lock => !lock.trim())) { throw new RuntimeInputError('Choose distinct named sections to lock') }

  return { session_id: sessionId, schema_version: 1, project_id: projectId,
    template_ref: { store: 'artifact_templates', template_id: template.template_id, version: template.version, sha256: template.sha256 },
    slot_values: { ...values }, locked_sections: [...locks] }
}

/** Validate full returned bytes before offering an approval, never reconstruct a preview from a baseline. */
export async function verifyTemplatePreview(result: TemplatePreviewResult, input: TemplatePreviewParams): Promise<void> {
  const ref = result.template_ref, expected = input.template_ref

  if (ref.store !== 'artifact_templates' || ref.template_id !== expected.template_id || ref.version !== expected.version || ref.sha256 !== expected.sha256 || result.project_id !== input.project_id) { throw new RuntimeInputError('Preview does not match the exact template pin and project') }

  if (result.mime !== 'text/markdown' || result.preview_mode !== 'plain_text' || result.publication_state !== 'preview_only' || result.baseline_copied !== false || result.assets_mode !== 'lineage_only' || result.exclusions_checked !== true) { throw new RuntimeInputError('Unsupported template preview semantics') }

  if (typeof result.content !== 'string' || new TextEncoder().encode(result.content).length !== result.size || result.size > 65536 || await markdownDigest(result.content) !== result.sha256) { throw new RuntimeInputError('Incomplete or mismatched template preview bytes') }

  const anchors = result.locked_sections.map(lock => {
    controlDigest(lock.sha256)

    return lock.anchor
  })

  if (new Set(anchors).size !== anchors.length || anchors.length !== (input.locked_sections ?? []).length || anchors.some(anchor => !input.locked_sections?.includes(anchor))) { throw new RuntimeInputError('Prepared section locks differ from the requested locks') }

  if (result.applied_style_keys.some(key => !['heading_level', 'section_spacing'].includes(key))) { throw new RuntimeInputError('Unsupported deterministic style claim') }
}

export async function verifyTemplatePrepared(result: TemplatePrepareResult, input: TemplatePrepareParams): Promise<void> {
  await verifyTemplatePreview(result.preview, input)
  const p = result.proposal
  controlId(p.artifact_id); controlId(p.approval_id); controlDigest(p.approval_digest); controlDigest(p.action_digest); controlInteger(p.version, 1)

  if (p.request_id !== input.request_id || p.project_id !== input.project_id || p.sha256 !== result.preview.sha256 || p.size !== result.preview.size || p.mime !== result.preview.mime || !Number.isFinite(p.expires_at) || p.expires_at * 1000 <= Date.now()) { throw new RuntimeInputError('Approval proposal does not bind the exact complete preview or has expired') }
}

export function verifyTemplatePublished(result: ArtifactPublishResult, prepared: TemplatePrepareResult): void {
  const p = prepared.proposal

  if (result.project_id !== p.project_id || result.artifact_id !== p.artifact_id || result.version !== p.version || result.sha256 !== p.sha256 || result.size !== p.size || result.mime !== p.mime || result.parent_version !== p.parent_version || result.validation_status !== 'passed' || result.approval_status !== 'approved') { throw new RuntimeInputError('Publication receipt differs from the approved artifact') }
}
