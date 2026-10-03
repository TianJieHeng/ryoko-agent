import type { RuntimeRequest } from '@hermes/shared/runtime-control'
// @vitest-environment jsdom
import { fireEvent, render, waitFor } from '@testing-library/react'
import { expect, it, vi } from 'vitest'

vi.mock('@/components/ui/button', () => ({ Button: (props: React.ComponentProps<'button'>) => <button {...props} /> }))
vi.mock('@/components/ui/input', () => ({ Input: (props: React.ComponentProps<'input'>) => <input {...props} /> }))
vi.mock('@/components/ui/textarea', () => ({ Textarea: (props: React.ComponentProps<'textarea'>) => <textarea {...props} /> }))
vi.mock('@/components/ui/confirm-dialog', () => ({ ConfirmDialog: ({ open, onConfirm }: { open: boolean; onConfirm: () => Promise<void> }) => open ? <button onClick={() => void onConfirm()}>Confirm exact bytes</button> : null }))
import { ArtifactWorkbench } from './artifact-workbench'

it('requires review before publishing and invalidates approval when content changes', async () => {
  const request = vi.fn(async (method: string) => method === 'runtime.artifact.prepare'
    ? { project_id: 'p', artifact_id: 'a', version: 1, size: 12, sha256: 'a'.repeat(64), approval_id: 'ap', approval_digest: 'b'.repeat(64), expires_at: Date.now() / 1000 + 60 }
    : method === 'runtime.artifact.cancel' ? { command_id: 'cmd', status: 'cancelled', result: { effects_undone: false } } : { artifact_id: 'a', version: 1, disposition: 'canonical', validation_status: 'passed' }) as RuntimeRequest

  const view = render(<ArtifactWorkbench connected request={request} sessionId="owned" />)
  fireEvent.change(view.getByLabelText('Project ID'), { target: { value: 'p' } })
  fireEvent.change(view.getByLabelText('Complete Markdown content'), { target: { value: '# Draft\nFirst' } })
  fireEvent.click(view.getByText('Prepare exact revision for review'))
  await waitFor(() => expect(view.getByText('Approve and publish this exact version')).toBeTruthy())
  expect(request).toHaveBeenCalledTimes(1)
  fireEvent.change(view.getByLabelText('Complete Markdown content'), { target: { value: '# Draft\nRevised' } })
  expect((view.getByText('Approve and publish this exact version') as HTMLButtonElement).disabled).toBe(true)
  fireEvent.click(view.getByText('Discard review'))
  await waitFor(() => expect(view.queryByText('Approve and publish this exact version')).toBeNull())
  fireEvent.click(view.getByText('Prepare exact revision for review'))
  await waitFor(() => expect(view.getByText('Approve and publish this exact version')).toBeTruthy())
  fireEvent.click(view.getByText('Approve and publish this exact version'))
  fireEvent.click(view.getByText('Confirm exact bytes'))
  await waitFor(() => expect(request).toHaveBeenCalledWith('runtime.artifact.publish', expect.objectContaining({ session_id: 'owned', content: '# Draft\nRevised', approval_id: 'ap', approval_digest: 'b'.repeat(64) })))
  view.unmount()
})
