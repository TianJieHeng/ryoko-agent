
import { describe, expect, it, vi } from 'vitest'

import type { ArtifactProposalResult, ConnectedSourcePrepareParams, ConnectedSourcePreviewParams, ConnectedSourceResult } from './gateway-contract.generated.js'
import { type ConnectedSourceInput, connectedSourcePublishInput, connectedSourceSelection, ConnectedSourceSession, parseConnectedSourceResult, readConnectedSourcePreview } from './runtime-connected-sources.js'
import type { RuntimeRequest } from './runtime-control.js'

const digest = 'a'.repeat(64)

const sourceTexts = {
  original: JSON.stringify({ payload: '<img src=x onerror=attack()>', retained: 'x'.repeat(66000) }) + '\n',
  projection: JSON.stringify({ schema_version: 1, messages: [{ body: '<script>attack()</script>' }] }) + '\n'
}

const sourceDigests = Object.fromEntries(await Promise.all(Object.entries(sourceTexts).map(async ([part, text]) => [part, Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text)))).map(byte => byte.toString(16).padStart(2, '0')).join('')])))
const base64 = (bytes: Uint8Array) => btoa(Array.from(bytes, byte => String.fromCharCode(byte)).join(''))
const input: ConnectedSourceInput = { project: 'project', kind: 'gmail_thread', account: 'account', mailbox: 'person@example.test', thread: 'thread', calendars: 'one@example.test\ntwo@example.test', timezone: 'America/New_York', start: '2026-10-03T10:00:00-04:00', end: '2026-10-04T10:00:00-04:00' }
const params: ConnectedSourcePrepareParams = { session_id: 'owned', schema_version: 1, project_id: 'project', command_id: 'command', request_id: 'request', selection: connectedSourceSelection(input) }

function fixture(p = params, projection = true) {
  const originalText = sourceTexts.original
  const projectionText = sourceTexts.projection
  const proposal = (part: 'original' | 'projection', text: string): ArtifactProposalResult => ({ request_id: `source-${part}-${digest}`, project_id: p.project_id, artifact_id: `connected_${part}_${digest}`, version: 2, sha256: sourceDigests[part], size: new TextEncoder().encode(text).length, mime: 'application/json', parent_version: 1, expected_head_version: 1, action_digest: digest, approval_id: `${part}-approval`, approval_digest: part === 'original' ? digest : 'b'.repeat(64), expires_at: Date.now() / 1000 + 300 })
  const gmail = p.selection.kind === 'gmail_thread', observed = Date.now() / 1000
  const record = { source_kind: p.selection.kind, selection: p.selection, scope: { principal_id: 'principal', profile_id: 'profile', agent_id: 'agent', project_id: p.project_id, policy_digest: digest }, retrieved_at: observed, fresh_until: observed + 900, provider_version: gmail ? 'history-1' : null, provider_version_basis: gmail ? 'history_id' : 'unavailable', tool_schema_sha256: digest, payload_sha256: digest, representation: 'canonical_gateway_json_not_rfc822_or_provider_http_bytes', coverage: projection ? 'complete' : 'partial', errors: projection ? [] : ['source_projection_incomplete'], execution_authority: false, claim_verification: 'not_performed', attachments: 'metadata_only_no_attachment_fetch', account_binding: 'explicit_execute_account_selector', gateway_http_attempts: 2, upstream_retry_count: 'not_attested', cost_tracking: 'untracked', connector: gmail ? 'gmail' : 'googlecalendar', tool: gmail ? 'GMAIL_FETCH_MESSAGE_BY_THREAD_ID' : 'GOOGLECALENDAR_FREE_BUSY_QUERY', gateway_recipient_id: 'recipient', gateway_endpoint_sha256: digest, arguments_sha256: digest, projection_kind: gmail ? 'inbox_snapshot' : 'calendar_availability_snapshot', publication_atomic: false, live_qualification: 'pending', attachment_bytes_fetched: false, recipient_identity_status: 'explicit_ids_not_person_identity_verification' }
  const result: ConnectedSourceResult = { project_id: p.project_id, source_kind: p.selection.kind, account_id: p.selection.account_id, state: 'awaiting_approval', preparation_id: 'preparation', observed_at: observed, fresh_until: observed + 900, coverage: projection ? 'complete' : 'partial', errors: record.errors, original: proposal('original', originalText), projection: projection ? proposal('projection', projectionText) : null, record_json: JSON.stringify(record) }

  const preview = (request: ConnectedSourcePreviewParams) => {
    const expected = result[request.part]!, text = request.part === 'original' ? originalText : projectionText, bytes = new TextEncoder().encode(text), offset = request.offset ?? 0
    const end = Math.min(offset + (request.limit ?? 65536), bytes.length)

    return { project_id: p.project_id, artifact_id: expected.artifact_id, version: expected.version, sha256: expected.sha256, size: expected.size, mime: expected.mime, offset, data_base64: base64(bytes.subarray(offset, end)), next_offset: end, eof: end === bytes.length, preview_mode: 'plain_text' as const, preparation_id: result.preparation_id!, part: request.part, approval_id: expected.approval_id, approval_digest: expected.approval_digest }
  }

  const receipt = (partial = false): ConnectedSourceResult => ({ ...result, state: partial ? 'partial' : 'published', record_json: JSON.stringify({ ...record, original_ref: { artifact_id: result.original!.artifact_id, version: 2, sha256: result.original!.sha256 }, projection_ref: partial || !result.projection ? null : { artifact_id: result.projection.artifact_id, version: 2, sha256: result.projection.sha256 }, ...(partial ? { publication_status: 'projection_not_confirmed', recovery: 'retry_exact_publish_without_refetch' } : { research_request: {}, projection_research_request: projection ? {} : null }) }) })

  return { result, record, preview, receipt, originalText, projectionText }
}

