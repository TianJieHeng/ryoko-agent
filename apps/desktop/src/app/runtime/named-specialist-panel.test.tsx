// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, type RenderResult, within } from '@testing-library/react'
import { StrictMode } from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import type { RuntimeRequest } from '../../../../shared/src/runtime-control'

import { namedSpecialistCopy, namedSpecialistLocales } from './named-specialist-copy'
import { NamedSpecialistPanel, type NamedSpecialistPanelProps } from './named-specialist-panel'

const language = vi.hoisted(() => ({ locale: 'en' }))
vi.mock('@/i18n', () => ({ useI18n: () => ({ locale: language.locale, t: { common: { confirm: 'Confirm', cancel: 'Cancel', loading: 'Loading', done: 'Done', close: 'Close' }, errors: { genericFailure: 'Failed' } } }) }))

const digest = 'a'.repeat(64), config = 'b'.repeat(64), policy = 'c'.repeat(64)

const specialist = { agent_id: 'analyst', responsibility: 'Review source evidence <img src=x onerror=bad()>', manifest_sha256: digest,
  methods_ref: { id: 'methods', version: 2, sha256: digest }, limits: { max_depth: 1, max_total_children: 1, max_concurrent_children: 1 },
  grants: { allowed_tools: ['read_file'], project_grants: ['project:read'], mcp_grants: { retained: ['read'] }, memory_backend: 'builtin', personal_memory_access: false }, builtin_memory_namespace: 'isolated-analyst', output_contract_json: '{"type":"object","properties":{"summary":{"type":"string"}}}' }

const catalog = { specialists: [specialist], unavailable: [{ agent_id: 'writer', code: 'methods_unavailable' }], teams_enabled: false, execution: 'local_single_child' }

function preview(params: Record<string, unknown>) {
  const { session_id: _session, schema_version: _schema, ...task } = params

  return { specialist, selection: { ...task, manifest_sha256: digest, config_digest: config, parent_policy_digest: policy, mission_id: 'mission', mission_revision: 7, expires_at: Date.now() / 1000 + 300 }, preview_sha256: policy, runtime_revision: 8 }
}

function transport(overrides: Record<string, (params: Record<string, unknown>) => unknown> = {}) {
  return vi.fn(async (method: string, params: Record<string, unknown>): Promise<unknown> => {
    if (overrides[method]) { return overrides[method](params) }

    if (method === 'runtime.specialist.catalog') { return catalog }

    if (method === 'runtime.specialist.preview') { return preview(params) }

    if (method === 'runtime.specialist.handoff') { return { schema_version: 1, command_id: params.command_id, status: 'accepted', durable_revision: 9, run_id: 'run' } }

    if (method === 'runtime.specialist.status') { return { command_id: params.command_id, run_id: 'run', specialist_id: 'analyst', manifest_sha256: digest, project_id: 'project', status: 'claimed', outcome: 'unknown', completion: null, execution_resumed: false } }
    throw new Error('Unexpected RPC')
  })
}

function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>(yes => { resolve = yes });

  return { promise, resolve } }

const element = (props: NamedSpecialistPanelProps) => <StrictMode><NamedSpecialistPanel {...props} /></StrictMode>

function mount(rpc = transport()) {
  const props: NamedSpecialistPanelProps = { request: rpc as RuntimeRequest, sessionId: 'owned', connected: true, connectionGeneration: 1 }

  return { rpc, props, view: render(element(props)) }
}

function change(view: RenderResult, label: string, value: string) { fireEvent.change(view.getByLabelText(label), { target: { value } }) }

async function load(view: RenderResult) {
  change(view, namedSpecialistCopy.project[0], 'project')
  fireEvent.click(view.getByRole('button', { name: namedSpecialistCopy.load[0] }))
  const picker = await view.findByRole('combobox', { name: namedSpecialistCopy.choose[0] })
  fireEvent.keyDown(picker, { key: 'Enter' })
  fireEvent.click(await view.findByRole('option', { name: 'analyst' }))
  await view.findByLabelText(namedSpecialistCopy.objective[0])
}

async function prepare(view: RenderResult) {
  await load(view)
  change(view, namedSpecialistCopy.objective[0], 'Review only exact evidence')
  fireEvent.click(view.getByRole('button', { name: namedSpecialistCopy.preview[0] }))
  await view.findByRole('button', { name: namedSpecialistCopy.review[0] })
}

