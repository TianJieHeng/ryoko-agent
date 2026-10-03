// @vitest-environment jsdom
import { act, cleanup, fireEvent, render } from '@testing-library/react'
import { StrictMode } from 'react'
import { afterEach, expect, it, vi } from 'vitest'

import type { RuntimeRequest } from '../../../../shared/src/runtime-control'

import { opportunityCopy } from './opportunity-copy'
vi.mock('@/i18n', () => ({ useI18n: () => ({ locale: 'en', t: { common: { confirm: 'Confirm', cancel: 'Cancel', loading: 'Loading', done: 'Done', close: 'Close' } } }) }))
import { OpportunityPanel } from './opportunity-panel'
const item = { candidate_id: 'candidate', project_id: 'project', authorized_project_refs: ['project'], kind: 'stale_artifact', title: '<script>source text</script>', evidence_refs: [], evidence_digest: 'a'.repeat(64), suggested_action: { kind: 'review_artifact', target_id: 'artifact', target_version: 1, description: 'Review draft' }, benefit: 'Check source', effort: 'One draft', confidence: { level: 'deterministic_rule_match', explanation: 'Local rule' }, disposition: 'proposed', revision: 1, changed_source_reason: null, evidence_current: true, created_at: 1, updated_at: 1, execution_authorized: false }
const list = { candidates: [item], project_ids: ['project'], limit: 20, result_limit_reached: false, complete: false }
afterEach(cleanup)
const props = (request: RuntimeRequest) => ({ connected: true, request, sessionId: 'owned', connectionGeneration: 1 })

async function load(view: ReturnType<typeof render>) {
  fireEvent.change(view.getByLabelText(opportunityCopy.scope[0]), { target: { value: 'project' } })
  fireEvent.click(view.getByText(opportunityCopy.open[0]))
  fireEvent.click(view.getByText(opportunityCopy.retained[0]))
  await view.findByText(item.title)
}

it('uses explicit selection, escaped evidence and a separate real confirmation before a recorded choice', async () => {
  const request = vi.fn(async (method: string) => method.endsWith('.list') ? list : { candidate: { ...item, disposition: 'accepted', revision: 2 }, tasks_created: false, execution_authorized: false, next_step: 'open_existing_review_control' }) as RuntimeRequest
  const open = vi.fn(), view = render(<StrictMode><OpportunityPanel {...props(request)} onOpenReview={open} /></StrictMode>)
  expect(request).not.toHaveBeenCalled(); await load(view)
  expect(view.container.querySelector('script')).toBeNull()
  fireEvent.click(view.getByText(opportunityCopy.accept[0]))
  expect(request).toHaveBeenCalledTimes(1)
  fireEvent.click(await view.findByRole('button', { name: opportunityCopy.confirm[0] }))
  fireEvent.click(await view.findByText(opportunityCopy.next[0]))
  expect(open).toHaveBeenCalledWith(expect.objectContaining({ disposition: 'accepted', execution_authorized: false }))
  expect(request).toHaveBeenCalledTimes(2)
})
it('invalidates a prepared choice on generation change and cannot paint late old-scope results', async () => {
  let resolve!: (value: unknown) => void
  const request = vi.fn(() => new Promise(done => { resolve = done })) as RuntimeRequest
  const view = render(<StrictMode><OpportunityPanel {...props(request)} /></StrictMode>)
  fireEvent.change(view.getByLabelText(opportunityCopy.scope[0]), { target: { value: 'project' } }); fireEvent.click(view.getByText(opportunityCopy.open[0])); fireEvent.click(view.getByText(opportunityCopy.retained[0]))
  view.rerender(<StrictMode><OpportunityPanel {...props(request)} connectionGeneration={2} /></StrictMode>)
  await act(async () => resolve(list)); expect(view.queryByText(item.title)).toBeNull()
})
it('unknown prior request blocks new discovery while retained read remains available', async () => {
  const request = vi.fn(async () => list) as RuntimeRequest
  const view = render(<OpportunityPanel {...props(request)} unresolvedRequestIds={['original-unknown']} />)
  await load(view)
  expect((view.getByText(opportunityCopy.discover[0]) as HTMLButtonElement).disabled).toBe(true)
  expect((view.getByText(opportunityCopy.retained[0]) as HTMLButtonElement).disabled).toBe(false)
  expect(view.getByText(/original-unknown/)).toBeTruthy()
})
it('all authored controls have exact nine-locale coverage', () => {
  for (const values of Object.values(opportunityCopy)) { expect(values).toHaveLength(9); expect(values.every(value => !!value.trim())).toBe(true) }
})
