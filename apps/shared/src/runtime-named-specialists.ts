import type { CommandReceipt, SpecialistCatalog, SpecialistDescriptor, SpecialistHandoffParams, SpecialistPreview, SpecialistPreviewParams, SpecialistReference, SpecialistSelection, SpecialistStatus } from './gateway-contract.generated.js'
import type { RuntimeRequest } from './runtime-control.js'
import { controlDigest, controlId, controlInteger, controlList, controlRecord } from './runtime-research.js'

export interface SpecialistReferenceDraft { id: string; version: string; sha256: string }
export interface NamedSpecialistDraft { projectId: string; specialistId: string; objective: string; artifacts: SpecialistReferenceDraft[]; evidence: SpecialistReferenceDraft[]; constraints: string }
export interface NamedSpecialistReview { preview: SpecialistPreview; params: SpecialistHandoffParams }
export type NamedSpecialistError = 'invalid' | 'unavailable' | 'changed' | 'unknown'
export interface NamedSpecialistAttempt { review: NamedSpecialistReview; receipt: CommandReceipt | null; unknown: boolean }
export interface NamedSpecialistState {
  connected: boolean; busy: boolean; draft: NamedSpecialistDraft; catalog: SpecialistCatalog | null
  review: NamedSpecialistReview | null; attempt: NamedSpecialistAttempt | null; status: SpecialistStatus | null
  recoveryId: string; unresolved: readonly string[]; error: NamedSpecialistError | null
}

class InvalidSpecialist extends Error {}

function requireValue(value: unknown): asserts value { if (!value) { throw new InvalidSpecialist() } }

function text(value: unknown, maximum: number): string {
  requireValue(typeof value === 'string' && value.trim().length > 0 && value.length <= maximum && !value.includes('\0'))

  return value
}

function list(value: unknown, maximum = 512): string[] { return controlList(value, 0, maximum).map(item => text(item, 4096)) }

function ref(value: unknown): SpecialistReference {
  const row = controlRecord(value, ['id', 'version', 'sha256'], [])

  return { id: controlId(row.id), version: controlInteger(row.version, 1, Number.MAX_SAFE_INTEGER), sha256: controlDigest(row.sha256) }
}

function references(value: unknown): SpecialistReference[] { return controlList(value, 0, 32).map(ref) }

function draftRefs(value: SpecialistReferenceDraft[]): SpecialistReference[] {
  return references(value.map(item => {
    requireValue(/^[1-9][0-9]*$/u.test(item.version))

    return { ...item, version: Number(item.version) }
  }))
}

function same(left: unknown, right: unknown): boolean {
  if (left === right) { return true }

  if (Array.isArray(left) && Array.isArray(right)) { return left.length === right.length && left.every((item, index) => same(item, right[index])) }

  if (!left || !right || typeof left !== 'object' || typeof right !== 'object') { return false }
  const a = left as Record<string, unknown>, b = right as Record<string, unknown>

  return Object.keys(a).length === Object.keys(b).length && Object.keys(a).every(key => Object.hasOwn(b, key) && same(a[key], b[key]))
}

export function namedSpecialistInput(sessionId: string, draft: NamedSpecialistDraft): SpecialistPreviewParams {
  const constraints = draft.constraints === '' ? [] : draft.constraints.split('\n').map(item => text(item, 2048))
  requireValue(constraints.length <= 16)

  return { schema_version: 1, session_id: controlId(sessionId), project_id: controlId(draft.projectId), specialist_id: controlId(draft.specialistId),
    objective: text(draft.objective, 16384), artifacts: draftRefs(draft.artifacts), evidence: draftRefs(draft.evidence), constraints }
}

export function parseNamedSpecialist(value: unknown): SpecialistDescriptor {
  const row = controlRecord(value, ['agent_id', 'responsibility', 'manifest_sha256', 'methods_ref', 'limits', 'grants', 'builtin_memory_namespace', 'output_contract_json'])
  const limits = controlRecord(row.limits, ['max_depth', 'max_total_children', 'max_concurrent_children'])
  const grants = controlRecord(row.grants, ['allowed_tools', 'project_grants', 'mcp_grants', 'memory_backend', 'personal_memory_access'])
  requireValue(grants.memory_backend === 'builtin' && grants.personal_memory_access === false)
  const allowed_tools = list(grants.allowed_tools)
  requireValue(!allowed_tools.includes('delegate_task'))
  const output = text(row.output_contract_json, 65536)
  controlRecord(JSON.parse(output))

  return { agent_id: controlId(row.agent_id), responsibility: text(row.responsibility, 4096), manifest_sha256: controlDigest(row.manifest_sha256), methods_ref: ref(row.methods_ref),
    limits: { max_depth: controlInteger(limits.max_depth), max_total_children: controlInteger(limits.max_total_children), max_concurrent_children: controlInteger(limits.max_concurrent_children) },
    grants: { allowed_tools, project_grants: list(grants.project_grants), mcp_grants: Object.fromEntries(Object.entries(controlRecord(grants.mcp_grants)).map(([key, tools]) => [key, list(tools)])), memory_backend: 'builtin', personal_memory_access: false },
    builtin_memory_namespace: text(row.builtin_memory_namespace, 4096), output_contract_json: output }
}

