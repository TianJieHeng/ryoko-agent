import type { OperationsApplyParams, OperationsRepairParams } from './gateway-contract.generated.js'
import type { RuntimeRequest } from './runtime-control.js'
import { controlChoice, controlDigest, controlId, controlInteger, controlJson, controlList, controlRecord, controlText } from './runtime-research.js'

export const operatorActions = ['reconcile-effect', 'retry-delivery', 'revoke-lease', 'rebuild-index', 'restore-checkpoint'] as const
export type OperatorAction = typeof operatorActions[number]
export interface OperatorActor { principal_id: string; profile_id: string; agent_id: string }
export interface OperatorEffect { effect_id: string; state: string; operation_type: string }
export interface OperatorDelivery { obligation_id: string; state: string; attempts: number; max_attempts: number; deadline_at: number | null }
export interface OperatorInspection {
  schema_version: 1; actor: OperatorActor; session_id: string; revision: number; replay_cursor: string
  state: { status: string | null; run_id: string | null; last_command_id: string | null }
  ownership: { active: boolean; generation: number | null }; waiting_reason: string
  effects: OperatorEffect[]; deliveries: OperatorDelivery[]
  budgets: { account_id: string; state: string; deadline: number | null; consumed: Record<string, number>; reserved: Record<string, number> }[]
  truncated: boolean; memory_backend: string; connection_health: { sqlite: 'readable'; provider: 'not_probed'; memory_remote: 'not_probed' }
  laya_mode: 'not_certified'; checkpoint: { checkpoint_id: string; included_seq: number; published_seq: number } | null
  sensitive_ingestion_certified: false
}
export interface OperatorAudit {
  status: 'ok' | 'snapshot_required'; last_cursor: string; has_more: boolean
  events: { schema_version: 1; type: string; purpose: 'operator'; correlation_ids: Record<string, string>; retention_class: 'operational_audit'; redacted_fields: string[] }[]
}
export interface OperatorRetention {
  schema_version: 1; session_id: string; stores: Record<string, number>; retention: Record<string, string>
  retained_derived_store_counts: Record<string, number>; limitations: string[]; complete_deletion_supported: false
}
export interface OperatorCheckpoint {
  status: 'unavailable' | 'incompatible' | 'qualified_for_isolated_drill'; restore_allowed: boolean; checks: Record<string, boolean>; blocking_gates: string[]
  checkpoint_id?: string; checkpoint_digest?: string; included_seq?: number; through_revision?: number; projection_digest?: string; replayed_events?: number
  restore_scope?: 'derived_projection_only'; history_rewind?: false; external_effect_replay?: false; full_profile_cutover_certified?: false
}
export interface OperatorRepairPlan {
  schema_version: 1; action: OperatorAction; actor: OperatorActor; session_id: string; target_id: string; affected_ids: string[]
  before_revision: number; target: Record<string, string | number | string[] | null>; invariant_checks: string[]; expected_after: string; expires_at: number; plan_digest: string
}
export interface OperatorDeletionManifest {
  schema_version: 1; kind: 'individual_memory' | 'transcript_payload'; actor: OperatorActor; session_id: string; record_refs: string[]
  stores: string[]; requested: number; expires_at: number; before_revision: number; source: Record<string, string | number>
  acknowledgments: { store: string; status: 'logical_deletion_acknowledged' }[]; limitations: string[]; complete_deletion: false; manifest_digest: string; completed_at?: number
}
export interface OperatorRepairReceipt {
  plan_digest?: string; outcome?: 'applied' | 'no_progress' | 'projection_reconstructed'; state?: string
  effect_id?: string; redispatched?: false; delivery_id?: string; attempt_count?: number; max_attempts?: number
  indexes_rebuilt?: number; remote_work_stopped?: false; revision?: number; generation?: number; history_rewound?: false; external_effects_replayed?: 0
}
export interface OperatorReview { kind: 'repair' | 'deletion'; json: string; digest: string; before: OperatorInspection; plan: OperatorRepairPlan | OperatorDeletionManifest }
export interface OperatorAttempt { review: OperatorReview; status: 'unknown' | 'acknowledged' | 'rejected'; receipt: OperatorRepairReceipt | OperatorDeletionManifest | null; after: OperatorInspection | null }
export type OperatorError = 'unavailable' | 'changed' | 'unknown' | 'rejected' | 'afterUnavailable'
export interface OperatorState {
  connected: boolean; busy: boolean; action: OperatorAction; target: string; memoryRecordId: string
  inspection: OperatorInspection | null; audit: OperatorAudit | null; retention: OperatorRetention | null; checkpoint: OperatorCheckpoint | null
  review: OperatorReview | null; attempt: OperatorAttempt | null; error: OperatorError | null
}

