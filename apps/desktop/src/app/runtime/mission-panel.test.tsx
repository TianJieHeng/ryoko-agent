import type { RuntimeRequest } from '@hermes/shared/runtime-control'
// @vitest-environment jsdom
import { fireEvent, render, waitFor } from '@testing-library/react'
import { expect, it, vi } from 'vitest'

vi.mock('@/components/ui/button', () => ({ Button: (props: React.ComponentProps<'button'>) => <button {...props} /> }))
vi.mock('@/components/ui/input', () => ({ Input: (props: React.ComponentProps<'input'>) => <input {...props} /> }))
vi.mock('@/components/ui/textarea', () => ({ Textarea: (props: React.ComponentProps<'textarea'>) => <textarea {...props} /> }))
vi.mock('@/components/ui/confirm-dialog', () => ({ ConfirmDialog: ({ open, onConfirm }: { open: boolean; onConfirm: () => Promise<void> }) => open ? <button onClick={() => void onConfirm()}>Confirm reviewed mission</button> : null }))
import { MissionPanel } from './mission-panel'

it('requires current deterministic evidence and explicit confirmation before acceptance', async () => {
  const mission = { mission_id: 'm', revision: 7, state: 'ready_to_review', outcome: 'Two outputs', execution_status: 'completed', acceptance_status: 'pending', delivery_status: 'pending', next_step: 'Review outputs', blockers: [], missed_steer: [], deliverables: [{ deliverable_id: 'report', artifact_ref: { artifact_id: 'a', version: 1 }, required: true }], verification_current: true, effect_refs: [], delivery_refs: [{ delivery_id: 'd', state: 'outcome_unknown' }] }
  const request = vi.fn(async (method: string) => method === 'runtime.mission.receipts.list' ? { receipts: [] } : { mission }) as RuntimeRequest
  const view = render(<MissionPanel connected request={request} sessionId="owned" />)
  fireEvent.click(view.getByText('Refresh current mission and evidence'))
  await waitFor(() => expect(view.getByText('Accept reviewed result')).toBeTruthy())
  expect(request).toHaveBeenCalledTimes(2)
  fireEvent.click(view.getByText('Accept reviewed result'))
  expect(request).toHaveBeenCalledTimes(2)
  fireEvent.click(view.getByText('Confirm reviewed mission'))
  await waitFor(() => expect(request).toHaveBeenCalledWith('runtime.mission.accept', { session_id: 'owned', schema_version: 1, expected_revision: 7 }))
  expect(view.getByText('d: outcome_unknown')).toBeTruthy()
  view.unmount()
})
