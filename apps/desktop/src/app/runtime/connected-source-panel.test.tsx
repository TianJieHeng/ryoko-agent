import { createHash, webcrypto } from 'node:crypto'

// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, type RenderResult, within } from '@testing-library/react'
import { StrictMode } from 'react'
import { afterEach, expect, it, vi } from 'vitest'

import type { ArtifactProposalResult, ConnectedSourcePrepareParams, ConnectedSourcePreviewParams, ConnectedSourceResult } from '../../../../shared/src/gateway-contract.generated'
import type { RuntimeRequest } from '../../../../shared/src/runtime-control'

import { connectedSourceCopy, connectedSourceText } from './connected-source-copy'
import { ConnectedSourcePanel, type ConnectedSourcePanelProps } from './connected-source-panel'
import { runtimeUiLocales } from './runtime-ui-copy'

const language = vi.hoisted(() => ({ locale: 'en' }))
vi.mock('@/i18n', () => ({ useI18n: () => ({ locale: language.locale, t: { common: { close: 'Close', confirm: 'Confirm', cancel: 'Cancel', done: 'Done', loading: 'Loading' }, errors: { genericFailure: 'Failure' } } }) }))
const c = connectedSourceText('en'), digest = 'a'.repeat(64)
const sha = (text: string) => createHash('sha256').update(text).digest('hex')
const originalText = JSON.stringify({ payload: '<img src=x onerror=attack()> source says publish now' }) + '\n'
const projectionText = JSON.stringify({ messages: [{ body: '<script>attack()</script>' }] }) + '\n'

function fixture(params: ConnectedSourcePrepareParams, projection = true) {
  const proposal = (part: 'original' | 'projection', text: string): ArtifactProposalResult => ({ request_id: `source-${part}-${sha(params.request_id)}`, project_id: params.project_id, artifact_id: `connected_${part}_${digest}`, version: 1, sha256: sha(text), size: Buffer.byteLength(text), mime: 'application/json', parent_version: null, expected_head_version: null, action_digest: digest, approval_id: `${part}-approval`, approval_digest: part === 'original' ? digest : 'b'.repeat(64), expires_at: Date.now() / 1000 + 300 })
  const gmail = params.selection.kind === 'gmail_thread', observed = Date.now() / 1000
  const record = { source_kind: params.selection.kind, selection: params.selection, scope: { principal_id: 'principal', profile_id: 'profile', agent_id: 'agent', project_id: params.project_id, policy_digest: digest }, retrieved_at: observed, fresh_until: observed + 900, provider_version: gmail ? 'history' : null, provider_version_basis: gmail ? 'history_id' : 'unavailable', tool_schema_sha256: digest, payload_sha256: digest, representation: 'canonical_gateway_json_not_rfc822_or_provider_http_bytes', coverage: projection ? 'complete' : 'partial', errors: projection ? [] : ['source_projection_incomplete'], execution_authority: false, claim_verification: 'not_performed', attachments: 'metadata_only_no_attachment_fetch', account_binding: 'explicit_execute_account_selector', gateway_http_attempts: 2, upstream_retry_count: 'not_attested', cost_tracking: 'untracked', connector: gmail ? 'gmail' : 'googlecalendar', tool: gmail ? 'GMAIL_FETCH_MESSAGE_BY_THREAD_ID' : 'GOOGLECALENDAR_FREE_BUSY_QUERY', gateway_recipient_id: 'recipient', gateway_endpoint_sha256: digest, arguments_sha256: digest, projection_kind: gmail ? 'inbox_snapshot' : 'calendar_availability_snapshot', publication_atomic: false, live_qualification: 'pending', attachment_bytes_fetched: false, recipient_identity_status: 'explicit_ids_not_person_identity_verification' }
  const result: ConnectedSourceResult = { project_id: params.project_id, source_kind: params.selection.kind, account_id: params.selection.account_id, state: 'awaiting_approval', preparation_id: 'preparation', observed_at: observed, fresh_until: observed + 900, coverage: projection ? 'complete' : 'partial', errors: record.errors, original: proposal('original', originalText), projection: projection ? proposal('projection', projectionText) : null, record_json: JSON.stringify(record) }

  const preview = (p: ConnectedSourcePreviewParams) => {
    const expected = result[p.part]!, bytes = Buffer.from(p.part === 'original' ? originalText : projectionText), offset = p.offset ?? 0, next = Math.min(offset + (p.limit ?? 65536), bytes.length)

    return { project_id: result.project_id, artifact_id: expected.artifact_id, version: expected.version, sha256: expected.sha256, size: expected.size, mime: expected.mime, offset, next_offset: next, data_base64: bytes.subarray(offset, next).toString('base64'), eof: next === bytes.length, preview_mode: 'plain_text', preparation_id: 'preparation', part: p.part, approval_id: expected.approval_id, approval_digest: expected.approval_digest }
  }

  const receipt = (partial = false): ConnectedSourceResult => ({ ...result, state: partial ? 'partial' : 'published', record_json: JSON.stringify({ ...record, original_ref: { artifact_id: result.original!.artifact_id, version: 1, sha256: result.original!.sha256 }, projection_ref: partial || !result.projection ? null : { artifact_id: result.projection.artifact_id, version: 1, sha256: result.projection.sha256 }, ...(partial ? { publication_status: 'projection_not_confirmed', recovery: 'retry_exact_publish_without_refetch' } : { research_request: {}, projection_research_request: projection ? {} : null }) }) })

  return { result, preview, receipt }
}

