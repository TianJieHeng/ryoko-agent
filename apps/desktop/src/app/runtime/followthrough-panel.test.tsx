// @vitest-environment jsdom
import type { RuntimeRequest } from '@hermes/shared/runtime-control'
import { act, cleanup, fireEvent, render, waitFor } from '@testing-library/react'
import type { ComponentProps, ReactNode } from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

const confirmation = vi.hoisted(() => vi.fn(async () => true))
vi.mock('@/store/confirm', () => ({ confirm: confirmation }))
vi.mock('@/i18n', () => ({ useI18n: () => ({ locale: 'en', t: { common: { error: 'Error', confirm: 'Confirm', cancel: 'Cancel', refresh: 'Refresh' } } }) }))
vi.mock('@/components/ui/button', () => ({ Button: ({ size: _size, variant: _variant, ...props }: ComponentProps<'button'> & { size?: string; variant?: string }) => <button {...props} /> }))
vi.mock('@/components/ui/input', () => ({ Input: (props: ComponentProps<'input'>) => <input {...props} /> }))
vi.mock('@/components/ui/textarea', () => ({ Textarea: (props: ComponentProps<'textarea'>) => <textarea {...props} /> }))
vi.mock('@/components/ui/loader', () => ({ Loader: ({ label }: { label: string }) => <div role="status">{label}</div> }))
vi.mock('@/components/ui/error-state', () => ({ ErrorState: ({ description }: { description: ReactNode }) => <div role="alert">{description}</div> }))
import { FollowthroughPanel } from './followthrough-panel'

const digest = 'a'.repeat(64), ref = { artifact_id: 'source', version: 1, sha256: digest }
const candidate = { project_id: 'p', candidate_id: 'candidate', revision: 3, commitment_id: null, accepted: false, owner: null, outcome: 'Please check this report', source_refs: [ref], date_uncertainty: 'unresolved' }
const rpc = (value: unknown) => ({ record_json: JSON.stringify(value) })

async function inspect(view: ReturnType<typeof render>) {
  fireEvent.change(view.getByLabelText('Project ID'), { target: { value: 'p' } }); fireEvent.change(view.getByLabelText('Candidate ID'), { target: { value: 'candidate' } })
  fireEvent.click(view.getByText('Inspect candidate'))
  await waitFor(() => expect(view.getByLabelText('Owner identity')).toBeTruthy())
}

afterEach(cleanup)
beforeEach(() => { confirmation.mockReset().mockResolvedValue(true) })

