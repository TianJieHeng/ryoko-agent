import { describe, expect, it, vi } from 'vitest'

import type { SpecialistDescriptor, SpecialistPreviewParams } from './gateway-contract.generated.js'
import type { RuntimeRequest } from './runtime-control.js'
import { type NamedSpecialistDraft, namedSpecialistInput, NamedSpecialistSession, parseNamedSpecialistCatalog, verifyNamedSpecialistPreview, verifyNamedSpecialistStatus } from './runtime-named-specialists.js'

const digest = 'a'.repeat(64), config = 'b'.repeat(64), policy = 'c'.repeat(64)

const specialist: SpecialistDescriptor = { agent_id: 'analyst', responsibility: 'Review exact evidence', manifest_sha256: digest, methods_ref: { id: 'methods', version: 2, sha256: digest },
  limits: { max_depth: 1, max_total_children: 1, max_concurrent_children: 1 }, grants: { allowed_tools: ['read_file'], project_grants: ['project:read'], mcp_grants: { retained: ['read'] }, memory_backend: 'builtin', personal_memory_access: false }, builtin_memory_namespace: 'isolated-analyst', output_contract_json: '{"type":"object","properties":{"summary":{"type":"string"}}}' }

const draft: NamedSpecialistDraft = { projectId: 'project', specialistId: 'analyst', objective: 'Review only supplied evidence', artifacts: [{ id: 'source', version: '3', sha256: digest }], evidence: [{ id: 'anchor', version: '4', sha256: config }], constraints: 'Do not publish\nState uncertainty' }
const catalog = { specialists: [specialist], unavailable: [{ agent_id: 'writer', code: 'methods_unavailable' }], teams_enabled: false, execution: 'local_single_child' }

function preview(input = namedSpecialistInput('owned', draft)) {
  const { session_id: _session, schema_version: _schema, ...task } = input

  return { specialist, selection: { ...task, manifest_sha256: digest, config_digest: config, parent_policy_digest: policy, mission_id: 'mission', mission_revision: 7, expires_at: Date.now() / 1000 + 300 }, preview_sha256: policy, runtime_revision: 8 }
}

function status(command_id: string, outcome = 'unknown') {
  return { command_id, run_id: 'run', specialist_id: 'analyst', manifest_sha256: digest, project_id: 'project', status: outcome === 'unknown' ? 'claimed' : outcome, outcome, completion: null, execution_resumed: false }
}

function deferred<T>() { let resolve!: (value: T) => void, reject!: (reason: unknown) => void; const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no });

  return { promise, resolve, reject } }

function fixture(overrides: Record<string, (params: Record<string, unknown>) => unknown> = {}) {
  const rpc = vi.fn(async (method: string, params: Record<string, unknown>): Promise<unknown> => {
    if (overrides[method]) { return overrides[method](params) }

    if (method === 'runtime.specialist.catalog') { return catalog }

    if (method === 'runtime.specialist.preview') { return preview(params as unknown as SpecialistPreviewParams) }

    if (method === 'runtime.specialist.handoff') { return { schema_version: 1, command_id: params.command_id, status: 'accepted', durable_revision: 9, run_id: 'run' } }

    if (method === 'runtime.specialist.status') { return status(String(params.command_id)) }
    throw new Error('Unexpected RPC')
  })

  let id = 0
  const session = new NamedSpecialistSession(rpc as RuntimeRequest, 'owned', () => `id-${++id}`)
  const detach = session.attach(true)

  return { session, rpc, detach }
}

async function prepare(session: NamedSpecialistSession) { session.edit({ projectId: 'project' }); await session.catalog(); session.edit(draft); await session.preview();

  return session.getState().review! }

it('builds only exact typed input without trimming or overriding scope', () => {
  expect(namedSpecialistInput('owned', draft)).toEqual({ session_id: 'owned', schema_version: 1, project_id: 'project', specialist_id: 'analyst', objective: draft.objective, artifacts: [{ id: 'source', version: 3, sha256: digest }], evidence: [{ id: 'anchor', version: 4, sha256: config }], constraints: ['Do not publish', 'State uncertainty'] })
  expect(() => namedSpecialistInput('owned', { ...draft, objective: ' ' })).toThrow()
  expect(() => namedSpecialistInput('owned', { ...draft, artifacts: [{ id: 'source', version: '1.5', sha256: digest }] })).toThrow()
  expect(() => namedSpecialistInput('owned', { ...draft, evidence: [{ id: 'anchor', version: '1', sha256: digest.toUpperCase() }] })).toThrow()
  expect(() => namedSpecialistInput('owned', { ...draft, constraints: Array(17).fill('constraint').join('\n') })).toThrow()
  expect(() => namedSpecialistInput('owned', { ...draft, artifacts: Array(33).fill(draft.artifacts[0]) })).toThrow()
})

