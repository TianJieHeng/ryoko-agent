import type { ArtifactControlStatus, ArtifactProposalResult, ConnectedSourcePrepareParams, ConnectedSourcePreviewResult, ConnectedSourcePublishParams, ConnectedSourceResult } from './gateway-contract.generated.js'
import type { RuntimeRequest } from './runtime-control.js'
import { controlChoice, controlDigest, controlId, controlInteger, controlJson, controlList, controlRecord, controlText } from './runtime-research.js'

export type ConnectedSelection = ConnectedSourcePrepareParams['selection']
export interface ConnectedSourceInput {
  project: string; kind: ConnectedSelection['kind']; account: string; mailbox: string; thread: string
  calendars: string; timezone: string; start: string; end: string
}
export interface ConnectedSourceRecord {
  selection?: ConnectedSelection; coverage: ConnectedSourceResult['coverage']; errors: string[]
  [key: string]: unknown
}
export interface ConnectedSourceReview {
  params: ConnectedSourcePrepareParams; result: ConnectedSourceResult; record: ConnectedSourceRecord
  originalText?: string; projectionText?: string
}
export interface ConnectedSourceAttempt {
  commandId: string; requestId: string; projectId: string; preparationId: string | null
  phase: 'prepare' | 'publish'; status: 'unknown' | 'prepared' | 'published' | 'partial' | 'unavailable' | 'lost' | 'resolved'
}
export type ConnectedSourceError = 'invalid' | 'changed' | 'unknown' | 'lost' | 'expired' | 'inspection'
export interface ConnectedSourceState {
  input: ConnectedSourceInput; connected: boolean; busy: boolean; review: ConnectedSourceReview | null
  result: ConnectedSourceResult | null; originalApproved: boolean; projectionApproved: boolean
  attempt: ConnectedSourceAttempt | null; inspection: ArtifactControlStatus | null; error: ConnectedSourceError | null
}

function requireValue(value: unknown): asserts value { if (!value) { throw new Error('Invalid connected source evidence') } }

function finite(value: unknown): number { requireValue(typeof value === 'number' && Number.isFinite(value) && value >= 0);

 return value }

function same(a: unknown, b: unknown): boolean {
  if (a === b) { return true }

  if (!a || !b || typeof a !== 'object' || typeof b !== 'object' || Array.isArray(a) !== Array.isArray(b)) { return false }
  const left = a as Record<string, unknown>, right = b as Record<string, unknown>

  return Object.keys(left).length === Object.keys(right).length && Object.entries(left).every(([key, value]) => Object.hasOwn(right, key) && same(value, right[key]))
}

