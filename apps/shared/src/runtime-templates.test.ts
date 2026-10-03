import { expect, it, vi } from 'vitest'

import type { RuntimeRequest } from './runtime-control.js'
import { runTemplateCommand } from './runtime-templates.js'

it('preserves structure, slots and exclusions without generating content or granting access', async () => {
  const data = { project_id: 'p', template_id: 't', baseline_ref: { artifact_id: 'approved', version: 2 }, structure: ['Overview', 'Evidence'], style: { tone: 'plain' }, assets: [], slots: [{ name: 'topic', purpose: 'New subject', required: true }], exclusions: ['Original names and examples'] }
  const request = vi.fn(async () => ({ template: { ...data, version: 1, parent_version: null } })) as RuntimeRequest
  const summary = await runTemplateCommand(`create ${JSON.stringify(data)}`, request, 'owned')
  expect(request).toHaveBeenCalledOnce()
  expect(request).toHaveBeenCalledWith('runtime.template.create', { ...data, version: 1, session_id: 'owned', schema_version: 1 })
  expect(summary).toContain('generates no output')
  expect(summary).toContain('Original names and examples')
  await expect(runTemplateCommand(`create ${JSON.stringify({ ...data, session_id: 'foreign' })}`, request, 'owned')).rejects.toThrow('identity/session')
  expect(request).toHaveBeenCalledOnce()
})
