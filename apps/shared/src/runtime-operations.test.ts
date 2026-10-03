import { describe, expect, it, vi } from 'vitest'

import type { RuntimeRequest } from './runtime-control.js'
import { OperationsSession, type OperatorAction, operatorActions, operatorApplyInput, type OperatorDeletionManifest, type OperatorInspection, type OperatorRepairPlan, parseOperatorAudit, parseOperatorCheckpoint, parseOperatorDeletion, parseOperatorInspection, parseOperatorReceipt, parseOperatorRepair, parseOperatorRetention } from './runtime-operations.js'

const digest = 'a'.repeat(64), sourceDigest = 'b'.repeat(64)
const limits = ['sqlite_free_pages_and_wal_not_forensically_erased', 'backups_require_separate_expiry', 'provider_copies_no_deletion_acknowledgment', 'exports_and_external_caches_not_enumerated', 'active_process_copies_require_session_end', 'mandatory_recovery_journal_retained', 'artifacts_and_mission_evidence_retained', 'context_projections_and_session_activity_metadata_retained', 'session_json_jsonl_and_request_dumps_retained', 'durable_schedules_monitors_and_commitments_retained', 'delegation_handoffs_roots_workspaces_retained', 'bounded_service_source_receipt_blobs_and_channel_bindings_retained', 'remote_harness_deletion_not_supported']

function inspection(): OperatorInspection {
  return { schema_version: 1, actor: { principal_id: 'p', profile_id: 'profile', agent_id: 'agent' }, session_id: 'owned', revision: 8, replay_cursor: 'epoch:8',
    state: { status: 'blocked', run_id: 'run', last_command_id: 'command' }, ownership: { active: true, generation: 2 }, waiting_reason: 'unresolved_effect',
    effects: [{ effect_id: 'effect', state: 'unknown', operation_type: 'send' }], deliveries: [{ obligation_id: 'delivery', state: 'unknown', attempts: 1, max_attempts: 3, deadline_at: 300 }],
    budgets: [{ account_id: 'budget', state: 'active', deadline: null, consumed: { tokens: 10 }, reserved: { tokens: 2 } }], truncated: false, memory_backend: 'builtin',
    connection_health: { sqlite: 'readable', provider: 'not_probed', memory_remote: 'not_probed' }, laya_mode: 'not_certified', checkpoint: { checkpoint_id: 'checkpoint', included_seq: 4, published_seq: 5 }, sensitive_ingestion_certified: false }
}

function repair(action: OperatorAction = 'reconcile-effect', before = inspection()): OperatorRepairPlan {
  const targets = {
    'reconcile-effect': { effect_id: 'effect', state: 'unknown', updated_at: 10, operation_type: 'send' },
    'retry-delivery': { delivery_id: 'delivery', state: 'unknown', attempt_count: 1, max_attempts: 3, deadline_at: 300 },
    'revoke-lease': { generation: 2, holder_digest: sourceDigest },
    'rebuild-index': { session_ids: ['owned', 'other-owned'], message_count: 7 },
    'restore-checkpoint': { checkpoint_id: 'checkpoint', checkpoint_digest: sourceDigest, included_seq: 4, through_revision: 8, projection_digest: sourceDigest, replayed_events: 4 }
  }

  const expected = { 'reconcile-effect': 'evidence_only_no_redispatch', 'retry-delivery': 'same_outbox_same_attempt_budget_no_send', 'revoke-lease': 'old_generation_cannot_dispatch_remote_work_may_continue', 'rebuild-index': 'derived_indexes_rebuilt_no_source_change', 'restore-checkpoint': 'derived_projection_reconstructed_no_history_or_effect_rewind' }
  const target_id = action === 'reconcile-effect' ? 'effect' : action === 'retry-delivery' ? 'delivery' : 'owned'

  return { schema_version: 1, action, actor: before.actor, session_id: 'owned', target_id, affected_ids: action === 'rebuild-index' ? ['owned', 'other-owned'] : [target_id], before_revision: 8,
    target: targets[action], invariant_checks: ['live_policy', 'exact_actor', 'owning_profile', 'preview_cas', 'mandatory_journal'], expected_after: expected[action], expires_at: Date.now() / 1000 + 300, plan_digest: digest }
}

