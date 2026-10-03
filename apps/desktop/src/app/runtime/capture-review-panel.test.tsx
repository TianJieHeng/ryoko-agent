// @vitest-environment jsdom
import { webcrypto } from 'node:crypto'

import { act, cleanup, fireEvent, render, type RenderResult, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { CaptureBatchParams, CaptureInspectResult } from '../../../../shared/src/gateway-contract.generated'
import type { RuntimeRequest } from '../../../../shared/src/runtime-control'

vi.mock('@/i18n', () => ({ useI18n: () => ({ locale: 'en', t: { common: { confirm: 'Confirm', cancel: 'Cancel', loading: 'Loading', done: 'Done', close: 'Close' }, ui: { search: { clear: 'Clear' } }, errors: { genericFailure: 'Failed' } } }) }))
import { CaptureReviewPanel } from './capture-review-panel'

function record(id = 'capture', revision = 0): CaptureInspectResult {
  return { capture: { capture_id: id, project_id: 'p', original_ref: { artifact_id: 'original', version: 2 }, acquired_at: 123, annotation: '<img src=x onerror=bad()> Retained annotation', source_url: 'https://example.test/source', suggested_project_id: null, filed_project_id: null, revision,
    extractions: [{ sequence: 1, status: 'unavailable', extracted_ref: null, failure_code: 'unsupported_local_extraction', created_at: 124 }] },
  processing: { status: 'metadata_only', method: 'none', source_ref: null, extraction_sequence: 1, indexed_characters: 0, truncated: false, failure_code: 'unsupported_local_extraction', indexed_at: 124 },
  consolidated_into: null, filing_history: [], consolidation_history: [] }
}

function gateway() {
  return vi.fn(async (method: string, params: unknown): Promise<unknown> => {
    const p = params as CaptureBatchParams & { capture_id: string }

    if (method === 'runtime.capture.inspect') { return record(p.capture_id) }

    if (method === 'runtime.capture.search') { return { matches: ['capture', 'duplicate'].map(id => ({ ...record(id), matched_terms: ['retained'], score: 1, excerpt: '<script>window.bad()</script>' + 'x'.repeat(400) })), scanned: 2, complete: false, truncated: false, search_mode: 'lexical_fuzzy', limitations: ['Only the bounded scan is searched'] } }

    if (method === 'runtime.capture.batch.preview') { return { batch_id: p.batch_id, project_id: p.project_id, preview_digest: 'a'.repeat(64), scope: 'capture_metadata_only', originals_preserved: true,
      items: p.items.map(item => ({ ...item, previous_filed_project_id: null, previous_consolidated_into: null, original_sha256: 'b'.repeat(64) })) } }

    if (method === 'runtime.capture.batch.commit') { return { batch_id: p.batch_id, project_id: p.project_id, preview_digest: 'a'.repeat(64), scope: 'capture_metadata_only', originals_preserved: true, replayed: false,
      items: p.items.map(item => ({ ...item, revision: item.expected_revision + 1 })) } }

    if (method === 'runtime.capture.process') { const value = record(); value.processing.extraction_sequence = 2; value.capture.extractions.push({ sequence: 2, status: 'unavailable', extracted_ref: null, failure_code: 'unsupported_local_extraction', created_at: 125 });

 return value }

    throw new Error(`Unexpected method ${method}`)
  })
}

async function search(view: RenderResult) {
  fireEvent.change(view.getByLabelText('Project ID'), { target: { value: 'p' } }); fireEvent.change(view.getByLabelText('Search captures'), { target: { value: 'retained' } })
  fireEvent.click(view.getByRole('button', { name: 'Search' })); await view.findByRole('button', { name: 'Inspect: capture' })
}

async function select(view: RenderResult, id = 'capture') {
  fireEvent.click(view.getByRole('button', { name: `Inspect: ${id}` })); await waitFor(() => expect(view.getByRole('region', { name: `Inspect: ${id}` })).toBeDefined())
  fireEvent.click(view.getByRole('button', { name: 'Select for batch' }))
}

async function preview(view: RenderResult) {
  fireEvent.click(view.getByRole('button', { name: 'Preview metadata changes' })); await view.findByRole('button', { name: 'Review exact batch' })
}

async function commit(view: RenderResult) {
  fireEvent.click(view.getByRole('button', { name: 'Review exact batch' })); fireEvent.click(view.getByRole('button', { name: 'Commit reviewed metadata' }))
}

function deferred<T>() { let resolve!: (value: T) => void, reject!: (reason: Error) => void; const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no });

 return { promise, resolve, reject } }

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('CaptureReviewPanel', () => {
  it('opens an unindexed original directly by receipt ID without requiring a search match', async () => {
    const rpc = gateway(), view = render(<CaptureReviewPanel connected request={rpc as RuntimeRequest} sessionId="owned" />)
    fireEvent.change(view.getByLabelText('Project ID'), { target: { value: 'p' } })
    fireEvent.change(view.getByLabelText('Capture ID'), { target: { value: 'unindexed' } })
    fireEvent.click(view.getByRole('button', { name: 'Inspect' }))
    await view.findByRole('region', { name: 'Inspect: unindexed' })
    expect(rpc).toHaveBeenCalledWith('runtime.capture.inspect', { session_id: 'owned', schema_version: 1, capture_id: 'unindexed' })
    expect(rpc.mock.calls.some(([method]) => method === 'runtime.capture.search')).toBe(false)
  })
  it('renders bounded inert search, original pointers and retained annotation/extraction failure', async () => {
    const rpc = gateway(), view = render(<CaptureReviewPanel connected request={rpc as RuntimeRequest} sessionId="owned" />)
    await search(view); await select(view)
    expect(view.getByLabelText('Annotation').textContent).toContain('<img src=x onerror=bad()>')
    expect(view.container.querySelector('img,script')).toBeNull()
    expect(view.container.querySelector('pre')!.textContent).toHaveLength(320)
    expect(view.getAllByText(/original@2/).length).toBeGreaterThan(0)
    expect(view.getAllByText(/unsupported_local_extraction/).length).toBeGreaterThan(0)
    expect(view.getByText(/complete: false/).textContent).toContain('2 scanned')
    expect(view.getByRole('button', { name: 'Process latest supplied text' }).hasAttribute('disabled')).toBe(true)
    expect(view.getByRole('button', { name: 'Download original' }).hasAttribute('disabled')).toBe(false)
  })
  it('processes only after explicit click using the inspected extraction sequence', async () => {
    const rpc = gateway(), view = render(<CaptureReviewPanel connected request={rpc as RuntimeRequest} sessionId="owned" />)
    await search(view); await select(view)
    expect(rpc.mock.calls.some(([m]) => m === 'runtime.capture.process')).toBe(false)
    fireEvent.click(view.getByRole('button', { name: 'Process original text' }))
    await view.findByText(/Processing acknowledged: metadata_only/)
    expect(rpc).toHaveBeenCalledWith('runtime.capture.process', { session_id: 'owned', schema_version: 1, capture_id: 'capture', expected_extraction_sequence: 1, source: 'original' })
    expect(view.getByLabelText('Annotation').textContent).toContain('Retained annotation')
  })
  it('reviews exact multi-row old/new metadata, requires confirmation and commits one retained digest', async () => {
    const rpc = gateway(), view = render(<CaptureReviewPanel connected request={rpc as RuntimeRequest} sessionId="owned" />)
    await search(view); await select(view); await select(view, 'duplicate')
    fireEvent.change(view.getByLabelText('Filing project (blank clears): capture'), { target: { value: 'destination' } })
    fireEvent.change(view.getByLabelText('Consolidate into capture (blank undoes): duplicate'), { target: { value: 'capture' } })
    await preview(view)
    const shown = view.getByLabelText('Before → after')
    expect(shown.textContent).toContain('Filing: ∅ → destination'); expect(shown.textContent).toContain('Consolidation: ∅ → capture')
    expect(rpc.mock.calls.some(([m]) => m === 'runtime.capture.batch.commit')).toBe(false)
    expect(view.getByText(/Filing is a metadata label/)).toBeDefined()
    fireEvent.click(view.getByRole('button', { name: 'Review exact batch' }))
    expect(within(view.getByRole('dialog')).getByLabelText('Before → after').textContent).toBe(shown.textContent)
    fireEvent.click(view.getByRole('button', { name: 'Commit reviewed metadata' }))
    await view.findByText(/Metadata only; originals retained/)
    const input = rpc.mock.calls.find(([m]) => m === 'runtime.capture.batch.preview')![1] as CaptureBatchParams
    expect(rpc).toHaveBeenCalledWith('runtime.capture.batch.commit', { ...input, preview_digest: 'a'.repeat(64) }); expect(input.items).toHaveLength(2)
  })
  it('invalidates preview on target edits and represents explicit blank-target undo as null', async () => {
    const rpc = gateway(), view = render(<CaptureReviewPanel connected request={rpc as RuntimeRequest} sessionId="owned" />)
    await search(view); await select(view)
    fireEvent.change(view.getByLabelText('Filing project (blank clears): capture'), { target: { value: 'destination' } }); await preview(view)
    fireEvent.change(view.getByLabelText('Filing project (blank clears): capture'), { target: { value: '' } })
    expect(view.queryByRole('button', { name: 'Review exact batch' })).toBeNull()
    await preview(view); await commit(view); await view.findByText(/Metadata only; originals retained/)
    const input = rpc.mock.calls.find(([m]) => m === 'runtime.capture.batch.commit')![1] as CaptureBatchParams
    expect(input.items[0].filed_project_id).toBeNull(); expect(input.items[0].consolidated_into).toBeNull()
  })
  it('locks repeated commit and retains unknown identity across unmount with inspect-only recovery', async () => {
    const rpc = gateway(), base = rpc.getMockImplementation()!, pending = deferred<unknown>()
    rpc.mockImplementation((method, params) => method === 'runtime.capture.batch.commit' ? pending.promise : base(method, params))
    const view = render(<CaptureReviewPanel connected request={rpc as RuntimeRequest} sessionId="owned" />)
    await search(view); await select(view); await preview(view)
    fireEvent.click(view.getByRole('button', { name: 'Review exact batch' }))
    const button = view.getByRole('button', { name: 'Commit reviewed metadata' }); fireEvent.click(button); fireEvent.click(button)
    const input = rpc.mock.calls.find(([m]) => m === 'runtime.capture.batch.commit')![1] as CaptureBatchParams
    expect(rpc.mock.calls.filter(([m]) => m === 'runtime.capture.batch.commit')).toHaveLength(1)
    view.unmount(); await act(async () => pending.reject(new Error('connection lost')))
    const reopened = render(<CaptureReviewPanel connected request={rpc as RuntimeRequest} sessionId="owned" />)
    expect(reopened.getByText(/Unknown outcome/).textContent).toContain(input.batch_id)
    fireEvent.click(reopened.getByRole('button', { name: 'Inspect retained operation' })); await reopened.findByText(/Outcome still unknown/)
    expect(reopened.queryByRole('button', { name: 'Review exact batch' })).toBeNull()
    expect(rpc.mock.calls.filter(([m]) => m === 'runtime.capture.batch.commit')).toHaveLength(1)
  })
  it.each(['session', 'request', 'disconnect', 'unmount'] as const)('ignores late search results after %s changes', async mode => {
    const pending = deferred<unknown>(), rpc = vi.fn(() => pending.promise)
    const view = render(<CaptureReviewPanel connected request={rpc as RuntimeRequest} sessionId="A" />)
    fireEvent.change(view.getByLabelText('Project ID'), { target: { value: 'p' } }); fireEvent.change(view.getByLabelText('Search captures'), { target: { value: 'retained' } }); fireEvent.click(view.getByRole('button', { name: 'Search' }))

    if (mode === 'unmount') { view.unmount() } else { view.rerender(<CaptureReviewPanel connected={mode !== 'disconnect'} request={(mode === 'request' ? gateway() : rpc) as RuntimeRequest} sessionId={mode === 'session' ? 'B' : 'A'} />) }
    const result = await gateway()('runtime.capture.search', {}); await act(async () => pending.resolve(result))
    expect(view.queryByRole('button', { name: 'Inspect: capture' })).toBeNull()
  })
  it('refuses a changed preview and shows an error without enabling commit', async () => {
    const rpc = gateway(), base = rpc.getMockImplementation()!
    rpc.mockImplementation(async (method, params) => {
      const result = await base(method, params)

      return method === 'runtime.capture.batch.preview' ? { ...result as object, project_id: 'foreign' } : result
    })
    const view = render(<CaptureReviewPanel connected request={rpc as RuntimeRequest} sessionId="owned" />)
    await search(view); await select(view); fireEvent.click(view.getByRole('button', { name: 'Preview metadata changes' }))
    await waitFor(() => expect(view.getByRole('alert').textContent).toContain('exact batch'))
    expect(view.queryByRole('button', { name: 'Review exact batch' })).toBeNull()
  })
  it('offers only verified complete original bytes as an inert download and revokes on disconnect', async () => {
    vi.stubGlobal('crypto', webcrypto)
    const create = vi.fn(() => 'blob:verified-original'), revoke = vi.fn()
    vi.stubGlobal('URL', { createObjectURL: create, revokeObjectURL: revoke })
    const rpc = gateway(), base = rpc.getMockImplementation()!
    let correct = false
    rpc.mockImplementation((method, params) => method !== 'runtime.artifact.get' ? base(method, params) : Promise.resolve({ project_id: 'p', artifact_id: 'original', version: 2, mime: 'text/html', sha256: correct ? 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad' : '0'.repeat(64), size: 3, offset: 0, next_offset: 3, data_base64: 'YWJj', eof: true, preview_mode: 'download_only' }))
    const view = render(<CaptureReviewPanel connected request={rpc as RuntimeRequest} sessionId="owned" />)
    await search(view); await select(view)
    fireEvent.click(view.getByRole('button', { name: 'Download original' }))
    await waitFor(() => expect(view.getByRole('alert').textContent).toContain('digest mismatch'))
    expect(view.queryByRole('link')).toBeNull(); expect(create).not.toHaveBeenCalled()
    correct = true; fireEvent.click(view.getByRole('button', { name: 'Download original' }))
    expect((await view.findByRole('link', { name: 'Save verified original (3 bytes)' })).getAttribute('href')).toBe('blob:verified-original')
    expect((create.mock.calls[0] as unknown as [Blob])[0].type).toBe('application/octet-stream')
    expect(rpc).toHaveBeenCalledWith('runtime.artifact.get', { session_id: 'owned', schema_version: 1, project_id: 'p', artifact_id: 'original', version: 2, offset: 0, limit: 65536 })
    view.rerender(<CaptureReviewPanel connected={false} request={rpc as RuntimeRequest} sessionId="owned" />)
    expect(view.queryByRole('link')).toBeNull(); expect(revoke).toHaveBeenCalledWith('blob:verified-original')
  })
  it('retains an unresolved batch through A→B→A and rejects the late success as a fresh approval', async () => {
    const rpc = gateway(), base = rpc.getMockImplementation()!, pending = deferred<unknown>()
    rpc.mockImplementation((method, params) => method === 'runtime.capture.batch.commit' ? pending.promise : base(method, params))
    const view = render(<CaptureReviewPanel connected request={rpc as RuntimeRequest} sessionId="A" />)
    await search(view); await select(view); await preview(view); await commit(view)
    const input = rpc.mock.calls.find(([method]) => method === 'runtime.capture.batch.commit')![1] as CaptureBatchParams
    view.rerender(<CaptureReviewPanel connected request={rpc as RuntimeRequest} sessionId="B" />)
    expect(view.queryByText(/Unknown outcome/)).toBeNull()
    view.rerender(<CaptureReviewPanel connected request={rpc as RuntimeRequest} sessionId="A" />)
    await act(async () => pending.resolve(await base('runtime.capture.batch.commit', input)))
    expect(view.getByText(/Unknown outcome/).textContent).toContain(input.batch_id)
    expect(view.queryByRole('button', { name: 'Review exact batch' })).toBeNull()
    expect(view.queryByText(/acknowledged/)).toBeNull()
  })
})
