import { describe, expect, it, vi } from 'vitest'

import type { RuntimeRequest } from './runtime-control.js'
import { runCommitmentCommand, runCorrespondenceCommand, summarizeCommitmentReview, summarizeCorrespondence } from './runtime-followthrough.js'

const digest = 'a'.repeat(64), ref = { artifact_id: 'source', version: 2, sha256: digest }
const rpcRecord = (value: unknown) => ({ record_json: JSON.stringify(value) })
const candidate = { project_id: 'p', candidate_id: 'candidate', commitment_id: null, revision: 1, accepted: false, owner: null, outcome: 'Please review the report', source_refs: [ref], date_uncertainty: 'unresolved' }
const commitment = (overrides = {}) => ({ ...candidate, accepted: true, commitment_id: 'accepted', revision: 1, state: 'ready', owner: 'person@example.test', due_or_check_at: null, evidence_refs: [ref], superseded_by: null, ...overrides })
const acceptance = { project_id: 'p', candidate_id: 'candidate', command_id: 'accept', expected_revision: 1, owner: 'person@example.test', outcome: 'Review the report', due_or_check_at: { at: '2026-11-01T01:30:00-05:00', timezone: 'America/New_York', kind: 'check' } }
const draft = (overrides = {}) => ({ correspondence_id: 'correspondence_canonical', project_id: 'p', recipients: ['person@example.test'], content: 'I will review the report.', source_refs: [ref], state: 'draft', sent: false, effect_receipt: null, possible_new_promise: true, promise_review_required: true, commitments_created: false, input_digest: digest, ...overrides })
const draftInput = { project_id: 'p', correspondence_id: 'user-selected', command_id: 'draft-1', recipients: ['person@example.test'], content: 'I will review the report.', source_refs_json: JSON.stringify([ref]) }