const expectations: Record<OperatorAction, string> = {
  'reconcile-effect': 'evidence_only_no_redispatch', 'retry-delivery': 'same_outbox_same_attempt_budget_no_send',
  'revoke-lease': 'old_generation_cannot_dispatch_remote_work_may_continue', 'rebuild-index': 'derived_indexes_rebuilt_no_source_change',
  'restore-checkpoint': 'derived_projection_reconstructed_no_history_or_effect_rewind'
}

const invariants = ['live_policy', 'exact_actor', 'owning_profile', 'preview_cas', 'mandatory_journal']
const transcriptStores = ['messages_including_inactive', 'message_fts_projections', 'session_title_and_system_prompt', 'unreferenced_system_prompts']
const memoryStores = ['individual_memory_all_versions', 'individual_memory_conflicts', 'individual_memory_markdown']

const retentionPolicy = {
  messages: 'explicit_previewed_logical_erasure', recovery_journal: 'checkpoint_bounded_pruning_only', effects: 'retain_unresolved_and_dedup_evidence',
  individual_memory: 'explicit_record_purge_and_projection_rebuild', artifacts: 'retained_no_automatic_expiry', logs: 'existing_log_rotation_not_certified',
  backups: 'operator_managed_no_expiry_proof', provider_copies: 'unknown'
}

const limitations = ['sqlite_free_pages_and_wal_not_forensically_erased', 'backups_require_separate_expiry', 'provider_copies_no_deletion_acknowledgment',
  'exports_and_external_caches_not_enumerated', 'active_process_copies_require_session_end', 'mandatory_recovery_journal_retained',
  'artifacts_and_mission_evidence_retained', 'context_projections_and_session_activity_metadata_retained', 'session_json_jsonl_and_request_dumps_retained',
  'durable_schedules_monitors_and_commitments_retained', 'delegation_handoffs_roots_workspaces_retained',
  'bounded_service_source_receipt_blobs_and_channel_bindings_retained', 'remote_harness_deletion_not_supported']

function requireValue(condition: unknown): asserts condition { if (!condition) { throw new Error('Invalid or changed operator projection') } }

function exact(value: unknown, keys: string[], optional: string[] = []) { return controlRecord(value, keys, optional) }

function record(json: string) { return controlRecord(controlJson(json, 262144, false)) }

function boolean(value: unknown): boolean { requireValue(typeof value === 'boolean');

 return value }

function number(value: unknown): number { requireValue(typeof value === 'number' && Number.isFinite(value) && value >= 0);

 return value }

function nullable<T>(value: unknown, parse: (value: unknown) => T): T | null { return value === null ? null : parse(value) }

function strings(value: unknown, maximum = 100): string[] { return controlList(value, 0, maximum).map(item => controlText(item)) }

function same(a: unknown, b: unknown): boolean {
  if (a === b) { return true }

  if (!a || !b || typeof a !== 'object' || typeof b !== 'object' || Array.isArray(a) !== Array.isArray(b)) { return false }
  const left = a as Record<string, unknown>, right = b as Record<string, unknown>

  return Object.keys(left).length === Object.keys(right).length && Object.entries(left).every(([key, value]) => Object.hasOwn(right, key) && same(value, right[key]))
}