function retention() {
  return { schema_version: 1, session_id: 'owned', stores: { messages: 3, runtime_commands: 1, runtime_events: 8, runtime_context_projections: 1, runtime_effects: 1, runtime_artifact_versions: 1 },
    retention: { messages: 'explicit_previewed_logical_erasure', recovery_journal: 'checkpoint_bounded_pruning_only', effects: 'retain_unresolved_and_dedup_evidence', individual_memory: 'explicit_record_purge_and_projection_rebuild', artifacts: 'retained_no_automatic_expiry', logs: 'existing_log_rotation_not_certified', backups: 'operator_managed_no_expiry_proof', provider_copies: 'unknown' }, retained_derived_store_counts: { durable_occurrences: 1 }, limitations: limits, complete_deletion_supported: false }
}

function deletion(recordId: string | null = null): OperatorDeletionManifest {
  const expires_at = Date.now() / 1000 + 300

  return { schema_version: 1, kind: recordId ? 'individual_memory' : 'transcript_payload', actor: inspection().actor, session_id: 'owned', record_refs: [recordId ?? 'owned'],
    stores: recordId ? ['individual_memory_all_versions', 'individual_memory_conflicts', 'individual_memory_markdown'] : ['messages_including_inactive', 'message_fts_projections', 'session_title_and_system_prompt', 'unreferenced_system_prompts'],
    requested: expires_at - 300, expires_at, before_revision: 8, source: recordId ? { record_id: recordId, version: 2, namespace_id: 'namespace' } : { message_count: 3, source_digest: sourceDigest }, acknowledgments: [], limitations: limits, complete_deletion: false, manifest_digest: digest }
}

function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>(yes => { resolve = yes });

 return { promise, resolve } }

function setup(overrides: Record<string, (params: Record<string, unknown>) => unknown> = {}) {
  const json = JSON.stringify(repair(), null, 2)

  const rpc = vi.fn(async (method: string, params: Record<string, unknown>): Promise<unknown> => {
    if (overrides[method]) { return overrides[method](params) }

    const results: Record<string, unknown> = {
      'runtime.operations.inspect': inspection(), 'runtime.operations.retention': retention(),
      'runtime.operations.audit': { status: 'ok', last_cursor: 'epoch:8', has_more: false, events: [] },
      'runtime.operations.repair.apply': { plan_digest: digest, outcome: 'applied', effect_id: 'effect', state: 'unknown', redispatched: false }
    }

    if (method === 'runtime.operations.repair.prepare') { return { record_json: json } }

    if (method === 'runtime.operations.deletion.prepare') { return { record_json: JSON.stringify(deletion(params.memory_record_id as string | null)) } }

    if (method === 'runtime.operations.deletion.apply') { const manifest = JSON.parse(params.plan_json as string);

 return { record_json: JSON.stringify({ ...manifest, acknowledgments: manifest.stores.map((store: string) => ({ store, status: 'logical_deletion_acknowledged' })), completed_at: Date.now() / 1000 }) } }

    if (!(method in results)) { throw new Error('Unexpected method') }

    return { record_json: JSON.stringify(results[method]) }
  })

  const session = new OperationsSession(rpc as RuntimeRequest, 'owned'); session.attach(true)

  return { session, rpc, json }
}

it('does nothing on mount and passes exact original reviewed JSON, digest and target only once', async () => {
  const held = deferred<{ record_json: string }>(), { session, rpc, json } = setup({ 'runtime.operations.repair.apply': () => held.promise })
  expect(rpc).not.toHaveBeenCalled()
  await session.inspect(); await session.prepare('repair')
  const review = session.getState().review!
  expect(rpc).toHaveBeenLastCalledWith('runtime.operations.repair.prepare', { session_id: 'owned', schema_version: 1, action: 'reconcile-effect', target_id: 'effect' })
  const first = session.apply(review), duplicate = session.apply(review)
  await duplicate
  expect(rpc.mock.calls.filter(([method]) => method.endsWith('.apply'))).toHaveLength(1)
  expect(rpc).toHaveBeenLastCalledWith('runtime.operations.repair.apply', { session_id: 'owned', schema_version: 1, plan_json: json, authorization_digest: digest })
  held.resolve({ record_json: JSON.stringify({ plan_digest: digest, outcome: 'applied', effect_id: 'effect', state: 'unknown', redispatched: false }) }); await first
  expect(session.getState().attempt).toMatchObject({ status: 'acknowledged', receipt: { state: 'unknown', redispatched: false }, after: { revision: 8 } })
  expect(session.getState().attempt?.review.before.revision).toBe(8)
})