async function confirm(view: RenderResult) {
  await prepare(view)
  fireEvent.click(view.getByRole('button', { name: namedSpecialistCopy.review[0] }))

  return view.getByRole('button', { name: namedSpecialistCopy.confirm[0] })
}

beforeEach(() => { Element.prototype.scrollIntoView = vi.fn(); Element.prototype.hasPointerCapture = vi.fn(() => false); Element.prototype.setPointerCapture = vi.fn(); Element.prototype.releasePointerCapture = vi.fn() })
afterEach(() => { cleanup(); vi.restoreAllMocks(); language.locale = 'en' })

it('shows the real project catalog, readable authority and exact typed input fields without executing', async () => {
  const { view, rpc } = mount()
  await load(view)
  expect(view.getByText(specialist.responsibility)).toBeTruthy()
  expect(view.getByText('isolated-analyst')).toBeTruthy()
  expect(view.getByText('writer: methods_unavailable')).toBeTruthy()
  expect(view.getByLabelText(namedSpecialistCopy.schema[0]).textContent).toContain('summary')
  expect(view.container.querySelector('img')).toBeNull()
  expect(rpc.mock.calls).toEqual([['runtime.specialist.catalog', { session_id: 'owned', schema_version: 1, project_id: 'project' }]])
  const artifacts = within(view.getByRole('group', { name: namedSpecialistCopy.artifacts[0] }))
  fireEvent.click(artifacts.getByRole('button', { name: namedSpecialistCopy.add[0] }))
  fireEvent.change(artifacts.getByLabelText(namedSpecialistCopy.id[0]), { target: { value: 'source' } })
  fireEvent.change(artifacts.getByLabelText(namedSpecialistCopy.version[0]), { target: { value: '3' } })
  fireEvent.change(artifacts.getByLabelText(namedSpecialistCopy.digest[0]), { target: { value: digest } })
  const evidence = within(view.getByRole('group', { name: namedSpecialistCopy.evidence[0] }))
  fireEvent.click(evidence.getByRole('button', { name: namedSpecialistCopy.add[0] }))
  fireEvent.change(evidence.getByLabelText(namedSpecialistCopy.id[0]), { target: { value: 'anchor' } })
  fireEvent.change(evidence.getByLabelText(namedSpecialistCopy.version[0]), { target: { value: '4' } })
  fireEvent.change(evidence.getByLabelText(namedSpecialistCopy.digest[0]), { target: { value: config } })
  change(view, namedSpecialistCopy.objective[0], 'Review only supplied evidence')
  change(view, namedSpecialistCopy.constraints[0], 'Do not publish\nState uncertainty')
  fireEvent.click(view.getByRole('button', { name: namedSpecialistCopy.preview[0] }))
  await view.findByRole('button', { name: namedSpecialistCopy.review[0] })
  expect(rpc.mock.calls.at(-1)).toEqual(['runtime.specialist.preview', { session_id: 'owned', schema_version: 1, project_id: 'project', specialist_id: 'analyst', objective: 'Review only supplied evidence', artifacts: [{ id: 'source', version: 3, sha256: digest }], evidence: [{ id: 'anchor', version: 4, sha256: config }], constraints: ['Do not publish', 'State uncertainty'] }])
  expect(rpc.mock.calls.some(call => call[0] === 'runtime.specialist.handoff')).toBe(false)
})

it('requires a separate real confirmation and suppresses duplicate clicks', async () => {
  const held = deferred<unknown>(), { view, rpc } = mount(transport({ 'runtime.specialist.handoff': () => held.promise }))
  const button = await confirm(view)
  const dialog = within(view.getByRole('dialog'))
  expect(dialog.getByText(config)).toBeTruthy()
  expect(dialog.getByText('mission')).toBeTruthy()
  expect(dialog.getByText(namedSpecialistCopy.confirmation[0])).toBeTruthy()
  fireEvent.click(button); fireEvent.click(button)
  const writes = rpc.mock.calls.filter(call => call[0] === 'runtime.specialist.handoff')
  expect(writes).toHaveLength(1)
  expect(writes[0][1]).toMatchObject({ session_id: 'owned', schema_version: 1, expected_revision: 8, preview_sha256: policy, selection: { project_id: 'project', specialist_id: 'analyst', objective: 'Review only exact evidence' } })
  expect(typeof writes[0][1].command_id).toBe('string')
  await act(async () => held.resolve({ schema_version: 1, command_id: writes[0][1].command_id, status: 'accepted', durable_revision: 9, run_id: 'run' }))
  expect(await view.findByText(namedSpecialistCopy.admission[0])).toBeTruthy()
  expect(view.queryByRole('dialog')).toBeNull()
})