function actor(value: unknown): OperatorActor {
  const row = exact(value, ['principal_id', 'profile_id', 'agent_id'])

  return { principal_id: controlId(row.principal_id), profile_id: controlId(row.profile_id), agent_id: controlId(row.agent_id) }
}

function numericMap(value: unknown): Record<string, number> {
  const row = controlRecord(value); requireValue(Object.keys(row).length <= 32)

  return Object.fromEntries(Object.entries(row).map(([key, value]) => [controlText(key), number(value)]))
}

function schema(row: Record<string, unknown>) { requireValue(row.schema_version === 1) }

function scoped(row: Record<string, unknown>, session: string) { schema(row); requireValue(row.session_id === session) }

export function parseOperatorInspection(json: string, session: string): OperatorInspection {
  const row = exact(record(json), ['schema_version', 'actor', 'session_id', 'revision', 'replay_cursor', 'state', 'ownership', 'waiting_reason', 'effects', 'deliveries', 'budgets', 'truncated', 'memory_backend', 'connection_health', 'laya_mode', 'checkpoint', 'sensitive_ingestion_certified'])
  scoped(row, session); actor(row.actor); controlInteger(row.revision); controlText(row.replay_cursor, 2048)
  const state = exact(row.state, ['status', 'run_id', 'last_command_id'])
  Object.values(state).forEach(value => nullable(value, controlText))
  const ownership = exact(row.ownership, ['active', 'generation'])
  boolean(ownership.active); nullable(ownership.generation, controlInteger)
  controlChoice(row.waiting_reason, ['unresolved_effect', 'input_or_approval', 'none_recorded'])
  controlList(row.effects, 0, 100).forEach(value => { const item = exact(value, ['effect_id', 'state', 'operation_type']); Object.values(item).forEach(value => controlText(value)) })
  controlList(row.deliveries, 0, 100).forEach(value => {
    const item = exact(value, ['obligation_id', 'state', 'attempts', 'max_attempts', 'deadline_at'])
    controlId(item.obligation_id); controlText(item.state); controlInteger(item.attempts); controlInteger(item.max_attempts); nullable(item.deadline_at, number)
  })
  controlList(row.budgets, 0, 100).forEach(value => {
    const item = exact(value, ['account_id', 'state', 'deadline', 'consumed', 'reserved'])
    controlId(item.account_id); controlText(item.state); nullable(item.deadline, number); numericMap(item.consumed); numericMap(item.reserved)
  })
  boolean(row.truncated); controlText(row.memory_backend)
  requireValue(same(row.connection_health, { sqlite: 'readable', provider: 'not_probed', memory_remote: 'not_probed' }))
  requireValue(row.laya_mode === 'not_certified' && row.sensitive_ingestion_certified === false)

  if (row.checkpoint !== null) {
    const checkpoint = exact(row.checkpoint, ['checkpoint_id', 'included_seq', 'published_seq'])
    controlId(checkpoint.checkpoint_id); controlInteger(checkpoint.included_seq); controlInteger(checkpoint.published_seq)
  }

  return row as unknown as OperatorInspection
}

export function parseOperatorAudit(json: string, session: string): OperatorAudit {
  const row = exact(record(json), ['status', 'last_cursor', 'has_more', 'events'])
  controlChoice(row.status, ['ok', 'snapshot_required']); controlText(row.last_cursor, 2048); boolean(row.has_more)
  controlList(row.events, 0, 100).forEach(value => {
    const event = exact(value, ['schema_version', 'type', 'purpose', 'correlation_ids', 'retention_class', 'redacted_fields'])
    schema(event); controlText(event.type); requireValue(event.purpose === 'operator' && event.retention_class === 'operational_audit')
    const ids = exact(event.correlation_ids, [], ['session_id', 'mission_id', 'run_id', 'operation_id', 'effect_id', 'delivery_id', 'approval_id'])
    Object.values(ids).forEach(controlId); requireValue(ids.session_id === undefined || ids.session_id === session)
    requireValue(same(event.redacted_fields, ['payload', 'provider_receipts', 'arguments', 'paths', 'credentials']))
  })

  return row as unknown as OperatorAudit
}

