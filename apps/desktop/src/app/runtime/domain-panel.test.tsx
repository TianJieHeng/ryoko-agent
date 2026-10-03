// @vitest-environment jsdom
import { webcrypto } from 'node:crypto'

import { act, cleanup, fireEvent, render, type RenderResult, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { ArtifactControlStatus, DomainPrepareParams, DomainPrepareResult, DomainPublishResult } from '../../../../shared/src/gateway-contract.generated'
import type { RuntimeRequest } from '../../../../shared/src/runtime-control'
import type { SelectedDomainJob } from '../../../../shared/src/runtime-domains'

const locale = vi.hoisted(() => ({ value: 'en' }))
vi.mock('@/i18n', () => ({ useI18n: () => ({ locale: locale.value, t: { common: { confirm: 'Confirm', cancel: 'Cancel', loading: 'Loading', done: 'Done', close: 'Close' }, errors: { genericFailure: 'Failed' } } }) }))
import { DomainPanel } from './domain-panel'

const digest = 'a'.repeat(64)

function prepared(params: DomainPrepareParams): DomainPrepareResult {
  const job = JSON.parse(params.job_json) as SelectedDomainJob

  return { publication_atomic: false, proposals: ['output', 'manifest'].map((name, index) => ({
    request_id: `domain:${job.job_id}:${index === 1 ? 'manifest' : index}`, project_id: job.project_id,
    artifact_id: `${name}-${job.job_id}`, version: 1, sha256: index ? 'c'.repeat(64) : 'b'.repeat(64), size: 16,
    mime: index ? 'application/json' : job.adapter === 'data' ? 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' : 'text/markdown',
    parent_version: null, expected_head_version: null, action_digest: 'd'.repeat(64), approval_id: `${name}-approval`, approval_digest: index ? 'f'.repeat(64) : 'e'.repeat(64), expires_at: Date.now() / 1000 + 300
  })) }
}

function publication(value: DomainPrepareResult): DomainPublishResult {
  const outputs = value.proposals.map(proposal => ({ project_id: proposal.project_id, artifact_id: proposal.artifact_id, version: proposal.version,
    sha256: proposal.sha256, size: proposal.size, mime: proposal.mime, parent_version: proposal.parent_version,
    disposition: 'canonical' as const, head_version: proposal.version, validation_status: 'passed' as const, approval_status: 'approved' as const }))

  return { project_id: outputs[0].project_id, outputs: outputs.slice(0, -1), manifest: outputs.at(-1)!, state: 'published', publication_atomic: false, external_production: 'not_performed' }
}

function receipt(command_id: string, status: ArtifactControlStatus['status']): ArtifactControlStatus {
  return { command_id, run_id: 'run', status, owner_live: !['cancelled', 'completed', 'failed', 'blocked'].includes(status), expires_at: null,
    result: status === 'cancelled' ? { cancel_requested: true, effects_undone: false } : null }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej })

  return { promise, resolve, reject }
}

function change(view: RenderResult, label: string, value: string) { fireEvent.change(view.getByLabelText(label), { target: { value } }) }

function fillData(view: RenderResult) {
  change(view, 'Project ID', 'project-a'); change(view, 'Retained artifact ID', 'retained-csv'); change(view, 'SHA-256', digest)
}

function fillCreative(view: RenderResult) {
  change(view, 'Project ID', 'project-a'); change(view, 'Package', 'creative'); change(view, 'Creative brief', 'Keep the mascot consistent')
  change(view, 'Prompts (one per line)', 'Portrait on white\nWide poster'); change(view, 'Continuity notes (one per line)', 'Same coat')
}

async function prepareAndReview(view: RenderResult) {
  fireEvent.click(view.getByRole('button', { name: 'Prepare for review' }))
  await view.findByRole('button', { name: 'Reviewed 1' })
  fireEvent.click(view.getByRole('button', { name: 'Reviewed 1' })); fireEvent.click(view.getByRole('button', { name: 'Reviewed 2' }))
}

async function confirmPublication(view: RenderResult) {
  fireEvent.click(view.getByRole('button', { name: 'Approve publication' }))
  const dialog = await view.findByRole('dialog')
  fireEvent.click(within(dialog).getByRole('button', { name: 'Publish exact package' }))
}