it('cancel closes the real confirmation without a handoff', async () => {
  const { view, rpc } = mount()
  await confirm(view)
  fireEvent.click(view.getByRole('button', { name: namedSpecialistCopy.cancel[0] }))
  expect(view.queryByRole('dialog')).toBeNull()
  expect(rpc.mock.calls.some(call => call[0] === 'runtime.specialist.handoff')).toBe(false)
})

it('retains an unknown original command across same-object reconnect and only inspects it', async () => {
  const { view, rpc, props } = mount(transport({ 'runtime.specialist.handoff': () => { throw new Error('lost') } }))
  fireEvent.click(await confirm(view))
  await view.findByRole('alert')
  const command = rpc.mock.calls.find(call => call[0] === 'runtime.specialist.handoff')![1].command_id
  view.rerender(element({ ...props, connected: false }))
  expect(view.getByText(namedSpecialistCopy.offline[0])).toBeTruthy()
  view.rerender(element({ ...props, connectionGeneration: 2 }))
  expect(view.queryByRole('dialog')).toBeNull()
  expect(view.queryByRole('button', { name: namedSpecialistCopy.review[0] })).toBeNull()
  fireEvent.click(view.getByRole('button', { name: namedSpecialistCopy.inspect[0] }))
  await view.findByText('Unknown', { exact: false })
  expect(rpc.mock.calls.at(-1)).toEqual(['runtime.specialist.status', { session_id: 'owned', schema_version: 1, command_id: command }])
  expect(rpc.mock.calls.filter(call => call[0] === 'runtime.specialist.handoff')).toHaveLength(1)
})

it.each(['session', 'request', 'generation', 'disconnect'] as const)('revokes an open confirmation on %s change', async kind => {
  const { view, rpc, props } = mount()
  const button = await confirm(view)
  const changed: NamedSpecialistPanelProps = { ...props }

  if (kind === 'session') { changed.sessionId = 'other-owned' }

  if (kind === 'request') { changed.request = transport() as RuntimeRequest }

  if (kind === 'generation') { changed.connectionGeneration = 2 }

  if (kind === 'disconnect') { changed.connected = false }
  view.rerender(element(changed))
  expect(view.queryByRole('dialog')).toBeNull()
  fireEvent.click(button)
  expect(rpc.mock.calls.some(call => call[0] === 'runtime.specialist.handoff')).toBe(false)
})

it('project edits revoke preview and reject a late catalog from the old project', async () => {
  const held = deferred<unknown>(), { view } = mount(transport({ 'runtime.specialist.catalog': () => held.promise }))
  change(view, namedSpecialistCopy.project[0], 'project')
  fireEvent.click(view.getByRole('button', { name: namedSpecialistCopy.load[0] }))
  change(view, namedSpecialistCopy.project[0], 'other-project')
  await act(async () => held.resolve(catalog))
  expect(view.queryByRole('combobox')).toBeNull()
  expect(view.queryByRole('button', { name: namedSpecialistCopy.review[0] })).toBeNull()
})

it('preview responses after an objective edit cannot authorize the new objective', async () => {
  const held = deferred<unknown>(), { view, rpc } = mount(transport({ 'runtime.specialist.preview': () => held.promise }))
  await load(view)
  change(view, namedSpecialistCopy.objective[0], 'Original')
  fireEvent.click(view.getByRole('button', { name: namedSpecialistCopy.preview[0] }))
  const input = rpc.mock.calls.at(-1)![1]
  change(view, namedSpecialistCopy.objective[0], 'Changed')
  await act(async () => held.resolve(preview(input)))
  expect(view.queryByRole('button', { name: namedSpecialistCopy.review[0] })).toBeNull()
})

