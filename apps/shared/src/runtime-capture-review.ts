import type { CaptureBatchCommitParams, CaptureBatchCommitResult, CaptureBatchItem, CaptureBatchParams, CaptureBatchPreviewResult, CaptureInspectResult, CaptureProcessParams, CaptureSearchParams, CaptureSearchResult } from './gateway-contract.generated.js'
import { type DownloadedArtifact, downloadRuntimeArtifact } from './runtime-artifacts.js'
import type { RuntimeRequest } from './runtime-control.js'
import { controlChoice, controlDigest, controlId, controlInteger, RuntimeInputError } from './runtime-research.js'

export interface CaptureDraft { before: CaptureInspectResult; item: CaptureBatchItem }
export interface CaptureReview { params: CaptureBatchParams; preview: CaptureBatchPreviewResult }
export interface CapturePending { batch?: CaptureReview; process?: CaptureProcessParams; project: string }
export interface CaptureReviewState {
  project: string; query: string; captureId: string; limit: number; scanLimit: number; busy: boolean; connected: boolean
  result: CaptureSearchResult | null; inspected: CaptureInspectResult | null; drafts: CaptureDraft[]
  review: CaptureReview | null; pending: CapturePending | null; download: DownloadedArtifact | null; error: string; message: string
}

const empty = (): CaptureReviewState => ({ project: '', query: '', captureId: '', limit: 20, scanLimit: 100, busy: false, connected: false,
  result: null, inspected: null, drafts: [], review: null, pending: null, download: null, error: '', message: '' })

export const captureSequence = (value: CaptureInspectResult): number => Math.max(0, ...value.capture.extractions.map(item => item.sequence))

export function captureSearchInput(session: string, project: string, query: string, limit = 20, scanLimit = 100): CaptureSearchParams {
  if (!query.trim() || [...query].length > 512) { throw new RuntimeInputError('Search needs 1–512 characters') }

  return { session_id: controlId(session), schema_version: 1, project_id: controlId(project), query,
    limit: controlInteger(limit, 1, 50), scan_limit: controlInteger(scanLimit, 1, 500) }
}

export function captureBatchInput(session: string, project: string, batchId: string, drafts: CaptureDraft[]): CaptureBatchParams {
  if (!drafts.length || drafts.length > 25 || new Set(drafts.map(d => d.item.capture_id)).size !== drafts.length) { throw new RuntimeInputError('Select 1–25 distinct captures') }

  const items = drafts.map(({ before, item }) => {
    if (before.capture.project_id !== project || before.capture.capture_id !== item.capture_id || before.capture.revision !== item.expected_revision) { throw new RuntimeInputError('Capture scope or revision changed; inspect again') }

    return { capture_id: controlId(item.capture_id), expected_revision: controlInteger(item.expected_revision, 0, 127),
      filed_project_id: item.filed_project_id === null ? null : controlId(item.filed_project_id),
      consolidated_into: item.consolidated_into === null ? null : controlId(item.consolidated_into) }
  })

  return { session_id: controlId(session), schema_version: 1, project_id: controlId(project), batch_id: controlId(batchId), items }
}

export function verifyCapturePreview(value: CaptureBatchPreviewResult, input: CaptureBatchParams, drafts: CaptureDraft[]): void {
  controlDigest(value.preview_digest)

  if (value.batch_id !== input.batch_id || value.project_id !== input.project_id || value.originals_preserved !== true || value.scope !== 'capture_metadata_only' || value.items.length !== input.items.length || new Set(value.items.map(i => i.capture_id)).size !== input.items.length) { throw new RuntimeInputError('Preview does not match the exact batch') }

  for (const item of input.items) {
    const found = value.items.find(row => row.capture_id === item.capture_id), before = drafts.find(row => row.item.capture_id === item.capture_id)?.before

    if (!found || !before || found.expected_revision !== item.expected_revision || found.filed_project_id !== item.filed_project_id || found.consolidated_into !== item.consolidated_into || found.previous_filed_project_id !== before.capture.filed_project_id || found.previous_consolidated_into !== before.consolidated_into) { throw new RuntimeInputError('Preview changed a reviewed revision or metadata target') }
    controlDigest(found.original_sha256)
  }
}