function deferred<T>() { let resolve!: (value: T) => void; let reject!: (error: unknown) => void; const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no });

 return { promise, resolve, reject } }

function transport(projection = true) {
  let data = fixture(params, projection)

  const rpc = vi.fn(async (method: string, p: unknown): Promise<unknown> => {
    if (method === 'runtime.sources.prepare') { data = fixture(p as ConnectedSourcePrepareParams, projection);

 return data.result }

    if (method === 'runtime.sources.preview') { return data.preview(p as ConnectedSourcePreviewParams) }

    if (method === 'runtime.sources.publish') { return data.receipt() }

    if (method === 'runtime.artifact.status') { return { command_id: (p as { command_id: string }).command_id, run_id: 'run', status: 'claimed', owner_live: true, expires_at: null, result: null } }
    throw new Error('Unexpected request')
  })

  const session = new ConnectedSourceSession(rpc as RuntimeRequest, 'owned', (() => { let id = 0;

 return () => `id-${++id}` })())

  const detach = session.attach(true); session.edit(input)

  return { rpc, session, detach }
}



it('constructs exact thread and calendar selections without default accounts or broad lookup', () => {
  expect(connectedSourceSelection(input)).toEqual(params.selection)
  expect(connectedSourceSelection({ ...input, kind: 'calendar_availability' })).toEqual({ kind: 'calendar_availability', account_id: input.account, calendar_ids: ['one@example.test', 'two@example.test'], timezone: input.timezone, start_at: input.start, end_at: input.end })
})
it.each([{ account: '' }, { mailbox: 'me' }, { thread: '*' }, { account: ' account' }, { kind: 'calendar_availability', calendars: 'primary' }, { kind: 'calendar_availability', calendars: 'one\none' }, { kind: 'calendar_availability', calendars: Array(21).fill(0).map((_, i) => `id-${i}`).join('\n') }, { kind: 'calendar_availability', timezone: 'Not/AZone' }, { kind: 'calendar_availability', start: '2026-10-03T10:00:00Z' }, { kind: 'calendar_availability', end: '2026-10-11T10:00:00-04:00' }, { kind: 'calendar_availability', start: '2026-02-30T10:00:00-05:00' }, { kind: 'calendar_availability', end: input.start }] as Partial<ConnectedSourceInput>[])('rejects invalid or implicit selections %j before RPC', patch => expect(() => connectedSourceSelection({ ...input, ...patch })).toThrow())

