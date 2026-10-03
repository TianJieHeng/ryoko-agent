import { describe, expect, it, vi } from 'vitest'

import type { CaptureBatchParams, CaptureBatchPreviewResult, CaptureInspectResult } from './gateway-contract.generated.js'
import { captureBatchInput, captureReviewSession, CaptureReviewSession, captureSearchInput, runCaptureReviewCommand, verifyCaptureCommit, verifyCapturePreview } from './runtime-capture-review.js'
import type { RuntimeRequest } from './runtime-control.js'

const record = (id = 'capture', revision = 0): CaptureInspectResult => ({
  capture: { capture_id: id, project_id: 'p', original_ref: { artifact_id: 'original', version: 2 }, acquired_at: 123, annotation: 'A retained note', source_url: null, suggested_project_id: null, filed_project_id: null, revision, extractions: [] },
  processing: { status: 'not_indexed', method: 'none', source_ref: null, extraction_sequence: 0, indexed_characters: 0, truncated: false, failure_code: null, indexed_at: null },
  consolidated_into: null, filing_history: [], consolidation_history: []
})

const draft = () => ({ before: record(), item: { capture_id: 'capture', expected_revision: 0, filed_project_id: 'destination', consolidated_into: null } })

function preview(input: CaptureBatchParams): CaptureBatchPreviewResult {
  return { batch_id: input.batch_id, project_id: input.project_id, preview_digest: 'a'.repeat(64), scope: 'capture_metadata_only', originals_preserved: true,
    items: input.items.map(item => ({ ...item, previous_filed_project_id: null, previous_consolidated_into: null, original_sha256: 'b'.repeat(64) })) }
}

function gateway() {
  return vi.fn(async (method: string, params: unknown): Promise<unknown> => {
    const input = params as CaptureBatchParams

    if (method === 'runtime.capture.inspect') { return record() }

    if (method === 'runtime.capture.batch.preview') { return preview(input) }

    if (method === 'runtime.capture.batch.commit') { return { ...preview(input), items: input.items.map(item => ({ ...item, revision: item.expected_revision + 1 })), replayed: false } }

    return { matches: [{ ...record(), score: 1, matched_terms: ['note'], excerpt: 'retained note' }], scanned: 1, search_mode: 'lexical_fuzzy', truncated: false, complete: false, limitations: [] }
  })
}

async function select(session: CaptureReviewSession) {
  session.attach(true); session.edit({ project: 'p', query: 'note' }); await session.inspect('capture'); session.select(); session.change('capture', { filed_project_id: 'destination' })
}

function deferred<T>() { let resolve!: (value: T) => void, reject!: (error: Error) => void; const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no });

 return { promise, resolve, reject } }

