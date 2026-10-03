// @vitest-environment jsdom
import { webcrypto } from 'node:crypto'

import { act, cleanup, fireEvent, render, type RenderResult, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { TemplatePrepareParams, TemplatePrepareResult, TemplatePreviewParams, TemplatePreviewResult, TemplateRecord } from '../../../../shared/src/gateway-contract.generated'
import type { RuntimeRequest } from '../../../../shared/src/runtime-control'
import { markdownDigest } from '../../../../shared/src/runtime-markdown'

vi.mock('@/i18n', () => ({ useI18n: () => ({ locale: 'en', t: { common: { confirm: 'Confirm', cancel: 'Cancel', loading: 'Loading', done: 'Done', close: 'Close' }, errors: { genericFailure: 'Failed' } } }) }))
import { TemplateApplicationPanel } from './template-application-panel'

const template: TemplateRecord = { template_id: 'approved', version: 3, sha256: 'a'.repeat(64), project_id: 'p', baseline_ref: { artifact_id: 'old-example', version: 2 }, parent_version: 2,
  structure: ['# ${slot.topic}\nReusable structure'], style: { heading_level: '1', voice: 'advisory' }, assets: [{ artifact_id: 'logo', version: 1 }], slots: [{ name: 'topic', purpose: 'New subject', required: true }], exclusions: ['Old Customer'] }

async function preview(params: TemplatePreviewParams): Promise<TemplatePreviewResult> {
  const content = `# ${params.slot_values.topic}\nReusable structure\n<script>untrusted()</script>`

  return { template_ref: params.template_ref, project_id: params.project_id, content, sha256: await markdownDigest(content), size: new TextEncoder().encode(content).length, mime: 'text/markdown', locked_sections: (params.locked_sections ?? []).map(anchor => ({ anchor, sha256: 'd'.repeat(64) })), advisory_style_keys: ['voice'], applied_style_keys: ['heading_level'], assets_mode: 'lineage_only', baseline_copied: false, assets: template.assets, exclusions_checked: true, publication_state: 'preview_only', preview_mode: 'plain_text' }
}

async function prepared(params: TemplatePrepareParams): Promise<TemplatePrepareResult> {
  const value = await preview(params)

  return { preview: value, proposal: { request_id: params.request_id, project_id: params.project_id, artifact_id: 'new-file', version: 1, sha256: value.sha256, size: value.size, mime: value.mime, parent_version: null, expected_head_version: null, action_digest: 'b'.repeat(64), approval_id: 'approval', approval_digest: 'c'.repeat(64), expires_at: Date.now() / 1000 + 300 } }
}

function gateway() {
  return vi.fn(async (method: string, params: unknown): Promise<unknown> => {
    const p = params as TemplatePrepareParams

    if (method === 'runtime.template.list') { return { templates: [template], complete: false, limit: 50, limit_reached: false } }

    if (method === 'runtime.template.get') { return { template } }

    if (method === 'runtime.template.preview') { return preview(p) }

    if (method === 'runtime.template.prepare') { return prepared(p) }

    if (method === 'runtime.template.publish') { return { ...(await prepared(p)).proposal, disposition: 'canonical', head_version: 1, validation_status: 'passed', approval_status: 'approved' } }

    return { command_id: p.command_id, run_id: 'run', status: method === 'runtime.artifact.cancel' ? 'cancelled' : 'claimed', owner_live: false, expires_at: null, result: null }
  })
}

async function select(view: RenderResult) {
  fireEvent.change(view.getByLabelText('Project ID'), { target: { value: 'p' } })
  fireEvent.click(view.getByRole('button', { name: 'List saved templates' }))
  fireEvent.click(await view.findByRole('button', { name: 'Inspect exact version: approved@3' }))
  fireEvent.change(await view.findByLabelText('topic (required) · New subject'), { target: { value: 'New Customer' } })
}

async function prepare(view: RenderResult) {
  fireEvent.click(view.getByRole('button', { name: 'Prepare for approval' }))
  await view.findByRole('button', { name: 'Approve exact artifact' })
}

function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>(done => { resolve = done });

 return { promise, resolve } }