export function parseNamedSpecialistCatalog(value: unknown): SpecialistCatalog {
  const row = controlRecord(value, ['specialists', 'unavailable', 'teams_enabled', 'execution'])
  requireValue(row.teams_enabled === false && row.execution === 'local_single_child')
  const specialists = controlList(row.specialists, 0, 64).map(parseNamedSpecialist)

  const unavailable = controlList(row.unavailable, 0, 64).map(value => {
    const item = controlRecord(value, ['agent_id', 'code'])

    return { agent_id: controlId(item.agent_id), code: text(item.code, 256) }
  })

  const ids = [...specialists, ...unavailable].map(item => item.agent_id)
  requireValue(ids.length <= 64 && new Set(ids).size === ids.length)

  return { specialists, unavailable, teams_enabled: false, execution: 'local_single_child' }
}

/** The server digest is an opaque pin, forwarded unchanged with the exact validated selection. */
export function verifyNamedSpecialistPreview(value: unknown, input: SpecialistPreviewParams, selected: SpecialistDescriptor, now = Date.now()): SpecialistPreview {
  const row = controlRecord(value, ['specialist', 'selection', 'preview_sha256', 'runtime_revision'])
  const specialist = parseNamedSpecialist(row.specialist)
  requireValue(same(specialist, selected))
  const selection = controlRecord(row.selection, ['project_id', 'specialist_id', 'objective', 'artifacts', 'evidence', 'constraints', 'manifest_sha256', 'config_digest', 'parent_policy_digest', 'mission_id', 'mission_revision', 'expires_at'], [])
  requireValue(selection.project_id === input.project_id && selection.specialist_id === input.specialist_id && selection.objective === input.objective)
  const artifacts = references(selection.artifacts), evidence = references(selection.evidence), constraints = list(selection.constraints, 16)
  requireValue(same(artifacts, input.artifacts ?? []) && same(evidence, input.evidence ?? []) && same(constraints, input.constraints ?? []))
  requireValue(selection.manifest_sha256 === specialist.manifest_sha256)
  const mission_id = selection.mission_id === null ? null : controlId(selection.mission_id)
  const mission_revision = selection.mission_revision === null ? null : controlInteger(selection.mission_revision, 1)
  requireValue((mission_id === null) === (mission_revision === null))
  requireValue(typeof selection.expires_at === 'number' && Number.isFinite(selection.expires_at) && selection.expires_at * 1000 > now && selection.expires_at < 253402300799)

  const exact: SpecialistSelection = { project_id: input.project_id, specialist_id: input.specialist_id, objective: input.objective, artifacts, evidence, constraints,
    manifest_sha256: controlDigest(selection.manifest_sha256), config_digest: controlDigest(selection.config_digest), parent_policy_digest: controlDigest(selection.parent_policy_digest), mission_id, mission_revision, expires_at: selection.expires_at }

  return { specialist, selection: exact, preview_sha256: controlDigest(row.preview_sha256), runtime_revision: controlInteger(row.runtime_revision) }
}

export function verifyNamedSpecialistReceipt(value: unknown, params: SpecialistHandoffParams): CommandReceipt {
  const row = controlRecord(value, ['schema_version', 'command_id', 'status', 'durable_revision', 'run_id'])
  requireValue(row.schema_version === 1 && row.command_id === params.command_id && ['accepted', 'duplicate', 'rejected'].includes(String(row.status)))
  requireValue(row.run_id === null || typeof row.run_id === 'string')
  const conflict = row.conflict == null ? null : controlRecord(row.conflict, ['code', 'message'])

  return { schema_version: 1, command_id: params.command_id, status: row.status as CommandReceipt['status'], durable_revision: controlInteger(row.durable_revision), run_id: row.run_id === null ? null : controlId(row.run_id),
    conflict: conflict ? { code: text(conflict.code, 256), message: text(conflict.message, 4096) } : null }
}

