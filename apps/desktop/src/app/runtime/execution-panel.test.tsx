import type { RuntimeRequest } from '@hermes/shared/runtime-control'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/components/ui/button', () => ({
  Button: ({
    variant: _variant,
    size: _size,
    ...props
  }: React.ComponentProps<'button'> & { variant?: string; size?: string }) => <button {...props} />
}))
vi.mock('@/components/ui/input', () => ({ Input: (props: React.ComponentProps<'input'>) => <input {...props} /> }))
import { cleanup } from '@testing-library/react'

import { ExecutionPanel } from './execution-panel'

const sha = 'a'.repeat(64)
const executor = { executor_id: 'executor-a', generation: 2, capability_digest: sha, location: 'local' }

const service = {
  service_id: 'local.text.normalize',
  capability: 'utf8_normalization',
  version: 1,
  processing_location: 'local_profile_store',
  destination: 'local_profile_store',
  authentication: 'live_identity_and_project_grant',
  data_policy: 'same_profile_no_network_no_publication',
  max_input_bytes: 65536,
  max_output_bytes: 65536
}

const manifest = {
  schema_version: 1,
  pipeline_id: 'pipeline-a',
  project_id: 'project-a',
  executor,
  source: { artifact_id: 'artifact-a', version: 1, sha256: sha, size: 20, mime: 'text/markdown' },
  stages: [service],
  transfer: {
    input_bytes: 20,
    max_intermediate_bytes: 65536,
    max_output_bytes: 65536,
    from: 'local_project_artifact',
    to: 'local_profile_store',
    remote_bytes: 0
  },
  publication: 'private_staged_output_requires_separate_artifact_approval'
}

const json = (value: unknown) => ({ response_json: JSON.stringify(value) })
const proposal = json({ manifest, manifest_sha256: sha })

const result = json({
  pipeline_id: 'pipeline-a',
  state: 'pending',
  receipts: [],
  manifest_sha256: sha,
  next_stage: 0,
  publication: manifest.publication,
  blocked_reason: 'executor_disconnected'
})

function prepare() {
  fireEvent.change(screen.getByLabelText('Project ID'), { target: { value: 'project-a' } })
  fireEvent.change(screen.getByLabelText('Artifact ID'), { target: { value: 'artifact-a' } })
  fireEvent.submit(screen.getByRole('form', { name: 'Prepare local pipeline' }))
}

afterEach(cleanup)

describe('typed execution panel', () => {
  it('does not issue requests on mount and requires exact review before one explicit execution', async () => {
    const fn = vi.fn(async (method: string) => (method === 'runtime.services.prepare' ? proposal : result))
    render(<ExecutionPanel connected request={fn as RuntimeRequest} sessionId="owned" />)
    expect(fn).not.toHaveBeenCalled()
    await act(async () => prepare())
    expect(fn).toHaveBeenCalledTimes(1)
    expect(fn).toHaveBeenCalledWith(
      'runtime.services.prepare',
      expect.objectContaining({ session_id: 'owned', project_id: 'project-a', artifact_id: 'artifact-a', version: 1 })
    )
    expect((screen.getByRole('button', { name: 'Execute reviewed local stages' }) as HTMLButtonElement).disabled).toBe(
      true
    )
    fireEvent.click(screen.getByRole('button', { name: 'I reviewed this exact source and route' }))
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Execute reviewed local stages' }))
      fireEvent.click(screen.getByRole('button', { name: 'Execute reviewed local stages' }))
    })
    expect(fn).toHaveBeenCalledTimes(2)
    expect(fn).toHaveBeenLastCalledWith('runtime.services.execute', {
      session_id: 'owned',
      schema_version: 1,
      pipeline_id: 'pipeline-a',
      manifest_sha256: sha
    })
    expect(screen.getByText(/pending; blocked: executor_disconnected/)).toBeTruthy()
  })

  it('re-prepares the same pure pipeline identity for explicit endpoint recovery', async () => {
    const fn = vi.fn(async () => proposal) as RuntimeRequest
    render(<ExecutionPanel connected request={fn} sessionId="owned" />)
    await act(async () => prepare())
    await act(async () => fireEvent.submit(screen.getByRole('form', { name: 'Prepare local pipeline' })))
    const calls = vi.mocked(fn).mock.calls
    expect(calls).toHaveLength(2)
    expect(calls[0][1]).toEqual(calls[1][1])
  })

  it('drops a late preparation after switching session and never enables execution in the new scope', async () => {
    let finish!: (value: unknown) => void

    const fn = vi.fn(
      () =>
        new Promise(resolve => {
          finish = resolve
        })
    ) as RuntimeRequest

    const view = render(<ExecutionPanel connected request={fn} sessionId="a" />)
    await act(async () => prepare())
    view.rerender(<ExecutionPanel connected request={fn} sessionId="b" />)
    await act(async () => finish(proposal))
    expect(screen.queryByRole('button', { name: 'Execute reviewed local stages' })).toBeNull()
    expect((screen.getByLabelText('Pipeline ID') as HTMLInputElement).value).toBe('')
    expect(screen.queryByText(/Prepared pipeline pipeline-a/)).toBeNull()
  })

  it('preserves a pipeline recovery reference while disconnect invalidates reviewed authority', async () => {
    const fn = vi.fn(async () => proposal) as RuntimeRequest
    const view = render(<ExecutionPanel connected request={fn} sessionId="owned" />)
    await act(async () => prepare())
    view.rerender(<ExecutionPanel connected={false} request={fn} sessionId="owned" />)
    expect((screen.getByLabelText('Pipeline ID') as HTMLInputElement).value).toBe('pipeline-a')
    expect(screen.queryByRole('button', { name: 'Execute reviewed local stages' })).toBeNull()
    expect((screen.getByRole('button', { name: 'Inspect pipeline status' }) as HTMLButtonElement).disabled).toBe(true)
    view.rerender(<ExecutionPanel connected request={fn} sessionId="owned" />)
    expect((screen.getByRole('button', { name: 'Inspect pipeline status' }) as HTMLButtonElement).disabled).toBe(false)
    expect(fn).toHaveBeenCalledTimes(1)
  })

  it('does not retry an unknown execute outcome and closing never sends cancellation', async () => {
    const fn = vi.fn(async (method: string) => {
      if (method === 'runtime.services.prepare') {
        return proposal
      }

      throw new Error('transport lost')
    }) as RuntimeRequest

    const view = render(<ExecutionPanel connected request={fn} sessionId="owned" />)
    await act(async () => prepare())
    fireEvent.click(screen.getByRole('button', { name: 'I reviewed this exact source and route' }))
    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Execute reviewed local stages' })))
    expect(screen.getByRole('alert').textContent).toContain('outcome unconfirmed')
    view.unmount()
    expect(fn).toHaveBeenCalledTimes(2)
  })
})