beforeEach(() => { vi.stubGlobal('crypto', webcrypto) })
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('typed template application panel', () => {
  it('selects a real immutable version, displays actual inert bytes and explicitly approves the exact prepared request', async () => {
    const rpc = gateway(), view = render(<TemplateApplicationPanel connected request={rpc as RuntimeRequest} sessionId="owned" />)
    await select(view)
    expect(rpc).toHaveBeenCalledWith('runtime.template.get', { session_id: 'owned', schema_version: 1, template_id: 'approved', version: 3 })
    fireEvent.click(view.getByRole('button', { name: 'Preview new topic' }))
    const actual = await view.findByLabelText('Complete inert preview')
    expect(actual.textContent).toContain('# New Customer')
    expect(actual.textContent).not.toContain('Old Customer')
    expect(actual.querySelector('script')).toBeNull()
    fireEvent.click(view.getByRole('button', { name: 'Lock section: New Customer' }))
    await prepare(view)
    const params = rpc.mock.calls.find(([method]) => method === 'runtime.template.prepare')![1] as TemplatePrepareParams
    expect(params.template_ref).toEqual({ store: 'artifact_templates', template_id: 'approved', version: 3, sha256: template.sha256 })
    expect(params.locked_sections).toEqual(['New Customer'])
    expect(view.getByLabelText('Exact prepared preview').textContent).toBe(actual.textContent)
    expect(rpc.mock.calls.some(([method]) => method === 'runtime.template.publish')).toBe(false)
    fireEvent.click(view.getByRole('button', { name: 'Approve exact artifact' }))
    expect(view.getByRole('dialog').textContent).toContain('Project p · artifact new-file@1')
    expect(view.getByLabelText('Publication confirmation bytes').textContent).toBe(view.getByLabelText('Exact prepared preview').textContent)
    fireEvent.click(view.getByRole('button', { name: 'Publish reviewed artifact' }))
    await waitFor(() => expect(rpc).toHaveBeenCalledWith('runtime.template.publish', { ...params, approval_id: 'approval', approval_digest: 'c'.repeat(64) }))
    await view.findByText(/Published new-file@1/)
  })
  it('invalidates old approval on slot changes and uses a new identity only after acknowledged discard', async () => {
    const rpc = gateway(), view = render(<TemplateApplicationPanel connected request={rpc as RuntimeRequest} sessionId="owned" />)
    await select(view); await prepare(view)
    const original = rpc.mock.calls.find(([method]) => method === 'runtime.template.prepare')![1] as TemplatePrepareParams
    fireEvent.change(view.getByLabelText('topic (required) · New subject'), { target: { value: 'Another topic' } })
    expect(view.getByRole('button', { name: 'Approve exact artifact' }).hasAttribute('disabled')).toBe(true)
    fireEvent.click(view.getByRole('button', { name: 'Discard command' }))
    await waitFor(() => expect(view.queryByLabelText('Exact prepared preview')).toBeNull())
    expect(rpc).toHaveBeenCalledWith('runtime.artifact.cancel', { session_id: 'owned', schema_version: 1, command_id: original.command_id })
    await prepare(view)
    const requests = rpc.mock.calls.filter(([method]) => method === 'runtime.template.prepare').map(([, params]) => params as TemplatePrepareParams)
    expect(requests[1].command_id).not.toBe(original.command_id)
    expect(requests[1].slot_values).toEqual({ topic: 'Another topic' })
  })
  it('retains an unknown preparation identity, fences double clicks and only offers read/cancel recovery', async () => {
    const rpc = gateway(), base = rpc.getMockImplementation()!, delayed = deferred<TemplatePrepareResult>()
    rpc.mockImplementation((method, params) => method === 'runtime.template.prepare' ? delayed.promise : base(method, params))
    const view = render(<TemplateApplicationPanel connected request={rpc as RuntimeRequest} sessionId="owned" />)
    await select(view)
    const button = view.getByRole('button', { name: 'Prepare for approval' })
    fireEvent.click(button); fireEvent.click(button)
    const params = rpc.mock.calls.find(([method]) => method === 'runtime.template.prepare')![1] as TemplatePrepareParams
    expect(rpc.mock.calls.filter(([method]) => method === 'runtime.template.prepare')).toHaveLength(1)
    view.rerender(<TemplateApplicationPanel connected={false} request={rpc as RuntimeRequest} sessionId="owned" />)
    view.rerender(<TemplateApplicationPanel connected request={rpc as RuntimeRequest} sessionId="owned" />)
    await act(async () => delayed.resolve(await prepared(params)))
    expect(view.queryByLabelText('Exact prepared preview')).toBeNull()
    fireEvent.click(view.getByRole('button', { name: 'Inspect command' }))
    await waitFor(() => expect(rpc).toHaveBeenCalledWith('runtime.artifact.status', { session_id: 'owned', schema_version: 1, command_id: params.command_id }))
    expect(view.getByRole('button', { name: 'Prepare for approval' }).hasAttribute('disabled')).toBe(true)
  })
  it('never retries an uncertain publication and keeps the original command', async () => {
    const rpc = gateway(), base = rpc.getMockImplementation()!
    rpc.mockImplementation((method, params) => method === 'runtime.template.publish' ? Promise.reject(new Error('Disconnected after send')) : base(method, params))
    const view = render(<TemplateApplicationPanel connected request={rpc as RuntimeRequest} sessionId="owned" />)
    await select(view); await prepare(view)
    fireEvent.click(view.getByRole('button', { name: 'Approve exact artifact' })); fireEvent.click(view.getByRole('button', { name: 'Publish reviewed artifact' }))
    await view.findByRole('alert')
    expect(view.getByRole('button', { name: 'Approve exact artifact' }).hasAttribute('disabled')).toBe(true)
    expect(rpc.mock.calls.filter(([method]) => method === 'runtime.template.publish')).toHaveLength(1)
    const params = rpc.mock.calls.find(([method]) => method === 'runtime.template.prepare')![1] as TemplatePrepareParams
    expect(view.getByText(/Retained command/).textContent).toContain(params.command_id)
  })
  it.each(['session', 'transport', 'unmount'] as const)('fences a late response after %s changes', async mode => {
    const rpc = gateway(), delayed = deferred<unknown>()
    rpc.mockImplementation(() => delayed.promise)
    const view = render(<TemplateApplicationPanel connected request={rpc as RuntimeRequest} sessionId="owned" />)
    fireEvent.change(view.getByLabelText('Project ID'), { target: { value: 'p' } }); fireEvent.click(view.getByRole('button', { name: 'List saved templates' }))

    if (mode === 'unmount') { view.unmount() }
    else { view.rerender(<TemplateApplicationPanel connected request={(mode === 'transport' ? gateway() : rpc) as RuntimeRequest} sessionId={mode === 'session' ? 'foreign' : 'owned'} />) }

    await act(async () => delayed.resolve({ templates: [template], complete: false, limit: 50, limit_reached: false }))
    expect(view.queryByRole('button', { name: 'Inspect exact version: approved@3' })).toBeNull()
  })
  it('refuses a mismatched prepared digest rather than making it approvable', async () => {
    const rpc = gateway(), base = rpc.getMockImplementation()!
    rpc.mockImplementation(async (method, params) => {
      if (method !== 'runtime.template.prepare') { return base(method, params) }
      const value = await prepared(params as TemplatePrepareParams)

      return { ...value, preview: { ...value.preview, content: 'truncated' } }
    })
    const view = render(<TemplateApplicationPanel connected request={rpc as RuntimeRequest} sessionId="owned" />)
    await select(view); fireEvent.click(view.getByRole('button', { name: 'Prepare for approval' }))
    expect((await view.findByRole('alert')).textContent).toContain('preview bytes')
    expect(view.queryByRole('button', { name: 'Approve exact artifact' })).toBeNull()
  })
  it.each(['Climate study', 'Maritime analysis', 'Urban gardens'])('reuses the saved structure on a new topic: %s', async topic => {
    const rpc = gateway(), view = render(<TemplateApplicationPanel connected request={rpc as RuntimeRequest} sessionId="owned" />)
    await select(view)
    fireEvent.change(view.getByLabelText('topic (required) · New subject'), { target: { value: topic } })
    await prepare(view)
    expect(view.getByLabelText('Exact prepared preview').textContent).toContain(`# ${topic}\nReusable structure`)
    expect(view.getByLabelText('Exact prepared preview').textContent).not.toContain('Old Customer')
    expect(view.getAllByText(/Applied deterministic style: heading_level/)[0].textContent).toContain('Advisory only: voice')
    fireEvent.click(view.getByRole('button', { name: 'Approve exact artifact' })); fireEvent.click(view.getByRole('button', { name: 'Publish reviewed artifact' }))
    await view.findByText(/Published new-file@1/)
    expect(rpc).toHaveBeenCalledWith('runtime.template.publish', expect.objectContaining({ slot_values: { topic }, template_ref: expect.objectContaining({ version: 3, sha256: template.sha256 }) }))
    expect(rpc.mock.calls.some(([method]) => method === 'runtime.artifact.read')).toBe(false)
  })
  it('retains an in-flight command across A→B→A without adopting its late review', async () => {
    const rpc = gateway(), base = rpc.getMockImplementation()!, delayed = deferred<TemplatePrepareResult>()
    rpc.mockImplementation((method, params) => method === 'runtime.template.prepare' ? delayed.promise : base(method, params))
    const view = render(<TemplateApplicationPanel connected request={rpc as RuntimeRequest} sessionId="A" />)
    await select(view); fireEvent.click(view.getByRole('button', { name: 'Prepare for approval' }))
    const params = rpc.mock.calls.find(([method]) => method === 'runtime.template.prepare')![1] as TemplatePrepareParams
    view.rerender(<TemplateApplicationPanel connected request={rpc as RuntimeRequest} sessionId="B" />)
    expect(view.queryByLabelText('topic (required) · New subject')).toBeNull()
    expect(view.queryByText(new RegExp(params.command_id))).toBeNull()
    view.rerender(<TemplateApplicationPanel connected request={rpc as RuntimeRequest} sessionId="A" />)
    await act(async () => delayed.resolve(await prepared(params)))
    expect(view.queryByLabelText('Exact prepared preview')).toBeNull()
    expect(view.getByText(/Retained command/).textContent).toContain(params.command_id)
    fireEvent.click(view.getByRole('button', { name: 'Inspect command' }))
    await waitFor(() => expect(rpc).toHaveBeenCalledWith('runtime.artifact.status', { session_id: 'A', schema_version: 1, command_id: params.command_id }))
  })

})
