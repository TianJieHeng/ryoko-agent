import type { RuntimeRequest } from '@hermes/shared/runtime-control'
// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
vi.mock('@/components/ui/button', () => ({ Button: (props: React.ComponentProps<'button'>) => <button {...props} /> }))
vi.mock('@/components/ui/input', () => ({ Input: (props: React.ComponentProps<'input'>) => <input {...props} /> }))
vi.mock('@/components/ui/textarea', () => ({ Textarea: (props: React.ComponentProps<'textarea'>) => <textarea {...props} /> }))
import { AgendaPanel } from './agenda-panel'
afterEach(cleanup)
const result = { calendar_changed: false, invitation_sent: false, obligations_created: false, live_availability_verified: false, view: 'day', timezone: 'America/New_York', captured_at: '2026-10-01T00:00:00Z', planning_at: '2026-10-03T00:00:00Z', stale: true, days: [{ date: '2026-11-01', planned_minutes: 30, capacity_minutes: 360, fixed: [{ start_at: '2026-11-01T01:00:00-04:00', end_at: '2026-11-01T01:30:00-04:00' }], flexible: [{ item_id: 'task', duration_minutes: 30, start_at: '2026-11-01T01:40:00-04:00', end_at: '2026-11-01T01:10:00-05:00' }] }], overflow: [{ item_id: 'overflow', duration_minutes: 90, reason: 'no_contiguous_capacity_after_fixed_work_and_buffers' }] }

function fill(view: ReturnType<typeof render>) {
  for (const [label, value] of Object.entries({ 'Agenda project ID': 'p', 'Availability artifact ID': 'availability', 'Availability SHA-256': 'a'.repeat(64), 'Agenda timezone': 'America/New_York', 'Exact participant identities (one per line)': 'person@example.test', 'Day 1 start ISO with offset': '2026-11-01T01:00:00-04:00', 'Day 1 end ISO with offset': '2026-11-01T04:00:00-05:00', 'Work 1 ID': 'task' })) {fireEvent.change(view.getByLabelText(label), { target: { value } })}
}

it('shows fixed, flexible, overflow and stale snapshot truth from the actual source-bound preview', async () => {
  const request = vi.fn(async () => ({ record_json: JSON.stringify(result) })) as RuntimeRequest
  const view = render(<AgendaPanel connected request={request} sessionId="owned" />)
  fill(view); fireEvent.click(view.getByText('Preview fixed, flexible and overflow work'))
  await waitFor(() => expect(view.getByText('Overflow (1)')).toBeTruthy())
  expect(view.getByText(/STALE availability/)).toBeTruthy()
  expect(request).toHaveBeenCalledExactlyOnceWith('runtime.agenda.plan', expect.objectContaining({ session_id: 'owned', project_id: 'p', timezone: 'America/New_York', work: [{ item_id: 'task', duration_minutes: 30 }], windows: [{ start_at: '2026-11-01T01:00:00-04:00', end_at: '2026-11-01T04:00:00-05:00' }] }))
  fireEvent.change(view.getByLabelText('Work 1 duration minutes'), { target: { value: '60' } })
  expect(view.queryByText('Overflow (1)')).toBeNull()
})
it('requires explicit offset and suppresses late old-scope plans', async () => {
  let resolve!: (value: unknown) => void
  const request = vi.fn(() => new Promise(done => { resolve = done })) as RuntimeRequest
  const view = render(<AgendaPanel connected request={request} sessionId="owned" />)
  fill(view); fireEvent.change(view.getByLabelText('Day 1 start ISO with offset'), { target: { value: '2026-11-01T01:00:00' } }); fireEvent.click(view.getByText('Preview fixed, flexible and overflow work'))
  await waitFor(() => expect(view.getByRole('alert')).toBeTruthy()); expect(request).not.toHaveBeenCalled()
  fireEvent.change(view.getByLabelText('Day 1 start ISO with offset'), { target: { value: '2026-11-01T01:00:00-04:00' } }); fireEvent.click(view.getByText('Preview fixed, flexible and overflow work'))
  view.rerender(<AgendaPanel connected request={request} sessionId="other" />)
  await act(async () => resolve({ record_json: JSON.stringify(result) }))
  expect(view.queryByText('Overflow (1)')).toBeNull()
})