export function verifyNamedSpecialistStatus(value: unknown, commandId: string, original?: NamedSpecialistReview, expectedRunId?: string | null): SpecialistStatus {
  const row = controlRecord(value, ['command_id', 'run_id', 'specialist_id', 'manifest_sha256', 'project_id', 'status', 'outcome', 'completion', 'execution_resumed'])
  requireValue(row.command_id === commandId && row.execution_resumed === false && (!expectedRunId || row.run_id === expectedRunId))
  requireValue(['accepted', 'claimed', 'completed', 'failed', 'blocked', 'cancelled'].includes(String(row.status)))
  const outcomes: Record<string, string[]> = { accepted: ['pending'], claimed: ['running', 'unknown'], completed: ['completed'], failed: ['failed'], blocked: ['blocked'], cancelled: ['cancelled'] }
  requireValue(outcomes[String(row.status)].includes(String(row.outcome)))
  const specialist_id = controlId(row.specialist_id), manifest_sha256 = controlDigest(row.manifest_sha256), project_id = controlId(row.project_id)

  if (original) { requireValue(specialist_id === original.params.selection.specialist_id && manifest_sha256 === original.params.selection.manifest_sha256 && project_id === original.params.selection.project_id) }
  let completion: SpecialistStatus['completion'] = null

  if (row.completion !== null) {
    const item = controlRecord(row.completion, ['specialist_id', 'manifest_sha256', 'project_id', 'child_id', 'handoff_sha256', 'state', 'summary', 'summary_truncated', 'schema_valid', 'parent_review_required', 'execution_resumed'])
    requireValue(item.specialist_id === specialist_id && item.manifest_sha256 === manifest_sha256 && item.project_id === project_id && item.parent_review_required === true && item.execution_resumed === false)
    requireValue(['completed', 'failed', 'blocked', 'cancelled', 'unknown'].includes(String(item.state)))
    requireValue(typeof item.summary === 'string' && item.summary.length <= 32768 && typeof item.summary_truncated === 'boolean' && (item.schema_valid === null || typeof item.schema_valid === 'boolean'))
    completion = { specialist_id, manifest_sha256, project_id, child_id: item.child_id === null ? null : controlId(item.child_id), handoff_sha256: item.handoff_sha256 === null ? null : controlDigest(item.handoff_sha256),
      state: item.state as NonNullable<SpecialistStatus['completion']>['state'], summary: item.summary, summary_truncated: item.summary_truncated, schema_valid: item.schema_valid, parent_review_required: true, execution_resumed: false }
  }

  return { command_id: commandId, run_id: controlId(row.run_id), specialist_id, manifest_sha256, project_id, status: row.status as SpecialistStatus['status'], outcome: row.outcome as SpecialistStatus['outcome'], completion, execution_resumed: false }
}

const terminal = new Set(['completed', 'failed', 'blocked', 'cancelled'])

