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
vi.mock('@/components/ui/loader', () => ({ Loader: ({ label }: { label: string }) => <div role="status">{label}</div> }))
vi.mock('@/components/ui/error-state', () => ({ ErrorState: ({ description }: { description: ReactNode }) => <div role="alert">{description}</div> }))
import { WorkflowPanel } from './workflow-panel'

const digest = 'a'.repeat(64), approvalDigest = 'b'.repeat(64)
const workflow = { project_id: 'p', workflow_id: 'w', version: 2, revision: 4, state: 'approved', sha256: digest, active_version: 2, head_revision: 3, evaluation_ref: 'evaluation', definition_json: JSON.stringify({ input_schema: { properties: { topic: { type: 'string' } }, required: ['topic'] }, output_schema: { required_sections: [], min_bytes: 1 }, steps: [{ step_id: 'render', kind: 'render_markdown' }], capability_requirements: ['artifact_write'] }) }
const proposal = (id: string) => ({ request_id: id, project_id: 'p', artifact_id: id, version: 1, sha256: digest, size: 12, mime: 'text/markdown', parent_version: null, expected_head_version: null, action_digest: digest, approval_id: `approval-${id}`, approval_digest: approvalDigest, expires_at: 2000000000 })
const receipt = (id: string) => ({ ...proposal(id), disposition: 'canonical', head_version: 1, validation_status: 'passed', approval_status: 'approved' })

const requestForRun = () => vi.fn(async (method: string, params: Record<string, unknown>) => {
  if (method === 'runtime.workflow.get') {return { workflow }}

  if (method === 'runtime.workflow.run.prepare') {return { workflow_run_id: 'prepared', pin_json: JSON.stringify({ ...params, parameters_sha256: digest }), proposals: [proposal('output'), proposal('manifest')], publication_atomic: false }}

  return { workflow_run_id: 'prepared', state: 'published', outputs: [receipt('output')], manifest: receipt('manifest'), publication_atomic: false, mission_completed: false }
}) as unknown as RuntimeRequest

async function inspect(view: ReturnType<typeof render>) {
  for (const [label, value] of [['Project ID', 'p'], ['Workflow ID', 'w'], ['Version', '2']]) {fireEvent.change(view.getByLabelText(label), { target: { value } })}
  fireEvent.click(view.getByText('Inspect workflow'))
  await waitFor(() => expect(view.getByLabelText('topic (string)')).toBeTruthy())

  for (const [label, value] of [['topic (string)', 'Reviewed topic'], ['Mission ID', 'm'], ['Mission revision', '5']]) {fireEvent.change(view.getByLabelText(label), { target: { value } })}
}

afterEach(cleanup)
beforeEach(() => { confirmation.mockReset().mockResolvedValue(true) })