describe('capture review contract', () => {
  it('constructs exact owned inputs and enforces search, batch and retained history bounds', () => {
    expect(captureSearchInput('owned', 'p', 'note', 50, 500)).toEqual({ session_id: 'owned', schema_version: 1, project_id: 'p', query: 'note', limit: 50, scan_limit: 500 })

    for (const [limit, scan] of [[51, 1], [1, 501], [0, 100], [1, 1.5]]) { expect(() => captureSearchInput('owned', 'p', 'note', limit, scan)).toThrow() }
    expect(() => captureSearchInput('owned', 'p', 'x'.repeat(513))).toThrow()
    expect(() => captureBatchInput('owned', 'p', 'batch', Array.from({ length: 26 }, draft))).toThrow()
    expect(() => captureBatchInput('owned', 'p', 'batch', [draft(), draft()])).toThrow()
    expect(() => captureBatchInput('owned', 'foreign', 'batch', [draft()])).toThrow(/scope/)
    expect(captureBatchInput('owned', 'p', 'batch', [draft()]).items).toEqual([draft().item])
  })
  it('refuses altered targets, previous values, duplicates or receipt digests', () => {
    const drafts = [draft()], input = captureBatchInput('owned', 'p', 'batch', drafts), value = preview(input)
    verifyCapturePreview(value, input, drafts)

    for (const field of ['filed_project_id', 'previous_filed_project_id', 'consolidated_into'] as const) {
      expect(() => verifyCapturePreview({ ...value, items: [{ ...value.items[0], [field]: 'unexpected' }] }, input, drafts)).toThrow(/Preview changed/)
    }

    expect(() => verifyCapturePreview({ ...value, project_id: 'other' }, input, drafts)).toThrow()
    expect(() => verifyCapturePreview({ ...value, items: [...value.items, value.items[0]] }, input, drafts)).toThrow()
    const committed = { ...value, replayed: false, items: [{ capture_id: 'capture', revision: 1, filed_project_id: 'destination', consolidated_into: null }] }
    verifyCaptureCommit(committed, { ...input, preview_digest: value.preview_digest })
    expect(() => verifyCaptureCommit({ ...committed, preview_digest: 'c'.repeat(64) }, { ...input, preview_digest: value.preview_digest })).toThrow()
  })
  it('locks repeated mutations, binds exact preview identity and invalidates on edit', async () => {
    const rpc = gateway(), session = new CaptureReviewSession(rpc as RuntimeRequest, 'owned', () => 'stable-batch')
    await select(session); await session.preview()
    const review = session.getState().review!
    session.change('capture', { consolidated_into: 'target' }); await session.commit(review)
    expect(rpc.mock.calls.some(([method]) => method === 'runtime.capture.batch.commit')).toBe(false)
    await session.preview()
    const pending = deferred<unknown>(), base = rpc.getMockImplementation()!
    rpc.mockImplementation((method, params) => method === 'runtime.capture.batch.commit' ? pending.promise : base(method, params))
    const current = session.getState().review!, flight = session.commit(current)
    await session.commit(current)
    expect(rpc.mock.calls.filter(([method]) => method === 'runtime.capture.batch.commit')).toHaveLength(1)
    expect(rpc).toHaveBeenCalledWith('runtime.capture.batch.commit', { ...current.params, preview_digest: current.preview.preview_digest })
    pending.reject(new Error('lost response')); await flight
    expect(session.getState().pending?.batch).toBe(current)
    await session.reconcile()
    expect(session.getState().pending?.batch).toBe(current)
    expect(session.getState().message).toContain('still unknown')
  })
  it('retains unknown batches across detach and clears only after exact current-state inspection', async () => {
    const rpc = gateway(), session = captureReviewSession(rpc as RuntimeRequest, 'A')
    await select(session); await session.preview()
    const review = session.getState().review!, base = rpc.getMockImplementation()!
    rpc.mockImplementation((method, params) => method === 'runtime.capture.batch.commit' ? Promise.reject(new Error('transport lost')) : base(method, params))
    await session.commit(review); session.attach(false)(); session.attach(true)
    expect(captureReviewSession(rpc as RuntimeRequest, 'B').getState().pending).toBeNull()
    expect(captureReviewSession(rpc as RuntimeRequest, 'A').getState().pending?.batch).toBe(review)
    rpc.mockImplementation(async () => ({ ...record('capture', 1), capture: { ...record('capture', 1).capture, filed_project_id: 'destination' } }))
    await session.reconcile()
    expect(session.getState().pending).toBeNull(); expect(session.getState().message).toContain('does not establish which request')
  })
  it('fences stale scope and unmount reads without assuming mutation cancellation', async () => {
    const pending = deferred<unknown>(), rpc = vi.fn(() => pending.promise), session = new CaptureReviewSession(rpc as RuntimeRequest, 'owned')
    const detach = session.attach(true); session.edit({ project: 'p', query: 'note' })
    const flight = session.inspect('capture'); detach(); session.attach(true)
    pending.resolve(record()); await flight
    expect(session.getState().inspected).toBeNull()
  })
  it('uses the latest extraction sequence rather than stale index sequence and refuses unavailable supplied text', async () => {
    const rpc = gateway(), base = rpc.getMockImplementation()!, value = record()
    value.capture.extractions = [{ sequence: 5, status: 'failed', extracted_ref: null, failure_code: 'unsupported', created_at: 123 }]
    rpc.mockImplementation((method, params) => method === 'runtime.capture.inspect' ? Promise.resolve(value) : base(method, params))
    const session = new CaptureReviewSession(rpc as RuntimeRequest, 'owned'); session.attach(true); session.edit({ project: 'p' }); await session.inspect('capture')
    await session.process('latest_extraction')
    expect(rpc.mock.calls.some(([method]) => method === 'runtime.capture.process')).toBe(false)
    rpc.mockImplementation((method, params) => method === 'runtime.capture.process' ? Promise.reject(new Error('lost response')) : base(method, params))
    await session.process('original')
    expect(rpc).toHaveBeenCalledWith('runtime.capture.process', { session_id: 'owned', schema_version: 1, capture_id: 'capture', expected_extraction_sequence: 5, source: 'original' })
    expect(session.getState().pending?.process?.expected_extraction_sequence).toBe(5)
  })
  it('routes only supported advanced methods with owned identity and honest summaries', async () => {
    const rpc = gateway(), found = await runCaptureReviewCommand('search p retained note', rpc as RuntimeRequest, 'owned')
    expect(found).toContain('complete: false'); expect(found).toContain('original original@2')
    expect(rpc).toHaveBeenCalledWith('runtime.capture.search', expect.objectContaining({ session_id: 'owned', query: 'retained note' }))
    const calls = rpc.mock.calls.length
    expect(await runCaptureReviewCommand('__proto__ p', rpc as RuntimeRequest, 'owned')).toContain('Capture review:')
    expect(await runCaptureReviewCommand('process capture 33 original', rpc as RuntimeRequest, 'owned')).toContain('outside')
    expect(rpc).toHaveBeenCalledTimes(calls)
  })
  it('blocks uncertain advanced processing until read-only inspection advances the exact capture sequence', async () => {
    const rpc = gateway(), base = rpc.getMockImplementation()!
    rpc.mockImplementation((method, params) => method === 'runtime.capture.process' ? Promise.reject(new Error('lost response')) : base(method, params))
    await runCaptureReviewCommand('process capture 0 original', rpc as RuntimeRequest, 'owned')
    expect(await runCaptureReviewCommand('process capture 0 original', rpc as RuntimeRequest, 'owned')).toContain('Retained processing')
    expect(rpc.mock.calls.filter(([m]) => m === 'runtime.capture.process')).toHaveLength(1)
    expect(await runCaptureReviewCommand('inspect capture', rpc as RuntimeRequest, 'owned')).toContain('remains unknown')
    const value = record(); value.capture.extractions = [{ sequence: 1, status: 'unavailable', extracted_ref: null, failure_code: 'unsupported', created_at: 125 }]
    rpc.mockImplementation((method, params) => method === 'runtime.capture.inspect' ? Promise.resolve(value) : base(method, params))
    expect(await runCaptureReviewCommand('inspect capture', rpc as RuntimeRequest, 'owned')).not.toContain('remains unknown')
  })
  it('keeps a current revision conflict recoverable and refreshes a selected baseline explicitly', async () => {
    const rpc = gateway(), session = new CaptureReviewSession(rpc as RuntimeRequest, 'owned')
    await select(session); await session.preview()
    const base = rpc.getMockImplementation()!, rejected = Object.assign(new Error('Capture filing changed'), { data: { code: 'revision_conflict' } })
    rpc.mockImplementation((method, params) => method === 'runtime.capture.batch.commit' ? Promise.reject(rejected) : base(method, params))
    await session.commit(session.getState().review!)
    expect(session.getState().pending).toBeNull(); expect(session.getState().error).toContain('revision_conflict')
    rpc.mockImplementation((method, params) => method === 'runtime.capture.inspect' ? Promise.resolve(record('capture', 3)) : base(method, params))
    await session.inspect('capture'); session.select()
    expect(session.getState().drafts[0].item.expected_revision).toBe(3)
    expect(session.getState().drafts[0].item.filed_project_id).toBe('destination')
  })
  it('aborts continued original download reads when its exact scope detaches', async () => {
    const rpc = gateway(), base = rpc.getMockImplementation()!, pending = deferred<unknown>()
    rpc.mockImplementation((method, params) => method === 'runtime.artifact.get' ? pending.promise : base(method, params))
    const session = new CaptureReviewSession(rpc as RuntimeRequest, 'owned'), detach = session.attach(true)
    session.edit({ project: 'p' }); await session.inspect('capture')
    const flight = session.downloadOriginal(); detach(); pending.resolve({}); await flight
    expect(rpc.mock.calls.filter(([m]) => m === 'runtime.artifact.get')).toHaveLength(1)
    expect(session.getState().download).toBeNull()
  })
})