export function parseOperatorRetention(json: string, session: string): OperatorRetention {
  const row = exact(record(json), ['schema_version', 'session_id', 'stores', 'retention', 'retained_derived_store_counts', 'limitations', 'complete_deletion_supported'])
  scoped(row, session)
  const stores = exact(row.stores, ['messages', 'runtime_commands', 'runtime_events', 'runtime_context_projections', 'runtime_effects', 'runtime_artifact_versions'])
  Object.values(stores).forEach(value => controlInteger(value))
  requireValue(same(row.retention, retentionPolicy)); requireValue(same(row.limitations, limitations)); requireValue(row.complete_deletion_supported === false)
  const retained = exact(row.retained_derived_store_counts, [], ['bounded_service_pipelines', 'bounded_service_stages', 'runtime_channel_bindings', 'delegation_handoffs', 'delegation_roots', 'durable_schedule_versions', 'durable_occurrences'])
  Object.values(retained).forEach(value => controlInteger(value))

  return row as unknown as OperatorRetention
}

export function parseOperatorCheckpoint(json: string): OperatorCheckpoint {
  const row = record(json)
  const status = controlChoice(row.status, ['unavailable', 'incompatible', 'qualified_for_isolated_drill'])
  const keys = ['status', 'restore_allowed', 'checks', 'blocking_gates']
  const targetKeys = ['checkpoint_id', 'checkpoint_digest', 'included_seq', 'through_revision', 'projection_digest', 'replayed_events']
  exact(row, status === 'qualified_for_isolated_drill' ? [...keys, ...targetKeys, 'restore_scope', 'history_rewind', 'external_effect_replay', 'full_profile_cutover_certified'] : keys)
  boolean(row.restore_allowed); strings(row.blocking_gates)

  if (status === 'qualified_for_isolated_drill') {
    validateCheckpointTarget(Object.fromEntries(targetKeys.map(key => [key, row[key]])))
    requireValue(same(row.checks, { versions: true, contiguous_replay: true, authoritative_references: true }))
    requireValue(row.restore_scope === 'derived_projection_only' && row.history_rewind === false && row.external_effect_replay === false && row.full_profile_cutover_certified === false)
  } else { requireValue(row.restore_allowed === false); exact(row.checks, []) }

  return row as unknown as OperatorCheckpoint
}

function validateCheckpointTarget(value: unknown) {
  const target = exact(value, ['checkpoint_id', 'checkpoint_digest', 'included_seq', 'through_revision', 'projection_digest', 'replayed_events'])
  controlId(target.checkpoint_id); controlDigest(target.checkpoint_digest); controlDigest(target.projection_digest)
  controlInteger(target.included_seq); controlInteger(target.through_revision); controlInteger(target.replayed_events, 0, 2000)
}

export function operatorTargets(inspection: OperatorInspection, action: OperatorAction): string[] {
  const targets: Record<OperatorAction, () => string[]> = {
    'reconcile-effect': () => inspection.effects.map(item => item.effect_id), 'retry-delivery': () => inspection.deliveries.map(item => item.obligation_id),
    'revoke-lease': () => inspection.ownership.active ? [inspection.session_id] : [], 'rebuild-index': () => [inspection.session_id], 'restore-checkpoint': () => inspection.checkpoint ? [inspection.session_id] : []
  }

  return targets[action]()
}