it('rejects team, personal-memory and hidden-delegation projections', () => {
  expect(parseNamedSpecialistCatalog(catalog).specialists[0]).toEqual(specialist)
  expect(() => parseNamedSpecialistCatalog({ ...catalog, teams_enabled: true })).toThrow()
  expect(() => parseNamedSpecialistCatalog({ ...catalog, specialists: [{ ...specialist, grants: { ...specialist.grants, personal_memory_access: true } }] })).toThrow()
  expect(() => parseNamedSpecialistCatalog({ ...catalog, specialists: [{ ...specialist, grants: { ...specialist.grants, allowed_tools: ['delegate_task'] } }] })).toThrow()
  expect(() => parseNamedSpecialistCatalog({ ...catalog, specialists: [specialist, specialist] })).toThrow()
})

it('pins manifest, exact references, objective, project, mission and expiry', () => {
  const input = namedSpecialistInput('owned', draft), result = preview(input)
  expect(verifyNamedSpecialistPreview(result, input, specialist)).toEqual(result)
  const changes = [{ project_id: 'other' }, { objective: 'different' }, { manifest_sha256: config }, { mission_revision: null }, { artifacts: [{ id: 'source', version: 4, sha256: digest }] }, { expires_at: Date.now() / 1000 - 1 }, { extra: 'unsafe' }]

  for (const change of changes) { expect(() => verifyNamedSpecialistPreview({ ...result, selection: { ...result.selection, ...change } }, input, specialist)).toThrow() }
  expect(() => verifyNamedSpecialistPreview({ ...result, specialist: { ...specialist, responsibility: 'Changed' } }, input, specialist)).toThrow()
})

it('catalog and preview never execute; confirmation sends exactly one stable canonical command', async () => {
  const { session, rpc } = fixture(), review = await prepare(session)
  expect(rpc.mock.calls.map(call => call[0])).toEqual(['runtime.specialist.catalog', 'runtime.specialist.preview'])
  await Promise.all([session.handoff(review), session.handoff(review)])
  const writes = rpc.mock.calls.filter(call => call[0] === 'runtime.specialist.handoff')
  expect(writes).toEqual([['runtime.specialist.handoff', review.params]])
  expect(review.params).toMatchObject({ session_id: 'owned', command_id: 'id-1', idempotency_key: 'id-2', expected_revision: 8, preview_sha256: policy })
  expect(session.getState().review).toBeNull()
  expect(session.getState().attempt?.receipt?.status).toBe('accepted')
  expect(session.blocked()).toBe(true)
})

it('an unknown response retains original input and exact read-only recovery without replay', async () => {
  const { session, rpc } = fixture({ 'runtime.specialist.handoff': () => { throw new Error('transport lost') } }), review = await prepare(session)
  await session.handoff(review)
  expect(session.getState().attempt).toMatchObject({ review, unknown: true })
  expect(session.getState().error).toBe('unknown')
  await session.preview(); await session.handoff(review); await session.inspect()
  expect(rpc.mock.calls.filter(call => call[0] === 'runtime.specialist.handoff')).toHaveLength(1)
  expect(rpc.mock.calls.at(-1)).toEqual(['runtime.specialist.status', { session_id: 'owned', schema_version: 1, command_id: 'id-1' }])
  expect(session.getState().attempt?.review.params).toEqual(review.params)
  expect(session.getState().status?.outcome).toBe('unknown')
  expect(session.blocked()).toBe(true)
})

it('recovery validates command, original scope, parent-review and non-resumption claims', () => {
  const base = { ...status('command', 'completed'), completion: { specialist_id: 'analyst', manifest_sha256: digest, project_id: 'project', child_id: 'child', handoff_sha256: config, state: 'completed', summary: 'Done', summary_truncated: false, schema_valid: true, parent_review_required: true, execution_resumed: false } }
  expect(verifyNamedSpecialistStatus(base, 'command').completion?.summary).toBe('Done')
  expect(() => verifyNamedSpecialistStatus(base, 'another-command')).toThrow()
  expect(() => verifyNamedSpecialistStatus(base, 'command', undefined, 'different-run')).toThrow()
  expect(() => verifyNamedSpecialistStatus({ ...base, execution_resumed: true }, 'command')).toThrow()
  expect(() => verifyNamedSpecialistStatus({ ...base, completion: { ...base.completion, parent_review_required: false } }, 'command')).toThrow()
  expect(() => verifyNamedSpecialistStatus({ ...base, status: 'claimed', outcome: 'completed' }, 'command')).toThrow()
})

