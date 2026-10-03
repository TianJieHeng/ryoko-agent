import { describe, expect, it } from 'vitest'

import type { TemplatePrepareResult, TemplatePreviewResult, TemplateRecord } from './gateway-contract.generated.js'
import { markdownDigest } from './runtime-markdown.js'
import { templateApplicationInput, verifyTemplatePrepared, verifyTemplatePreview, verifyTemplatePublished } from './runtime-template-application.js'

const template: TemplateRecord = { template_id: 'approved', version: 3, sha256: 'a'.repeat(64), project_id: 'p', baseline_ref: { artifact_id: 'example', version: 2 }, parent_version: 2,
  structure: ['# ${slot.topic}\nNew content'], style: { heading_level: '1', voice: 'advisory' }, assets: [{ artifact_id: 'logo', version: 1 }], slots: [{ name: 'topic', purpose: 'New topic', required: true }], exclusions: ['Old Customer'] }

const input = () => templateApplicationInput('session', 'p', template, { topic: 'New Customer' }, [])

async function preview(): Promise<TemplatePreviewResult> {
  const content = '# New Customer\nNew content'

  return { ...input(), content, sha256: await markdownDigest(content), size: new TextEncoder().encode(content).length, mime: 'text/markdown', locked_sections: [], advisory_style_keys: ['voice'], applied_style_keys: ['heading_level'], assets_mode: 'lineage_only', baseline_copied: false, assets: template.assets, exclusions_checked: true, publication_state: 'preview_only', preview_mode: 'plain_text' }
}

async function prepared(): Promise<TemplatePrepareResult> {
  const value = await preview()

  return { preview: value, proposal: { request_id: 'request', project_id: 'p', artifact_id: 'new', version: 1, sha256: value.sha256, size: value.size, mime: value.mime, parent_version: null, expected_head_version: null, action_digest: 'b'.repeat(64), approval_id: 'approval', approval_digest: 'c'.repeat(64), expires_at: Date.now() / 1000 + 60 } }
}

describe('canonical saved-template application', () => {
  it('binds immutable store/version/digest and literal slots without copying the baseline', () => {
    expect(input().template_ref).toEqual({ store: 'artifact_templates', template_id: 'approved', version: 3, sha256: template.sha256 })
    expect(input().slot_values).toEqual({ topic: 'New Customer' })
    expect(input()).not.toHaveProperty('content')
    expect(input()).not.toHaveProperty('baseline_ref')
  })
  it('rejects wrong project, missing required slots, undeclared slots and invalid pins', () => {
    expect(() => templateApplicationInput('session', 'other', template, {}, [])).toThrow('different project')
    expect(() => templateApplicationInput('session', 'p', template, {}, [])).toThrow('required slot')
    expect(() => templateApplicationInput('session', 'p', template, { topic: 'new', extra: 'no' }, [])).toThrow('declared')
    expect(() => templateApplicationInput('session', 'p', { ...template, sha256: 'latest' }, { topic: 'new' }, [])).toThrow('SHA-256')
  })
  it('checks complete UTF-8 bytes and exact pin before displaying preview', async () => {
    const value = await preview()
    await expect(verifyTemplatePreview(value, input())).resolves.toBeUndefined()
    await expect(verifyTemplatePreview({ ...value, content: '<script>run()</script>' }, input())).rejects.toThrow('preview bytes')
    await expect(verifyTemplatePreview({ ...value, template_ref: { ...value.template_ref, version: 4 } }, input())).rejects.toThrow('exact template pin')
    await expect(verifyTemplatePreview({ ...value, size: value.size + 1 }, input())).rejects.toThrow('preview bytes')
  })
  it('does not claim semantic style application or silently change section locks', async () => {
    const value = await preview()
    await expect(verifyTemplatePreview({ ...value, applied_style_keys: ['tone'] }, input())).rejects.toThrow('deterministic style')
    await expect(verifyTemplatePreview(value, { ...input(), locked_sections: ['New Customer'] })).rejects.toThrow('section locks')
  })
  it('binds approval to the actual preview, request and expiry', async () => {
    const value = await prepared(), params = { ...input(), command_id: 'command', request_id: 'request' }
    await expect(verifyTemplatePrepared(value, params)).resolves.toBeUndefined()
    await expect(verifyTemplatePrepared({ ...value, proposal: { ...value.proposal, request_id: 'foreign' } }, params)).rejects.toThrow('Approval proposal')
    await expect(verifyTemplatePrepared({ ...value, proposal: { ...value.proposal, sha256: 'f'.repeat(64) } }, params)).rejects.toThrow('Approval proposal')
    await expect(verifyTemplatePrepared({ ...value, proposal: { ...value.proposal, expires_at: 0 } }, params)).rejects.toThrow('expired')
  })
  it('checks actual publication against reviewed artifact', async () => {
    const value = await prepared(), result = { ...value.proposal, disposition: 'canonical' as const, head_version: 1, validation_status: 'passed' as const, approval_status: 'approved' as const }
    expect(() => verifyTemplatePublished(result, value)).not.toThrow()
    expect(() => verifyTemplatePublished({ ...result, version: 2 }, value)).toThrow('approved artifact')
  })
  it('treats prototype-shaped declared slots as literal own keys', () => {
    const special = { ...template, slots: [{ name: '__proto__', required: true, purpose: 'Literal input' }] }
    const values = Object.fromEntries([['__proto__', 'New topic']])
    expect(templateApplicationInput('session', 'p', special, values, []).slot_values).toEqual(values)
    expect(() => templateApplicationInput('session', 'p', { ...special, slots: [{ ...special.slots[0], name: 'constructor' }] }, {}, [])).toThrow('required slot')
  })

})