export function verifyCaptureCommit(value: CaptureBatchCommitResult, input: CaptureBatchCommitParams): void {
  if (value.batch_id !== input.batch_id || value.project_id !== input.project_id || value.preview_digest !== input.preview_digest || value.scope !== 'capture_metadata_only' || value.originals_preserved !== true || value.items.length !== input.items.length || new Set(value.items.map(i => i.capture_id)).size !== input.items.length) { throw new RuntimeInputError('Commit receipt differs from the reviewed batch; inspect') }

  for (const item of input.items) {
    const found = value.items.find(row => row.capture_id === item.capture_id)

    if (!found || found.revision !== item.expected_revision + 1 || found.filed_project_id !== item.filed_project_id || found.consolidated_into !== item.consolidated_into) { throw new RuntimeInputError('Commit receipt changed a reviewed target or revision; inspect') }
  }
}

function verifyInspect(value: CaptureInspectResult, id: string, project: string): void {
  if (value.capture.capture_id !== id || value.capture.project_id !== project) { throw new RuntimeInputError('Inspection returned a different capture or project') }
}

const rejectedCodes = new Set(['revision_conflict', 'duplicate_mismatch', 'invalid_artifact', 'identity_mismatch', 'project_grant_revoked'])

/** One cache per exact request function/session. Only unresolved mutation identity survives remounts. */
export class CaptureReviewSession {
  private state = empty()
  private listeners = new Set<() => void>()
  private epoch = 0
  private mounted = false
  private downloadAbort: AbortController | null = null
  private request: RuntimeRequest
  private sessionId: string
  private newId: () => string
  constructor(request: RuntimeRequest, sessionId: string, newId: () => string = () => crypto.randomUUID()) {
    this.request = request
    this.sessionId = sessionId
    this.newId = newId
  }
  getState = (): CaptureReviewState => this.state
  subscribe = (listener: () => void): (() => void) => { this.listeners.add(listener);

 return () => this.listeners.delete(listener) }
  private set(patch: Partial<CaptureReviewState>): void { this.state = { ...this.state, ...patch }; this.listeners.forEach(listener => listener()) }
  attach(connected: boolean): () => void {
    this.downloadAbort?.abort()
    this.mounted = true; this.epoch++
    this.set({ ...empty(), pending: this.state.pending, connected })

    return () => { this.downloadAbort?.abort(); this.mounted = false; this.epoch++; this.set({ ...empty(), pending: this.state.pending }) }
  }
  private valid(epoch: number): boolean { return this.mounted && this.state.connected && this.epoch === epoch }
  edit(patch: Partial<Pick<CaptureReviewState, 'project' | 'query' | 'captureId' | 'limit' | 'scanLimit'>>): void {
    if (this.state.busy) { return }
    this.epoch++
    this.set({ ...patch, review: null, message: '', ...(patch.captureId === undefined ? { result: null } : { inspected: null, download: null }), ...(patch.project !== undefined ? { inspected: null, drafts: [], download: null } : {}) })
  }
  select(): void {
    const before = this.state.inspected

    if (!before || this.state.busy || this.state.pending) { return }
    const existing = this.state.drafts.find(d => d.item.capture_id === before.capture.capture_id)

    if (existing) {
      this.set({ review: null, drafts: this.state.drafts.map(d => d === existing ? { before, item: { ...d.item, expected_revision: before.capture.revision } } : d), message: `Selected revision refreshed: ${before.capture.capture_id}@${before.capture.revision}` })

      return
    }

    if (this.state.drafts.length === 25) { this.set({ error: 'A batch is limited to 25 captures' });

 return }

    this.set({ review: null, drafts: [...this.state.drafts, { before, item: { capture_id: before.capture.capture_id, expected_revision: before.capture.revision, filed_project_id: before.capture.filed_project_id, consolidated_into: before.consolidated_into } }] })
  }
  change(id: string, patch: Partial<Pick<CaptureBatchItem, 'filed_project_id' | 'consolidated_into'>> | null): void {
    if (this.state.busy || this.state.pending) { return }
    this.epoch++
    this.set({ review: null, message: '', drafts: patch === null ? this.state.drafts.filter(d => d.item.capture_id !== id) : this.state.drafts.map(d => d.item.capture_id === id ? { ...d, item: { ...d.item, ...patch } } : d) })
  }
  private async action(work: (epoch: number) => Promise<void>): Promise<void> {
    if (!this.mounted || !this.state.connected || this.state.busy) { return }
    const epoch = ++this.epoch, prior = this.state.pending
    this.set({ busy: true, error: '', message: '' })

    try { await work(epoch) }
    catch (error) {
      if (this.valid(epoch)) {
        const code = (error as { data?: { code?: string } } | null)?.data?.code
        // These capture transaction failures reject the request before committing effects.
        const rejected = code !== undefined && rejectedCodes.has(code) && this.state.pending !== prior

        if (rejected) { this.set({ pending: null, inspected: null }) }
        this.set({ error: `${error instanceof Error ? error.message : 'Capture request failed'}${code ? ` (${code})` : ''}.${this.state.pending ? ' Outcome is unconfirmed. Inspect retained identity; no automatic retry was sent.' : ' Inspect current revisions before retrying.'}`, review: null })
      }
    }
    finally { if (this.valid(epoch)) { this.set({ busy: false }) } }
  }
  search(): Promise<void> {
    return this.action(async epoch => {
      const input = captureSearchInput(this.sessionId, this.state.project, this.state.query, this.state.limit, this.state.scanLimit)
      const result = await this.request('runtime.capture.search', input)

      if (!this.valid(epoch)) { return }

      if (result.search_mode !== 'lexical_fuzzy' || result.complete !== false || result.matches.length > input.limit! || result.scanned > input.scan_limit! || result.matches.some(m => m.capture.project_id !== input.project_id)) { throw new RuntimeInputError('Search returned unsupported bounds or scope') }
      this.set({ result, inspected: null, download: null })
    })
  }
  inspect(id: string): Promise<void> {
    return this.action(async epoch => {
      const project = controlId(this.state.project)
      const value = await this.request('runtime.capture.inspect', { session_id: this.sessionId, schema_version: 1, capture_id: controlId(id) })

      if (!this.valid(epoch)) { return }
      verifyInspect(value, id, project)
      this.set({ inspected: value, captureId: id, download: null, review: null })
    })
  }
  process(source: 'original' | 'latest_extraction'): Promise<void> {
    return this.action(async epoch => {
      const value = this.state.inspected

      if (!value || this.state.pending) { return }
      const sequence = controlInteger(captureSequence(value), 0, 31)
      const latest = value.capture.extractions.at(-1)

      if (source === 'latest_extraction' && (latest?.status !== 'succeeded' || !latest.extracted_ref)) { throw new RuntimeInputError('Inspect a successful latest supplied-text extraction first') }
      const input: CaptureProcessParams = { session_id: this.sessionId, schema_version: 1, capture_id: value.capture.capture_id, expected_extraction_sequence: sequence, source }
      this.set({ pending: { process: input, project: value.capture.project_id }, review: null })
      const result = await this.request('runtime.capture.process', input)

      if (!this.valid(epoch)) { return }
      verifyInspect(result, input.capture_id, value.capture.project_id)

      const original = value.capture.original_ref, expectedSource = source === 'original' ? original : latest!.extracted_ref!

      if (captureSequence(result) !== sequence + 1 || result.processing.extraction_sequence !== sequence + 1 || result.capture.original_ref.artifact_id !== original.artifact_id || result.capture.original_ref.version !== original.version) { throw new RuntimeInputError('Processing returned an unexpected extraction sequence or original') }

      if (!['indexed', 'metadata_only'].includes(result.processing.status) || (result.processing.status === 'indexed' && (result.processing.source_ref?.artifact_id !== expectedSource.artifact_id || result.processing.source_ref.version !== expectedSource.version))) { throw new RuntimeInputError('Processing receipt does not match the selected local source') }
      this.set({ pending: null, inspected: result, result: null, message: `Processing acknowledged: ${result.processing.status}; extraction ${captureSequence(result)}. Original retained.` })
    })
  }
  preview(): Promise<void> {
    return this.action(async epoch => {
      if (this.state.pending) { return }
      const drafts = this.state.drafts, params = captureBatchInput(this.sessionId, this.state.project, this.newId(), drafts)
      const preview = await this.request('runtime.capture.batch.preview', params)

      if (!this.valid(epoch)) { return }
      verifyCapturePreview(preview, params, drafts)
      this.set({ review: { params, preview } })
    })
  }
  commit(review: CaptureReview): Promise<void> {
    return this.action(async epoch => {
      if (this.state.pending || this.state.review !== review) { return }
      verifyCapturePreview(review.preview, review.params, this.state.drafts)
      const params = { ...review.params, preview_digest: review.preview.preview_digest }
      this.set({ pending: { batch: review, project: params.project_id }, review: null })
      const result = await this.request('runtime.capture.batch.commit', params)

      if (!this.valid(epoch)) { return }
      verifyCaptureCommit(result, params)
      this.set({ pending: null, drafts: [], inspected: null, result: null, message: `Batch ${result.batch_id} acknowledged: ${result.items.map(i => `${i.capture_id} revision ${i.revision}`).join(', ')}. Metadata only; originals retained.` })
    })
  }
  reconcile(): Promise<void> {
    return this.action(async epoch => {
      const pending = this.state.pending

      if (!pending) { return }
      const ids = pending.batch?.params.items.map(i => i.capture_id) ?? [pending.process!.capture_id]
      const values = await Promise.all(ids.map(capture_id => this.request('runtime.capture.inspect', { session_id: this.sessionId, schema_version: 1, capture_id })))

      if (!this.valid(epoch)) { return }
      values.forEach((value, i) => verifyInspect(value, ids[i], pending.project))
      const proven = pending.batch ? pending.batch.params.items.every((item, i) => values[i].capture.revision === item.expected_revision + 1 && values[i].capture.filed_project_id === item.filed_project_id && values[i].consolidated_into === item.consolidated_into) : captureSequence(values[0]) > pending.process!.expected_extraction_sequence
      this.set({ pending: proven ? null : pending, review: null, drafts: [], result: null, inspected: null,
        message: `${values.map(v => `${v.capture.capture_id}: revision ${v.capture.revision}, extraction ${captureSequence(v)}, filed ${v.capture.filed_project_id ?? 'none'}, consolidated ${v.consolidated_into ?? 'none'}`).join('; ')}. ${proven ? 'Current state reconciled by inspection; this does not establish which request wrote it. Inspect again before new work.' : 'Outcome still unknown; retained identity remains locked. No retry sent.'}` })
    })
  }
  downloadOriginal(): Promise<void> {
    return this.action(async epoch => {
      const capture = this.state.inspected?.capture

      if (!capture) { return }
      this.downloadAbort = new AbortController()
      const result = await downloadRuntimeArtifact(this.request, this.sessionId, capture.project_id, capture.original_ref.artifact_id, capture.original_ref.version, this.downloadAbort.signal)

      if (this.valid(epoch)) { this.set({ download: result }) }
    })
  }
}

