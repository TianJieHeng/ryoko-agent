import type { RuntimeRequest } from '@hermes/shared/runtime-control'
// @vitest-environment jsdom
import { cleanup, fireEvent, render, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
vi.mock('@/components/ui/button', () => ({ Button: (props: React.ComponentProps<'button'>) => <button {...props} /> }))
vi.mock('@/components/ui/input', () => ({ Input: (props: React.ComponentProps<'input'>) => <input {...props} /> }))
vi.mock('@/components/ui/confirm-dialog', () => ({ ConfirmDialog: ({ open, onConfirm }: { open: boolean; onConfirm: () => Promise<void> }) => open ? <button onClick={() => void onConfirm()}>Confirm reviewed policy</button> : null }))
import { MonitorPolicyPanel } from './monitor-policy-panel'
afterEach(cleanup)
const record = { revision: 3, schedule_state: 'active', pending_total: 1, notices_total: 1, remaining_deliveries: 0, policy: null, destination: null, snoozed_until: null, notices: [{ intent_id: 'n', payload: { question: 'Changed report' }, state: 'pending', hold_reason: 'awaiting_policy', delivery_id: null, delivery: null, dismissed_at: null }], next_cursor_json: null }

async function open(view: ReturnType<typeof render>) {
  fireEvent.change(view.getByLabelText('Monitor project ID'), { target: { value: 'p' } })
  fireEvent.change(view.getByLabelText('Monitor schedule ID'), { target: { value: 's' } })
  fireEvent.click(view.getByText('Inspect retained notices and policy'))
  await waitFor(() => expect(view.getByLabelText('Notification timezone')).toBeTruthy())
}

it('requires exact finite delivery review and never supplies a client-selected destination', async () => {
  const request = vi.fn(async () => ({ record_json: JSON.stringify(record) })) as RuntimeRequest
  const view = render(<MonitorPolicyPanel connected request={request} sessionId="owned" />)
  await open(view)
  fireEvent.change(view.getByLabelText('Policy expiry ISO with offset'), { target: { value: '2030-10-04T18:00:00Z' } })
  fireEvent.change(view.getByLabelText('Quiet start HH:MM'), { target: { value: '22:00' } })
  fireEvent.change(view.getByLabelText('Quiet end HH:MM'), { target: { value: '07:00' } })
  fireEvent.click(view.getByText('Review exact local delivery policy'))
  expect(request).toHaveBeenCalledTimes(1)
  fireEvent.click(view.getByText('Confirm reviewed policy'))
  await waitFor(() => expect(request).toHaveBeenCalledTimes(2))
  const [, params] = vi.mocked(request).mock.calls[1]
  expect(params).toMatchObject({ session_id: 'owned', project_id: 'p', schedule_id: 's', expected_revision: 3 })
  const policy = JSON.parse((params as { policy_json: string }).policy_json)
  expect(policy).toMatchObject({ kind: 'local_runtime', timezone: 'UTC', max_deliveries: 10, quiet_hours: { start_minute: 1320, end_minute: 420, fold: 'both', gap: 'next_valid' } })
  expect(policy.destination).toBeUndefined()
})
it('retains unknown control identity and blocks a duplicate mutation until terminal evidence', async () => {
  const request = vi.fn(async (method: string) => {
    if (method === 'runtime.monitor.policy.set') {throw new Error('lost transport')}

    return { record_json: JSON.stringify(record) }
  }) as RuntimeRequest

  const view = render(<MonitorPolicyPanel connected request={request} sessionId="owned" />)
  await open(view)
  fireEvent.change(view.getByLabelText('Policy expiry ISO with offset'), { target: { value: '2030-10-04T18:00:00Z' } })
  fireEvent.click(view.getByText('Review exact local delivery policy')); fireEvent.click(view.getByText('Confirm reviewed policy'))
  await waitFor(() => expect(view.getByRole('alert')).toBeTruthy())
  expect((view.getByText('Review exact local delivery policy') as HTMLButtonElement).disabled).toBe(true)
  expect(view.getByText('Inspect original notification control')).toBeTruthy()
  expect(vi.mocked(request).mock.calls.filter(([method]) => method === 'runtime.monitor.policy.set')).toHaveLength(1)
})
it('keeps older-page cursors opaque and does not acknowledge history inspection as delivery', async () => {
  const request = vi.fn(async () => ({ record_json: JSON.stringify({ ...record, next_cursor_json: '{"created_at":1,"intent_id":"n"}', history_truncated: true }) })) as RuntimeRequest
  const view = render(<MonitorPolicyPanel connected request={request} sessionId="owned" />)
  await open(view)
  fireEvent.click(view.getByText('Load older retained notices'))
  await waitFor(() => expect(request).toHaveBeenCalledWith('runtime.monitor.notifications', expect.objectContaining({ cursor_json: '{"created_at":1,"intent_id":"n"}' })))
  expect(vi.mocked(request).mock.calls.every(([method]) => method === 'runtime.monitor.notifications')).toBe(true)
})
