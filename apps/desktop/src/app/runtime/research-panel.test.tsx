// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, waitFor, within } from '@testing-library/react'
import type { ComponentProps, ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { ArtifactProposalResult, ResearchResponse } from '../../../../shared/src/gateway-contract.generated'
import type { RuntimeRequest } from '../../../../shared/src/runtime-control'

vi.mock('@/i18n', () => ({ useI18n: () => ({ locale: 'en' }) }))
vi.mock('@/components/ui/button', () => ({
  Button: ({
    size: _size,
    variant: _variant,
    ...props
  }: ComponentProps<'button'> & { size?: string; variant?: string }) => <button {...props} />
}))
vi.mock('@/components/ui/input', () => ({ Input: (props: ComponentProps<'input'>) => <input {...props} /> }))
vi.mock('@/components/ui/textarea', () => ({
  Textarea: (props: ComponentProps<'textarea'>) => <textarea {...props} />
}))
vi.mock('@/components/ui/checkbox', () => ({
  Checkbox: ({
    checked,
    disabled,
    onCheckedChange
  }: {
    checked: boolean
    disabled: boolean
    onCheckedChange: (checked: boolean) => void
  }) => (
    <input
      checked={checked}
      disabled={disabled}
      onChange={event => onCheckedChange(event.target.checked)}
      type="checkbox"
    />
  )
}))
vi.mock('@/components/ui/select', () => ({
  Select: ({
    value,
    onValueChange,
    children
  }: {
    value: string
    onValueChange: (value: string) => void
    children: ReactNode
  }) => (
    <div data-selected={value} onChange={event => onValueChange((event.target as unknown as HTMLSelectElement).value)}>
      {children}
    </div>
  ),
  SelectTrigger: ({ children }: { children: ReactNode }) => <span>{children}</span>,
  SelectValue: () => null,
  SelectContent: ({ children }: { children: ReactNode }) => <span>{children}</span>,
  SelectItem: ({ children }: { children: ReactNode }) => <span>{children}</span>
}))
vi.mock('@/components/ui/loader', () => ({ Loader: ({ label }: { label: string }) => <span>{label}</span> }))
vi.mock('@/components/ui/error-state', () => ({
  ErrorState: ({ title, description }: { title: string; description: string }) => (
    <div>
      {title}: {description}
    </div>
  )
}))
// The dialog boundary is isolated, while the actual panel controls and RPC chain run.
vi.mock('@/components/ui/confirm-dialog', () => ({
  ConfirmDialog: ({
    open,
    title,
    onConfirm,
    children
  }: {
    open: boolean
    title: string
    onConfirm: () => Promise<void>
    children: ReactNode
  }) =>
    open ? (
      <div aria-label={title} role="dialog">
        {children}
        <button onClick={() => void onConfirm().catch(() => undefined)}>Confirm exact publication</button>
      </div>
    ) : null
}))
import { ResearchPanel } from './research-panel'

const sha = 'a'.repeat(64),
  manifestSha = 'b'.repeat(64)

const proposal: ArtifactProposalResult = {
  project_id: 'project-a',
  artifact_id: 'brief',
  version: 3,
  parent_version: 2,
  expected_head_version: 2,
  request_id: 'request-a',
  sha256: sha,
  size: 100,
  mime: 'text/markdown',
  action_digest: sha,
  approval_id: 'approval-brief',
  approval_digest: sha,
  expires_at: Date.now() / 1000 + 300
}

const manifest: ArtifactProposalResult = {
  ...proposal,
  artifact_id: 'manifest',
  mime: 'application/json',
  approval_id: 'approval-manifest',
  sha256: manifestSha,
  approval_digest: manifestSha
}

function resolved(requestJson: string, patch: Record<string, unknown> = {}): ResearchResponse {
  const sources = (JSON.parse(requestJson) as Record<string, unknown>[]).map(source => ({
    ...source,
    scope: { project_id: source.project_id, principal_id: 'private-principal' },
    availability: 'available',
    freshness: 'within_declared_window',
    errors: [],
    covered_bytes: source.evidence_ranges ? 2 : 0,
    size: 20,
    evidence_ranges: [],
    head_version: source.version,
    ...source,
    ...patch
  }))

  return {
    response_json: JSON.stringify({
      sources,
      coverage: {
        requested: sources.length,
        available: sources.filter(source => source.availability === 'available').length,
        stale: sources.filter(source => source.freshness === 'stale').length
      },
      complete: sources.every(source => source.availability === 'available')
    })
  }
}

function prepared(params: Record<string, unknown>): ResearchResponse {
  const body = JSON.parse(String(params.request_json)) as { requests: unknown[] }

  return {
    response_json: JSON.stringify({
      state: 'awaiting_approval',
      project_id: 'project-a',
      publication_atomic: false,
      brief: proposal,
      manifest,
      factual_changes: ['limit'],
      interpretation_changes: [],
      source_manifest: JSON.parse(resolved(JSON.stringify(body.requests)).response_json)
    })
  }
}

function published(partial = false): ResearchResponse {
  const publication = (value: ArtifactProposalResult) => ({
    ...value,
    disposition: 'canonical',
    head_version: 3,
    validation_status: 'passed',
    approval_status: 'approved'
  })

  return {
    response_json: JSON.stringify({
      state: partial ? 'partial' : 'published',
      publication_atomic: false,
      brief: publication(proposal),
      manifest: partial ? null : publication(manifest)
    })
  }
}

function mockRequest() {
  return vi.fn(async (method: string, params: Record<string, unknown>) => {
    if (method === 'runtime.research.resolve') {
      return resolved(String(params.request_json))
    }

    if (method === 'runtime.brief.prepare') {
      return prepared(params)
    }

    if (method === 'runtime.brief.publish') {
      return published()
    }

    return {
      command_id: params.command_id,
      run_id: 'owned-run',
      status: method === 'runtime.artifact.cancel' ? 'cancelled' : 'claimed',
      owner_live: false,
      expires_at: null,
      result: { cancel_requested: true, effects_undone: false }
    }
  })
}

const deferred = <T,>() => {
  let resolve!: (value: T) => void

  const promise = new Promise<T>(done => {
    resolve = done
  })

  return { promise, resolve }
}

function fill(view: ReturnType<typeof render>) {
  const source = within(view.getAllByRole('group', { name: 'Current source references 1' })[0])
  const set = (label: string, value: string) => fireEvent.change(view.getByLabelText(label), { target: { value } })
  fireEvent.change(source.getByLabelText('Source ID'), { target: { value: 'source-a' } })
  fireEvent.change(source.getByLabelText('Project ID'), { target: { value: 'project-a' } })
  fireEvent.change(source.getByLabelText('Exact version'), { target: { value: '2' } })
  fireEvent.change(source.getByLabelText('SHA-256'), { target: { value: sha } })
  fireEvent.change(source.getByLabelText('End byte (exclusive)'), { target: { value: '2' } })
  fireEvent.change(source.getByLabelText('Range SHA-256'), { target: { value: sha } })
  fireEvent.change(source.getByLabelText('Exact UTF-8 quote'), { target: { value: '20' } })
  fireEvent.change(view.getAllByLabelText('Project ID')[1], { target: { value: 'project-a' } })
  set('Existing brief artifact ID', 'brief')
  set('Brief parent version', '2')
  set('Command ID (new for each preparation)', 'command-a')
  set('Request ID', 'request-a')
  set('Prior dependency manifest ID', 'manifest')
  set('Manifest exact version', '2')
  set('Manifest SHA-256', manifestSha)
  set('Claim ID to update', 'limit')
  set('Replacement claim text', 'The limit is 20.')
}

async function preparePanel(view: ReturnType<typeof render>) {
  fireEvent.click(view.getByText('Check exact source coverage'))
  await waitFor(() => expect((view.getByText('Prepare exact brief refresh') as HTMLButtonElement).disabled).toBe(false))
  fireEvent.click(view.getByText('Prepare exact brief refresh'))
  await waitFor(() => expect(view.getByText('I reviewed both exact outputs and their ordered approvals')).toBeTruthy())
}

afterEach(cleanup)

describe('retained-source research panel', () => {
  it('resolves exact current pins and trustworthy citation metadata, then requires review and confirmation of both ordered approvals', async () => {
    const request = mockRequest(),
      view = render(<ResearchPanel connected request={request as RuntimeRequest} sessionId="owned-session" />)

    fill(view)
    await preparePanel(view)
    expect(view.getAllByText(/Citation source-a v2: bytes \[0, 2\), SHA-256/)).toHaveLength(2)
    expect(view.container.textContent).not.toContain('private-principal')
    expect((view.getByText('Publish reviewed brief and manifest') as HTMLButtonElement).disabled).toBe(true)
    expect(request.mock.calls.map(([method]) => method)).toEqual(['runtime.research.resolve', 'runtime.brief.prepare'])
    fireEvent.click(view.getByLabelText('I reviewed both exact outputs and their ordered approvals'))
    fireEvent.click(view.getByText('Publish reviewed brief and manifest'))
    const dialog = within(view.getByRole('dialog'))
    expect(dialog.getAllByRole('listitem').map(item => item.textContent?.includes('approval-brief'))).toEqual([
      true,
      false
    ])
    expect(request).toHaveBeenCalledTimes(2)
    fireEvent.click(dialog.getByText('Confirm exact publication'))
    await waitFor(() => expect(request).toHaveBeenCalledTimes(3))

    const prepare = request.mock.calls[1][1],
      publish = request.mock.calls[2][1]

    expect(publish).toEqual({
      ...prepare,
      brief_approval_id: 'approval-brief',
      brief_approval_digest: sha,
      manifest_approval_id: 'approval-manifest',
      manifest_approval_digest: manifestSha
    })
    await waitFor(() => expect(view.getByText(/Published brief: brief v3/)).toBeTruthy())
    expect(view.getByText(/Delivery and independent claim verification are not confirmed/)).toBeTruthy()
    expect(view.queryByText(/nothing published/)).toBeNull()
  })
  it('keeps changed proposal identity and requires a terminal discard receipt before preparing another command', async () => {
    const request = mockRequest(),
      view = render(<ResearchPanel connected request={request as RuntimeRequest} sessionId="owned-session" />)

    fill(view)
    await preparePanel(view)
    fireEvent.click(view.getByLabelText('I reviewed both exact outputs and their ordered approvals'))
    fireEvent.change(view.getByLabelText('Replacement claim text'), { target: { value: 'The limit is now 21.' } })
    fireEvent.change(view.getByLabelText('Command ID (new for each preparation)'), { target: { value: 'command-b' } })
    expect(view.getByText('command-a')).toBeTruthy()
    expect(
      (view.getByLabelText('I reviewed both exact outputs and their ordered approvals') as HTMLInputElement).checked
    ).toBe(false)
    const cancellation = deferred<unknown>()
    request.mockImplementationOnce(async () => cancellation.promise as never)
    fireEvent.click(view.getByText('Discard prepared command'))
    fireEvent.click(view.getByText('Discard prepared command'))
    expect(request).toHaveBeenCalledTimes(3)
    expect(request.mock.calls[2]).toEqual([
      'runtime.artifact.cancel',
      { session_id: 'owned-session', schema_version: 1, command_id: 'command-a' }
    ])
    expect((view.getByText('Prepare exact brief refresh') as HTMLButtonElement).disabled).toBe(true)
    await act(async () =>
      cancellation.resolve({
        command_id: 'command-a',
        run_id: 'owned-run',
        status: 'claimed',
        owner_live: true,
        expires_at: Date.now() / 1000 + 300,
        result: { cancel_requested: true, effects_undone: false }
      })
    )
    expect((view.getByText('Prepare exact brief refresh') as HTMLButtonElement).disabled).toBe(true)
    request.mockImplementationOnce(async () => ({
      command_id: 'command-a',
      run_id: 'owned-run',
      status: 'cancelled',
      owner_live: false,
      expires_at: null,
      result: { cancel_requested: true, effects_undone: false }
    }))
    fireEvent.click(view.getByText('Inspect original command'))
    await waitFor(() => expect(view.getByText(/cancelled; already committed effects are not undone/)).toBeTruthy())
    fireEvent.click(view.getByText('Check exact source coverage'))
    await waitFor(() =>
      expect((view.getByText('Prepare exact brief refresh') as HTMLButtonElement).disabled).toBe(false)
    )
    expect(request.mock.calls.filter(([method]) => method === 'runtime.brief.prepare')).toHaveLength(1)
  })
  it('blocks incomplete or stale coverage and rejects source identities that differ from the selected pins', async () => {
    const request = mockRequest(),
      view = render(<ResearchPanel connected request={request as RuntimeRequest} sessionId="owned-session" />)

    fill(view)
    request.mockImplementationOnce(async (_method, params) =>
      resolved(String(params.request_json), { freshness: 'stale', head_version: 3, errors: ['source_not_head'] })
    )
    fireEvent.click(view.getByText('Check exact source coverage'))
    await waitFor(() => expect(view.getByText(/1 stale/)).toBeTruthy())
    expect((view.getByText('Prepare exact brief refresh') as HTMLButtonElement).disabled).toBe(true)
    request.mockImplementationOnce(async (_method, params) =>
      resolved(String(params.request_json), {
        availability: 'missing',
        covered_bytes: 0,
        size: null,
        evidence_ranges: []
      })
    )
    fireEvent.click(view.getByText('Check exact source coverage'))
    await waitFor(() => expect(view.getByText(/0\/1 sources available/)).toBeTruthy())
    expect((view.getByText('Prepare exact brief refresh') as HTMLButtonElement).disabled).toBe(true)
    request.mockImplementationOnce(async (_method, params) =>
      resolved(String(params.request_json), { source_id: 'foreign-source' })
    )
    fireEvent.click(view.getByText('Check exact source coverage'))
    await waitFor(() =>
      expect(view.getByRole('alert').textContent).toContain('differs from the exact selected references')
    )
    expect(view.container.textContent).not.toContain('foreign-source')
    expect(request.mock.calls.every(([method]) => method === 'runtime.research.resolve')).toBe(true)
  })
  it('rejects stale asynchronous source results after session or request scope switches', async () => {
    const first = deferred<ResearchResponse>(),
      requestA = vi.fn(() => first.promise),
      requestB = mockRequest()

    const view = render(<ResearchPanel connected request={requestA as RuntimeRequest} sessionId="session-a" />)
    fill(view)
    fireEvent.click(view.getByText('Check exact source coverage'))
    const args = requestA.mock.calls[0] as unknown as [string, { request_json: string }]
    view.rerender(<ResearchPanel connected request={requestB as RuntimeRequest} sessionId="session-b" />)
    await act(async () => first.resolve(resolved(args[1].request_json)))
    expect(view.queryByText(/Exact local evidence:/)).toBeNull()
    expect(requestB).not.toHaveBeenCalled()
    expect((view.getByText('Prepare exact brief refresh') as HTMLButtonElement).disabled).toBe(true)
  })
  it('invalidates approval and hides old proposal output on a scope switch, without clearing the prior command identity', async () => {
    const request = mockRequest(),
      view = render(<ResearchPanel connected request={request as RuntimeRequest} sessionId="session-a" />)

    fill(view)
    await preparePanel(view)
    fireEvent.click(view.getByLabelText('I reviewed both exact outputs and their ordered approvals'))
    fireEvent.click(view.getByText('Publish reviewed brief and manifest'))
    view.rerender(<ResearchPanel connected request={request as RuntimeRequest} sessionId="session-b" />)
    expect(view.queryByRole('dialog')).toBeNull()
    expect(view.queryByText(/Prepared brief;/)).toBeNull()
    expect(view.getByText('command-a')).toBeTruthy()
    expect(view.getByText(/Original command belongs to another connection or session/)).toBeTruthy()
    view.rerender(<ResearchPanel connected request={request as RuntimeRequest} sessionId="session-a" />)
    expect((view.getByText('Publish reviewed brief and manifest') as HTMLButtonElement).disabled).toBe(true)
    expect(request.mock.calls.filter(([method]) => method === 'runtime.brief.publish')).toHaveLength(0)
  })
  it('preserves uncertain publication and partial effects without retrying or preparing anew', async () => {
    const request = mockRequest(),
      view = render(<ResearchPanel connected request={request as RuntimeRequest} sessionId="owned-session" />)

    fill(view)
    await preparePanel(view)
    request.mockRejectedValueOnce(new Error('private runtime error'))
    fireEvent.click(view.getByLabelText('I reviewed both exact outputs and their ordered approvals'))
    fireEvent.click(view.getByText('Publish reviewed brief and manifest'))
    fireEvent.click(view.getByText('Confirm exact publication'))
    fireEvent.click(view.getByText('Confirm exact publication'))
    await waitFor(() => expect(view.getByRole('alert').textContent).toContain('no automatic retry'))
    expect(request.mock.calls.filter(([method]) => method === 'runtime.brief.publish')).toHaveLength(1)
    expect(view.container.textContent).not.toContain('private runtime error')
    expect(view.getByText('command-a')).toBeTruthy()
    expect((view.getByText('Prepare exact brief refresh') as HTMLButtonElement).disabled).toBe(true)
  })

  it('submits both retained source kinds with exact pins and does not invent connected discovery', async () => {
    const request = mockRequest(),
      view = render(<ResearchPanel connected request={request as RuntimeRequest} sessionId="owned-session" />)

    fill(view)
    fireEvent.click(view.getByText('Add source'))
    const capture = within(view.getByRole('group', { name: 'Current source references 2' }))
    fireEvent.click(capture.getByText('Original capture'))

    for (const [label, value] of [
      ['Source ID', 'capture-a'],
      ['Project ID', 'project-a'],
      ['Exact version', '4'],
      ['SHA-256', manifestSha]
    ]) {
      fireEvent.change(capture.getByLabelText(label), { target: { value } })
    }

    fireEvent.click(view.getByText('Check exact source coverage'))
    await waitFor(() => expect(view.getByText(/2\/2 sources available/)).toBeTruthy())
    const sources = JSON.parse(String(request.mock.calls[0][1].request_json))
    expect(
      sources.map((source: Record<string, unknown>) => [source.source_type, source.source_id, source.version])
    ).toEqual([
      ['project_artifact', 'source-a', 2],
      ['capture_original', 'capture-a', 4]
    ])
    expect(request.mock.calls.map(([method]) => method)).toEqual(['runtime.research.resolve'])
  })
  it('keeps a pending preparation bound to its original session when its result arrives after navigation', async () => {
    const request = mockRequest(),
      view = render(<ResearchPanel connected request={request as RuntimeRequest} sessionId="session-a" />)

    fill(view)
    fireEvent.click(view.getByText('Check exact source coverage'))
    await waitFor(() =>
      expect((view.getByText('Prepare exact brief refresh') as HTMLButtonElement).disabled).toBe(false)
    )
    const pending = deferred<ResearchResponse>()
    request.mockImplementationOnce(async () => pending.promise)
    fireEvent.click(view.getByText('Prepare exact brief refresh'))
    fireEvent.click(view.getByText('Prepare exact brief refresh'))
    expect(request).toHaveBeenCalledTimes(2)
    view.rerender(<ResearchPanel connected request={request as RuntimeRequest} sessionId="session-b" />)
    await act(async () => pending.resolve(prepared(request.mock.calls[1][1])))
    expect(view.queryByText(/Prepared brief;/)).toBeNull()
    expect(view.getByText('command-a')).toBeTruthy()
    expect((view.getByText('Prepare exact brief refresh') as HTMLButtonElement).disabled).toBe(true)
    view.rerender(<ResearchPanel connected request={request as RuntimeRequest} sessionId="session-a" />)
    expect(view.queryByText('I reviewed both exact outputs and their ordered approvals')).toBeNull()
    expect(view.getByText('Discard prepared command')).toBeTruthy()
  })
  it('revokes reviewed approval on disconnect and retains the command for inspection after reconnect', async () => {
    const request = mockRequest(),
      view = render(<ResearchPanel connected request={request as RuntimeRequest} sessionId="owned-session" />)

    fill(view)
    await preparePanel(view)
    fireEvent.click(view.getByLabelText('I reviewed both exact outputs and their ordered approvals'))
    fireEvent.click(view.getByText('Publish reviewed brief and manifest'))
    view.rerender(<ResearchPanel connected={false} request={request as RuntimeRequest} sessionId="owned-session" />)
    expect(view.queryByRole('dialog')).toBeNull()
    expect(view.getByText('command-a')).toBeTruthy()
    view.rerender(<ResearchPanel connected request={request as RuntimeRequest} sessionId="owned-session" />)
    expect((view.getByText('Publish reviewed brief and manifest') as HTMLButtonElement).disabled).toBe(true)
    expect((view.getByText('Inspect original command') as HTMLButtonElement).disabled).toBe(false)
    expect(request).toHaveBeenCalledTimes(2)
  })
  it('retains partial publication and never treats cancellation as rollback', async () => {
    const request = mockRequest(),
      view = render(<ResearchPanel connected request={request as RuntimeRequest} sessionId="owned-session" />)

    fill(view)
    await preparePanel(view)
    request.mockImplementationOnce(async () => published(true))
    fireEvent.click(view.getByLabelText('I reviewed both exact outputs and their ordered approvals'))
    fireEvent.click(view.getByText('Publish reviewed brief and manifest'))
    fireEvent.click(view.getByText('Confirm exact publication'))
    await waitFor(() => expect(view.getByText(/Partial publication: brief committed/)).toBeTruthy())
    expect(view.getByText(/dependency manifest commit is unconfirmed/)).toBeTruthy()
    expect(view.queryByText(/nothing published/)).toBeNull()
    expect((view.getByText('Prepare exact brief refresh') as HTMLButtonElement).disabled).toBe(true)
    expect(request.mock.calls.filter(([method]) => method === 'runtime.brief.publish')).toHaveLength(1)
  })
  it('uses the same stable transport and exact initial baseline bytes across owned source resolution, prepare and publish', async () => {
    const request = mockRequest(),
      view = render(<ResearchPanel connected request={request as RuntimeRequest} sessionId="owned-session" />)

    fill(view)
    fireEvent.click(view.getByText('Establish first dependency baseline'))
    const prior = within(view.getByRole('group', { name: 'Previous source references 1' }))

    for (const [label, value] of [
      ['Source ID', 'source-a'],
      ['Project ID', 'project-a'],
      ['Exact version', '1'],
      ['SHA-256', sha],
      ['End byte (exclusive)', '2'],
      ['Range SHA-256', sha],
      ['Exact UTF-8 quote', '10']
    ]) {
      fireEvent.change(prior.getByLabelText(label), { target: { value } })
    }

    for (const [label, value] of [
      ['Original section heading', 'Facts'],
      ['Original section SHA-256', sha],
      ['Original claim text', 'The limit is 10.'],
      ['Prior citation source ID (range 0)', 'source-a']
    ]) {
      fireEvent.change(view.getByLabelText(label), { target: { value } })
    }

    await preparePanel(view)
    expect(request.mock.calls.map(([method]) => method)).toEqual([
      'runtime.research.resolve',
      'runtime.research.resolve',
      'runtime.brief.prepare'
    ])
    fireEvent.click(view.getByLabelText('I reviewed both exact outputs and their ordered approvals'))
    fireEvent.click(view.getByText('Publish reviewed brief and manifest'))
    fireEvent.click(view.getByText('Confirm exact publication'))
    await waitFor(() => expect(request).toHaveBeenCalledTimes(4))
    expect(request.mock.calls[3][1].request_json).toBe(request.mock.calls[2][1].request_json)
    const initial = JSON.parse(String(request.mock.calls[2][1].request_json))
    expect(initial.previous_sources[0].scope.principal_id).toBe('private-principal')
    expect(initial.requests[0].version).toBe(2)
    expect(view.container.textContent).not.toContain('private-principal')
  })
  it('releases only local preparation state when validation fails before any backend prepare dispatch', async () => {
    const request = mockRequest(), view = render(<ResearchPanel connected request={request as RuntimeRequest} sessionId="owned-session" />)
    fill(view)
    fireEvent.click(view.getByText('Establish first dependency baseline'))
    const prior = within(view.getByRole('group', { name: 'Previous source references 1' }))

    for (const [label, value] of [['Source ID', 'source-a'], ['Project ID', 'project-a'], ['Exact version', '1'], ['SHA-256', sha], ['End byte (exclusive)', '2'], ['Range SHA-256', sha], ['Exact UTF-8 quote', '10']]) {fireEvent.change(prior.getByLabelText(label), { target: { value } })}

    for (const [label, value] of [['Original section heading', 'Facts'], ['Original section SHA-256', sha], ['Prior citation source ID (range 0)', 'source-a']]) {fireEvent.change(view.getByLabelText(label), { target: { value } })}
    fireEvent.click(view.getByText('Check exact source coverage'))
    await waitFor(() => expect((view.getByText('Prepare exact brief refresh') as HTMLButtonElement).disabled).toBe(false))
    fireEvent.click(view.getByText('Prepare exact brief refresh'))
    await waitFor(() => expect(view.getByRole('alert')).toBeTruthy())
    expect(request.mock.calls.map(([method]) => method)).toEqual(['runtime.research.resolve'])
    expect((view.getByText('Prepare exact brief refresh') as HTMLButtonElement).disabled).toBe(false)
    expect(view.queryByText('Inspect command')).toBeNull()
  })

})