describe('broker plan and receipt contracts', () => {
  it.each(operatorActions)('validates %s without merging its distinct semantics', action => {
    const before = inspection(), plan = repair(action), review = parseOperatorRepair(JSON.stringify(plan), before, action, plan.target_id)

    const receipts = {
      'reconcile-effect': { plan_digest: digest, outcome: 'applied', effect_id: 'effect', state: 'unknown', redispatched: false },
      'retry-delivery': { plan_digest: digest, outcome: 'applied', delivery_id: 'delivery', state: 'pending', attempt_count: 1, max_attempts: 3 },
      'revoke-lease': { state: 'revoked', remote_work_stopped: false },
      'rebuild-index': { plan_digest: digest, outcome: 'applied', indexes_rebuilt: 7 },
      'restore-checkpoint': { plan_digest: digest, outcome: 'projection_reconstructed', revision: 10, generation: 3, history_rewound: false, external_effects_replayed: 0 }
    }

    expect(parseOperatorReceipt(JSON.stringify(receipts[action]), review)).toEqual(receipts[action])
    expect(() => parseOperatorReceipt(JSON.stringify({ ...receipts[action], credentials: 'secret' }), review)).toThrow()
  })
  it.each(['actor', 'session_id', 'target_id', 'affected_ids', 'before_revision', 'target', 'plan_digest', 'invariant_checks', 'expected_after', 'expires_at'])('rejects changed or malformed %s before authorization', field => {
    const before = inspection(), plan = repair(), values: Record<string, unknown> = { actor: { ...before.actor, profile_id: 'other' }, session_id: 'other', target_id: 'other', affected_ids: ['other'], before_revision: 7, target: { ...plan.target, state: 'confirmed' }, plan_digest: 'bad', invariant_checks: [], expected_after: 'mark_successful', expires_at: 0 }
    expect(() => parseOperatorRepair(JSON.stringify({ ...plan, [field]: values[field] }), before, 'reconcile-effect', 'effect')).toThrow()
  })
  it('rejects plan edits, expired confirmation and approval retargeting even after review', () => {
    const plan = repair(), before = inspection(), review = parseOperatorRepair(JSON.stringify(plan), before, plan.action, plan.target_id)
    expect(() => operatorApplyInput('other', review)).toThrow()
    expect(() => operatorApplyInput('owned', review, plan.expires_at + 1)).toThrow()
    review.plan.before_revision++
    expect(() => operatorApplyInput('owned', review)).toThrow()
  })
})

it('invalidates approvals on input edits and ignores a late preview after same-object reconnect', async () => {
  const held = deferred<{ record_json: string }>(), { session, rpc, json } = setup()
  await session.inspect(); await session.prepare('repair')
  const review = session.getState().review!
  session.edit({ target: 'different' }); await session.apply(review)
  expect(session.getState().review).toBeNull()
  expect(rpc.mock.calls.some(([method]) => method.endsWith('.apply'))).toBe(false)
  rpc.mockImplementationOnce(async () => held.promise)
  session.edit({ target: 'effect' }); const prepare = session.prepare('repair')
  session.attach(false)(); session.attach(true)
  held.resolve({ record_json: json }); await prepare
  expect(session.getState()).toMatchObject({ inspection: null, review: null, connected: true, busy: false })
})

