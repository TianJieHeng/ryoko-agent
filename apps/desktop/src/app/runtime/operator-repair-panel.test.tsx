// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, type RenderResult, within } from '@testing-library/react'
import { StrictMode } from 'react'
import { afterEach, expect, it, vi } from 'vitest'

import type { RuntimeRequest } from '../../../../shared/src/runtime-control'

import type { OperatorRepairPanelProps } from './operator-repair-panel'

const language = vi.hoisted(() => ({ locale: 'en' }))
vi.mock('@/i18n', () => ({ useI18n: () => ({ locale: language.locale, t: { common: { confirm: 'Confirm', cancel: 'Cancel', loading: 'Loading', done: 'Done', close: 'Close' }, errors: { genericFailure: 'Failed' } } }) }))
import { operatorCopy, OperatorRepairPanel } from './operator-repair-panel'

const digest = 'a'.repeat(64), otherDigest = 'b'.repeat(64)
const limits = ['sqlite_free_pages_and_wal_not_forensically_erased', 'backups_require_separate_expiry', 'provider_copies_no_deletion_acknowledgment', 'exports_and_external_caches_not_enumerated', 'active_process_copies_require_session_end', 'mandatory_recovery_journal_retained', 'artifacts_and_mission_evidence_retained', 'context_projections_and_session_activity_metadata_retained', 'session_json_jsonl_and_request_dumps_retained', 'durable_schedules_monitors_and_commitments_retained', 'delegation_handoffs_roots_workspaces_retained', 'bounded_service_source_receipt_blobs_and_channel_bindings_retained', 'remote_harness_deletion_not_supported']

function inspection(session = 'owned') {
  return { schema_version: 1, actor: { principal_id: 'actor-one', profile_id: 'profile-one', agent_id: 'agent-one' }, session_id: session, revision: 8, replay_cursor: 'epoch:8',
    state: { status: 'blocked', run_id: 'run-one', last_command_id: 'command-one' }, ownership: { active: true, generation: 2 }, waiting_reason: 'unresolved_effect',
    effects: [{ effect_id: 'effect-one', state: 'unknown', operation_type: '<img src=x onerror=bad()>' }], deliveries: [], budgets: [], truncated: false, memory_backend: 'builtin',
    connection_health: { sqlite: 'readable', provider: 'not_probed', memory_remote: 'not_probed' }, laya_mode: 'not_certified', checkpoint: null, sensitive_ingestion_certified: false }
}

function plan() {
  return { schema_version: 1, action: 'reconcile-effect', actor: inspection().actor, session_id: 'owned', target_id: 'effect-one', affected_ids: ['effect-one'], before_revision: 8,
    target: { effect_id: 'effect-one', state: 'unknown', updated_at: 10, operation_type: '<img src=x onerror=bad()>' }, invariant_checks: ['live_policy', 'exact_actor', 'owning_profile', 'preview_cas', 'mandatory_journal'], expected_after: 'evidence_only_no_redispatch', expires_at: Date.now() / 1000 + 300, plan_digest: digest }
}

function privacy() {
  return { schema_version: 1, session_id: 'owned', stores: { messages: 3, runtime_commands: 1, runtime_events: 8, runtime_context_projections: 1, runtime_effects: 1, runtime_artifact_versions: 1 },
    retention: { messages: 'explicit_previewed_logical_erasure', recovery_journal: 'checkpoint_bounded_pruning_only', effects: 'retain_unresolved_and_dedup_evidence', individual_memory: 'explicit_record_purge_and_projection_rebuild', artifacts: 'retained_no_automatic_expiry', logs: 'existing_log_rotation_not_certified', backups: 'operator_managed_no_expiry_proof', provider_copies: 'unknown' }, retained_derived_store_counts: {}, limitations: limits, complete_deletion_supported: false }
}

function manifest() {
  const expires_at = Date.now() / 1000 + 300

  return { schema_version: 1, kind: 'transcript_payload', actor: inspection().actor, session_id: 'owned', record_refs: ['owned'], stores: ['messages_including_inactive', 'message_fts_projections', 'session_title_and_system_prompt', 'unreferenced_system_prompts'], requested: expires_at - 300, expires_at, before_revision: 8, source: { message_count: 3, source_digest: otherDigest }, acknowledgments: [], limitations: limits, complete_deletion: false, manifest_digest: digest }
}