it('parses original/projection metadata as inert exact evidence and rejects scope drift', () => {
  const data = fixture()
  expect(parseConnectedSourceResult(data.result, params).record).toEqual(data.record)
  expect(() => parseConnectedSourceResult({ ...data.result, account_id: 'foreign' }, params)).toThrow()
  expect(() => parseConnectedSourceResult({ ...data.result, record_json: JSON.stringify({ ...data.record, selection: { ...params.selection, account_id: 'foreign' } }) }, params)).toThrow()
  expect(() => parseConnectedSourceResult({ ...data.result, projection: { ...data.result.projection, approval_id: data.result.original!.approval_id } }, params)).toThrow()
})
it.each(['execution_authority', 'attachment_bytes_fetched', 'publication_atomic'])('rejects changed trust boundary %s', key => {
  const data = fixture(); expect(() => parseConnectedSourceResult({ ...data.result, record_json: JSON.stringify({ ...data.record, [key]: true }) }, params)).toThrow()
})
it('keeps unsupported pinned account unavailable and never constructs approvals', () => {
  const result: ConnectedSourceResult = { project_id: 'project', source_kind: 'gmail_thread', account_id: 'account', state: 'unavailable', coverage: 'unavailable', errors: ['source_pinned_request_rejected'], record_json: JSON.stringify({ coverage: 'unavailable', errors: ['source_pinned_request_rejected'] }) }
  const review = parseConnectedSourceResult(result, params)
  expect(review.result.state).toBe('unavailable'); expect(() => connectedSourcePublishInput(review)).toThrow()
})
it('reads all exact cached chunks and verifies complete SHA before exposing escaped-ready text', async () => {

  const data = fixture(), review = parseConnectedSourceResult(data.result, params), rpc = vi.fn(async (_method, p) => data.preview(p))
  expect(await readConnectedSourcePreview(rpc as RuntimeRequest, review, 'original')).toBe(data.originalText)
  expect(rpc).toHaveBeenCalledTimes(2)
  expect(rpc.mock.calls.map(([, p]) => p.offset)).toEqual([0, 65536])
  expect(rpc.mock.calls.every(([m]) => m === 'runtime.sources.preview')).toBe(true)
})
it.each(['sha256', 'approval_digest', 'preparation_id', 'artifact_id', 'part', 'next_offset', 'data_base64', 'eof'])('rejects inconsistent cached preview %s without publishing', async key => {

  const data = fixture(), review = parseConnectedSourceResult(data.result, params)
  const rpc = vi.fn(async (_m, p) => ({ ...data.preview(p), [key]: key === 'next_offset' ? 2 : key === 'eof' ? true : key === 'data_base64' ? btoa('\0'.repeat(65536)) : 'wrong' }))
  await expect(readConnectedSourcePreview(rpc as RuntimeRequest, review, 'original')).rejects.toThrow()
})
it('requires actual full content and rejects an expired approval', () => {
  const data = fixture(), review = parseConnectedSourceResult(data.result, params)
  expect(() => connectedSourcePublishInput(review)).toThrow()
  const full = { ...review, originalText: data.originalText, projectionText: data.projectionText }
  expect(connectedSourcePublishInput(full).command_id).toBe(params.command_id)
  expect(() => connectedSourcePublishInput(full, Date.now() + 600000)).toThrow()
})