/** Keep offsets explicit; datetime-local would silently substitute the browser zone. */
function zonedTime(value: string, timezone: string): number {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,3}))?(Z|[+-]\d{2}:\d{2})$/u.exec(value)
  requireValue(match)
  const at = Date.parse(value)
  requireValue(Number.isFinite(at))
  const parts = new Intl.DateTimeFormat('en-GB', { timeZone: timezone, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' }).formatToParts(at)
  const fields = Object.fromEntries(parts.map(part => [part.type, part.value]))
  requireValue(['year', 'month', 'day', 'hour', 'minute', 'second'].every((key, index) => fields[key] === match[index + 1]))

  return at
}

export function connectedSourceSelection(input: ConnectedSourceInput): ConnectedSelection {
  const account_id = controlId(input.account)

  if (input.kind === 'gmail_thread') {
    const mailbox = controlText(input.mailbox, 320)
    requireValue(mailbox === mailbox.trim() && /^[^\s<>@*]+@[^\s<>@*]+$/u.test(mailbox))

    return { kind: 'gmail_thread', account_id, mailbox, thread_id: controlId(input.thread) }
  }

  requireValue(input.kind === 'calendar_availability')
  const calendar_ids = input.calendars.split('\n').map(id => controlId(id))
  requireValue(calendar_ids.length >= 1 && calendar_ids.length <= 20 && new Set(calendar_ids).size === calendar_ids.length && !calendar_ids.includes('primary'))
  const timezone = controlText(input.timezone, 128)
  const start_at = controlText(input.start, 64), end_at = controlText(input.end, 64)
  const start = zonedTime(start_at, timezone), end = zonedTime(end_at, timezone)
  requireValue(end > start && end - start <= 7 * 86400000)

  return { kind: 'calendar_availability', account_id, calendar_ids, timezone, start_at, end_at }
}

function proposal(value: unknown, project: string): ArtifactProposalResult {
  const row = controlRecord(value, ['request_id', 'project_id', 'artifact_id', 'version', 'sha256', 'size', 'mime', 'parent_version', 'expected_head_version', 'action_digest', 'approval_id', 'approval_digest', 'expires_at'], [])
  requireValue(row.project_id === project && row.mime === 'application/json')
  controlId(row.request_id); controlId(row.artifact_id); controlId(row.approval_id)
  controlInteger(row.version, 1); controlInteger(row.size, 1, 2 * 1024 * 1024 + 1)
  controlDigest(row.sha256); controlDigest(row.action_digest); controlDigest(row.approval_digest); finite(row.expires_at)

  if (row.parent_version !== null) { controlInteger(row.parent_version, 1) }

  if (row.expected_head_version !== null) { controlInteger(row.expected_head_version, 1) }

  return row as unknown as ArtifactProposalResult
}

const metadataKeys = ['source_kind', 'selection', 'scope', 'retrieved_at', 'fresh_until', 'provider_version', 'provider_version_basis', 'tool_schema_sha256', 'payload_sha256', 'representation', 'coverage', 'errors', 'execution_authority', 'claim_verification', 'attachments', 'account_binding', 'gateway_http_attempts', 'upstream_retry_count', 'cost_tracking', 'connector', 'tool', 'gateway_recipient_id', 'gateway_endpoint_sha256', 'arguments_sha256', 'projection_kind', 'publication_atomic', 'live_qualification', 'attachment_bytes_fetched', 'recipient_identity_status']

/** The frozen record contains evidence metadata, never executable provider instructions. */
export function parseConnectedSourceResult(value: unknown, params: ConnectedSourcePrepareParams, prepared?: ConnectedSourceReview): ConnectedSourceReview {
  const row = controlRecord(value, ['project_id', 'state', 'source_kind', 'account_id', 'coverage', 'errors', 'record_json'], ['preparation_id', 'observed_at', 'fresh_until', 'original', 'projection'])
  requireValue(row.project_id === params.project_id && row.account_id === params.selection.account_id && row.source_kind === params.selection.kind)
  const state = controlChoice(row.state, ['awaiting_approval', 'published', 'partial', 'unavailable'])
  const coverage = controlChoice(row.coverage, ['complete', 'partial', 'unavailable'])
  const errors = controlList(row.errors, 0, 100).map(controlId)
  requireValue(typeof row.record_json === 'string')
  const record = controlRecord(controlJson(row.record_json, 65536, false))
  requireValue(record.coverage === coverage && same(record.errors, errors))

  if (state === 'unavailable') {
    controlRecord(record, ['coverage', 'errors'], [])
    requireValue(!prepared && coverage === 'unavailable' && errors.length > 0 && row.original == null && row.projection == null && row.preparation_id == null && row.observed_at == null && row.fresh_until == null)
  } else {
    requireValue(coverage !== 'unavailable')
    controlRecord(record, metadataKeys, ['original_ref', 'projection_ref', 'research_request', 'projection_research_request', 'publication_status', 'recovery'])
    requireValue(same(record.selection, params.selection) && record.source_kind === params.selection.kind)
    const scope = controlRecord(record.scope, ['principal_id', 'profile_id', 'agent_id', 'project_id', 'policy_digest'], [])
    Object.values(scope).forEach(value => controlId(value)); controlDigest(scope.policy_digest)
    requireValue(scope.project_id === params.project_id)
    requireValue(finite(record.retrieved_at) === row.observed_at && finite(record.fresh_until) === row.fresh_until && Number(row.fresh_until) > Number(row.observed_at))
    requireValue(record.execution_authority === false && record.claim_verification === 'not_performed' && record.attachment_bytes_fetched === false && record.publication_atomic === false && record.live_qualification === 'pending')
    requireValue(record.account_binding === 'explicit_execute_account_selector' && record.recipient_identity_status === 'explicit_ids_not_person_identity_verification')
    requireValue(record.representation === 'canonical_gateway_json_not_rfc822_or_provider_http_bytes' && record.attachments === 'metadata_only_no_attachment_fetch' && record.upstream_retry_count === 'not_attested' && record.cost_tracking === 'untracked')
    controlInteger(record.gateway_http_attempts, 1, 2); controlId(record.gateway_recipient_id)

    for (const key of ['tool_schema_sha256', 'payload_sha256', 'gateway_endpoint_sha256', 'arguments_sha256']) { controlDigest(record[key]) }
    requireValue(record.provider_version == null || typeof record.provider_version === 'string')
    requireValue(record.provider_version_basis === (record.provider_version ? 'history_id' : 'unavailable'))
    const gmail = params.selection.kind === 'gmail_thread'
    requireValue(record.connector === (gmail ? 'gmail' : 'googlecalendar') && record.tool === (gmail ? 'GMAIL_FETCH_MESSAGE_BY_THREAD_ID' : 'GOOGLECALENDAR_FREE_BUSY_QUERY') && record.projection_kind === (gmail ? 'inbox_snapshot' : 'calendar_availability_snapshot'))
    controlId(row.preparation_id)
    const original = proposal(row.original, params.project_id)
    const projection = row.projection == null ? null : proposal(row.projection, params.project_id)
    requireValue((coverage === 'complete') === !!projection && (coverage === 'complete' ? errors.length === 0 : errors.length > 0))

    if (projection) { requireValue(projection.approval_id !== original.approval_id && projection.artifact_id !== original.artifact_id) }

    if (prepared) {
      requireValue(state === 'published' || state === 'partial')
      requireValue(row.preparation_id === prepared.result.preparation_id && same(row.original, prepared.result.original) && same(row.projection, prepared.result.projection))
      requireValue(metadataKeys.every(key => same(record[key], prepared.record[key])))

      const ref = (value: unknown, artifact: ArtifactProposalResult) => {
        const item = controlRecord(value, ['artifact_id', 'version', 'sha256'], [])
        requireValue(item.artifact_id === artifact.artifact_id && item.version === artifact.version && item.sha256 === artifact.sha256)
      }

      ref(record.original_ref, original)

      if (state === 'partial') { requireValue(projection && record.projection_ref === null && record.publication_status === 'projection_not_confirmed' && record.recovery === 'retry_exact_publish_without_refetch') }
      else if (projection) { ref(record.projection_ref, projection) }
      else { requireValue(record.projection_ref === null) }
    } else { requireValue(state === 'awaiting_approval' && !Object.hasOwn(record, 'original_ref') && !Object.hasOwn(record, 'research_request')) }
  }

  // Clone wire data so transport caches cannot mutate an already reviewed object.
  return structuredClone({ params, result: row as unknown as ConnectedSourceResult, record: record as ConnectedSourceRecord })
}

/** Read only the volatile preparation, never a provider or a current artifact head. */
export async function readConnectedSourcePreview(request: RuntimeRequest, review: ConnectedSourceReview, part: 'original' | 'projection', current: () => boolean = () => true): Promise<string> {
  const expected = review.result[part]
  requireValue(expected && review.result.preparation_id)
  const bytes = new Uint8Array(controlInteger(expected.size, 1, 2 * 1024 * 1024 + 1))
  let offset = 0

  while (offset < bytes.length) {
    requireValue(current())

    const chunk: ConnectedSourcePreviewResult = await request('runtime.sources.preview', { session_id: review.params.session_id, schema_version: 1, project_id: review.params.project_id,
      command_id: review.params.command_id, preparation_id: review.result.preparation_id, part, offset, limit: 65536 })

    requireValue(current())
    requireValue(chunk.project_id === expected.project_id && chunk.artifact_id === expected.artifact_id && chunk.version === expected.version && chunk.size === expected.size && chunk.sha256 === expected.sha256 && chunk.mime === expected.mime)
    requireValue(chunk.preparation_id === review.result.preparation_id && chunk.part === part && chunk.approval_id === expected.approval_id && chunk.approval_digest === expected.approval_digest && chunk.preview_mode === 'plain_text')
    requireValue(chunk.offset === offset && typeof chunk.data_base64 === 'string' && chunk.data_base64.length <= 87384)
    const decoded = Uint8Array.from(atob(chunk.data_base64), char => char.charCodeAt(0))
    requireValue(decoded.length > 0 && decoded.length <= 65536 && chunk.next_offset === offset + decoded.length && chunk.next_offset <= expected.size)
    requireValue(chunk.eof === (chunk.next_offset === expected.size))
    bytes.set(decoded, offset); offset = chunk.next_offset
  }

  const digest = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))).map(byte => byte.toString(16).padStart(2, '0')).join('')
  requireValue(current() && digest === expected.sha256)

  return new TextDecoder('utf-8', { fatal: true }).decode(bytes)
}