const sessions = new WeakMap<RuntimeRequest, Map<string, CaptureReviewSession>>()

export function captureReviewSession(request: RuntimeRequest, sessionId: string): CaptureReviewSession {
  let owned = sessions.get(request)

  if (!owned) { owned = new Map(); sessions.set(request, owned) }
  let session = owned.get(sessionId)

  if (!session) { session = new CaptureReviewSession(request, sessionId); owned.set(sessionId, session) }

  return session
}

const help = 'Capture review: inspect <capture-id> | search <project-id> <query> | process <capture-id> <expected-extraction-sequence> <original|latest_extraction>\nUse the typed desktop batch review for exact metadata previews and confirmation. Search is bounded lexical/fuzzy, never semantic or exhaustive. Original bytes remain unchanged.'
const commandPending = new WeakMap<RuntimeRequest, Map<string, CaptureProcessParams>>()

export async function runCaptureReviewCommand(argument: string, request: RuntimeRequest, sessionId: string): Promise<string> {
  const [action, id, ...rest] = argument.trim().split(/\s+/u), base = { session_id: controlId(sessionId), schema_version: 1 as const }
  let pending = commandPending.get(request)

  if (!pending) { pending = new Map(); commandPending.set(request, pending) }
  const key = `${sessionId.length}:${sessionId}:${id}`

  const handlers: Record<string, () => Promise<string>> = {
    inspect: async () => {
      const r = await request('runtime.capture.inspect', { ...base, capture_id: controlId(id) })

      if (r.capture.capture_id !== id) { throw new RuntimeInputError('Inspection returned another capture') }
      const prior = pending.get(key)

      if (prior && captureSequence(r) > prior.expected_extraction_sequence) { pending.delete(key) }

      return `${r.capture.capture_id}: revision ${r.capture.revision}; ${r.processing.status}; extraction ${captureSequence(r)}\nOriginal ${r.capture.original_ref.artifact_id}@${r.capture.original_ref.version}; ${new Date(r.capture.acquired_at * 1000).toISOString()}\n${r.capture.annotation.slice(0, 8192)}\nFiled ${r.capture.filed_project_id ?? 'none'}; consolidated ${r.consolidated_into ?? 'none'}${pending.has(key) ? '\nProcessing outcome remains unknown; no retry permitted yet' : ''}`
    },
    search: async () => {
      const result = await request('runtime.capture.search', captureSearchInput(sessionId, id, rest.join(' ')))

      return `Bounded lexical/fuzzy search: ${result.scanned} scanned; complete: false\n${result.matches.map(m => `${m.capture.capture_id} (${m.processing.status}) · original ${m.capture.original_ref.artifact_id}@${m.capture.original_ref.version}\n${m.excerpt.slice(0, 320)}`).join('\n\n') || 'No matches in this scan'}\n${result.limitations.join('\n')}`
    },
    process: async () => {
      if (pending.has(key)) { throw new RuntimeInputError(`Retained processing ${id}, expected extraction ${pending.get(key)!.expected_extraction_sequence}; inspect first`) }
      const input = { ...base, capture_id: controlId(id), expected_extraction_sequence: controlInteger(Number(rest[0]), 0, 31), source: controlChoice(rest[1], ['original', 'latest_extraction']) }
      pending.set(key, input)
      const result = await request('runtime.capture.process', input)

      if (result.capture.capture_id !== id || captureSequence(result) !== input.expected_extraction_sequence + 1) { throw new RuntimeInputError('Unexpected processing receipt') }
      pending.delete(key)

      return `Processing acknowledged: ${result.processing.status}; extraction ${captureSequence(result)}. Original retained.`
    }
  }

  try { return Object.hasOwn(handlers, action) ? await handlers[action]() : help }
  catch (error) {
    if (action === 'process' && rejectedCodes.has((error as { data?: { code?: string } } | null)?.data?.code ?? '')) { pending.delete(key) }

    return `${error instanceof Error ? error.message : 'Capture request failed'}. ${action === 'process' ? 'Inspect current state before any retry. No automatic retry sent.' : 'No mutation was requested.'}`
  }
}