/** UI authority is lease-bound; recovery remembers input identity, never permission to replay. */
export class NamedSpecialistSession {
  private request: RuntimeRequest
  private sessionId: string
  private newId: () => string
  private epoch = 0
  private attached = false
  private listeners = new Set<() => void>()
  private resolved = new Set<string>()
  private state: NamedSpecialistState = { connected: false, busy: false, draft: { projectId: '', specialistId: '', objective: '', artifacts: [], evidence: [], constraints: '' }, catalog: null, review: null, attempt: null, status: null, recoveryId: '', unresolved: [], error: null }
  constructor(request: RuntimeRequest, sessionId: string, newId: () => string = () => crypto.randomUUID()) { this.request = request; this.sessionId = sessionId; this.newId = newId }
  getState = (): NamedSpecialistState => this.state
  subscribe = (listener: () => void) => { this.listeners.add(listener);

    return () => { this.listeners.delete(listener) } }
  private set(patch: Partial<NamedSpecialistState>) { this.state = { ...this.state, ...patch }; this.listeners.forEach(listener => listener()) }
  attach(connected: boolean) {
    this.epoch++; this.attached = true
    this.set({ connected, busy: false, catalog: null, review: null, status: null, error: null })

    return () => { this.attached = false; this.epoch++; this.set({ connected: false, busy: false, review: null, catalog: null, status: null }) }
  }
  setUnresolved(ids: readonly string[]) {
    const unresolved = [...new Set(ids)].filter(id => !this.resolved.has(id))

    if (!same(unresolved, this.state.unresolved)) { this.set({ unresolved, review: null }) }
  }
  blocked() {
    const attempt = this.state.attempt
    const ownId = attempt?.review.params.command_id
    const ownComplete = !!ownId && this.resolved.has(ownId) || attempt?.receipt?.status === 'rejected' || this.state.status && this.state.status.command_id === ownId && terminal.has(this.state.status.outcome)

    return !!attempt && !ownComplete || this.state.unresolved.some(id => !this.resolved.has(id) && id !== ownId)
  }
  edit(patch: Partial<NamedSpecialistDraft>) {
    this.epoch++
    const projectChanged = patch.projectId !== undefined && patch.projectId !== this.state.draft.projectId
    this.set({ draft: { ...this.state.draft, ...patch, ...(projectChanged ? { specialistId: '' } : {}) }, review: null, busy: false, error: null, ...(projectChanged ? { catalog: null } : {}) })
  }
  recoveryId(value: string) { this.set({ recoveryId: value }) }
  private valid(epoch: number) { return this.attached && this.state.connected && epoch === this.epoch }
  private async read(work: (epoch: number) => Promise<void>) {
    if (!this.attached || !this.state.connected || this.state.busy) { return }
    const epoch = ++this.epoch
    this.set({ busy: true, error: null })

    try { await work(epoch) }
    catch { if (this.valid(epoch)) { this.set({ error: 'unavailable', review: null }) } }
    finally { if (this.valid(epoch)) { this.set({ busy: false }) } }
  }
  async catalog() {
    await this.read(async epoch => {
      const project_id = controlId(this.state.draft.projectId)
      this.set({ catalog: null, review: null, draft: { ...this.state.draft, specialistId: '' } })
      const result = parseNamedSpecialistCatalog(await this.request('runtime.specialist.catalog', { session_id: this.sessionId, schema_version: 1, project_id }))

      if (this.valid(epoch)) { this.set({ catalog: result }) }
    })
  }
  async preview() {
    if (this.blocked()) { return }
    await this.read(async epoch => {
      this.set({ review: null })
      let input: SpecialistPreviewParams

      try { input = namedSpecialistInput(this.sessionId, this.state.draft) }
      catch { this.set({ error: 'invalid' });

 return }

      const selected = this.state.catalog?.specialists.find(item => item.agent_id === input.specialist_id)

      if (!selected) { this.set({ error: 'changed' });

 return }

      const result = await this.request('runtime.specialist.preview', input)

      if (!this.valid(epoch)) { return }
      const preview = verifyNamedSpecialistPreview(result, input, selected)
      const params: SpecialistHandoffParams = { session_id: this.sessionId, schema_version: 1, command_id: this.newId(), idempotency_key: this.newId(), expected_revision: preview.runtime_revision, selection: preview.selection, preview_sha256: preview.preview_sha256 }
      this.set({ review: { preview, params } })
    })
  }
  async handoff(review: NamedSpecialistReview) {
    if (!this.attached || !this.state.connected || this.state.busy || this.blocked()) { return }

    if (review !== this.state.review || review.params.selection.expires_at * 1000 <= Date.now()) { this.set({ review: null, error: 'changed' });

 return }

    const epoch = ++this.epoch
    // Consume this review synchronously before the RPC, including duplicate keyboard/click events.
    this.set({ review: null, busy: true, status: null, error: null, attempt: { review, receipt: null, unknown: true } })

    try {
      const receipt = verifyNamedSpecialistReceipt(await this.request('runtime.specialist.handoff', review.params), review.params)

      if (this.valid(epoch)) { this.set({ attempt: { review, receipt, unknown: false } }) }
    } catch { if (this.valid(epoch)) { this.set({ error: 'unknown' }) } }
    finally { if (this.valid(epoch)) { this.set({ busy: false }) } }
  }
  async inspect(commandId = this.state.attempt?.review.params.command_id ?? this.state.recoveryId) {
    await this.read(async epoch => {
      const command_id = controlId(commandId)
      const original = this.state.attempt?.review.params.command_id === command_id ? this.state.attempt.review : undefined
      const status = verifyNamedSpecialistStatus(await this.request('runtime.specialist.status', { session_id: this.sessionId, schema_version: 1, command_id }), command_id, original, original ? this.state.attempt?.receipt?.run_id : undefined)

      if (!this.valid(epoch)) { return }

      if (terminal.has(status.outcome)) { this.resolved.add(command_id) }
      this.set({ status, review: null, unresolved: [...new Set([...this.state.unresolved, ...(!original && !terminal.has(status.outcome) ? [command_id] : [])])].filter(id => !this.resolved.has(id)),
        ...(original ? { attempt: { review: original, receipt: this.state.attempt!.receipt, unknown: status.outcome === 'unknown' } } : {}) })
    })
  }
}