function validateRepairTarget(plan: OperatorRepairPlan, before: OperatorInspection) {
  const target = plan.target

  const validators: Record<OperatorAction, () => void> = {
    'reconcile-effect': () => {
      exact(target, ['effect_id', 'state', 'updated_at', 'operation_type']); controlText(target.state); number(target.updated_at); controlText(target.operation_type)
      const effect = before.effects.find(item => item.effect_id === plan.target_id)
      requireValue(effect && target.effect_id === effect.effect_id && target.state === effect.state && target.operation_type === effect.operation_type)
    },
    'retry-delivery': () => {
      exact(target, ['delivery_id', 'state', 'attempt_count', 'max_attempts', 'deadline_at']); nullable(target.deadline_at, number)
      const delivery = before.deliveries.find(item => item.obligation_id === plan.target_id)
      requireValue(delivery && target.delivery_id === delivery.obligation_id && target.state === delivery.state && target.attempt_count === delivery.attempts && target.max_attempts === delivery.max_attempts && target.deadline_at === delivery.deadline_at)
    },
    'revoke-lease': () => { exact(target, ['generation', 'holder_digest']); controlDigest(target.holder_digest); requireValue(target.generation === before.ownership.generation && before.ownership.active) },
    'rebuild-index': () => { exact(target, ['session_ids', 'message_count']); const ids = strings(target.session_ids); ids.forEach(controlId); controlInteger(target.message_count, 0, 10000); requireValue(ids.includes(before.session_id) && new Set(ids).size === ids.length && same(plan.affected_ids, ids)) },
    'restore-checkpoint': () => { validateCheckpointTarget(target); const checkpoint = before.checkpoint; requireValue(checkpoint); requireValue(target.checkpoint_id === checkpoint.checkpoint_id && target.included_seq === checkpoint.included_seq && target.through_revision === before.revision) }
  }

  validators[plan.action]()

  if (plan.action !== 'rebuild-index') { requireValue(same(plan.affected_ids, [plan.target_id])) }
}

export function parseOperatorRepair(json: string, before: OperatorInspection, action: OperatorAction, target: string, now = Date.now() / 1000): OperatorReview {
  const row = exact(record(json), ['schema_version', 'action', 'actor', 'session_id', 'target_id', 'affected_ids', 'before_revision', 'target', 'invariant_checks', 'expected_after', 'expires_at', 'plan_digest'])
  scoped(row, before.session_id); actor(row.actor); controlInteger(row.before_revision); strings(row.affected_ids); controlDigest(row.plan_digest)
  requireValue(row.action === action && row.target_id === target && operatorTargets(before, action).includes(target))
  requireValue(row.before_revision === before.revision && same(row.actor, before.actor) && same(row.invariant_checks, invariants) && row.expected_after === expectations[action])
  requireValue(number(row.expires_at) > now && number(row.expires_at) <= now + 301)
  const plan = row as unknown as OperatorRepairPlan
  validateRepairTarget(plan, before)

  return { kind: 'repair', json, digest: plan.plan_digest, before, plan }
}

export function parseOperatorDeletion(json: string, before: OperatorInspection, recordId: string | null, now = Date.now() / 1000, applied = false): OperatorDeletionManifest {
  const row = exact(record(json), ['schema_version', 'kind', 'actor', 'session_id', 'record_refs', 'stores', 'requested', 'expires_at', 'before_revision', 'source', 'acknowledgments', 'limitations', 'complete_deletion', 'manifest_digest', ...(applied ? ['completed_at'] : [])])
  scoped(row, before.session_id); actor(row.actor); controlDigest(row.manifest_digest)
  requireValue(row.before_revision === before.revision && same(row.actor, before.actor) && row.complete_deletion === false && same(row.limitations, limitations))
  requireValue(number(row.requested) === number(row.expires_at) - 300)

  if (!applied) { requireValue(number(row.expires_at) > now && number(row.expires_at) <= now + 301) }
  requireValue(row.kind === (recordId ? 'individual_memory' : 'transcript_payload') && same(row.record_refs, [recordId ?? before.session_id]))
  requireValue(same(row.stores, recordId ? memoryStores : transcriptStores))

  if (recordId) {
    requireValue(before.memory_backend === 'builtin'); const source = exact(row.source, ['record_id', 'version', 'namespace_id'])
    requireValue(source.record_id === recordId); controlInteger(source.version, 1); controlId(source.namespace_id)
  } else {
    const source = exact(row.source, ['message_count', 'source_digest']); controlInteger(source.message_count, 0, 1000); controlDigest(source.source_digest)
  }

  const acknowledgments = controlList(row.acknowledgments, 0, 4)
  acknowledgments.forEach(item => { const ack = exact(item, ['store', 'status']); requireValue((row.stores as string[]).includes(controlText(ack.store)) && ack.status === 'logical_deletion_acknowledged') })
  requireValue(new Set(acknowledgments.map(item => (item as { store: string }).store)).size === acknowledgments.length)

  if (!applied) { requireValue(acknowledgments.length === 0) }
  else { requireValue(same(acknowledgments.map(item => (item as { store: string }).store), row.stores)) }

  if (row.completed_at !== undefined) { number(row.completed_at) }

  return row as unknown as OperatorDeletionManifest
}