it('keeps unknown apply identity across reconnect and allows only inspection without blind resubmit', async () => {
  const { session, rpc } = setup({ 'runtime.operations.repair.apply': () => { throw new Error('secret transport detail') } })
  await session.inspect(); await session.prepare('repair'); await session.apply(session.getState().review!)
  expect(session.getState()).toMatchObject({ error: 'unknown', review: null, attempt: { status: 'unknown', review: { digest } } })
  session.attach(false)(); session.attach(true)
  await session.inspect(); await session.inspectAudit(); await session.prepare('repair')
  expect(rpc.mock.calls.filter(([method]) => method.endsWith('.apply'))).toHaveLength(1)
  expect(rpc.mock.calls.filter(([method]) => method.endsWith('.prepare'))).toHaveLength(1)
  expect(session.getState().attempt?.status).toBe('unknown')
})

it('requires fresh inspection after a definite broker CAS rejection and never automatically prepares again', async () => {
  const { session, rpc } = setup({ 'runtime.operations.repair.apply': () => { throw { data: { code: 'operations_preview_changed' } } } })
  await session.inspect(); await session.prepare('repair'); await session.apply(session.getState().review!)
  expect(session.getState()).toMatchObject({ inspection: null, error: 'rejected', attempt: { status: 'rejected' } })
  await session.prepare('repair')
  expect(rpc.mock.calls.filter(([method]) => method.endsWith('.prepare'))).toHaveLength(1)
  await session.inspect(); await session.prepare('repair')
  expect(rpc.mock.calls.filter(([method]) => method.endsWith('.prepare'))).toHaveLength(2)
})

it('represents partial transcript deletion acknowledgments and rejects fabricated complete erasure', async () => {
  const { session, rpc } = setup()
  await session.inspect(); await session.inspectPrivacy(); await session.prepare('deletion')
  const review = session.getState().review!
  expect((review.plan as OperatorDeletionManifest).stores).toContain('messages_including_inactive')
  await session.apply(review)
  expect(rpc).toHaveBeenCalledWith('runtime.operations.deletion.apply', operatorApplyInput('owned', review))
  expect(session.getState().attempt).toMatchObject({ status: 'acknowledged', receipt: { complete_deletion: false, acknowledgments: expect.arrayContaining([{ store: 'messages_including_inactive', status: 'logical_deletion_acknowledged' }]) } })
  expect(() => parseOperatorDeletion(JSON.stringify({ ...deletion(), complete_deletion: true }), inspection(), null)).toThrow()
  expect(() => parseOperatorReceipt(JSON.stringify({ ...deletion(), manifest_digest: sourceDigest }), review)).toThrow()
})

it('limits optional record purge to builtin memory and rejects path-like input without dispatch', async () => {
  const { session, rpc } = setup()
  await session.inspect(); await session.inspectPrivacy(); session.edit({ memoryRecordId: '/tmp/secret' }); await session.prepare('deletion')
  expect(rpc.mock.calls.some(([method]) => method === 'runtime.operations.deletion.prepare')).toBe(false)
  session.edit({ memoryRecordId: 'memory-record' }); await session.prepare('deletion')
  expect(session.getState().review?.plan).toMatchObject({ kind: 'individual_memory', source: { record_id: 'memory-record' } })
  expect(() => parseOperatorDeletion(JSON.stringify(deletion('memory-record')), { ...inspection(), memory_backend: 'primary-harness' }, 'memory-record')).toThrow()
})

