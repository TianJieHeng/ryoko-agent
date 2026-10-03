import { expect, it, vi } from 'vitest'

import type { OpportunityCandidate } from './gateway-contract.generated.js'
import type { RuntimeRequest } from './runtime-control.js'
import { OpportunityReviewSession } from './runtime-opportunities.js'

const candidate = (changes: Partial<OpportunityCandidate> = {}): OpportunityCandidate => ({ candidate_id: 'candidate', project_id: 'project', authorized_project_refs: ['project'], kind: 'stale_artifact', title: 'Review retained draft', evidence_refs: [{ store: 'runtime_artifact_versions', project_id: 'project', record_id: 'artifact', version: 1, revision: null, sha256: 'a'.repeat(64), detail: 'Retained version' }], evidence_digest: 'b'.repeat(64), suggested_action: { kind: 'review_artifact', target_id: 'artifact', target_version: 1, description: 'Review existing artifact' }, benefit: 'Check stale source', effort: 'Review one draft', confidence: { level: 'deterministic_rule_match', explanation: 'Explicit bounded rule' }, disposition: 'proposed', revision: 1, changed_source_reason: null, evidence_current: true, created_at: 1, updated_at: 1, execution_authorized: false, ...changes })
const listing = (item = candidate()) => ({ candidates: [item], project_ids: ['project'], limit: 20, result_limit_reached: false, complete: false })
it('loads retained candidates without automatic discovery and records exact CAS choices without execution', async () => {
  const item = candidate()
  const request = vi.fn(async (method: string) => method.endsWith('.list') ? listing(item) : { candidate: { ...item, revision: 2, disposition: 'accepted' }, tasks_created: false, execution_authorized: false, next_step: 'open_existing_review_control' }) as RuntimeRequest
  const session = new OpportunityReviewSession(request, 'owned', ['project'], () => 'stable-choice')
  expect(request).not.toHaveBeenCalled()
  await session.load(); await session.choose(session.getState().candidates[0], 'accepted')
  expect(request).toHaveBeenLastCalledWith('runtime.opportunity.disposition', { session_id: 'owned', schema_version: 1, project_id: 'project', candidate_id: 'candidate', request_id: 'stable-choice', expected_revision: 1, expected_evidence_digest: 'b'.repeat(64), disposition: 'accepted' })
  expect(session.getState().pending).toBeNull()
  expect(vi.mocked(request).mock.calls.map(([method]) => method)).toEqual(['runtime.opportunity.list', 'runtime.opportunity.disposition'])
})
it('requires explicit bounded selected-project discovery and blocks duplicate/unknown discovery', async () => {
  let reject!: (error: Error) => void
  const request = vi.fn(() => new Promise((_, fail) => { reject = fail })) as RuntimeRequest
  const session = new OpportunityReviewSession(request, 'owned', ['project'], () => 'original')
  const first = session.load(true); await session.load(true)
  expect(request).toHaveBeenCalledExactlyOnceWith('runtime.opportunity.discover', expect.objectContaining({ project_ids: ['project'], request_id: 'original', limit: 20, scan_limit_per_source: 20 }))
  reject(new Error('reply lost')); await first; await session.load(true)
  expect(request).toHaveBeenCalledTimes(1); expect(session.getState().pending?.requestId).toBe('original')
})
it('stale candidates cannot be saved or accepted but may be dismissed', async () => {
  const request = vi.fn(async () => listing(candidate({ evidence_current: false }))) as RuntimeRequest
  const session = new OpportunityReviewSession(request, 'owned', ['project'])
  await session.load(); const item = session.getState().candidates[0]
  await session.choose(item, 'saved'); await session.choose(item, 'accepted')
  expect(request).toHaveBeenCalledTimes(1); expect(session.getState().error).toBe('stale')
})
it('rejects foreign scope or unexpected execution authority', async () => {
  const request = vi.fn(async () => listing(candidate({ project_id: 'foreign' }))) as RuntimeRequest
  const session = new OpportunityReviewSession(request, 'owned', ['project'])
  await session.load(); expect(session.getState().candidates).toEqual([]); expect(session.getState().error).toBe('unavailable')
})
it('does not apply a late candidate response after scope retirement', async () => {
  let resolve!: (value: unknown) => void
  const request = vi.fn(() => new Promise(done => { resolve = done })) as RuntimeRequest
  const session = new OpportunityReviewSession(request, 'owned', ['project'])
  const pending = session.load(); session.close(); resolve(listing()); await pending
  expect(session.getState().candidates).toEqual([])
})
it('retains unknown choice and allows only read-only inspection, not another choice', async () => {
  const request = vi.fn(async (method: string) => { if (method.endsWith('.list')) { return listing() } throw new Error('lost') }) as RuntimeRequest
  const session = new OpportunityReviewSession(request, 'owned', ['project'], () => 'stable')
  await session.load(); const item = session.getState().candidates[0]
  await session.choose(item, 'dismissed'); await session.choose(item, 'accepted')
  expect(request).toHaveBeenCalledTimes(2); expect(session.getState().pending).toEqual({ requestId: 'stable', candidateId: 'candidate', disposition: 'dismissed' })
  await session.load(); expect(session.getState().pending?.requestId).toBe('stable')
})