export function operatorApplyInput(session: string, review: OperatorReview, now = Date.now() / 1000): OperationsApplyParams {
  requireValue(review.before.session_id === session && review.plan.expires_at > now)

  const current = review.kind === 'repair'
    ? parseOperatorRepair(review.json, review.before, (review.plan as OperatorRepairPlan).action, (review.plan as OperatorRepairPlan).target_id, now).plan
    : parseOperatorDeletion(review.json, review.before, (review.plan as OperatorDeletionManifest).kind === 'individual_memory' ? (review.plan as OperatorDeletionManifest).record_refs[0] : null, now)

  requireValue(same(current, review.plan) && (review.kind === 'repair' ? (current as OperatorRepairPlan).plan_digest : (current as OperatorDeletionManifest).manifest_digest) === review.digest)

  return { session_id: controlId(session), schema_version: 1, plan_json: review.json, authorization_digest: controlDigest(review.digest) }
}

export function parseOperatorReceipt(json: string, review: OperatorReview): OperatorRepairReceipt | OperatorDeletionManifest {
  if (review.kind === 'deletion') {
    const plan = review.plan as OperatorDeletionManifest
    const receipt = parseOperatorDeletion(json, review.before, plan.kind === 'individual_memory' ? plan.record_refs[0] : null, 0, true)
    const { acknowledgments: _acknowledgments, completed_at: _completedAt, ...original } = receipt
    requireValue(same({ ...original, acknowledgments: [] }, plan))

    return receipt
  }

  const plan = review.plan as OperatorRepairPlan, row = record(json)

  const validators: Record<OperatorAction, () => void> = {
    'reconcile-effect': () => { exact(row, ['plan_digest', 'outcome', 'effect_id', 'state', 'redispatched']); requireValue(row.outcome === 'applied' && row.effect_id === plan.target_id && row.redispatched === false); controlText(row.state) },
    'retry-delivery': () => { exact(row, ['plan_digest', 'outcome', 'delivery_id', 'state', 'attempt_count', 'max_attempts']); requireValue(row.outcome === 'applied' && row.delivery_id === plan.target_id && row.attempt_count === plan.target.attempt_count && row.max_attempts === plan.target.max_attempts); controlText(row.state) },
    'revoke-lease': () => { exact(row, ['state', 'remote_work_stopped']); requireValue(row.state === 'revoked' && row.remote_work_stopped === false) },
    'rebuild-index': () => { exact(row, ['plan_digest', 'outcome', 'indexes_rebuilt']); controlInteger(row.indexes_rebuilt); requireValue(row.outcome === (row.indexes_rebuilt === 0 ? 'no_progress' : 'applied')) },
    'restore-checkpoint': () => { exact(row, ['plan_digest', 'outcome', 'revision', 'generation', 'history_rewound', 'external_effects_replayed']); requireValue(row.outcome === 'projection_reconstructed' && row.history_rewound === false && row.external_effects_replayed === 0); controlInteger(row.revision, plan.before_revision + 1); controlInteger(row.generation) }
  }

  validators[plan.action]()

  if (plan.action !== 'revoke-lease') { requireValue(row.plan_digest === review.digest); controlChoice(row.outcome, ['applied', 'no_progress', 'projection_reconstructed']) }

  return row as OperatorRepairReceipt
}