it('prepares exact scalar inputs and publishes only after explicit confirmation', async () => {
  const request = requestForRun(), view = render(<WorkflowPanel connected request={request} sessionId="owned" />)
  expect(request).not.toHaveBeenCalled()
  await inspect(view)
  fireEvent.click(view.getByText('Prepare run')); fireEvent.click(view.getByText('Prepare run'))
  await waitFor(() => expect((view.getByText('Publish reviewed outputs') as HTMLButtonElement).disabled).toBe(false))
  expect(request).toHaveBeenCalledTimes(2)
  expect(vi.mocked(request).mock.calls[1][1]).toMatchObject({ session_id: 'owned', project_id: 'p', workflow_id: 'w', version: 2, sha256: digest, mission_id: 'm', mission_revision: 5, parameters_json: JSON.stringify({ topic: 'Reviewed topic' }) })
  fireEvent.click(view.getByText('Publish reviewed outputs'))
  await waitFor(() => expect(request).toHaveBeenCalledTimes(3))
  expect(confirmation).toHaveBeenCalledTimes(1)
  expect(vi.mocked(request).mock.calls[2]).toEqual(['runtime.workflow.run.publish', { ...vi.mocked(request).mock.calls[1][1], approvals: [{ approval_id: 'approval-output', approval_digest: approvalDigest }, { approval_id: 'approval-manifest', approval_digest: approvalDigest }] }])
  await waitFor(() => expect(view.getByText(/Mission completion and delivery are not confirmed/u)).toBeTruthy())
})
it('invalidates a prepared approval when a visible input changes', async () => {
  const request = requestForRun(), view = render(<WorkflowPanel connected request={request} sessionId="owned" />)
  await inspect(view); fireEvent.click(view.getByText('Prepare run'))
  await waitFor(() => expect((view.getByText('Publish reviewed outputs') as HTMLButtonElement).disabled).toBe(false))
  fireEvent.change(view.getByLabelText('topic (string)'), { target: { value: 'Changed topic' } })
  expect((view.getByText('Publish reviewed outputs') as HTMLButtonElement).disabled).toBe(true)
  expect(request).toHaveBeenCalledTimes(2)
})
it('discards old-session reads and leaves focus on the current field', async () => {
  let resolve!: (value: unknown) => void
  const request = vi.fn(() => new Promise(value => { resolve = value })) as unknown as RuntimeRequest
  const view = render(<WorkflowPanel connected request={request} sessionId="old" />)

  for (const [label, value] of [['Project ID', 'p'], ['Workflow ID', 'w'], ['Version', '2']]) {fireEvent.change(view.getByLabelText(label), { target: { value } })}
  fireEvent.click(view.getByText('Inspect workflow'))
  view.rerender(<WorkflowPanel connected request={request} sessionId="new" />)
  const input = view.getByLabelText('Project ID'); input.focus()
  await act(async () => resolve({ workflow }))
  expect(view.queryByLabelText('topic (string)')).toBeNull()
  expect((input as HTMLInputElement).value).toBe('')
  expect(input.ownerDocument.activeElement).toBe(input)
})
it('blocks a stale publication confirmation after disconnect', async () => {
  let resolve!: (value: boolean) => void
  confirmation.mockImplementation(() => new Promise(value => { resolve = value }))
  const request = requestForRun(), view = render(<WorkflowPanel connected request={request} sessionId="owned" />)
  await inspect(view); fireEvent.click(view.getByText('Prepare run'))
  await waitFor(() => expect((view.getByText('Publish reviewed outputs') as HTMLButtonElement).disabled).toBe(false))
  fireEvent.click(view.getByText('Publish reviewed outputs'))
  view.rerender(<WorkflowPanel connected={false} request={request} sessionId="owned" />)
  await act(async () => resolve(true))
  expect(request).toHaveBeenCalledTimes(2)
})
it('retains stale preparation until acknowledged discard, then permits a varied run', async () => {
  const base = requestForRun()
  const request = vi.fn(async (method: string, params: Record<string, unknown>) => method === 'runtime.artifact.cancel' ? { command_id: params.command_id, run_id: 'run', status: 'cancelled', owner_live: false, expires_at: null, result: { cancel_requested: true, effects_undone: false } } : base(method as never, params as never)) as unknown as RuntimeRequest
  const view = render(<WorkflowPanel connected request={request} sessionId="owned" />)
  await inspect(view); fireEvent.click(view.getByText('Prepare run'))
  await waitFor(() => expect((view.getByText('Publish reviewed outputs') as HTMLButtonElement).disabled).toBe(false))
  const original = vi.mocked(request).mock.calls[1][1] as { command_id: string }
  fireEvent.change(view.getByLabelText('topic (string)'), { target: { value: 'Varied input' } })
  expect((view.getByText('Prepare run') as HTMLButtonElement).disabled).toBe(true)
  fireEvent.click(view.getByText('Discard prepared run'))
  await waitFor(() => expect((view.getByText('Prepare run') as HTMLButtonElement).disabled).toBe(false))
  expect(request).toHaveBeenNthCalledWith(3, 'runtime.artifact.cancel', { session_id: 'owned', schema_version: 1, command_id: original.command_id })
  fireEvent.click(view.getByText('Prepare run'))
  await waitFor(() => expect(request).toHaveBeenCalledTimes(4))
  expect(vi.mocked(request).mock.calls[3][1]).toMatchObject({ parameters_json: JSON.stringify({ topic: 'Varied input' }) })
})
it('keeps failed cancellation unresolved and does not prepare a replacement', async () => {
  const base = requestForRun()

  const request = vi.fn(async (method: string, params: Record<string, unknown>) => {
    if (method === 'runtime.artifact.cancel') {throw new Error('connection lost')}

    return base(method as never, params as never)
  }) as unknown as RuntimeRequest

  const view = render(<WorkflowPanel connected request={request} sessionId="owned" />)
  await inspect(view); fireEvent.click(view.getByText('Prepare run'))
  await waitFor(() => expect((view.getByText('Publish reviewed outputs') as HTMLButtonElement).disabled).toBe(false))
  fireEvent.change(view.getByLabelText('topic (string)'), { target: { value: 'Changed' } }); fireEvent.click(view.getByText('Discard prepared run'))
  await waitFor(() => expect(view.getByRole('alert')).toBeTruthy())
  expect((view.getByText('Prepare run') as HTMLButtonElement).disabled).toBe(true)
  expect(request).toHaveBeenCalledTimes(3)
})
it('isolates same-session data when the request transport changes', async () => {
  let resolve!: (value: unknown) => void
  const old = vi.fn(() => new Promise(value => { resolve = value })) as unknown as RuntimeRequest
  const fresh = requestForRun(), view = render(<WorkflowPanel connected request={old} sessionId="owned" />)

  for (const [label, value] of [['Project ID', 'p'], ['Workflow ID', 'w'], ['Version', '2']]) {fireEvent.change(view.getByLabelText(label), { target: { value } })}
  fireEvent.click(view.getByText('Inspect workflow'))
  view.rerender(<WorkflowPanel connected request={fresh} sessionId="owned" />)
  await act(async () => resolve({ workflow }))
  expect(view.queryByLabelText('topic (string)')).toBeNull()
  expect(fresh).not.toHaveBeenCalled()
})
it('reviews schedule health before pausing its exact revision and reports conflicts honestly', async () => {
  const schedule = { schedule_id: 's', project_id: 'p', version: 1, revision: 7, state: 'active', health: 'unhealthy', last_success: null, next_due: 1900000000, last_error: 'source_unavailable', remaining_checks: 3, authority: 'hermes_cron', definition: { timezone: 'Etc/UTC', expires_at: 2000000000, trigger: { kind: 'interval' }, policy: { missed_run: 'latest', overlap: 'block' }, kind: 'weekly_review', specification: {} }, occurrences: [], intents: [], history_truncated: false }

  const request = vi.fn(async (method: string) => {
    if (method === 'runtime.schedule.get') {return { record_json: JSON.stringify(schedule) }}
    throw { data: { code: 'schedule_revision_conflict' } }
  }) as unknown as RuntimeRequest

  const view = render(<WorkflowPanel connected request={request} sessionId="owned" />)
  fireEvent.change(view.getByLabelText('Project ID'), { target: { value: 'p' } }); fireEvent.change(view.getByLabelText('Schedule ID'), { target: { value: 's' } }); fireEvent.click(view.getByText('Inspect schedule'))
  await waitFor(() => expect((view.getByText('Pause schedule') as HTMLButtonElement).disabled).toBe(false))
  expect(view.getByText(/Health unhealthy/u)).toBeTruthy()
  fireEvent.click(view.getByText('Pause schedule'))
  await waitFor(() => expect(view.getByText(/Schedule revision changed/u)).toBeTruthy())
  expect(vi.mocked(request).mock.calls[1]).toEqual(['runtime.schedule.update', expect.objectContaining({ session_id: 'owned', project_id: 'p', schedule_id: 's', expected_revision: 7, state: 'paused' })])
  expect(request).toHaveBeenCalledTimes(2)
  expect((view.getByText('Resume schedule') as HTMLButtonElement).disabled).toBe(true)
})
