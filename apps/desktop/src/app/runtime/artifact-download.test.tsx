import type { RuntimeRequest } from '@hermes/shared/runtime-control'
// @vitest-environment jsdom
import { fireEvent, render, waitFor } from '@testing-library/react'
import { expect, it, vi } from 'vitest'

vi.mock('@/components/ui/button', () => ({ Button: (props: React.ComponentProps<'button'>) => <button {...props} /> }))
vi.mock('@/components/ui/input', () => ({ Input: (props: React.ComponentProps<'input'>) => <input {...props} /> }))
import { ArtifactDownload } from './artifact-download'

it('cancelled downloads expose no partial link even when bytes arrive later', async () => {
  let resolve!: (value: unknown) => void
  const request = vi.fn(() => new Promise(done => { resolve = done })) as RuntimeRequest
  const view = render(<ArtifactDownload connected request={request} sessionId="owned" />)
  fireEvent.change(view.getByLabelText('Project ID'), { target: { value: 'p' } })
  fireEvent.change(view.getByLabelText('Artifact ID'), { target: { value: 'a' } })
  fireEvent.change(view.getByLabelText('Version'), { target: { value: '1' } })
  fireEvent.click(view.getByText('Verify complete download'))
  await waitFor(() => expect(request).toHaveBeenCalledOnce())
  fireEvent.click(view.getByText('Cancel download'))
  resolve({ project_id: 'p', artifact_id: 'a', version: 1, sha256: 'a'.repeat(64), size: 1, offset: 0, data_base64: 'eA==', next_offset: 1, eof: true, mime: 'text/html', preview_mode: 'download_only' })
  await waitFor(() => expect(view.queryByText(/Save verified/)).toBeNull())
  view.unmount()
})