it('requires owner correction and confirmation before creating an obligation', async () => {
  const request = vi.fn(async (method: string, params: Record<string, unknown>) => rpc(method === 'runtime.commitment.candidate' ? candidate : { ...candidate, ...params, commitment_id: 'accepted', accepted: true, state: 'ready', revision: 1, evidence_refs: [ref] })) as unknown as RuntimeRequest
  const view = render(<FollowthroughPanel connected request={request} sessionId="owned" />)
  await inspect(view)
  expect((view.getByText('Accept reviewed commitment') as HTMLButtonElement).disabled).toBe(true)
  fireEvent.change(view.getByLabelText('Owner identity'), { target: { value: 'owner@example.test' } }); fireEvent.change(view.getByLabelText('Exact outcome'), { target: { value: 'Review the exact report' } })
  fireEvent.click(view.getByText('Accept reviewed commitment'))
  await waitFor(() => expect(request).toHaveBeenCalledTimes(2))
  expect(confirmation).toHaveBeenCalledTimes(1)
  expect(vi.mocked(request).mock.calls[1]).toEqual(['runtime.commitment.accept', expect.objectContaining({ session_id: 'owned', project_id: 'p', candidate_id: 'candidate', expected_revision: 3, owner: 'owner@example.test', outcome: 'Review the exact report', due_or_check_at: null })])
})
it('leaves candidates unaccepted without a mutating RPC', async () => {
  const request = vi.fn(async () => rpc(candidate)) as unknown as RuntimeRequest
  const view = render(<FollowthroughPanel connected request={request} sessionId="owned" />)
  await inspect(view); fireEvent.click(view.getByText('Leave unaccepted'))
  expect(view.queryByText('Accept reviewed commitment')).toBeNull()
  expect(request).toHaveBeenCalledTimes(1)
})
it('does not accept after its confirmation context unmounts', async () => {
  let resolve!: (value: boolean) => void
  confirmation.mockImplementation(() => new Promise(value => { resolve = value }))
  const request = vi.fn(async () => rpc(candidate)) as unknown as RuntimeRequest
  const view = render(<FollowthroughPanel connected request={request} sessionId="old" />)
  await inspect(view); fireEvent.change(view.getByLabelText('Owner identity'), { target: { value: 'owner@example.test' } }); fireEvent.click(view.getByText('Accept reviewed commitment'))
  view.rerender(<FollowthroughPanel connected request={request} sessionId="new" />)
  await act(async () => resolve(true))
  expect(request).toHaveBeenCalledTimes(1)
  expect((view.getByLabelText('Project ID') as HTMLInputElement).value).toBe('')
})
it('creates only the exact sourced draft and keeps the canonical inspection ID', async () => {
  const request = vi.fn(async () => rpc({ correspondence_id: 'canonical', project_id: 'p', recipients: ['recipient@example.test'], content: 'Draft only', source_refs: [ref], state: 'draft', sent: false, effect_receipt: null, possible_new_promise: false, promise_review_required: true, commitments_created: false, input_digest: digest })) as unknown as RuntimeRequest
  const view = render(<FollowthroughPanel connected request={request} sessionId="owned" />)

  for (const [label, value] of [['Project ID', 'p'], ['New draft identifier', 'draft'], ['Recipient identities (one per line)', 'recipient@example.test'], ['Exact draft content', 'Draft only'], ['Source artifact ID', 'source'], ['Source SHA-256', digest]]) {fireEvent.change(view.getByLabelText(label), { target: { value } })}
  fireEvent.click(view.getByText('Create draft only'))
  await waitFor(() => expect((view.getByLabelText('Returned correspondence ID') as HTMLInputElement).value).toBe('canonical'))
  expect(request).toHaveBeenCalledExactlyOnceWith('runtime.correspondence.draft', expect.objectContaining({ session_id: 'owned', project_id: 'p', correspondence_id: 'draft', recipients: ['recipient@example.test'], content: 'Draft only', source_refs_json: JSON.stringify([ref]) }))
  expect(confirmation).not.toHaveBeenCalled(); expect(view.queryByText('Send')).toBeNull()
  expect(view.getByText(/draft; not sent/u)).toBeTruthy()
})
it('discards late reads and keeps a failed source distinct from an empty review', async () => {
  let resolve!: (value: unknown) => void
  const request = vi.fn(() => new Promise(value => { resolve = value })) as unknown as RuntimeRequest
  const view = render(<FollowthroughPanel connected request={request} sessionId="old" />)
  fireEvent.change(view.getByLabelText('Project ID'), { target: { value: 'p' } }); fireEvent.change(view.getByLabelText('Candidate ID'), { target: { value: 'candidate' } }); fireEvent.click(view.getByText('Inspect candidate'))
  view.rerender(<FollowthroughPanel connected request={request} sessionId="new" />)
  await act(async () => resolve(rpc(candidate)))
  expect(view.queryByLabelText('Owner identity')).toBeNull()
  const unavailable = vi.fn(async () => { throw new Error('source unavailable') }) as RuntimeRequest
  view.rerender(<FollowthroughPanel connected request={unavailable} sessionId="new" />)
  fireEvent.change(view.getByLabelText('Project ID'), { target: { value: 'p' } }); fireEvent.click(view.getByText('Review active commitments'))
  await waitFor(() => expect(view.getByText(/runtime request failed/u)).toBeTruthy())
  expect(view.queryByText('No accepted active items returned')).toBeNull(); expect(unavailable).toHaveBeenCalledTimes(1)
})

it('confirms a durable decline and never accepts the candidate', async () => {
  const request = vi.fn(async (method: string) => rpc(method === 'runtime.commitment.candidate' ? candidate : { ...candidate, revision: 4, review_state: 'declined' })) as RuntimeRequest
  const view = render(<FollowthroughPanel connected request={request} sessionId="owned" />)
  await inspect(view)
  fireEvent.change(view.getByLabelText('Reason for declining'), { target: { value: 'Outside my responsibility' } })
  fireEvent.click(view.getByText('Decline reviewed candidate'))
  await waitFor(() => expect(request).toHaveBeenCalledWith('runtime.commitment.decline', expect.objectContaining({ candidate_id: 'candidate', expected_revision: 3, reason: 'Outside my responsibility', session_id: 'owned' })))
  expect(confirmation).toHaveBeenCalledOnce()
  expect(vi.mocked(request).mock.calls.some(([method]) => method === 'runtime.commitment.accept')).toBe(false)
})