export function connectedSourcePublishInput(review: ConnectedSourceReview, now = Date.now()): ConnectedSourcePublishParams {
  const original = review.result.original, projection = review.result.projection
  requireValue(review.result.preparation_id && original && typeof review.originalText === 'string' && (!projection || typeof review.projectionText === 'string'))
  requireValue(original.expires_at * 1000 > now && (!projection || projection.expires_at * 1000 > now))

  return { session_id: review.params.session_id, schema_version: 1, project_id: review.params.project_id, command_id: review.params.command_id,
    preparation_id: review.result.preparation_id, original_approval_id: original.approval_id, original_approval_digest: original.approval_digest,
    ...(projection ? { projection_approval_id: projection.approval_id, projection_approval_digest: projection.approval_digest } : {}) }
}

const initialInput = (): ConnectedSourceInput => ({ project: '', kind: 'gmail_thread', account: '', mailbox: '', thread: '', calendars: '', timezone: '', start: '', end: '' })
const initial = (): ConnectedSourceState => ({ input: initialInput(), connected: false, busy: false, review: null, result: null, originalApproved: false, projectionApproved: false, attempt: null, inspection: null, error: null })

/** UI authority ends with its mounted connection generation. IDs survive as passive recovery hints. */
export class ConnectedSourceSession {
  private state = initial()
  private epoch = 0
  private mounted = false
  private listeners = new Set<() => void>()
  private externalPending: readonly string[] = []
  private request: RuntimeRequest
  private sessionId: string
  private newId: () => string
  constructor(request: RuntimeRequest, sessionId: string, newId: () => string = () => crypto.randomUUID()) { this.request = request; this.sessionId = sessionId; this.newId = newId }
  getState = () => this.state
  subscribe = (listener: () => void) => { this.listeners.add(listener);

 return () => { this.listeners.delete(listener) } }
  private set(patch: Partial<ConnectedSourceState>) { this.state = { ...this.state, ...patch }; this.listeners.forEach(listener => listener()) }
  attach(connected: boolean) {
    this.mounted = true; this.epoch++
    this.set({ ...initial(), connected, attempt: this.state.attempt })

    return () => { this.mounted = false; this.epoch++; this.set({ ...initial(), attempt: this.state.attempt }) }
  }
  setUnresolved(ids: readonly string[]) {
    this.externalPending = ids.filter(id => id !== this.state.attempt?.commandId)

    if (this.externalPending.length) { this.set({ review: null, originalApproved: false, projectionApproved: false }) }
  }
  private current(epoch: number) { return this.mounted && this.state.connected && epoch === this.epoch }
  blocked() { return this.externalPending.length > 0 || this.state.attempt?.status === 'unknown' }
  edit(patch: Partial<ConnectedSourceInput>) {
    if (this.state.busy || this.blocked()) { return }
    this.epoch++; this.set({ input: { ...this.state.input, ...patch }, review: null, result: null, originalApproved: false, projectionApproved: false, error: null })
  }
  approve(kind: 'original' | 'projection', approved: boolean) {
    if (!this.mounted || !this.state.connected || this.state.busy || this.blocked() || !this.state.review) { return }
    this.set(kind === 'original' ? { originalApproved: approved } : { projectionApproved: approved })
  }
  dismiss(review: ConnectedSourceReview | null) { if (review === this.state.review) { this.set({ originalApproved: false, projectionApproved: false }) } }
  async prepare() {
    if (!this.mounted || !this.state.connected || !this.sessionId || this.state.busy || this.blocked()) { return }
    let params: ConnectedSourcePrepareParams

    try { params = { session_id: controlId(this.sessionId), schema_version: 1, project_id: controlId(this.state.input.project), command_id: this.newId(), request_id: this.newId(), selection: connectedSourceSelection(this.state.input) } }
    catch { this.set({ error: 'invalid' });

 return }

    const epoch = ++this.epoch
    const attempt: ConnectedSourceAttempt = { commandId: params.command_id, requestId: params.request_id, projectId: params.project_id, preparationId: null, phase: 'prepare', status: 'unknown' }
    this.set({ busy: true, review: null, result: null, originalApproved: false, projectionApproved: false, attempt, inspection: null, error: null })

    try {
      const raw = await this.request('runtime.sources.prepare', params)

      if (!this.current(epoch)) { return }
      const review = parseConnectedSourceResult(raw, params)
      this.set({ result: review.result, attempt: { ...attempt, preparationId: review.result.preparation_id ?? null, status: review.result.state === 'unavailable' ? 'unavailable' : 'prepared' } })

      if (review.result.state === 'awaiting_approval') {
        try {
          const originalText = await readConnectedSourcePreview(this.request, review, 'original', () => this.current(epoch))
          const projectionText = review.result.projection ? await readConnectedSourcePreview(this.request, review, 'projection', () => this.current(epoch)) : undefined

          if (this.current(epoch)) { this.set({ review: { ...review, originalText, projectionText } }) }
        } catch (error) {
          if (!this.current(epoch)) { return }
          const code = (error as { data?: { code?: unknown } } | null)?.data?.code
          this.set({ error: code === 'source_preparation_lost' ? 'lost' : 'changed' })
        }
      }
    } catch { if (this.current(epoch)) { this.set({ error: 'unknown' }) } }
    finally { if (this.current(epoch)) { this.set({ busy: false }) } }
  }
  async publish(review: ConnectedSourceReview) {
    if (!this.mounted || !this.state.connected || this.state.busy || this.blocked() || review !== this.state.review || !this.state.originalApproved || review.result.projection && !this.state.projectionApproved) { return }
    let params: ConnectedSourcePublishParams

    try { params = connectedSourcePublishInput(review) } catch { this.set({ review: null, error: 'expired', originalApproved: false, projectionApproved: false });

 return }

    const epoch = ++this.epoch
    const attempt: ConnectedSourceAttempt = { commandId: params.command_id, requestId: review.params.request_id, projectId: params.project_id, preparationId: params.preparation_id, phase: 'publish', status: 'unknown' }
    this.set({ busy: true, review: null, result: null, attempt, originalApproved: false, projectionApproved: false, error: null })

    try {
      const result = await this.request('runtime.sources.publish', params)

      if (!this.current(epoch)) { return }
      const receipt = parseConnectedSourceResult(result, review.params, review)
      this.set({ result: receipt.result, review: receipt.result.state === 'partial' ? review : null, attempt: { ...attempt, status: receipt.result.state === 'partial' ? 'partial' : 'published' } })
    } catch (error) {
      if (!this.current(epoch)) { return }
      const code = (error as { data?: { code?: unknown } } | null)?.data?.code
      this.set({ error: code === 'source_preparation_lost' ? 'lost' : 'unknown', attempt: { ...attempt, status: code === 'source_preparation_lost' ? 'lost' : 'unknown' } })
    } finally { if (this.current(epoch)) { this.set({ busy: false }) } }
  }
  async inspect(commandId = this.state.attempt?.commandId) {
    if (!this.mounted || !this.state.connected || this.state.busy || !commandId) { return }

    if (commandId !== this.state.attempt?.commandId && !this.externalPending.includes(commandId)) { return }
    const epoch = ++this.epoch
    this.set({ busy: true, review: null, originalApproved: false, projectionApproved: false, error: null })

    try {
      const result = await this.request('runtime.artifact.status', { session_id: this.sessionId, schema_version: 1, command_id: commandId })

      if (!this.current(epoch)) { return }
      requireValue(result.command_id === commandId)
      controlChoice(result.status, ['accepted', 'claimed', 'completed', 'cancelled', 'failed', 'blocked'])
      const terminal = ['completed', 'cancelled', 'failed', 'blocked'].includes(result.status)
      this.set({ inspection: result, ...(terminal && this.state.attempt?.commandId === commandId ? { attempt: { ...this.state.attempt, status: 'resolved' as const } } : {}) })
    } catch { if (this.current(epoch)) { this.set({ error: 'inspection' }) } }
    finally { if (this.current(epoch)) { this.set({ busy: false }) } }
  }
}