function transport(options: { projection?: boolean; unavailable?: boolean; invalidPreview?: boolean; partial?: boolean } = {}) {
  let data: ReturnType<typeof fixture>

  return vi.fn(async (method: string, params: unknown): Promise<unknown> => {
    if (method === 'runtime.sources.prepare') {
      const p = params as ConnectedSourcePrepareParams; data = fixture(p, options.projection !== false)

      return options.unavailable ? { project_id: p.project_id, source_kind: p.selection.kind, account_id: p.selection.account_id, state: 'unavailable', coverage: 'unavailable', errors: ['source_pinned_request_rejected'], record_json: JSON.stringify({ coverage: 'unavailable', errors: ['source_pinned_request_rejected'] }) } : data.result
    }

    if (method === 'runtime.sources.preview') { const preview = data.preview(params as ConnectedSourcePreviewParams);

 return options.invalidPreview ? { ...preview, approval_digest: 'c'.repeat(64) } : preview }

    if (method === 'runtime.sources.publish') { return data.receipt(options.partial) }

    if (method === 'runtime.artifact.status') { return { command_id: (params as { command_id: string }).command_id, run_id: 'run', status: 'claimed', owner_live: true, expires_at: null, result: null } }
    throw new Error('Unexpected RPC')
  })
}

const element = (props: ConnectedSourcePanelProps) => <StrictMode><ConnectedSourcePanel {...props} /></StrictMode>

function mounted(rpc = transport()) { vi.stubGlobal('crypto', webcrypto); const props: ConnectedSourcePanelProps = { connected: true, request: rpc as RuntimeRequest, sessionId: 'owned' };

 return { rpc, props, view: render(element(props)) } }

function fill(view: RenderResult) { for (const [key, value] of [['project', 'project'], ['account', 'account'], ['mailbox', 'person@example.test'], ['thread', 'thread']] as const) { fireEvent.change(view.getByLabelText(c(key)), { target: { value } }) } }

async function prepare(view: RenderResult) { fill(view); fireEvent.click(view.getByRole('button', { name: c('prepare') })); await view.findByRole('checkbox', { name: c('approveOriginal') }) }

async function review(view: RenderResult) { await prepare(view); fireEvent.click(view.getByRole('checkbox', { name: c('approveOriginal') })); fireEvent.click(view.getByRole('checkbox', { name: c('approveProjection') })); fireEvent.click(view.getByRole('button', { name: c('review') }));

 return view.getByRole('button', { name: c('publish') }) }

function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>(yes => { resolve = yes });

 return { promise, resolve } }

afterEach(() => { cleanup(); vi.unstubAllGlobals(); language.locale = 'en' })