it('rejects cross-scope or raw sensitive fields in projections and preserves explicit unavailable checkpoint status', () => {
  expect(parseOperatorInspection(JSON.stringify(inspection()), 'owned').connection_health.provider).toBe('not_probed')
  expect(() => parseOperatorInspection(JSON.stringify(inspection()), 'other')).toThrow()
  expect(() => parseOperatorInspection(JSON.stringify({ ...inspection(), credentials: 'do-not-show' }), 'owned')).toThrow()
  expect(() => parseOperatorInspection('{"schema_version":1,"schema_version":2}', 'owned')).toThrow()
  expect(parseOperatorRetention(JSON.stringify(retention()), 'owned').complete_deletion_supported).toBe(false)
  expect(() => parseOperatorRetention(JSON.stringify({ ...retention(), complete_deletion_supported: true }), 'owned')).toThrow()
  expect(parseOperatorCheckpoint('{"status":"unavailable","restore_allowed":false,"checks":{},"blocking_gates":["checkpoint_missing"]}').restore_allowed).toBe(false)
  expect(() => parseOperatorCheckpoint('{"status":"unavailable","restore_allowed":true,"checks":{},"blocking_gates":[]}')).toThrow()
  expect(() => parseOperatorAudit(JSON.stringify({ status: 'ok', last_cursor: 'epoch:8', has_more: false, events: [{ schema_version: 1, type: 'operations.repair_finished', purpose: 'operator', correlation_ids: { session_id: 'other', operation_id: digest }, retention_class: 'operational_audit', redacted_fields: ['payload', 'provider_receipts', 'arguments', 'paths', 'credentials'] }] }), 'owned')).toThrow()
})

it('retains acknowledged receipt truth when subsequent state inspection fails and blocks externally retained unknown IDs', async () => {
  const { session, rpc } = setup()
  await session.inspect(); await session.prepare('repair')
  const review = session.getState().review!
  rpc.mockImplementationOnce(async () => ({ record_json: JSON.stringify({ plan_digest: digest, outcome: 'applied', effect_id: 'effect', state: 'unknown', redispatched: false }) }))
  rpc.mockImplementationOnce(async () => { throw new Error('offline') })
  await session.apply(review)
  expect(session.getState()).toMatchObject({ error: 'afterUnavailable', attempt: { status: 'acknowledged', after: null, receipt: { state: 'unknown' } } })
  session.attach(true, [sourceDigest]); await session.inspect(); await session.prepare('repair')
  expect(session.getState().review).toBeNull()
})

it('rejects receipts that change the reviewed effect, digest, delivery budget or irreversible guarantees', () => {
  const cases: [OperatorAction, Record<string, unknown>][] = [
    ['reconcile-effect', { plan_digest: digest, outcome: 'applied', effect_id: 'other', state: 'confirmed', redispatched: false }],
    ['reconcile-effect', { plan_digest: sourceDigest, outcome: 'applied', effect_id: 'effect', state: 'confirmed', redispatched: false }],
    ['retry-delivery', { plan_digest: digest, outcome: 'applied', delivery_id: 'delivery', state: 'pending', attempt_count: 0, max_attempts: 30 }],
    ['revoke-lease', { state: 'revoked', remote_work_stopped: true }],
    ['restore-checkpoint', { plan_digest: digest, outcome: 'projection_reconstructed', revision: 10, generation: 3, history_rewound: true, external_effects_replayed: 1 }]
  ]

  for (const [action, receipt] of cases) { const plan = repair(action); const review = parseOperatorRepair(JSON.stringify(plan), inspection(), action, plan.target_id); expect(() => parseOperatorReceipt(JSON.stringify(receipt), review)).toThrow() }
  const plan = deletion(), review = { kind: 'deletion' as const, json: JSON.stringify(plan), digest, before: inspection(), plan }
  expect(() => parseOperatorReceipt(JSON.stringify({ ...plan, completed_at: Date.now() / 1000, acknowledgments: [{ store: plan.stores[0], status: 'logical_deletion_acknowledged' }] }), review)).toThrow()
})

it('retains an operation as unknown when a late apply reply belongs to an earlier connection generation', async () => {
  const held = deferred<{ record_json: string }>(), { session, rpc } = setup({ 'runtime.operations.repair.apply': () => held.promise })
  await session.inspect(); await session.prepare('repair')
  const pending = session.apply(session.getState().review!)
  session.attach(false)(); session.attach(true)
  held.resolve({ record_json: JSON.stringify({ plan_digest: digest, outcome: 'applied', effect_id: 'effect', state: 'confirmed', redispatched: false }) }); await pending
  expect(session.getState().attempt).toMatchObject({ status: 'unknown', receipt: null, review: { digest } })
  await session.inspect(); await session.prepare('repair')
  expect(rpc.mock.calls.filter(([method]) => method.endsWith('.apply'))).toHaveLength(1)
  expect(session.getState().review).toBeNull()
})
