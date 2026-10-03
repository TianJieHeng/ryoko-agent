import type { RuntimeRequest } from '@hermes/shared/runtime-control'
// @vitest-environment jsdom
import { cleanup, fireEvent, render, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
vi.mock('@/components/ui/button', () => ({ Button: (props: React.ComponentProps<'button'>) => <button {...props} /> }))
vi.mock('@/components/ui/input', () => ({ Input: (props: React.ComponentProps<'input'>) => <input {...props} /> }))
vi.mock('@/components/ui/textarea', () => ({ Textarea: (props: React.ComponentProps<'textarea'>) => <textarea {...props} /> }))
vi.mock('@/components/ui/confirm-dialog', () => ({ ConfirmDialog: ({ open, onConfirm }: { open: boolean; onConfirm: () => Promise<void> }) => open ? <button onClick={() => void onConfirm()}>Confirm paused monitor</button> : null }))
import { MonitorCreatePanel } from './monitor-create-panel'
afterEach(cleanup)

function fill(view: ReturnType<typeof render>) {
  for (const [label, value] of Object.entries({ 'New monitor project ID': 'p', 'New monitor ID': 'watch', 'Question to monitor': 'Has the report materially changed?', 'Retained source artifact IDs (one per line)': 'report', 'First check ISO with offset': '2030-10-04T09:00:00Z', 'Monitor expiry ISO with offset': '2030-10-05T09:00:00Z' })) {fireEvent.change(view.getByLabelText(label), { target: { value } })}
}

it('creates only an explicitly reviewed finite paused definition, never delivery or activation', async () => {
  const request = vi.fn(async (_method: string, params: Record<string, unknown>) => ({ record_json: JSON.stringify({ project_id: 'p', schedule_id: 'watch', version: 1, revision: 1, state: 'paused', definition: JSON.parse(String(params.definition_json)), health: 'unknown', occurrences: [], intents: [], remaining_checks: 24, authority: 'hermes_cron', last_success: null, next_due: null, last_error: null }) })) as RuntimeRequest
  const view = render(<MonitorCreatePanel connected request={request} sessionId="owned" />)
  fill(view); fireEvent.click(view.getByLabelText('Allow later local-notification policy review (this does not grant delivery)'))
  fireEvent.click(view.getByText('Review paused monitor definition')); expect(request).not.toHaveBeenCalled()
  fireEvent.click(view.getByText('Confirm paused monitor'))
  await waitFor(() => expect(request).toHaveBeenCalledOnce())
  const [method, params] = vi.mocked(request).mock.calls[0]
  expect(method).toBe('runtime.schedule.create')
  await waitFor(() => expect(view.getByText(/Schedule watch v1.*paused/)).toBeTruthy())
  const definition = JSON.parse((params as { definition_json: string }).definition_json)
  expect(definition).toMatchObject({ kind: 'monitor', version: 1, budget: { max_checks: 24, max_bytes: 262144 }, specification: { source_set: ['report'], predicate: { kind: 'normalized_text', version: 1 }, notify_policy: 'local_runtime', condition_action: null } })
  expect(vi.mocked(request).mock.calls.some(([name]) => name === 'runtime.schedule.update' || name === 'runtime.monitor.policy.set')).toBe(false)
})
it('invalidates review on content edit and retains unknown creation for inspection', async () => {
  const request = vi.fn(async () => { throw new Error('transport lost') }) as RuntimeRequest
  const view = render(<MonitorCreatePanel connected request={request} sessionId="owned" />)
  fill(view); fireEvent.click(view.getByText('Review paused monitor definition'))
  fireEvent.change(view.getByLabelText('Question to monitor'), { target: { value: 'Revised question' } })
  expect(view.queryByText('Confirm paused monitor')).toBeNull()
  fireEvent.click(view.getByText('Review paused monitor definition')); fireEvent.click(view.getByText('Confirm paused monitor'))
  await waitFor(() => expect(view.getByText(/no automatic retry/i)).toBeTruthy())
  expect((view.getByText('Review paused monitor definition') as HTMLButtonElement).disabled).toBe(true)
  expect(view.getByText('Inspect original monitor creation')).toBeTruthy()
  expect(request).toHaveBeenCalledOnce()
})