function transport(overrides: Record<string, (params: Record<string, unknown>) => unknown> = {}) {
  const original = JSON.stringify(plan(), null, 2), deletion = JSON.stringify(manifest(), null, 2)

  const rpc = vi.fn(async (method: string, params: Record<string, unknown>): Promise<unknown> => {
    if (overrides[method]) { return overrides[method](params) }

    const results: Record<string, unknown> = {
      'runtime.operations.inspect': inspection(params.session_id as string), 'runtime.operations.retention': privacy(),
      'runtime.operations.repair.apply': { plan_digest: digest, outcome: 'applied', effect_id: 'effect-one', state: 'unknown', redispatched: false },
      'runtime.operations.audit': { status: 'ok', last_cursor: 'epoch:8', has_more: false, events: [{ schema_version: 1, type: 'operations.repair_finished', purpose: 'operator', correlation_ids: { session_id: 'owned', operation_id: digest }, retention_class: 'operational_audit', redacted_fields: ['payload', 'provider_receipts', 'arguments', 'paths', 'credentials'] }] },
      'runtime.operations.checkpoint': { status: 'unavailable', restore_allowed: false, checks: {}, blocking_gates: ['checkpoint_missing'] }
    }

    if (method === 'runtime.operations.repair.prepare') { return { record_json: original } }

    if (method === 'runtime.operations.deletion.prepare') { return { record_json: deletion } }

    if (method === 'runtime.operations.deletion.apply') { const value = JSON.parse(deletion);

 return { record_json: JSON.stringify({ ...value, acknowledgments: value.stores.map((store: string) => ({ store, status: 'logical_deletion_acknowledged' })), completed_at: Date.now() / 1000 }) } }

    if (!(method in results)) { throw new Error('Unexpected method') }

    return { record_json: JSON.stringify(results[method]) }
  })

  return { rpc, original, deletion }
}

const element = (props: OperatorRepairPanelProps) => <StrictMode><OperatorRepairPanel {...props} /></StrictMode>

function mounted(rpc: ReturnType<typeof transport>['rpc']) {
  const props = { connected: true, request: rpc as RuntimeRequest, sessionId: 'owned' }

  return { props, view: render(element(props)) }
}

async function inspect(view: RenderResult) { fireEvent.click(view.getByRole('button', { name: operatorCopy.inspect[0] })); await view.findByLabelText(operatorCopy.target[0]) }

async function preview(view: RenderResult) {
  await inspect(view)
  fireEvent.click(view.getByRole('button', { name: operatorCopy.previewRepair[0] }))
  await view.findByRole('button', { name: operatorCopy.review[0] })
}

async function review(view: RenderResult) { await preview(view); fireEvent.click(view.getByRole('button', { name: operatorCopy.review[0] }));

 return view.getByRole('button', { name: operatorCopy.confirm[0] }) }

function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>(yes => { resolve = yes });

 return { promise, resolve } }

afterEach(() => { cleanup(); language.locale = 'en' })

it('uses the compiled StrictMode path, actual dialog, and exact preview bytes with a separate deliberate confirmation', async () => {
  const { rpc, original } = transport(), { view } = mounted(rpc)
  expect(rpc).not.toHaveBeenCalled()
  const confirm = await review(view)
  expect(rpc.mock.calls.some(([method]) => method.endsWith('.apply'))).toBe(false)
  const dialog = view.getByRole('dialog')
  expect(dialog.textContent).toContain(operatorCopy.confirmation[0]); expect(dialog.textContent).toContain(operatorCopy.reconcileMeaning[0])
  expect(dialog.querySelector('pre')?.textContent).toBe(original)
  expect(view.container.querySelector('img')).toBeNull()
  fireEvent.click(confirm); fireEvent.click(confirm)
  await view.findByText(operatorCopy.receipt[0])
  expect(rpc.mock.calls.filter(([method]) => method.endsWith('.apply'))).toHaveLength(1)
  expect(rpc).toHaveBeenCalledWith('runtime.operations.repair.apply', { session_id: 'owned', schema_version: 1, plan_json: original, authorization_digest: digest })
  expect(view.getByText(operatorCopy.before[0])).toBeTruthy()
  await view.findByText(operatorCopy.after[0])
})