describe('reviewed obligations', () => {
  it('shows a nonbinding candidate and rejects an unreviewed malformed decline', async () => {
    const request = vi.fn(async () => rpcRecord(candidate)) as unknown as RuntimeRequest
    const result = await runCommitmentCommand('candidate p candidate', request, 'owned')
    expect(result).toContain('candidate; not accepted')
    expect(result).toContain('Owner: unresolved')
    expect(result).toContain('Due/check date unresolved')
    vi.mocked(request).mockClear()
    expect(await runCommitmentCommand('decline p candidate', request, 'owned')).toContain('Invalid input')
    expect(request).not.toHaveBeenCalled()
  })
  it('accepts only the explicit action with exact candidate revision and resolved time', async () => {
    const request = vi.fn(async () => rpcRecord(commitment({ outcome: acceptance.outcome, due_or_check_at: acceptance.due_or_check_at }))) as unknown as RuntimeRequest
    const result = await runCommitmentCommand(`accept ${JSON.stringify(acceptance)}`, request, 'owned')
    expect(request).toHaveBeenCalledExactlyOnceWith('runtime.commitment.accept', { ...acceptance, session_id: 'owned', schema_version: 1 })
    expect(result).toContain('Accepted commitment accepted: ready')
    expect(result).toContain('2026-11-01T01:30:00-05:00 (America/New_York)')
  })
  it('rejects ambiguous due dates and caller-owned identity before RPC', async () => {
    const request = vi.fn() as unknown as RuntimeRequest

    for (const value of [{ ...acceptance, session_id: 'other' }, { ...acceptance, due_or_check_at: { ...acceptance.due_or_check_at, at: 'tomorrow' } }, { ...acceptance, due_or_check_at: { ...acceptance.due_or_check_at, identity: 'other' } }]) {
      expect(await runCommitmentCommand(`accept ${JSON.stringify(value)}`, request, 'owned')).toContain('Invalid input')
    }

    expect(request).not.toHaveBeenCalled()
  })
  it.each(['commitment_revision_conflict', 'commitment_already_accepted', 'commitment_timezone_mismatch'])('does not retry conflicting acceptance (%s)', async code => {
    const request = vi.fn(async () => { throw { data: { code } } }) as RuntimeRequest
    const result = await runCommitmentCommand(`accept ${JSON.stringify(acceptance)}`, request, 'owned')
    expect(request).toHaveBeenCalledTimes(1)
    expect(result).toContain('No automatic retry')
    expect(result).not.toContain('Accepted commitment')
  })
  it('shows duplicate acceptance as one returned authoritative obligation', async () => {
    const request = vi.fn(async () => rpcRecord(commitment())) as unknown as RuntimeRequest
    const command = `accept ${JSON.stringify(acceptance)}`
    expect(await runCommitmentCommand(command, request, 'owned')).toContain('Accepted commitment accepted')
    expect(await runCommitmentCommand(command, request, 'owned')).toContain('Accepted commitment accepted')
    expect(vi.mocked(request).mock.calls[0]).toEqual(vi.mocked(request).mock.calls[1])
  })
  it('keeps weekly review read-only and terminal obligations out of active work', async () => {
    const review = { source: 'accepted_commitment_registry', ready: [commitment()], waiting: [commitment({ commitment_id: 'wait', state: 'waiting' })], mutated: false, automatic_followups_authorized: false, possibly_truncated: false }
    const request = vi.fn(async () => rpcRecord(review)) as unknown as RuntimeRequest
    const result = await runCommitmentCommand('waiting p', request, 'owned')
    expect(result).toContain('Accepted commitment wait: waiting')
    expect(result).not.toContain('Accepted commitment accepted: ready')
    expect(request).toHaveBeenCalledExactlyOnceWith('runtime.commitment.review', { session_id: 'owned', schema_version: 1, project_id: 'p' })
    expect(() => summarizeCommitmentReview({ ...review, ready: [commitment({ state: 'done' })] })).toThrow('terminal')
    vi.mocked(request).mockClear()
    expect(await runCommitmentCommand('reopen p accepted', request, 'owned')).toContain('cannot reopen')
    expect(request).not.toHaveBeenCalled()
  })
  it('requires immutable evidence and exact revision to close an obligation', async () => {
    const request = vi.fn(async () => rpcRecord(commitment({ state: 'done', revision: 2 }))) as unknown as RuntimeRequest
    const input = { project_id: 'p', commitment_id: 'accepted', command_id: 'close', expected_revision: 1, state: 'done', evidence_ref_json: JSON.stringify(ref) }
    const result = await runCommitmentCommand(`update ${JSON.stringify(input)}`, request, 'owned')
    expect(result).toContain('done; revision 2')
    expect(request).toHaveBeenCalledExactlyOnceWith('runtime.commitment.update', { ...input, session_id: 'owned', schema_version: 1, superseded_by: null, due_or_check_at: null })
    vi.mocked(request).mockClear()
    expect(await runCommitmentCommand(`update ${JSON.stringify({ ...input, evidence_ref_json: '{}' })}`, request, 'owned')).toContain('Invalid input')
    expect(request).not.toHaveBeenCalled()
  })
  it('reports source failure instead of an empty inbox or no open work', async () => {
    const request = vi.fn(async () => { throw { data: { code: 'inbox_selection_missing' } } }) as RuntimeRequest
    const input = { project_id: 'p', command_id: 'inbox', source_ref_json: JSON.stringify(ref), selection_json: JSON.stringify({ thread_ids: ['thread'] }) }
    const result = await runCommitmentCommand(`inbox-prepare ${JSON.stringify(input)}`, request, 'owned')
    expect(result).toContain('no empty-inbox conclusion')
    expect(result).not.toContain('No accepted')
    expect(request).toHaveBeenCalledTimes(1)
  })
  it('retains bounded snapshot selection and candidate uncertainty', async () => {
    const request = vi.fn(async () => rpcRecord({ preview_id: 'preview', live_mailbox_checked: false, source_content_is_authority: false, candidate_ids: ['candidate'], groups: [{ thread_id: 'thread', messages: [{ message_id: 'message', classification: 'request', candidate_id: 'candidate' }] }] })) as unknown as RuntimeRequest
    const input = { project_id: 'p', command_id: 'inbox', source_ref_json: JSON.stringify(ref), selection_json: JSON.stringify({ thread_ids: ['thread'] }) }
    const result = await runCommitmentCommand(`inbox-prepare ${JSON.stringify(input)}`, request, 'owned')
    expect(result).toContain('1 unaccepted candidate')
    expect(result).toContain('dates and owners remain unresolved')
    expect(result).toContain('Live mailbox was not checked')
    expect(request).toHaveBeenCalledExactlyOnceWith('runtime.inbox.prepare', { ...input, session_id: 'owned', schema_version: 1 })
  })
})