const initial = (): OperatorState => ({ connected: false, busy: false, action: 'reconcile-effect', target: '', memoryRecordId: '', inspection: null, audit: null, retention: null, checkpoint: null, review: null, attempt: null, error: null })
const preflightRejections = new Set(['operations_preview_changed', 'operations_preview_expired', 'operations_exact_authorization_required', 'deletion_preview_changed', 'deletion_preview_expired', 'deletion_exact_authorization_required'])

/** UI lifetime only. The parent retains passive operation IDs across request/session changes. */
export class OperationsSession {
  private state = initial()
  private epoch = 0
  private mounted = false
  private listeners = new Set<() => void>()
  private externalPending: readonly string[] = []
  private request: RuntimeRequest
  private sessionId: string
  constructor(request: RuntimeRequest, sessionId: string) { this.request = request; this.sessionId = sessionId }
  getState = (): OperatorState => this.state
  subscribe = (listener: () => void) => { this.listeners.add(listener);

 return () => { this.listeners.delete(listener) } }
  private set(patch: Partial<OperatorState>) { this.state = { ...this.state, ...patch }; this.listeners.forEach(listener => listener()) }
  attach(connected: boolean, pending: readonly string[] = []): () => void {
    this.mounted = true; this.epoch++; this.externalPending = pending
    this.set({ ...initial(), connected, attempt: this.state.attempt })

    return () => { this.mounted = false; this.epoch++; this.set({ ...initial(), attempt: this.state.attempt }) }
  }
  setUnresolved(pending: readonly string[]) {
    this.externalPending = pending.filter(id => id !== this.state.attempt?.review.digest)

    if (this.externalPending.length) { this.set({ review: null }) }
  }
  private valid(epoch: number) { return this.mounted && this.state.connected && epoch === this.epoch }
  private blocked() { return this.externalPending.length > 0 || this.state.attempt?.status === 'unknown' }
  edit(patch: Partial<Pick<OperatorState, 'action' | 'target' | 'memoryRecordId'>>) {
    if (this.state.busy || this.blocked()) { return }
    this.epoch++
    this.set({ ...patch, review: null, error: null })
  }
  dismissReview() { this.set({ review: null }) }
  private async run(work: (epoch: number) => Promise<void>) {
    if (!this.mounted || !this.state.connected || this.state.busy) { return }
    const epoch = ++this.epoch
    this.set({ busy: true, error: null })

    try { await work(epoch) } catch { if (this.valid(epoch)) { this.set({ error: 'unavailable', review: null }) } }
    finally { if (this.valid(epoch)) { this.set({ busy: false }) } }
  }
  inspect(): Promise<void> {
    return this.run(async epoch => {
      this.set({ review: null, inspection: null })
      const result = await this.request('runtime.operations.inspect', { session_id: this.sessionId, schema_version: 1 })

      if (!this.valid(epoch)) { return }
      const inspection = parseOperatorInspection(result.record_json, this.sessionId)
      const ids = operatorTargets(inspection, this.state.action)
      this.set({ inspection, target: ids.includes(this.state.target) ? this.state.target : ids[0] ?? '', attempt: this.state.attempt ? { ...this.state.attempt, after: inspection } : null })
    })
  }
  inspectAudit(older = false): Promise<void> {
    return this.run(async epoch => {
      const cursor = older ? this.state.audit?.last_cursor : null
      this.set({ review: null })
      const result = await this.request('runtime.operations.audit', { session_id: this.sessionId, schema_version: 1, cursor, limit: 50 })

      if (this.valid(epoch)) { this.set({ audit: parseOperatorAudit(result.record_json, this.sessionId) }) }
    })
  }
  inspectPrivacy(): Promise<void> {
    return this.run(async epoch => {
      this.set({ review: null, retention: null })
      const result = await this.request('runtime.operations.retention', { session_id: this.sessionId, schema_version: 1 })

      if (this.valid(epoch)) { this.set({ retention: parseOperatorRetention(result.record_json, this.sessionId) }) }
    })
  }
  inspectCheckpoint(): Promise<void> {
    return this.run(async epoch => {
      this.set({ review: null, checkpoint: null })
      const result = await this.request('runtime.operations.checkpoint', { session_id: this.sessionId, schema_version: 1 })

      if (this.valid(epoch)) { this.set({ checkpoint: parseOperatorCheckpoint(result.record_json) }) }
    })
  }
  prepare(kind: OperatorReview['kind']): Promise<void> {
    return this.run(async epoch => {
      const before = this.state.inspection

      if (!before || this.blocked()) { return }
      this.set({ review: null })

      if (kind === 'repair') {
        const { action, target } = this.state
        requireValue(operatorTargets(before, action).includes(target))

        if (action === 'restore-checkpoint') { requireValue(this.state.checkpoint?.restore_allowed && this.state.checkpoint.through_revision === before.revision) }
        const params: OperationsRepairParams = { session_id: this.sessionId, schema_version: 1, action, target_id: controlId(target) }
        const result = await this.request('runtime.operations.repair.prepare', params)

        if (this.valid(epoch)) { this.set({ review: parseOperatorRepair(result.record_json, before, action, target) }) }
      } else {
        requireValue(this.state.retention?.session_id === this.sessionId)
        const memory_record_id = this.state.memoryRecordId ? controlId(this.state.memoryRecordId) : null
        requireValue(!memory_record_id || !/[\\/]/u.test(memory_record_id))
        requireValue(!memory_record_id || before.memory_backend === 'builtin')
        const result = await this.request('runtime.operations.deletion.prepare', { session_id: this.sessionId, schema_version: 1, memory_record_id })

        if (!this.valid(epoch)) { return }
        const plan = parseOperatorDeletion(result.record_json, before, memory_record_id)
        this.set({ review: { kind, json: result.record_json, digest: plan.manifest_digest, before, plan } })
      }
    })
  }
  apply(review: OperatorReview): Promise<void> {
    return this.run(async epoch => {
      if (this.blocked() || this.state.review !== review || this.state.inspection !== review.before) { return }
      const params = operatorApplyInput(this.sessionId, review)
      this.set({ review: null, inspection: null, attempt: { review, status: 'unknown', receipt: null, after: null } })

      try {
        const result = await this.request(review.kind === 'repair' ? 'runtime.operations.repair.apply' : 'runtime.operations.deletion.apply', params)

        if (!this.valid(epoch)) { return }
        const receipt = parseOperatorReceipt(result.record_json, review)
        this.set({ attempt: { review, status: 'acknowledged', receipt, after: null } })
      } catch (error) {
        if (!this.valid(epoch)) { return }
        const code = (error as { data?: { code?: string } } | null)?.data?.code
        // Only broker validation failures before side effects qualify; all other errors remain unknown.
        const rejected = code !== undefined && preflightRejections.has(code)
        this.set({ error: rejected ? 'rejected' : 'unknown', attempt: { review, status: rejected ? 'rejected' : 'unknown', receipt: null, after: null } })

        return
      }

      try {
        const result = await this.request('runtime.operations.inspect', { session_id: this.sessionId, schema_version: 1 })

        if (!this.valid(epoch)) { return }
        const after = parseOperatorInspection(result.record_json, this.sessionId)
        requireValue(same(after.actor, review.before.actor))
        this.set({ inspection: after, attempt: { ...this.state.attempt!, after } })
      } catch { if (this.valid(epoch)) { this.set({ error: 'afterUnavailable' }) } }
    })
  }
}