it.each(['session', 'request', 'connected', 'generation'] as const)('invalidates review and hides stale scope data on %s change', async change => {
  const { rpc } = transport(), { props, view } = mounted(rpc)
  const staleConfirm = await review(view)
  const next = { ...props }

  const updates = {
    session: () => { next.sessionId = 'other' }, request: () => { next.request = transport().rpc as RuntimeRequest },
    connected: () => { next.connected = false }, generation: () => undefined
  }

  updates[change]()
  view.rerender(element({ ...next, connectionGeneration: change === 'generation' ? 2 : undefined }))
  expect(view.queryByRole('dialog')).toBeNull()
  expect(view.container.textContent).not.toContain('effect-one')
  fireEvent.click(staleConfirm)

  if (change === 'connected') { view.rerender(element(props)); expect(view.queryByRole('dialog')).toBeNull() }
  expect(rpc.mock.calls.some(([method]) => method.endsWith('.apply'))).toBe(false)
})

it('invalidates on target edit and cancellation, without reusing dismissed approval', async () => {
  const { rpc } = transport(), { view } = mounted(rpc)
  await preview(view)
  fireEvent.change(view.getByLabelText(operatorCopy.target[0]), { target: { value: 'other' } })
  expect(view.queryByRole('button', { name: operatorCopy.review[0] })).toBeNull()
  expect((view.getByRole('button', { name: operatorCopy.previewRepair[0] }) as HTMLButtonElement).disabled).toBe(true)
  fireEvent.change(view.getByLabelText(operatorCopy.target[0]), { target: { value: 'effect-one' } })
  fireEvent.click(view.getByRole('button', { name: operatorCopy.previewRepair[0] })); await view.findByRole('button', { name: operatorCopy.review[0] })
  fireEvent.click(view.getByRole('button', { name: operatorCopy.review[0] })); fireEvent.click(view.getByRole('button', { name: operatorCopy.cancel[0] }))
  expect(view.queryByRole('dialog')).toBeNull()
  expect(view.queryByRole('button', { name: operatorCopy.review[0] })).toBeNull()
  expect(rpc.mock.calls.some(([method]) => method.endsWith('.apply'))).toBe(false)
})

it('ignores late preview after same-object reconnect and never performs automatic reconnect work', async () => {
  const held = deferred<{ record_json: string }>()
  const { rpc, original } = transport({ 'runtime.operations.repair.prepare': () => held.promise }), { view, props } = mounted(rpc)
  await inspect(view); fireEvent.click(view.getByRole('button', { name: operatorCopy.previewRepair[0] }))
  view.rerender(element({ ...props, connected: false })); view.rerender(element(props))
  await act(async () => { held.resolve({ record_json: original }); await held.promise })
  expect(view.queryByRole('button', { name: operatorCopy.review[0] })).toBeNull()
  expect(view.queryByLabelText(operatorCopy.target[0])).toBeNull()
  expect(rpc).toHaveBeenCalledTimes(2)
})

it('keeps unknown apply digest through reconnect, hides transport secrets, and treats audit as inspection only', async () => {
  const { rpc } = transport({ 'runtime.operations.repair.apply': () => { throw new Error('credential=DO_NOT_SHOW') } }), { view, props } = mounted(rpc)
  fireEvent.click(await review(view)); await view.findByRole('alert')
  expect(view.container.textContent).toContain(digest)
  expect(view.container.textContent).not.toContain('DO_NOT_SHOW')
  view.rerender(element({ ...props, connected: false })); view.rerender(element(props))
  expect(view.container.textContent).toContain(digest)
  await inspect(view); fireEvent.click(view.getByRole('button', { name: operatorCopy.audit[0] }))
  await view.findByText(operatorCopy.auditLimit[0])
  expect((view.getByRole('button', { name: operatorCopy.previewRepair[0] }) as HTMLButtonElement).disabled).toBe(true)
  expect(rpc.mock.calls.filter(([method]) => method.endsWith('.apply'))).toHaveLength(1)
  view.rerender(element({ ...props, sessionId: 'other' }))
  expect(view.container.textContent).not.toContain(digest)
  expect(view.container.textContent).not.toContain('effect-one')
})