describe('immutable correspondence and honest delivery', () => {
  it('creates a reviewable exact draft with its returned canonical ID and never sends', async () => {
    const request = vi.fn(async () => rpcRecord(draft())) as unknown as RuntimeRequest
    const result = await runCorrespondenceCommand(`draft ${JSON.stringify(draftInput)}`, request, 'owned')
    expect(result).toContain('correspondence_canonical: draft; not sent')
    expect(result).toContain('Exact recipients: person@example.test')
    expect(result).toContain('Exact content:\nI will review the report.')
    expect(result).toContain('possible new promise detected')
    expect(request).toHaveBeenCalledExactlyOnceWith('runtime.correspondence.draft', { ...draftInput, session_id: 'owned', schema_version: 1 })
  })
  it('keeps identical draft retry identity and defers deduplication to the backend', async () => {
    const request = vi.fn(async () => rpcRecord(draft())) as unknown as RuntimeRequest
    const command = `draft ${JSON.stringify(draftInput)}`
    expect(await runCorrespondenceCommand(command, request, 'owned')).toContain('correspondence_canonical')
    expect(await runCorrespondenceCommand(command, request, 'owned')).toContain('correspondence_canonical')
    expect(vi.mocked(request).mock.calls[0]).toEqual(vi.mocked(request).mock.calls[1])
    expect(vi.mocked(request).mock.calls.every(([method]) => method === 'runtime.correspondence.draft')).toBe(true)
  })
  it('does not reuse a stale draft approval after content or recipient changes', async () => {
    const request = vi.fn(async () => { throw { data: { code: 'correspondence_immutable' } } }) as RuntimeRequest
    const result = await runCorrespondenceCommand(`draft ${JSON.stringify({ ...draftInput, content: 'Different content' })}`, request, 'owned')
    expect(result).toContain('new correspondence identifier and review')
    expect(request).toHaveBeenCalledTimes(1)
  })
  it('records send proof but never upgrades unknown provider delivery to delivered', async () => {
    const proof = { effect_id: 'effect', evidence_id: 'evidence', receipt: { provider_response: 'private untrusted details', delivered: true } }
    const request = vi.fn(async () => rpcRecord(draft({ state: 'sent_receipt_recorded', sent: true, effect_receipt: proof }))) as unknown as RuntimeRequest
    const input = { project_id: 'p', correspondence_id: 'correspondence_canonical', command_id: 'receipt', effect_id: 'effect' }
    const result = await runCorrespondenceCommand(`receipt ${JSON.stringify(input)}`, request, 'owned')
    expect(result).toContain('confirmed send receipt recorded; delivery unknown')
    expect(result).not.toContain('private untrusted details')
    expect(request).toHaveBeenCalledExactlyOnceWith('runtime.correspondence.receipt', { ...input, session_id: 'owned', schema_version: 1 })
  })
  it('leaves lost or mismatched send receipts unconfirmed without retrying', async () => {
    const request = vi.fn(async () => { throw { data: { code: 'correspondence_receipt_missing' } } }) as RuntimeRequest
    const input = { project_id: 'p', correspondence_id: 'correspondence_canonical', command_id: 'receipt', effect_id: 'effect' }
    const result = await runCorrespondenceCommand(`receipt ${JSON.stringify(input)}`, request, 'owned')
    expect(result).toContain('send and delivery remain unconfirmed')
    expect(request).toHaveBeenCalledTimes(1)
    expect(() => summarizeCorrespondence(draft({ state: 'sent_receipt_recorded', sent: false }))).toThrow('inconsistent')
  })
  it('offers no send, automatic retry or implicit authority from nested source JSON', async () => {
    const request = vi.fn() as unknown as RuntimeRequest
    expect(await runCorrespondenceCommand('send p correspondence_canonical', request, 'owned')).toContain('no send or delivery retry')
    expect(await runCorrespondenceCommand(`draft ${JSON.stringify({ ...draftInput, source_refs_json: JSON.stringify([{ ...ref, identity: 'other' }]) })}`, request, 'owned')).toContain('runtime-owned')
    expect(request).not.toHaveBeenCalled()
  })
})

it('durably declines only the exact candidate revision without accepting an obligation', async () => {
  const request = vi.fn(async () => rpcRecord({ ...candidate, revision: 2, review_state: 'declined' })) as RuntimeRequest
  const input = { project_id: 'p', candidate_id: 'candidate', command_id: 'decline-1', expected_revision: 1, reason: 'Not my responsibility' }
  expect(await runCommitmentCommand(`decline ${JSON.stringify(input)}`, request, 'owned')).toContain('No obligation was created')
  expect(request).toHaveBeenCalledExactlyOnceWith('runtime.commitment.decline', { ...input, session_id: 'owned', schema_version: 1 })
})