it('expired confirmation fails closed before the RPC', async () => {
  const { view, rpc } = mount()
  const button = await confirm(view)
  vi.spyOn(Date, 'now').mockReturnValue(Date.now() + 301000)
  fireEvent.click(button)
  expect(await view.findByRole('alert')).toBeTruthy()
  expect(rpc.mock.calls.some(call => call[0] === 'runtime.specialist.handoff')).toBe(false)
})

it('external recovery identifiers block new submission and return inert reviewed completion', async () => {
  const rpc = transport({ 'runtime.specialist.status': p => ({ command_id: p.command_id, run_id: 'run', specialist_id: 'analyst', manifest_sha256: digest, project_id: 'project', status: 'completed', outcome: 'completed', execution_resumed: false,
    completion: { specialist_id: 'analyst', manifest_sha256: digest, project_id: 'project', child_id: 'child', handoff_sha256: config, state: 'completed', summary: '<script>unsafe()</script>', summary_truncated: true, schema_valid: false, parent_review_required: true, execution_resumed: false } }) })

  const { view, props } = mount(rpc)
  view.rerender(element({ ...props, unresolvedCommandIds: ['old-command'] }))
  await load(view)
  change(view, namedSpecialistCopy.objective[0], 'Review')
  expect((view.getByRole('button', { name: namedSpecialistCopy.preview[0] }) as HTMLButtonElement).disabled).toBe(true)
  fireEvent.click(view.getByRole('button', { name: `${namedSpecialistCopy.inspect[0]}: old-command` }))
  expect(await view.findByText(namedSpecialistCopy.reviewRequired[0])).toBeTruthy()
  expect(view.getByLabelText(namedSpecialistCopy.summary[0]).textContent).toBe('<script>unsafe()</script>')
  expect(view.container.querySelector('script')).toBeNull()
  expect(rpc.mock.calls.map(call => call[0])).toEqual(['runtime.specialist.catalog', 'runtime.specialist.status'])
})

it.each(namedSpecialistLocales)('has complete authored %s copy and renders its safety guidance', locale => {
  const index = namedSpecialistLocales.indexOf(locale)

  for (const translations of Object.values(namedSpecialistCopy)) { expect(translations).toHaveLength(namedSpecialistLocales.length); expect(translations[index].trim()).not.toBe('') }
  language.locale = locale
  const { view } = mount()
  expect(view.getByText(namedSpecialistCopy.scope[index])).toBeTruthy()
  expect(view.getByRole('button', { name: namedSpecialistCopy.load[index] })).toBeTruthy()
})


it.each(['session', 'request', 'generation', 'disconnect'] as const)('drops a delayed preview on %s change', async kind => {
  const held = deferred<unknown>(), { view, props, rpc } = mount(transport({ 'runtime.specialist.preview': () => held.promise }))
  await load(view)
  change(view, namedSpecialistCopy.objective[0], 'Original')
  fireEvent.click(view.getByRole('button', { name: namedSpecialistCopy.preview[0] }))
  const input = rpc.mock.calls.at(-1)![1]
  const changed: NamedSpecialistPanelProps = { ...props }

  if (kind === 'session') { changed.sessionId = 'other-owned' }

  if (kind === 'request') { changed.request = transport() as RuntimeRequest }

  if (kind === 'generation') { changed.connectionGeneration = 2 }

  if (kind === 'disconnect') { changed.connected = false }
  view.rerender(element(changed))
  await act(async () => held.resolve(preview(input)))
  expect(view.queryByRole('button', { name: namedSpecialistCopy.review[0] })).toBeNull()
  expect(view.queryByRole('dialog')).toBeNull()
  expect(rpc.mock.calls.some(call => call[0] === 'runtime.specialist.handoff')).toBe(false)
})

it('an unmounted preview response cannot affect a newly mounted panel', async () => {
  const held = deferred<unknown>(), { view, props, rpc } = mount(transport({ 'runtime.specialist.preview': () => held.promise }))
  await load(view)
  change(view, namedSpecialistCopy.objective[0], 'Original')
  fireEvent.click(view.getByRole('button', { name: namedSpecialistCopy.preview[0] }))
  const input = rpc.mock.calls.at(-1)![1]
  view.unmount()
  const fresh = render(element(props))
  await act(async () => held.resolve(preview(input)))
  expect(fresh.queryByRole('button', { name: namedSpecialistCopy.review[0] })).toBeNull()
})