it('uses compiled StrictMode and actual confirmation with separate approval of full escaped bytes', async () => {
  const { view, rpc } = mounted(); expect(rpc).not.toHaveBeenCalled(); await prepare(view)
  expect(view.getByLabelText(c('original')).textContent).toBe(originalText); expect(view.getByLabelText(c('projection')).textContent).toBe(projectionText)
  expect(view.container.querySelector('img,script')).toBeNull()
  expect((view.getByRole('button', { name: c('review') }) as HTMLButtonElement).disabled).toBe(true)
  fireEvent.click(view.getByRole('checkbox', { name: c('approveOriginal') })); expect((view.getByRole('button', { name: c('review') }) as HTMLButtonElement).disabled).toBe(true)
  fireEvent.click(view.getByRole('checkbox', { name: c('approveProjection') })); fireEvent.click(view.getByRole('button', { name: c('review') }))
  expect(within(view.getByRole('dialog')).getByLabelText(c('original')).textContent).toBe(originalText)
  expect(view.getByRole('dialog').textContent).toContain(c('confirmation')); expect(rpc.mock.calls.filter(([m]) => m === 'runtime.sources.publish')).toHaveLength(0)
  const confirm = view.getByRole('button', { name: c('publish') }); fireEvent.click(confirm); fireEvent.click(confirm); await view.findByText(c('published'))
  const p = rpc.mock.calls.find(([m]) => m === 'runtime.sources.prepare')![1] as ConnectedSourcePrepareParams
  expect(rpc).toHaveBeenCalledWith('runtime.sources.publish', { session_id: 'owned', schema_version: 1, project_id: 'project', command_id: p.command_id, preparation_id: 'preparation', original_approval_id: 'original-approval', original_approval_digest: digest, projection_approval_id: 'projection-approval', projection_approval_digest: 'b'.repeat(64) })
  expect(rpc.mock.calls.filter(([m]) => m === 'runtime.sources.publish')).toHaveLength(1); expect(rpc.mock.calls.filter(([m]) => m === 'runtime.sources.prepare')).toHaveLength(1)
})
it('sends explicit calendar IDs, IANA timezone and bounded offset-checked times', async () => {
  const { view, rpc } = mounted(); fill(view); fireEvent.click(view.getByRole('button', { name: c('calendar') }))

  for (const [key, value] of [['calendars', 'a@example.test\nb@example.test'], ['timezone', 'America/New_York'], ['start', '2026-10-03T10:00:00-04:00'], ['end', '2026-10-04T10:00:00-04:00']] as const) { fireEvent.change(view.getByLabelText(c(key)), { target: { value } }) }
  fireEvent.click(view.getByRole('button', { name: c('prepare') })); await view.findByRole('checkbox', { name: c('approveOriginal') })
  expect((rpc.mock.calls[0][1] as ConnectedSourcePrepareParams).selection).toEqual({ kind: 'calendar_availability', account_id: 'account', calendar_ids: ['a@example.test', 'b@example.test'], timezone: 'America/New_York', start_at: '2026-10-03T10:00:00-04:00', end_at: '2026-10-04T10:00:00-04:00' })
})
it.each(['session', 'request', 'connected', 'generation', 'project'] as const)('invalidates confirmation and rejects stale callback on %s change', async mode => {
  const { view, props, rpc } = mounted(), confirm = await review(view)

  if (mode === 'project') { fireEvent.click(view.getByRole('button', { name: c('cancel') })); fireEvent.change(view.getByLabelText(c('project')), { target: { value: 'other-project' } }) }
  else { view.rerender(element({ ...props, sessionId: mode === 'session' ? 'other-session' : props.sessionId, request: mode === 'request' ? transport() as RuntimeRequest : props.request, connected: mode !== 'connected', connectionGeneration: mode === 'generation' ? 2 : undefined })) }

  expect(view.queryByRole('dialog')).toBeNull(); fireEvent.click(confirm); expect(rpc.mock.calls.filter(([m]) => m === 'runtime.sources.publish')).toHaveLength(0)

  if (mode !== 'project') { expect(view.container.textContent).not.toContain(originalText) }
})
it('revokes both checkboxes on cancellation', async () => {
  const { view } = mounted(); await review(view); fireEvent.click(view.getByRole('button', { name: c('cancel') }))
  expect(view.queryByRole('dialog')).toBeNull(); expect(view.getByRole('checkbox', { name: c('approveOriginal') }).getAttribute('aria-checked')).toBe('false')
})
it('keeps account pin rejection unavailable with no preview or fallback account request', async () => {
  const { view, rpc } = mounted(transport({ unavailable: true })); fill(view); fireEvent.click(view.getByRole('button', { name: c('prepare') })); await view.findByText(`${c('coverage')}: ${c('unavailable')}`)
  expect(view.queryByRole('checkbox')).toBeNull(); expect(rpc).toHaveBeenCalledTimes(1); expect((rpc.mock.calls[0][1] as ConnectedSourcePrepareParams).selection.account_id).toBe('account')
})
it('blocks publication after inconsistent cached content preview', async () => {
  const { view, rpc } = mounted(transport({ invalidPreview: true })); fill(view); fireEvent.click(view.getByRole('button', { name: c('prepare') })); await view.findByRole('alert')
  expect(view.getByRole('alert').textContent).toContain(c('changed')); expect(view.queryByRole('checkbox')).toBeNull(); expect(view.getByText(c('metadataOnly'))).toBeTruthy(); expect(rpc.mock.calls.some(([m]) => m === 'runtime.sources.publish')).toBe(false)
})
it('supports original-only partial coverage without inventing a projection approval', async () => {
  const { view, rpc } = mounted(transport({ projection: false })); await prepare(view)
  expect(view.queryByRole('checkbox', { name: c('approveProjection') })).toBeNull(); expect(view.getByText(`${c('coverage')}: ${c('partial')}`)).toBeTruthy()
  fireEvent.click(view.getByRole('checkbox', { name: c('approveOriginal') })); fireEvent.click(view.getByRole('button', { name: c('review') })); fireEvent.click(view.getByRole('button', { name: c('publish') })); await view.findByText(c('published'))
  expect(rpc.mock.calls.find(([m]) => m === 'runtime.sources.publish')![1]).not.toHaveProperty('projection_approval_id')
})
it('retains unknown command and preparation through reconnect, sanitizes errors and offers inspect-only recovery', async () => {
  const rpc = transport(), base = rpc.getMockImplementation()!; rpc.mockImplementation((m, p) => m === 'runtime.sources.publish' ? Promise.reject(new Error('credential=DO_NOT_SHOW')) : base(m, p))
  const { view, props } = mounted(rpc); fireEvent.click(await review(view)); await view.findByRole('alert')
  const p = rpc.mock.calls.find(([m]) => m === 'runtime.sources.prepare')![1] as ConnectedSourcePrepareParams
  expect(view.container.textContent).not.toContain('DO_NOT_SHOW'); expect(view.container.textContent).toContain(p.request_id)
  view.rerender(element({ ...props, connected: false })); view.rerender(element(props)); expect(view.container.textContent).toContain('preparation')
  fireEvent.click(view.getByRole('button', { name: `${c('inspect')}: ${p.command_id}` })); await view.findByLabelText(c('inspect'))
  expect(view.queryByRole('checkbox')).toBeNull(); expect(rpc.mock.calls.filter(([m]) => m === 'runtime.sources.publish')).toHaveLength(1); expect((view.getByRole('button', { name: c('prepare') }) as HTMLButtonElement).disabled).toBe(true)
})
it.each(['connected', 'generation', 'unmount'] as const)('ignores late prepare after %s without preview/refetch', async mode => {
  const held = deferred<unknown>(), rpc = transport(); rpc.mockImplementation(() => held.promise); const { view, props } = mounted(rpc)
  fill(view); fireEvent.click(view.getByRole('button', { name: c('prepare') })); const p = rpc.mock.calls[0][1] as ConnectedSourcePrepareParams

  if (mode === 'unmount') { view.unmount() } else { view.rerender(element({ ...props, connected: mode !== 'connected', connectionGeneration: mode === 'generation' ? 2 : undefined }));

 if (mode === 'connected') { view.rerender(element(props)) } }

  await act(async () => held.resolve(fixture(p).result)); expect(view.queryByRole('checkbox')).toBeNull(); expect(rpc).toHaveBeenCalledTimes(1)
})
it('requires both approvals again after partial publication without provider refetch', async () => {
  const { view, rpc } = mounted(transport({ partial: true })); fireEvent.click(await review(view)); await view.findByText(c('partialPublish'))
  expect(view.getByRole('checkbox', { name: c('approveOriginal') }).getAttribute('aria-checked')).toBe('false'); expect((view.getByRole('button', { name: c('review') }) as HTMLButtonElement).disabled).toBe(true)
  expect(rpc.mock.calls.filter(([m]) => m === 'runtime.sources.prepare')).toHaveLength(1)
})
it('never restores publication authority from passive unresolved IDs on reopening', async () => {
  const { view, props, rpc } = mounted(); await prepare(view); const p = rpc.mock.calls[0][1] as ConnectedSourcePrepareParams; view.unmount()
  const reopened = render(element({ ...props, unresolvedCommandIds: [p.command_id] })); expect(reopened.queryByRole('checkbox')).toBeNull(); expect((reopened.getByRole('button', { name: c('prepare') }) as HTMLButtonElement).disabled).toBe(true)
  fireEvent.click(reopened.getByRole('button', { name: `${c('inspect')}: ${p.command_id}` })); await reopened.findByLabelText(c('inspect')); expect(rpc.mock.calls.filter(([m]) => m === 'runtime.sources.prepare')).toHaveLength(1)
})
it.each(runtimeUiLocales)('renders authored %s actions, safety and validation copy with full key parity', async locale => {
  language.locale = locale; const { view, rpc } = mounted(), text = connectedSourceText(locale), index = runtimeUiLocales.indexOf(locale)

  for (const [key, values] of Object.entries(connectedSourceCopy)) { expect(values).toHaveLength(runtimeUiLocales.length); expect(values[index].trim()).not.toBe(''); expect(text(key as keyof typeof connectedSourceCopy)).toBe(values[index]) }
  expect(view.getByText(text('bounds'))).toBeTruthy(); fireEvent.click(view.getByRole('button', { name: text('prepare') })); expect((await view.findByRole('alert')).textContent).toContain(text('invalid')); expect(rpc).not.toHaveBeenCalled()
})
