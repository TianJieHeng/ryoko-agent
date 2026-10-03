import type { RuntimeRequest } from '@hermes/shared/runtime-control'
// @vitest-environment jsdom
import { cleanup, fireEvent, render, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

vi.mock('@/components/ui/button', () => ({ Button: (props: React.ComponentProps<'button'>) => <button {...props} /> }))
vi.mock('@/components/ui/input', () => ({ Input: (props: React.ComponentProps<'input'>) => <input {...props} /> }))
vi.mock('@/components/ui/checkbox', () => ({ Checkbox: ({ checked, disabled, onCheckedChange }: { checked: boolean; disabled: boolean; onCheckedChange: (value: boolean) => void }) => <input checked={checked} disabled={disabled} onChange={event => onCheckedChange(event.target.checked)} type="checkbox" /> }))
vi.mock('@/components/ui/confirm-dialog', () => ({ ConfirmDialog: ({ open, onConfirm }: { open: boolean; onConfirm: () => Promise<void> }) => open ? <button onClick={() => void onConfirm()}>Confirm exact merge</button> : null }))
vi.mock('@hermes/shared/runtime-artifacts', () => ({ downloadRuntimeArtifact: vi.fn(async (_request: unknown, _session: string, _project: string, _artifact: string, version: number) => ({ metadata: { version, mime: 'text/markdown', preview_mode: 'plain_text' }, bytes: new TextEncoder().encode(version === 2 ? '# Title\n## Keep\nCurrent\n## Choose\nCurrent\n' : '# Title\n## Keep\nAlternative\n## Choose\nChosen\n') })) }))
import { BranchMergePanel } from './branch-merge-panel'
afterEach(cleanup)

function transport(failPrepare = false) {
  return vi.fn(async (method: string, params: Record<string, unknown>) => {
    if (method === 'runtime.artifact.get') {return { version: 2 }}

    if (method === 'runtime.artifact.merge.prepare') {
      if (failPrepare) {throw new Error('Transport interrupted; outcome unknown')}

      return { version: 4, sha256: 'a'.repeat(64), approval_id: 'approve', approval_digest: 'b'.repeat(64), expires_at: Date.now() / 1000 + 60 }
    }

    if (method === 'runtime.artifact.merge.publish') {return { artifact_id: 'a', version: 4, disposition: 'canonical' }}

    return { command_id: params.command_id, status: method === 'runtime.artifact.cancel' ? 'cancelled' : 'prepared' }
  })
}

async function select(view: ReturnType<typeof render>) {
  fireEvent.change(view.getByLabelText('Project ID'), { target: { value: 'p' } })
  fireEvent.change(view.getByLabelText('Artifact ID'), { target: { value: 'a' } })
  fireEvent.change(view.getByLabelText('Alternative branch version'), { target: { value: '3' } })
  fireEvent.click(view.getByText('Compare with current canonical head'))
  await waitFor(() => expect(view.getByLabelText('Choose')).toBeTruthy())
  fireEvent.click(view.getByLabelText('Choose'))
  fireEvent.click(view.getByText('Prepare only selected section changes'))
}

it('publishes only the exact selected section after a distinct confirmation', async () => {
  const rpc = transport(), view = render(<BranchMergePanel connected request={rpc as RuntimeRequest} sessionId="owned" />)
  await select(view)
  await waitFor(() => expect((view.getByText('Approve selected merge') as HTMLButtonElement).disabled).toBe(false))
  expect(rpc).toHaveBeenCalledWith('runtime.artifact.merge.prepare', expect.objectContaining({ current_head_version: 2, branch_version: 3, approved_anchors: ['Choose'], session_id: 'owned' }))
  expect(rpc.mock.calls.some(([method]) => method.endsWith('.publish'))).toBe(false)
  fireEvent.click(view.getByText('Approve selected merge'))
  expect(rpc.mock.calls.some(([method]) => method.endsWith('.publish'))).toBe(false)
  fireEvent.click(view.getByText('Confirm exact merge'))
  await waitFor(() => expect(rpc).toHaveBeenCalledWith('runtime.artifact.merge.publish', expect.objectContaining({ approved_anchors: ['Choose'], approval_id: 'approve', approval_digest: 'b'.repeat(64) })))
})
it('retains an unknown prepare identity until terminal cancellation and never blind retries', async () => {
  const rpc = transport(true), view = render(<BranchMergePanel connected request={rpc as RuntimeRequest} sessionId="owned" />)
  await select(view)
  await waitFor(() => expect(view.getByRole('alert').textContent).toContain('outcome unknown'))
  expect((view.getByText('Prepare only selected section changes') as HTMLButtonElement).disabled).toBe(true)
  fireEvent.click(view.getByText('Inspect merge control'))
  await waitFor(() => expect(view.getByText(/Merge control prepared/)).toBeTruthy())
  expect(view.getByText('Discard merge preparation')).toBeTruthy()
  fireEvent.click(view.getByText('Discard merge preparation'))
  await waitFor(() => expect(view.queryByText('Discard merge preparation')).toBeNull())
  expect(rpc.mock.calls.filter(([method]) => method === 'runtime.artifact.merge.prepare')).toHaveLength(1)
})
it('scope switches cannot reuse old prepared authority or display old source bytes', async () => {
  const rpc = transport(), view = render(<BranchMergePanel connected request={rpc as RuntimeRequest} sessionId="owned" />)
  await select(view)
  await waitFor(() => expect(view.getByText('Approve selected merge')).toBeTruthy())
  view.rerender(<BranchMergePanel connected request={rpc as RuntimeRequest} sessionId="other" />)
  expect(view.queryByText('Approve selected merge')).toBeNull()
  expect((view.getByLabelText('Project ID') as HTMLInputElement).value).toBe('')
  expect(rpc.mock.calls.some(([method]) => method.endsWith('.publish'))).toBe(false)
})