it('shows broker rejection, removes stale review, and requires explicit fresh inspection', async () => {
  const { rpc } = transport({ 'runtime.operations.repair.apply': () => { throw { data: { code: 'operations_preview_changed' } } } }), { view } = mounted(rpc)
  fireEvent.click(await review(view)); await view.findByRole('alert')
  expect(view.queryByLabelText(operatorCopy.target[0])).toBeNull()
  expect(view.queryByRole('dialog')).toBeNull()
  expect(view.getByRole('alert').textContent).toContain(operatorCopy.rejected[0])
  expect(rpc).toHaveBeenCalledTimes(3)
})

it('previews scoped transcript deletion and displays real partial acknowledgments without claiming full erasure', async () => {
  const { rpc, deletion } = transport(), { view } = mounted(rpc)
  await inspect(view); fireEvent.click(view.getByRole('button', { name: operatorCopy.retention[0] }))
  await view.findByRole('button', { name: operatorCopy.previewDelete[0] })
  fireEvent.click(view.getByRole('button', { name: operatorCopy.previewDelete[0] })); await view.findByRole('button', { name: operatorCopy.review[0] })
  expect(rpc).toHaveBeenCalledWith('runtime.operations.deletion.prepare', { session_id: 'owned', schema_version: 1, memory_record_id: null })
  fireEvent.click(view.getByRole('button', { name: operatorCopy.review[0] }))
  expect(within(view.getByRole('dialog')).getByText(new RegExp('Logical deletion is partial'))).toBeTruthy()
  fireEvent.click(view.getByRole('button', { name: operatorCopy.confirm[0] })); await view.findByText(operatorCopy.partial[0])
  expect(rpc).toHaveBeenCalledWith('runtime.operations.deletion.apply', { session_id: 'owned', schema_version: 1, plan_json: deletion, authorization_digest: digest })
  expect(view.container.textContent).toContain('logical_deletion_acknowledged')
  expect(view.container.textContent).toContain('"complete_deletion": false')
})

it('never restores authority from passive reopened digests and does not discard an in-flight response when its own ID is retained', async () => {
  const held = deferred<{ record_json: string }>(), { rpc } = transport({ 'runtime.operations.repair.apply': () => held.promise }), { view, props } = mounted(rpc)
  fireEvent.click(await review(view))
  view.rerender(element({ ...props, unresolvedOperationIds: [digest] }))
  await act(async () => { held.resolve({ record_json: JSON.stringify({ plan_digest: digest, outcome: 'applied', effect_id: 'effect-one', state: 'unknown', redispatched: false }) }); await held.promise })
  await view.findByText(operatorCopy.receipt[0])
  view.unmount()
  const reopened = render(element({ ...props, unresolvedOperationIds: [otherDigest] }))
  expect(reopened.queryByRole('dialog')).toBeNull()
  await inspect(reopened)
  expect((reopened.getByRole('button', { name: operatorCopy.previewRepair[0] }) as HTMLButtonElement).disabled).toBe(true)
})

it.each(['en', 'de', 'es', 'fr', 'ja', 'zh', 'zh-hant', 'ar', 'ru'])('renders authored %s controls and safety copy without requests on opening', locale => {
  language.locale = locale
  const { rpc } = transport(), { view } = mounted(rpc), index = ['en', 'de', 'es', 'fr', 'ja', 'zh', 'zh-hant', 'ar', 'ru'].indexOf(locale)
  expect(view.getByRole('button', { name: operatorCopy.inspect[index] })).toBeTruthy()
  expect(view.getByText(operatorCopy.privacy[index])).toBeTruthy()
  expect(rpc).not.toHaveBeenCalled()

  for (const translated of Object.values(operatorCopy)) { expect(translated).toHaveLength(9); expect(translated[index].trim().length).toBeGreaterThan(0) }
})
