import type { RuntimeRequest } from '@hermes/shared/runtime-control'
// @vitest-environment jsdom
import { act, fireEvent, render, waitFor } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
vi.mock('@/i18n', () => ({ useI18n: () => ({ locale: 'en' }) }))
vi.mock('@/components/ui/button', () => ({ Button: (props: React.ComponentProps<'button'>) => <button {...props} /> }))
import { DecisionPanel } from './decision-panel'

it('is read-only and marks missing history without inventing a model explanation', async () => {
  const request = vi.fn(async () => ({ status: 'snapshot_required', events: [], snapshot: null, last_cursor: 'new:3', has_more: false })) as RuntimeRequest
  const view = render(<DecisionPanel connected request={request} sessionId="owned" />)
  fireEvent.click(view.getByText('Inspect receipt history'))
  await waitFor(() => expect(view.getByText(/Earlier decisions cannot be reconstructed/)).toBeTruthy())
  expect(request).toHaveBeenCalledOnce()
  expect(vi.mocked(request).mock.calls[0][0]).toBe('runtime.events.since')
  expect((view.getByText('Next retained page') as HTMLButtonElement).disabled).toBe(true)
  view.unmount()
})
it('clears old pages and ignores pending receipts on disconnect', async () => {
  let resolve!: (value: unknown) => void
  const request = vi.fn(() => new Promise(done => { resolve = done })) as RuntimeRequest
  const view = render(<DecisionPanel connected request={request} sessionId="owned" />)
  fireEvent.click(view.getByText('Inspect receipt history'))
  view.rerender(<DecisionPanel connected={false} request={request} sessionId="owned" />)
  await act(async () => resolve({ status: 'ok', events: [], last_cursor: 'old:3', has_more: false }))
  expect(view.queryByText(/No decision receipts in this bounded page/)).toBeNull()
  expect((view.getByText('Inspect receipt history') as HTMLButtonElement).disabled).toBe(true)
  view.unmount()
})
