import type { RuntimeRequest } from '@hermes/shared/runtime-control'
// @vitest-environment jsdom
import { act, fireEvent, render, waitFor } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
vi.mock('@/i18n', () => ({ useI18n: () => ({ locale: 'en' }) }))
vi.mock('@/components/ui/button', () => ({ Button: (props: React.ComponentProps<'button'>) => <button {...props} /> }))
vi.mock('@/components/ui/input', () => ({ Input: (props: React.ComponentProps<'input'>) => <input {...props} /> }))
import { StatusPanel } from './status-panel'

it('does not turn failed queues into an all-clear or run repairs during inspection', async () => {
  const request = vi.fn(async () => { throw new Error('offline') }) as RuntimeRequest
  const view = render(<StatusPanel connected request={request} sessionId="owned" />)
  expect(request).not.toHaveBeenCalled()
  fireEvent.click(view.getByText('Inspect current queues'))
  await waitFor(() => expect(view.getByText(/Mission status unavailable/)).toBeTruthy())
  expect(vi.mocked(request).mock.calls.some(([method]) => method === 'runtime.effect.reconcile')).toBe(false)
  view.unmount()
})
it('ignores old scope results after session replacement', async () => {
  let resolve!: (value: unknown) => void
  const request = vi.fn((method: string) => method === 'runtime.mission.list' ? new Promise(done => { resolve = done }) : Promise.reject(new Error('offline'))) as RuntimeRequest
  const view = render(<StatusPanel connected request={request} sessionId="a" />)
  fireEvent.click(view.getByText('Inspect current queues'))
  view.rerender(<StatusPanel connected request={request} sessionId="b" />)
  await act(async () => resolve({ missions: [{ mission_id: 'private-a', state: 'ready_to_review', outcome: 'Old private work', next_step: '', blockers: [] }], limit_reached: false }))
  expect(view.queryByText('Old private work')).toBeNull()
  view.unmount()
})