function runtime(handler?: (method: string, params: Record<string, unknown>) => unknown) {
  let proposal: DomainPrepareResult

  const spy = vi.fn(async (method: string, params: Record<string, unknown>) => {
    const custom = handler?.(method, params)

    if (custom !== undefined) {return custom}

    if (method === 'runtime.domain.prepare') { proposal = prepared(params as unknown as DomainPrepareParams);

 return proposal }

    if (method === 'runtime.domain.publish') {return publication(proposal)}

    if (method === 'runtime.artifact.cancel') {return receipt(params.command_id as string, 'cancelled')}

    if (method === 'runtime.artifact.status') {return receipt(params.command_id as string, 'claimed')}
    throw new Error(`Unexpected method ${method}`)
  })

  return { spy, request: spy as RuntimeRequest }
}

beforeEach(() => { locale.value = 'en'; Object.defineProperty(globalThis, 'crypto', { configurable: true, value: webcrypto }) })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('DomainPanel with the actual domain helpers and desktop confirmation dialog', () => {
  it('prepares exact typed data assumptions, then requires every ordered review and final confirmation', async () => {
    const { spy, request } = runtime()
    const view = render(<DomainPanel connected request={request} sessionId="owned" />)
    fillData(view)
    change(view, 'Exact version', '4'); change(view, 'Encoding', 'utf-8-sig'); change(view, 'Delimiter', ';')
    change(view, 'Date format', 'DD/MM/YYYY'); change(view, 'Currency (ISO code, blank for none)', 'EUR')
    change(view, 'Null markers (one per line)', 'NA\nmissing'); change(view, 'Duplicate keys', 'keep_first'); change(view, 'Key columns (one per line)', 'id')
    fireEvent.click(view.getByRole('button', { name: 'Add unit' })); change(view, 'Unit column 1', 'amount'); change(view, 'Unit 1', 'EUR')
    change(view, 'Aggregation', 'sum'); change(view, 'Group columns (one per line)', 'category'); change(view, 'Value column', 'amount'); change(view, 'Result column', 'total')
    change(view, 'Chart', 'bar'); change(view, 'Category column', 'category'); change(view, 'Chart: Value column', 'total')
    fireEvent.click(view.getByRole('button', { name: 'Prepare for review' }))
    await view.findByRole('button', { name: 'Reviewed 1' })
    expect(spy).toHaveBeenCalledTimes(1)
    const [, input] = spy.mock.calls[0]
    const job = JSON.parse(input.job_json as string)
    expect(job.arguments.inputs).toEqual([{ source_id: 'source', ref: { artifact_id: 'retained-csv', version: 4, sha256: digest }, options: {
      encoding: 'utf-8-sig', delimiter: ';', date_format: 'DD/MM/YYYY', currency: 'EUR', null_values: ['', 'NA', 'missing'], units: { amount: 'EUR' }, duplicate_keys: 'keep_first', keys: ['id']
    } }])
    expect(job.arguments.recipe).toEqual({ base: 'source', exports: ['xlsx'], aggregate: { group_by: ['category'], aggregations: { total: { operation: 'sum', column: 'amount' } }, nulls: 'reject' }, charts: [{ kind: 'bar', category: 'category', value: 'total' }] })
    const approve = view.getByRole('button', { name: 'Approve publication' }) as HTMLButtonElement
    expect(approve.disabled).toBe(true)
    fireEvent.click(view.getByRole('button', { name: 'Reviewed 1' })); expect(approve.disabled).toBe(true)
    fireEvent.click(view.getByRole('button', { name: 'Reviewed 2' })); expect(approve.disabled).toBe(false)
    fireEvent.click(approve)
    const dialog = await view.findByRole('dialog')
    expect(dialog.textContent).toContain('Publication is not atomic')
    expect(dialog.textContent).toContain(input.command_id)
    expect(spy).toHaveBeenCalledTimes(1)
    const publish = within(dialog).getByRole('button', { name: 'Publish exact package' })
    act(() => { fireEvent.click(publish); fireEvent.click(publish) })
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(2))
    expect(spy.mock.calls[1]).toEqual(['runtime.domain.publish', { ...input, approvals: [{ approval_id: 'output-approval', approval_digest: 'e'.repeat(64) }, { approval_id: 'manifest-approval', approval_digest: 'f'.repeat(64) }] }])
    await view.findByRole('button', { name: 'Inspect verified manifest' })
    expect(view.getByText(/Published 1 output/).textContent).toContain('validation passed')
  })

  it('invalidates edits but retains the command until cancellation reaches a terminal receipt', async () => {
    let cancellations = 0
    const { spy, request } = runtime((method, params) => method === 'runtime.artifact.cancel' ? receipt(params.command_id as string, ++cancellations === 1 ? 'claimed' : 'cancelled') : method === 'runtime.artifact.status' && cancellations ? receipt(params.command_id as string, 'cancelled') : undefined)
    const view = render(<DomainPanel connected request={request} sessionId="owned" />)
    fillData(view); await prepareAndReview(view)
    const original = spy.mock.calls[0][1].command_id
    change(view, 'Currency (ISO code, blank for none)', 'USD')
    expect((view.getByRole('button', { name: 'Approve publication' }) as HTMLButtonElement).disabled).toBe(true)
    fireEvent.click(view.getByRole('button', { name: 'Discard proposal' }))
    await view.findByText(/active; publication is not confirmed/)
    expect((view.getByRole('button', { name: 'Prepare for review' }) as HTMLButtonElement).disabled).toBe(true)
    expect((view.getByRole('button', { name: 'Discard proposal' }) as HTMLButtonElement).disabled).toBe(true)
    fireEvent.click(view.getByRole('button', { name: 'Inspect command' }))
    await waitFor(() => expect((view.getByRole('button', { name: 'Prepare for review' }) as HTMLButtonElement).disabled).toBe(false))
    expect(spy.mock.calls.slice(1).map(([, params]) => params.command_id)).toEqual([original, original])
    fireEvent.click(view.getByRole('button', { name: 'Prepare for review' }))
    await view.findByRole('button', { name: 'Reviewed 1' })
    expect(spy.mock.calls[3][1].command_id).not.toBe(original)
  })

  it('keeps uncertain preparation bound and never retries automatically', async () => {
    const { spy, request } = runtime(method => method === 'runtime.domain.prepare' ? Promise.reject(new Error('network unavailable with private detail')) : undefined)
    const view = render(<DomainPanel connected request={request} sessionId="owned" />)
    fillData(view); fireEvent.click(view.getByRole('button', { name: 'Prepare for review' }))
    await view.findByRole('alert')
    expect(view.getByRole('alert').textContent).toContain('no automatic retry')
    expect(view.getByRole('alert').textContent).not.toContain('private detail')
    expect((view.getByRole('button', { name: 'Prepare for review' }) as HTMLButtonElement).disabled).toBe(true)
    expect(spy).toHaveBeenCalledTimes(1)
    fireEvent.click(view.getByRole('button', { name: 'Inspect command' }))
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(2))
    expect(spy.mock.calls[1][1].command_id).toBe(spy.mock.calls[0][1].command_id)
  })

  it('retains uncertain publication, disables old approvals and uses status rather than retry', async () => {
    const { spy, request } = runtime(method => method === 'runtime.domain.publish' ? Promise.reject(new Error('connection lost')) : undefined)
    const view = render(<DomainPanel connected request={request} sessionId="owned" />)
    fillData(view); await prepareAndReview(view); await confirmPublication(view)
    await waitFor(() => expect(view.getByRole('dialog').textContent).toContain('no automatic retry'))
    fireEvent.click(within(view.getByRole('dialog')).getByRole('button', { name: 'Cancel' }))
    await view.findByRole('alert')
    expect((view.getByRole('button', { name: 'Approve publication' }) as HTMLButtonElement).disabled).toBe(true)
    expect((view.getByRole('button', { name: 'Prepare for review' }) as HTMLButtonElement).disabled).toBe(true)
    expect(spy.mock.calls.map(([method]) => method)).toEqual(['runtime.domain.prepare', 'runtime.domain.publish'])
  })

  it('ignores a late prepare after a session change and retains the old command for the old scope', async () => {
    const pending = deferred<DomainPrepareResult>()
    const { spy, request } = runtime(method => method === 'runtime.domain.prepare' ? pending.promise : undefined)
    const view = render(<DomainPanel connected request={request} sessionId="first" />)
    fillData(view)
    const prepare = view.getByRole('button', { name: 'Prepare for review' })
    act(() => { fireEvent.click(prepare); fireEvent.click(prepare) })
    expect(spy).toHaveBeenCalledTimes(1)
    const params = spy.mock.calls[0][1] as unknown as DomainPrepareParams
    view.rerender(<DomainPanel connected request={request} sessionId="second" />)
    expect(view.getByText(/Another session or connection/).textContent).toContain(params.command_id)
    await act(async () => pending.resolve(prepared(params)))
    expect(view.queryByRole('button', { name: 'Reviewed 1' })).toBeNull()
    expect((view.getByLabelText('Project ID') as HTMLInputElement).value).toBe('')
    view.rerender(<DomainPanel connected request={request} sessionId="first" />)
    expect(view.queryByRole('button', { name: 'Reviewed 1' })).toBeNull()
    expect((view.getByRole('button', { name: 'Prepare for review' }) as HTMLButtonElement).disabled).toBe(true)
    fireEvent.click(view.getByRole('button', { name: 'Inspect command' }))
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(2))
    expect(spy.mock.calls[1][1]).toEqual({ command_id: params.command_id, session_id: 'first', schema_version: 1 })
  })

  it('isolates identical session IDs on different request transports', async () => {
    const first = runtime(), second = runtime()
    const view = render(<DomainPanel connected request={first.request} sessionId="same-id" />)
    fillData(view); await prepareAndReview(view)
    view.rerender(<DomainPanel connected request={second.request} sessionId="same-id" />)
    expect(view.queryByRole('button', { name: 'Reviewed 1' })).toBeNull()
    fillCreative(view); await prepareAndReview(view); await confirmPublication(view)
    await view.findByRole('button', { name: 'Inspect verified manifest' })
    expect(first.spy.mock.calls.map(([method]) => method)).toEqual(['runtime.domain.prepare'])
    expect(second.spy.mock.calls.map(([method]) => method)).toEqual(['runtime.domain.prepare', 'runtime.domain.publish'])
  })

  it('closes confirmation on disconnect, then requires status or discard after reconnect', async () => {
    const { spy, request } = runtime()
    const view = render(<DomainPanel connected request={request} sessionId="owned" />)
    fillData(view); await prepareAndReview(view)
    fireEvent.click(view.getByRole('button', { name: 'Approve publication' })); await view.findByRole('dialog')
    view.rerender(<DomainPanel connected={false} request={request} sessionId="owned" />)
    expect(view.queryByRole('dialog')).toBeNull()
    view.rerender(<DomainPanel connected request={request} sessionId="owned" />)
    expect((view.getByRole('button', { name: 'Approve publication' }) as HTMLButtonElement).disabled).toBe(true)
    expect(spy).toHaveBeenCalledTimes(1)
    fireEvent.click(view.getByRole('button', { name: 'Discard proposal' }))
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(2))
    expect(spy.mock.calls[1][1].command_id).toBe(spy.mock.calls[0][1].command_id)
  })

  it('ignores a late disconnected result and cannot unlock a newer status operation', async () => {
    const pendingPrepare = deferred<DomainPrepareResult>(), pendingStatus = deferred<ArtifactControlStatus>()
    const { spy, request } = runtime(method => method === 'runtime.domain.prepare' ? pendingPrepare.promise : method === 'runtime.artifact.status' ? pendingStatus.promise : undefined)
    const view = render(<DomainPanel connected request={request} sessionId="owned" />)
    fillData(view); fireEvent.click(view.getByRole('button', { name: 'Prepare for review' }))
    const params = spy.mock.calls[0][1] as unknown as DomainPrepareParams
    view.rerender(<DomainPanel connected={false} request={request} sessionId="owned" />)
    view.rerender(<DomainPanel connected request={request} sessionId="owned" />)
    fireEvent.click(view.getByRole('button', { name: 'Inspect command' }))
    await act(async () => pendingPrepare.resolve(prepared(params)))
    expect(view.queryByRole('button', { name: 'Reviewed 1' })).toBeNull()
    expect((view.getByRole('button', { name: 'Inspect command' }) as HTMLButtonElement).disabled).toBe(true)
    await act(async () => pendingStatus.resolve(receipt(params.command_id, 'cancelled')))
    expect((view.getByRole('button', { name: 'Prepare for review' }) as HTMLButtonElement).disabled).toBe(false)
  })

  it('creates a prompt-only package with supplied rights and no generation or upload RPC', async () => {
    const { spy, request } = runtime()
    const view = render(<DomainPanel connected request={request} sessionId="owned" />)
    fillCreative(view)
    fireEvent.click(view.getByRole('button', { name: 'Supplied reference' }))
    change(view, 'Reference name', 'reference.png'); change(view, 'Retained artifact ID', 'existing-image'); change(view, 'Exact version', '2'); change(view, 'SHA-256', digest); change(view, 'Declared rights', 'licensed')
    await prepareAndReview(view)
    expect(view.getByText(/Creative prompt-only package; generated assets: 0/)).toBeTruthy()
    const job = JSON.parse(spy.mock.calls[0][1].job_json as string)
    expect(job.arguments).toEqual({ brief: 'Keep the mascot consistent', prompts: ['Portrait on white', 'Wide poster'], continuity: ['Same coat'], assets: [{ name: 'reference.png', ref: { artifact_id: 'existing-image', version: 2, sha256: digest }, rights: 'licensed', stage: 'supplied' }] })
    await confirmPublication(view); await view.findByRole('button', { name: 'Inspect verified manifest' })
    expect(spy.mock.calls.map(([method]) => method)).toEqual(['runtime.domain.prepare', 'runtime.domain.publish'])
  })

  it('rejects malformed source pins locally and invalid prepared output ordering before approval', async () => {
    const { spy, request } = runtime((method, params) => {
      if (method !== 'runtime.domain.prepare') {return undefined}
      const result = prepared(params as unknown as DomainPrepareParams)
      result.proposals[0].request_id = 'wrong-order'

      return result
    })

    const view = render(<DomainPanel connected request={request} sessionId="owned" />)
    fillData(view); change(view, 'SHA-256', 'not-a-digest'); fireEvent.click(view.getByRole('button', { name: 'Prepare for review' }))
    await view.findByRole('alert'); expect(spy).not.toHaveBeenCalled()
    change(view, 'SHA-256', digest); fireEvent.click(view.getByRole('button', { name: 'Prepare for review' }))
    await waitFor(() => expect(view.getByRole('alert').textContent).toContain('output order'))
    expect(view.queryByRole('button', { name: 'Approve publication' })).toBeNull()
    expect(view.getByRole('button', { name: 'Discard proposal' })).toBeTruthy()
  })

  it('checks proposal expiry at confirmation and refuses publication', async () => {
    const { spy, request } = runtime((method, params) => {
      if (method !== 'runtime.domain.prepare') {return undefined}
      const result = prepared(params as unknown as DomainPrepareParams)
      result.proposals.forEach(proposal => { proposal.expires_at = 1 })

      return result
    })

    const view = render(<DomainPanel connected request={request} sessionId="owned" />)
    fillData(view); await prepareAndReview(view); await confirmPublication(view)
    await waitFor(() => expect(view.getByRole('dialog').textContent).toContain('Proposal expired'))
    fireEvent.click(within(view.getByRole('dialog')).getByRole('button', { name: 'Cancel' }))
    await view.findByRole('alert')
    expect(view.getByRole('alert').textContent).toContain('Proposal expired')
    expect(spy).toHaveBeenCalledTimes(1)
  })

  it('reads digest-verified manifest profile and selected row lineage without source cells', async () => {
    const manifest = { schema_version: 1, adapter: 'data', project_id: 'project-a', domain_metadata: {
      formula_status: 'preserved_not_recalculated', profile: { rows: 1, columns: [{ name: 'total', null_count: 0, formula_count: 0, unit: 'EUR' }] },
      recipe: { base: 'source', aggregate: { group_by: ['category'] } }, lineage: [[{ source_id: 'source', row: 2, sha256: digest }]]
    } }

    const bytes = new TextEncoder().encode(JSON.stringify(manifest))
    const hash = Buffer.from(await webcrypto.subtle.digest('SHA-256', bytes)).toString('hex')

    const { spy, request } = runtime((method, params) => {
      if (method === 'runtime.domain.prepare') { const result = prepared(params as unknown as DomainPrepareParams); result.proposals[1].sha256 = hash; result.proposals[1].size = bytes.length;

 return result }

      if (method === 'runtime.domain.publish') {
        const result = prepared(params as unknown as DomainPrepareParams); result.proposals[1].sha256 = hash; result.proposals[1].size = bytes.length;

 return publication(result)
      }

      if (method === 'runtime.artifact.get') {return { ...params, sha256: hash, size: bytes.length, mime: 'application/json', data_base64: Buffer.from(bytes).toString('base64'), next_offset: bytes.length, eof: true, preview_mode: 'download_only' }}

      return undefined
    })

    const view = render(<DomainPanel connected request={request} sessionId="owned" />)
    fillData(view); await prepareAndReview(view); await confirmPublication(view)
    await waitFor(() => expect((view.getByRole('button', { name: 'Inspect verified manifest' }) as HTMLButtonElement).disabled).toBe(false))
    fireEvent.click(view.getByRole('button', { name: 'Inspect verified manifest' }))
    await view.findByRole('button', { name: 'Show lineage' })
    expect(view.getByText(/Data manifest: 1 result rows/).textContent).toContain('0 nulls, 0 formulas; unit EUR')
    fireEvent.click(view.getByRole('button', { name: 'Show lineage' }))
    expect(view.getByText(/Result row 0 derives from 1 source row/).textContent).toContain('source: source row 2')
    expect(spy.mock.calls.filter(([method]) => method === 'runtime.artifact.get')).toHaveLength(1)
    change(view, 'Result row (zero-based)', '3'); fireEvent.click(view.getByRole('button', { name: 'Show lineage' }))
    expect(view.getByRole('alert').textContent).toContain('outside the supported range')
  })

  it('ignores a late manifest after a scope change and a late prepare after unmount', async () => {
    const pendingRead = deferred<unknown>()
    const { spy, request } = runtime(method => method === 'runtime.artifact.get' ? pendingRead.promise : undefined)
    const view = render(<DomainPanel connected request={request} sessionId="owned" />)
    fillData(view); await prepareAndReview(view); await confirmPublication(view)
    await waitFor(() => expect((view.getByRole('button', { name: 'Inspect verified manifest' }) as HTMLButtonElement).disabled).toBe(false))
    fireEvent.click(view.getByRole('button', { name: 'Inspect verified manifest' }))
    await waitFor(() => expect(spy.mock.calls.some(([method]) => method === 'runtime.artifact.get')).toBe(true))
    view.rerender(<DomainPanel connected request={request} sessionId="different" />)
    await act(async () => pendingRead.reject(new Error('late private backend error')))
    expect(view.queryByRole('alert')).toBeNull()
    const pendingPrepare = deferred<DomainPrepareResult>()
    const later = runtime(method => method === 'runtime.domain.prepare' ? pendingPrepare.promise : undefined)
    view.rerender(<DomainPanel connected request={later.request} sessionId="different" />)
    fillData(view); fireEvent.click(view.getByRole('button', { name: 'Prepare for review' })); view.unmount()
    await act(async () => pendingPrepare.resolve(prepared(later.spy.mock.calls[0][1] as unknown as DomainPrepareParams)))
    expect(later.spy).toHaveBeenCalledTimes(1)
    expect(spy.mock.calls.filter(([method]) => method === 'runtime.artifact.cancel')).toHaveLength(0)
  })

  it('rejects oversized local jobs before reserving a control lease', async () => {
    const { spy, request } = runtime()
    const view = render(<DomainPanel connected request={request} sessionId="owned" />)
    fillCreative(view)
    change(view, 'Prompts (one per line)', Array(5).fill('x'.repeat(15000)).join('\n'))
    fireEvent.click(view.getByRole('button', { name: 'Prepare for review' }))
    await view.findByRole('alert')
    expect(view.getByRole('alert').textContent).toContain('65536-byte')
    expect(spy).not.toHaveBeenCalled()
    expect(view.queryByRole('button', { name: 'Inspect command' })).toBeNull()
    expect((view.getByRole('button', { name: 'Prepare for review' }) as HTMLButtonElement).disabled).toBe(false)
  })

  it.each(['en', 'zh', 'zh-hant', 'ja', 'ar', 'ru', 'fr', 'de', 'es'])('renders localized feature chrome for %s without missing labels', value => {
    locale.value = value
    const { request } = runtime()
    const view = render(<DomainPanel connected request={request} sessionId="owned" />)
    expect(view.container.textContent).not.toContain('undefined')
    expect(view.getAllByRole('button').every(button => button.textContent!.length > 0)).toBe(true)

    if (value !== 'en') {expect(view.queryByText('Data and creative packages')).toBeNull()}
  })
})