describe('owned connected source lifetime', () => {
  it('fetches once, requires independent approvals, locks duplicate clicks, and publishes original IDs without refetching', async () => {

    const { rpc, session } = transport()
    await Promise.all([session.prepare(), session.prepare()])
    const review = session.getState().review!
    expect(review.originalText).toContain('<img'); expect(review.projectionText).toContain('<script>')
    session.approve('original', true); await session.publish(review)
    expect(rpc.mock.calls.filter(([m]) => m === 'runtime.sources.publish')).toHaveLength(0)
    session.approve('projection', true); await Promise.all([session.publish(review), session.publish(review)])
    expect(rpc.mock.calls.filter(([m]) => m === 'runtime.sources.prepare')).toHaveLength(1)
    expect(rpc.mock.calls.filter(([m]) => m === 'runtime.sources.publish')).toHaveLength(1)
    expect(rpc).toHaveBeenCalledWith('runtime.sources.publish', { session_id: 'owned', schema_version: 1, project_id: 'project', command_id: review.params.command_id, preparation_id: 'preparation', original_approval_id: 'original-approval', original_approval_digest: digest, projection_approval_id: 'projection-approval', projection_approval_digest: 'b'.repeat(64) })
    expect(session.getState().attempt?.status).toBe('published')
  })
  it('supports partial source coverage with original approval alone', async () => {

    const { rpc, session } = transport(false); await session.prepare(); const review = session.getState().review!
    session.approve('original', true); await session.publish(review)
    const sent = rpc.mock.calls.find(([m]) => m === 'runtime.sources.publish')![1]
    expect(sent).not.toHaveProperty('projection_approval_id'); expect(session.getState().result?.coverage).toBe('partial')
  })
  it('unknown prepare retains original command, blocks repeats, and terminal inspection permits only a new explicit fetch', async () => {
    const { rpc, session } = transport(); rpc.mockRejectedValue(new Error('secret token'))
    await session.prepare(); const retained = session.getState().attempt!
    await session.prepare(); expect(rpc).toHaveBeenCalledTimes(1)
    expect(retained.status).toBe('unknown'); expect(session.getState().error).toBe('unknown')
    rpc.mockResolvedValue({ command_id: retained.commandId, status: 'completed', run_id: 'run', owner_live: false, expires_at: null, result: null })
    await session.inspect(); expect(session.getState().review).toBeNull(); expect(rpc).toHaveBeenCalledTimes(2); expect(session.getState().attempt?.status).toBe('resolved')
    await session.prepare(); expect(rpc).toHaveBeenCalledTimes(3); expect((rpc.mock.calls[2][1] as ConnectedSourcePrepareParams).command_id).not.toBe(retained.commandId)
  })
  it('ignores late prepare after reconnect without issuing preview calls or a new read', async () => {
    const { rpc, session, detach } = transport(), held = deferred<ConnectedSourceResult>()
    rpc.mockImplementation(() => held.promise)
    const request = session.prepare(), sent = rpc.mock.calls[0][1] as ConnectedSourcePrepareParams
    detach(); session.attach(false)(); session.attach(true)
    held.resolve(fixture(sent).result); await request
    expect(session.getState().review).toBeNull(); expect(session.getState().attempt?.commandId).toBe(sent.command_id); expect(rpc).toHaveBeenCalledTimes(1)
  })
  it('invalidates reviewed content after a project edit and never accepts stale publication callbacks', async () => {

    const { rpc, session } = transport(); await session.prepare(); const review = session.getState().review!
    session.approve('original', true); session.approve('projection', true); session.edit({ project: 'other' }); await session.publish(review)
    expect(session.getState().review).toBeNull(); expect(rpc.mock.calls.some(([m]) => m === 'runtime.sources.publish')).toBe(false)
  })
  it('retains exact publish identity across unknown completion and ignores late reconnect success', async () => {

    const { rpc, session, detach } = transport(); await session.prepare(); const review = session.getState().review!, held = deferred<ConnectedSourceResult>()
    rpc.mockImplementation(() => held.promise); session.approve('original', true); session.approve('projection', true)
    const publishing = session.publish(review); detach(); session.attach(true)
    held.resolve(fixture(review.params).receipt()); await publishing
    expect(session.getState().attempt).toMatchObject({ commandId: review.params.command_id, preparationId: 'preparation', status: 'unknown', phase: 'publish' })
    expect(session.getState().result).toBeNull(); expect(session.getState().review).toBeNull()
  })
  it('cannot gain authority by supplying unresolved IDs, but own pending identity does not invalidate the in-flight read', async () => {

    const { rpc, session } = transport(), base = rpc.getMockImplementation()!, held = deferred<ConnectedSourceResult>()
    rpc.mockImplementation((m, p) => m === 'runtime.sources.prepare' ? held.promise : base(m, p))
    const preparing = session.prepare(), p = rpc.mock.calls[0][1] as ConnectedSourcePrepareParams
    session.setUnresolved([p.command_id]); held.resolve(fixture(p).result); await preparing
    expect(session.getState().attempt?.status).toBe('prepared'); expect(session.getState().review).not.toBeNull()
    session.setUnresolved(['foreign-command']); expect(session.getState().review).toBeNull(); await session.prepare()
    expect(rpc.mock.calls.filter(([m]) => m === 'runtime.sources.prepare')).toHaveLength(1)
  })
  it('requires new independent approval after an explicitly partial publication, using the same IDs', async () => {

    const { rpc, session } = transport(); await session.prepare(); const review = session.getState().review!
    const data = fixture(review.params); const partial = { ...data.receipt(true), original: review.result.original, projection: review.result.projection, observed_at: review.result.observed_at, fresh_until: review.result.fresh_until, record_json: JSON.stringify({ ...review.record, original_ref: { artifact_id: review.result.original!.artifact_id, version: 2, sha256: review.result.original!.sha256 }, projection_ref: null, publication_status: 'projection_not_confirmed', recovery: 'retry_exact_publish_without_refetch' }) }
    rpc.mockResolvedValue(partial); session.approve('original', true); session.approve('projection', true); await session.publish(review)
    expect(session.getState().attempt?.status).toBe('partial'); expect(session.getState().originalApproved).toBe(false)
    await session.publish(review); expect(rpc.mock.calls.filter(([m]) => m === 'runtime.sources.publish')).toHaveLength(1)
  })
})