it('terminal recovery releases the blocker across reconnect without reusing preview', async () => {
  const { session, detach, rpc } = fixture({ 'runtime.specialist.status': p => status(String(p.command_id), 'completed') })
  await session.handoff(await prepare(session)); await session.inspect()
  expect(session.blocked()).toBe(false)
  detach(); session.attach(true)
  expect(session.blocked()).toBe(false)
  expect(session.getState().review).toBeNull()
  expect(rpc.mock.calls.filter(call => call[0] === 'runtime.specialist.handoff')).toHaveLength(1)
})

it('unknown recovery IDs block new handoffs and cannot transfer confirmation authority', async () => {
  const { session, rpc } = fixture()
  session.setUnresolved(['previous-command'])
  expect(await prepare(session)).toBeNull()
  await session.inspect('previous-command')
  expect(session.blocked()).toBe(true)
  expect(rpc.mock.calls.filter(call => call[0] === 'runtime.specialist.preview')).toHaveLength(0)
})

describe('scope and lifetime fences', () => {
  it('drops delayed project catalogs and previews after an input edit', async () => {
    const held = deferred<unknown>(), { session } = fixture({ 'runtime.specialist.catalog': () => held.promise })
    session.edit({ projectId: 'project' }); const loading = session.catalog()
    session.edit({ projectId: 'other-project' }); held.resolve(catalog); await loading
    expect(session.getState()).toMatchObject({ catalog: null, review: null, busy: false, draft: { projectId: 'other-project' } })
    const waiting = deferred<unknown>(), next = fixture({ 'runtime.specialist.preview': () => waiting.promise })
    next.session.edit({ projectId: 'project' }); await next.session.catalog(); next.session.edit(draft)
    const preparing = next.session.preview(); next.session.edit({ objective: 'Changed objective' }); waiting.resolve(preview()); await preparing
    expect(next.session.getState().review).toBeNull()
  })

  it('ignores old response across detach/reattach and preserves the original unknown command', async () => {
    const held = deferred<unknown>(), { session, rpc, detach } = fixture({ 'runtime.specialist.handoff': () => held.promise })
    const review = await prepare(session), running = session.handoff(review)
    detach(); session.attach(true)
    held.resolve({ schema_version: 1, command_id: review.params.command_id, status: 'accepted', durable_revision: 9, run_id: 'run' }); await running
    expect(session.getState().attempt).toMatchObject({ review, unknown: true, receipt: null })
    expect(session.getState()).toMatchObject({ busy: false, review: null, catalog: null })
    await session.handoff(review)
    expect(rpc.mock.calls.filter(call => call[0] === 'runtime.specialist.handoff')).toHaveLength(1)
  })

  it('rejects expired, previously inspected and edited reviews before any handoff', async () => {
    const { session, rpc } = fixture(), review = await prepare(session)
    session.edit({ constraints: 'Changed' }); await session.handoff(review)
    expect(session.getState().error).toBe('changed')
    const fresh = await prepare(session)
    vi.spyOn(Date, 'now').mockReturnValue(fresh.params.selection.expires_at * 1000 + 1)
    await session.handoff(fresh)
    vi.restoreAllMocks()
    expect(session.getState().error).toBe('changed')
    expect(rpc.mock.calls.filter(call => call[0] === 'runtime.specialist.handoff')).toHaveLength(0)
  })
})


it('manual unknown status blocks a fresh handoff and later terminal proof releases only that command', async () => {
  let completed = false
  const { session } = fixture({ 'runtime.specialist.status': p => status(String(p.command_id), completed ? 'completed' : 'unknown') })
  await session.inspect('manual-command')
  expect(session.blocked()).toBe(true)
  expect(session.getState().unresolved).toEqual(['manual-command'])
  completed = true; await session.inspect('manual-command')
  expect(session.blocked()).toBe(false)
})

it('an external recovery change consumes any prepared preview authority', async () => {
  const { session, rpc } = fixture(), review = await prepare(session)
  session.setUnresolved(['previous-command'])
  expect(session.getState().review).toBeNull()
  session.setUnresolved([]); await session.handoff(review)
  expect(rpc.mock.calls.some(call => call[0] === 'runtime.specialist.handoff')).toBe(false)
})
