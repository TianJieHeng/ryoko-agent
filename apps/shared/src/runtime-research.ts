import type {
  ArtifactControlStatus, ArtifactProposalResult, ArtifactPublishResult, BriefManifestRef,
  BriefPrepareParams, BriefPublishParams, ResearchResponse
} from './gateway-contract.generated.js'
import type { RuntimeRequest } from './runtime-control.js'

type OwnedBrief = Omit<BriefPrepareParams, 'session_id' | 'schema_version'>
type OwnedPublish = Omit<BriefPublishParams, 'session_id' | 'schema_version'>
export interface ResearchSourceRequest {
  source_id: string
  source_type: 'project_artifact' | 'capture_original'
  project_id: string
  version: number
  sha256: string
  authority?: 'authoritative_spec' | 'casual_note' | 'source_claim' | 'user_statement'
  evidence_ranges?: EvidenceByteRange[]
  fresh_until?: number | null
}
export interface EvidenceByteRange { start: number; end: number; sha256: string; quote: string; anchor_id?: string | null }
export interface ResearchSourceView {
  sourceId: string; projectId: string; version: number; sha256: string; headVersion: number | null
  availability: string; freshness: string; coveredBytes: number; size: number | null
  errors: string[]; ranges: { start: number; end: number; sha256: string }[]
}
export interface ResearchView { sources: ResearchSourceView[]; available: number; requested: number; stale: number; complete: boolean }

/** These validators parse JSON fields that the frozen wire contract intentionally encodes. */
export class RuntimeInputError extends Error {}

export function controlRecord(value: unknown, required: string[] = [], optional?: string[]): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {throw new RuntimeInputError('Expected a JSON object')}
  const record = value as Record<string, unknown>

  if (required.some(key => !Object.hasOwn(record, key)) || (optional && Object.keys(record).some(key => !required.includes(key) && !optional.includes(key)))) {
    throw new RuntimeInputError('Missing required fields or unsupported fields')
  }

  return record
}

export function controlText(value: unknown, maximum = 256): string {
  if (typeof value !== 'string' || !value.length || value.length > maximum || [...value].some(char => char.charCodeAt(0) < 32 || char.charCodeAt(0) === 127)) {throw new RuntimeInputError('Expected bounded text without control characters')}

  return value
}

export function controlId(value: unknown): string {
  const result = controlText(value)

  if (result.trim() !== result || result.includes('*')) {throw new RuntimeInputError('Exact identifiers are required')}

  return result
}

export function controlDigest(value: unknown): string {
  if (typeof value !== 'string' || !/^[a-f0-9]{64}$/u.test(value)) {throw new RuntimeInputError('An exact lowercase SHA-256 is required')}

  return value
}

export function controlInteger(value: unknown, minimum = 0, maximum = 2 ** 31 - 1): number {
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < minimum || value > maximum) {throw new RuntimeInputError('Integer is outside the supported range')}

  return value
}

export function controlList(value: unknown, minimum = 0, maximum = 32): unknown[] {
  if (!Array.isArray(value) || value.length < minimum || value.length > maximum) {throw new RuntimeInputError('List is outside the supported size')}

  return value
}

export function controlChoice<const T extends string>(value: unknown, choices: readonly T[]): T {
  if (typeof value !== 'string' || !choices.includes(value as T)) {throw new RuntimeInputError(`Supported values: ${choices.join(', ')}`)}

  return value as T
}

export function controlJson(text: string, maximum = 2 * 1024 * 1024, ownedInput = true): unknown {
  if (new TextEncoder().encode(text).length > maximum) {throw new RuntimeInputError('JSON exceeds the supported byte limit')}
  let value: unknown

  try { value = JSON.parse(text) } catch { throw new RuntimeInputError('Expected valid JSON') }
  // JSON.parse otherwise silently chooses the last duplicate. Detect ambiguity
  // in the original bytes before a mutation can bind a different interpretation.
  const stack: (Set<string> | null)[] = []

  for (let index = 0; index < text.length; index++) {
    const char = text[index]

    if (char === '{' || char === '[') { stack.push(char === '{' ? new Set() : null) }

    if (char === '}' || char === ']') { stack.pop() }

    if (char !== '"') { continue }
    const start = index++

    while (index < text.length && text[index] !== '"') {
      if (text[index] === '\\') { index++ }
      index++
    }

    let next = index + 1

    while (/\s/u.test(text[next] ?? '') && next < text.length) { next++ }

    if (text[next] === ':') {
      const key = JSON.parse(text.slice(start, index + 1)) as string
      const keys = stack.at(-1)

      if (keys?.has(key)) { throw new RuntimeInputError('Duplicate JSON fields are not allowed') }
      keys?.add(key)
    }
  }

  let nodes = 0
  const reserved = new Set(['session_id', 'schema_version', 'identity', 'principal_id', 'profile_id', 'agent_id', 'policy_digest', 'context', 'db', 'now', '__proto__', 'constructor', 'prototype'])

  const visit = (item: unknown, depth: number): void => {
    if (depth > 20 || ++nodes > 100000) {throw new RuntimeInputError('JSON structure exceeds the supported bound')}

    if (typeof item === 'number' && !Number.isFinite(item)) {throw new RuntimeInputError('JSON numbers must be finite')}

    if (item && typeof item === 'object') {
      for (const [key, child] of Object.entries(item)) {
        if (ownedInput && reserved.has(key)) {throw new RuntimeInputError('Session, schema and identity fields are runtime-owned')}
        visit(child, depth + 1)
      }
    }
  }

  visit(value, 0)

  return value
}

export function controlRef(value: unknown): BriefManifestRef {
  const row = controlRecord(value, ['artifact_id', 'version', 'sha256'], [])

  return { artifact_id: controlId(row.artifact_id), version: controlInteger(row.version, 1), sha256: controlDigest(row.sha256) }
}

function evidenceRange(value: unknown): EvidenceByteRange {
  const row = controlRecord(value, ['start', 'end', 'sha256', 'quote'], ['anchor_id'])
  const start = controlInteger(row.start, 0, 8 * 1024 * 1024)
  const end = controlInteger(row.end, start + 1, 8 * 1024 * 1024)

  if (typeof row.quote !== 'string' || !row.quote || row.quote.includes('\0') || new TextEncoder().encode(row.quote).length > 8192) {throw new RuntimeInputError('Evidence quote must be bounded UTF-8 text')}

  return { start, end, sha256: controlDigest(row.sha256), quote: row.quote,
    ...(row.anchor_id === undefined ? {} : { anchor_id: row.anchor_id === null ? null : controlId(row.anchor_id) }) }
}

export function validateResearchSources(value: unknown): ResearchSourceRequest[] {
  const result = controlList(value, 1, 32).map(item => {
    const row = controlRecord(item, ['source_id', 'source_type', 'project_id', 'version', 'sha256'], ['authority', 'evidence_ranges', 'fresh_until'])

    const source: ResearchSourceRequest = { source_id: controlId(row.source_id), source_type: controlChoice(row.source_type, ['project_artifact', 'capture_original']),
      project_id: controlId(row.project_id), version: controlInteger(row.version, 1), sha256: controlDigest(row.sha256) }

    if (row.authority !== undefined) {source.authority = controlChoice(row.authority, ['authoritative_spec', 'casual_note', 'source_claim', 'user_statement'])}

    if (row.evidence_ranges !== undefined) {
      source.evidence_ranges = controlList(row.evidence_ranges).map(evidenceRange)

      if (source.evidence_ranges.reduce((size, span) => size + new TextEncoder().encode(span.quote).length, 0) > 65536) {throw new RuntimeInputError('Evidence exceeds the source quote byte limit')}
    }

    if (row.fresh_until !== undefined) {
      if (row.fresh_until !== null && (typeof row.fresh_until !== 'number' || !Number.isFinite(row.fresh_until) || row.fresh_until < 0 || row.fresh_until > 253402300799)) {throw new RuntimeInputError('Freshness requires a finite UTC timestamp')}
      source.fresh_until = row.fresh_until as number | null
    }

    return source
  })

  if (new Set(result.map(source => source.source_id)).size !== result.length) {throw new RuntimeInputError('Source identities must be unique')}

  return result
}

export function researchView(response: ResearchResponse): ResearchView {
  const record = controlRecord(controlJson(response.response_json, 3 * 1024 * 1024, false), ['sources', 'coverage', 'complete'])
  const coverage = controlRecord(record.coverage, ['available', 'requested', 'stale'])

  const sources = controlList(record.sources, 1, 32).map(item => {
    const source = controlRecord(item)
    const scope = controlRecord(source.scope)

    return { sourceId: controlId(source.source_id), projectId: controlId(scope.project_id), version: controlInteger(source.version, 1), sha256: controlDigest(source.sha256),
      headVersion: source.head_version == null ? null : controlInteger(source.head_version, 1),
      availability: controlChoice(source.availability, ['available', 'missing', 'inaccessible', 'invalid']),
      freshness: controlChoice(source.freshness, ['within_declared_window', 'unspecified', 'stale', 'unknown']),
      coveredBytes: controlInteger(source.covered_bytes, 0, 8 * 1024 * 1024), size: source.size == null ? null : controlInteger(source.size, 0, 8 * 1024 * 1024),
      errors: controlList(source.errors, 0, 36).map(error => controlId(error)),
      ranges: controlList(source.evidence_ranges).map(span => { const range = evidenceRange(span);

 return { start: range.start, end: range.end, sha256: range.sha256 } }) }
  })

  const available = controlInteger(coverage.available, 0, 32), requested = controlInteger(coverage.requested, 1, 32), stale = controlInteger(coverage.stale, 0, 32)

  if (sources.length !== requested || available !== sources.filter(source => source.availability === 'available').length || stale !== sources.filter(source => source.freshness === 'stale').length || record.complete !== (available === requested)) {throw new RuntimeInputError('Source coverage does not match the returned evidence')}

  return { sources, available, requested, stale, complete: record.complete === true }
}

export function summarizeResearch(response: ResearchResponse): string {
  const view = researchView(response)

  return [`Exact local evidence: ${view.available}/${view.requested} sources available; ${view.stale} stale`,
    ...view.sources.map(source => `${source.sourceId} v${source.version} (${source.projectId}): ${source.availability}, ${source.freshness}${source.headVersion === null ? '' : `; head v${source.headVersion}`}; cited ${source.coveredBytes}/${source.size ?? '?'} bytes; SHA-256 ${source.sha256}${source.errors.length ? `; ${source.errors.join(', ')}` : ''}`),
    ...view.sources.flatMap(source => source.ranges.map(range => `Citation ${source.sourceId} v${source.version}: bytes [${range.start}, ${range.end}), SHA-256 ${range.sha256}`)),
    'Claims are not verified. Source authority is caller-declared. External connected search and monitoring were not performed.'].join('\n')
}

function validateUpdates(value: unknown): void {
  const ids = controlList(value, 1, 100).map(item => {
    const row = controlRecord(item, ['claim_id', 'replacement'], [])
    controlText(row.replacement, 4096)

    return controlId(row.claim_id)
  })

  if (new Set(ids).size !== ids.length) {throw new RuntimeInputError('Claim updates must be unique')}
}

function briefInput(value: unknown, publish: boolean): OwnedBrief | OwnedPublish {
  const approvalFields = ['brief_approval_id', 'brief_approval_digest', 'manifest_approval_id', 'manifest_approval_digest']
  const row = controlRecord(value, ['project_id', 'command_id', 'request_id', 'artifact_id', 'parent_version', 'request_json', 'manifest_ref', ...(publish ? approvalFields : [])], [])

  if (typeof row.request_json !== 'string') {throw new RuntimeInputError('request_json must preserve the exact request JSON string')}
  const body = controlRecord(controlJson(row.request_json), ['requests', 'updates'], [])
  validateResearchSources(body.requests)
  validateUpdates(body.updates)

  const params: OwnedBrief = { project_id: controlId(row.project_id), command_id: controlId(row.command_id), request_id: controlId(row.request_id),
    artifact_id: controlId(row.artifact_id), parent_version: controlInteger(row.parent_version, 1), request_json: row.request_json, manifest_ref: controlRef(row.manifest_ref) }

  return publish ? { ...params, ...briefApprovals(row) } : params
}

function briefApprovals(row: Record<string, unknown>): Pick<BriefPublishParams, 'brief_approval_id' | 'brief_approval_digest' | 'manifest_approval_id' | 'manifest_approval_digest'> {
  return { brief_approval_id: controlId(row.brief_approval_id), brief_approval_digest: controlDigest(row.brief_approval_digest),
    manifest_approval_id: controlId(row.manifest_approval_id), manifest_approval_digest: controlDigest(row.manifest_approval_digest) }
}

export async function resolveResearch(sources: ResearchSourceRequest[], request: RuntimeRequest, sessionId: string): Promise<ResearchResponse> {
  validateResearchSources(sources)

  return request('runtime.research.resolve', { session_id: controlId(sessionId), schema_version: 1, request_json: JSON.stringify(sources) })
}

export async function prepareBrief(input: OwnedBrief, request: RuntimeRequest, sessionId: string): Promise<ResearchResponse> {
  const params = briefInput(input, false)
  const response = await request('runtime.brief.prepare', { ...params, session_id: controlId(sessionId), schema_version: 1 })
  assertBriefBinding(response, params, true)

  return response
}

export async function publishBrief(input: OwnedPublish, request: RuntimeRequest, sessionId: string): Promise<ResearchResponse> {
  const params = briefInput(input, true) as OwnedPublish
  const response = await request('runtime.brief.publish', { ...params, session_id: controlId(sessionId), schema_version: 1 })
  assertBriefBinding(response, params, false)

  return response
}

function assertBriefBinding(response: ResearchResponse, input: OwnedBrief, prepared: boolean): void {
  const row = controlRecord(controlJson(response.response_json, 3 * 1024 * 1024, false))
  const brief = prepared ? artifactProposal(row.brief) : artifactPublication(row.brief)

  if (brief.project_id !== input.project_id || brief.artifact_id !== input.artifact_id || brief.parent_version !== input.parent_version) { throw new RuntimeInputError('Brief response differs from the selected project, artifact or parent version') }

  if (prepared && artifactProposal(row.brief).request_id !== input.request_id) { throw new RuntimeInputError('Brief proposal belongs to another request') }

  if (row.manifest !== null) {
    const manifest = prepared ? artifactProposal(row.manifest) : artifactPublication(row.manifest)

    if (manifest.project_id !== input.project_id || manifest.mime !== 'application/json' || (input.manifest_ref && (manifest.artifact_id !== input.manifest_ref.artifact_id || manifest.parent_version !== input.manifest_ref.version))) { throw new RuntimeInputError('Dependency manifest differs from the selected scope or version') }
  }
}

/** Initial baselines obtain identity-scoped manifests from an owned read, never JSON supplied by the caller.
 * Volatile read timestamps are part of the exact request binding, so only the live transport retains them. */
interface InitialBriefBinding { params: OwnedBrief; input: string; response?: ResearchResponse; busy: boolean; published: boolean }
const initialBriefs = new WeakMap<RuntimeRequest, Map<string, Map<string, InitialBriefBinding>>>()

function initialSession(request: RuntimeRequest, sessionId: string): Map<string, InitialBriefBinding> {
  let sessions = initialBriefs.get(request)

  if (!sessions) { sessions = new Map(); initialBriefs.set(request, sessions) }
  let commands = sessions.get(sessionId)

  if (!commands) {
    if (sessions.size >= 16) {throw new RuntimeInputError('Initial baseline session limit reached; reconnect before a new session')}
    commands = new Map(); sessions.set(sessionId, commands)
  }

  return commands
}

function validateClaims(value: unknown, sources: ResearchSourceRequest[]): void {
  const ids = controlList(value, 1, 100).map(item => {
    const claim = controlRecord(item, ['claim_id', 'section', 'text', 'kind', 'section_sha256', 'citations'], [])
    controlId(claim.section); controlText(claim.text, 4096); controlChoice(claim.kind, ['fact', 'interpretation']); controlDigest(claim.section_sha256)
    controlList(claim.citations, 1, 32).forEach(item => {
      const citation = controlRecord(item, ['source_id', 'range_index'], ['source_version', 'source_sha256', 'request_digest'])
      const source = sources.find(source => source.source_id === controlId(citation.source_id))

      if (!source) {throw new RuntimeInputError('Claim source must be selected in previous_requests')}
      controlInteger(citation.range_index, 0, (source.evidence_ranges?.length ?? 0) - 1)

      if (citation.source_version != null || citation.source_sha256 != null) {
        if (controlInteger(citation.source_version, 1) !== source.version || controlDigest(citation.source_sha256) !== source.sha256) {throw new RuntimeInputError('Claim citation version differs from the previous source')}
      }

      if (citation.request_digest != null) { controlDigest(citation.request_digest); controlInteger(citation.source_version, 1) }
    })

    return controlId(claim.claim_id)
  })

  if (new Set(ids).size !== ids.length) {throw new RuntimeInputError('Claim identities must be unique')}
}

export async function prepareInitialBrief(input: string, request: RuntimeRequest, sessionId: string): Promise<ResearchResponse> {
  const row = controlRecord(controlJson(input), ['project_id', 'command_id', 'request_id', 'artifact_id', 'parent_version', 'previous_requests', 'claims', 'requests', 'updates'], [])
  const session_id = controlId(sessionId), command_id = controlId(row.command_id)
  const previous = validateResearchSources(row.previous_requests)
  validateResearchSources(row.requests); validateUpdates(row.updates); validateClaims(row.claims, previous)
  const commands = initialSession(request, session_id)
  let binding = commands.get(command_id)

  if (binding?.input !== undefined && binding.input !== input) {throw new RuntimeInputError('Command is already bound to different input; inspect the original command')}

  if (binding?.busy || binding?.published) {throw new RuntimeInputError('Command is pending or already published; inspect it before another action')}

  if (!binding) {
    if (commands.size >= 32) {throw new RuntimeInputError('Initial baseline limit reached for this session; use durable-manifest refresh for existing briefs')}
    // Reserve before awaiting a read so duplicate clicks cannot create two timestamp-bound proposals.
    binding = { input, busy: true, published: false, params: { project_id: controlId(row.project_id), command_id,
      request_id: controlId(row.request_id), artifact_id: controlId(row.artifact_id), parent_version: controlInteger(row.parent_version, 1), request_json: '' } }
    commands.set(command_id, binding)

    try {
      const previousResponse = await resolveResearch(previous, request, session_id)

      if (!researchView(previousResponse).complete) {throw new RuntimeInputError('Previous sources are missing or inaccessible; retain the original brief')}
      const observed = controlRecord(controlJson(previousResponse.response_json, 3 * 1024 * 1024, false))
      binding.params.request_json = JSON.stringify({ previous_sources: observed.sources, claims: row.claims, requests: row.requests, updates: row.updates })

      if (new TextEncoder().encode(binding.params.request_json).length > 2 * 1024 * 1024) {throw new RuntimeInputError('Resolved brief baseline exceeds the request byte bound')}
    } catch (error) { commands.delete(command_id); throw error }
  }

  binding.busy = true

  try {
    const response = await request('runtime.brief.prepare', { ...binding.params, session_id, schema_version: 1 })
    assertBriefBinding(response, binding.params, true)
    summarizeBrief(response)
    binding.response = response

    return response
  } finally { binding.busy = false }
}

export async function publishInitialBrief(input: string, request: RuntimeRequest, sessionId: string): Promise<ResearchResponse> {
  const row = controlRecord(controlJson(input), ['command_id', 'brief_approval_id', 'brief_approval_digest', 'manifest_approval_id', 'manifest_approval_digest'], [])
  const session_id = controlId(sessionId), binding = initialBriefs.get(request)?.get(session_id)?.get(controlId(row.command_id))

  if (!binding?.response || binding.busy || binding.published) {throw new RuntimeInputError('No prepared initial baseline is available on this session and connection; inspect the original command')}
  const prepared = controlRecord(controlJson(binding.response.response_json, 3 * 1024 * 1024, false))
  const brief = artifactProposal(prepared.brief), manifest = artifactProposal(prepared.manifest), approvals = briefApprovals(row)

  if (approvals.brief_approval_id !== brief.approval_id || approvals.brief_approval_digest !== brief.approval_digest || approvals.manifest_approval_id !== manifest.approval_id || approvals.manifest_approval_digest !== manifest.approval_digest) {throw new RuntimeInputError('Approval fields differ from the exact prepared initial baseline')}
  binding.busy = true

  try {
    const response = await request('runtime.brief.publish', { ...binding.params, ...approvals, session_id, schema_version: 1 })
    assertBriefBinding(response, binding.params, false)
    summarizeBrief(response)
    binding.published = controlRecord(controlJson(response.response_json, 3 * 1024 * 1024, false)).state === 'published'

    return response
  } finally { binding.busy = false }
}

export function artifactProposal(value: unknown): ArtifactProposalResult {
  const row = controlRecord(value)

  return { request_id: controlId(row.request_id), project_id: controlId(row.project_id), artifact_id: controlId(row.artifact_id),
    version: controlInteger(row.version, 1), sha256: controlDigest(row.sha256), size: controlInteger(row.size), mime: controlText(row.mime),
    parent_version: row.parent_version === null ? null : controlInteger(row.parent_version, 1),
    expected_head_version: row.expected_head_version === null ? null : controlInteger(row.expected_head_version, 1),
    action_digest: controlDigest(row.action_digest), approval_id: controlId(row.approval_id), approval_digest: controlDigest(row.approval_digest),
    expires_at: typeof row.expires_at === 'number' && Number.isFinite(row.expires_at) ? row.expires_at : (() => { throw new RuntimeInputError('Proposal expiry is unavailable') })() }
}

export function artifactPublication(value: unknown): ArtifactPublishResult {
  const row = controlRecord(value)

  return { project_id: controlId(row.project_id), artifact_id: controlId(row.artifact_id), version: controlInteger(row.version, 1), sha256: controlDigest(row.sha256),
    size: controlInteger(row.size), mime: controlText(row.mime), parent_version: row.parent_version === null ? null : controlInteger(row.parent_version, 1),
    disposition: controlChoice(row.disposition, ['canonical', 'branch']), head_version: row.head_version === null ? null : controlInteger(row.head_version, 1),
    validation_status: controlChoice(row.validation_status, ['passed']), approval_status: controlChoice(row.approval_status, ['approved']) }
}

export function artifactReference(row: Pick<ArtifactPublishResult, 'artifact_id' | 'version' | 'sha256' | 'mime'>): string {
  return `${row.artifact_id} v${row.version} (${row.mime}), SHA-256 ${row.sha256}`
}

export function summarizeBrief(response: ResearchResponse): string {
  const row = controlRecord(controlJson(response.response_json, 3 * 1024 * 1024, false))

  if (row.publication_atomic !== false) {throw new RuntimeInputError('Unexpected brief publication contract')}

  if (row.state === 'awaiting_approval') {
    const brief = artifactProposal(row.brief), manifest = artifactProposal(row.manifest)

    const approvals = { brief_approval_id: brief.approval_id, brief_approval_digest: brief.approval_digest,
      manifest_approval_id: manifest.approval_id, manifest_approval_digest: manifest.approval_digest }

    return [`Prepared brief; awaiting explicit approval, nothing published`, `Brief: ${artifactReference(brief)}; parent v${brief.parent_version}`,
      `Dependency manifest: ${artifactReference(manifest)}`,
      `Factual changes: ${controlList(row.factual_changes, 0, 100).map(controlId).join(', ') || 'none'}`,
      `Interpretation changes: ${controlList(row.interpretation_changes, 0, 100).map(controlId).join(', ') || 'none'}`,
      `Exact approval fields: ${JSON.stringify(approvals)}`,
      'Publish with the identical command, request, parent version and dependency reference. Bundle publication is not atomic.',
      summarizeResearch({ response_json: JSON.stringify(row.source_manifest) })].join('\n')
  }

  const brief = artifactPublication(row.brief)

  if (row.state === 'partial' && row.manifest === null) {return `Partial publication: brief committed (${artifactReference(brief)}); dependency manifest commit is unconfirmed. Inspect the original command and its effects before any explicit exact retry. Delivery is not confirmed.`}

  if (row.state !== 'published') {throw new RuntimeInputError('Unexpected brief state; inspect the command')}
  const manifest = artifactPublication(row.manifest)

  return `Published brief: ${artifactReference(brief)}\nPublished dependency manifest: ${artifactReference(manifest)}\nPublication was not atomic. Delivery and independent claim verification are not confirmed.`
}

const errorMessages: Record<string, string> = {
  brief_section_changed: 'The target section changed; review the new version before preparing a new edit',
  brief_source_unavailable: 'Current source evidence is unavailable or stale; the previous brief must be retained',
  brief_prior_source_unavailable: 'A retained dependency cannot be reopened; the previous brief must be retained',
  source_version_mismatch: 'The source version or digest conflicts with the requested evidence',
  approval_mismatch: 'Approval IDs or digests do not match the exact prepared outputs',
  idempotency_conflict: 'This command ID is bound to different request bytes; inspect the original command',
  project_grant_revoked: 'Project access was revoked; source and publication access must be reviewed',
  artifact_conflict: 'The artifact version changed; inspect the current head before a new proposal',
  artifact_control_finished: 'This command has already finished; inspect its committed result',
  budget_unavailable: 'Preparation is blocked by the runtime budget',
  method_not_found: 'This runtime does not support the requested operation'
}

export function controlFailure(error: unknown, mutation: boolean): string {
  if (error instanceof RuntimeInputError) {return `Invalid input or response: ${error.message}${mutation ? '. Operation outcome is not confirmed; inspect the original command' : ''}`}
  const record = error && typeof error === 'object' ? error as Record<string, unknown> : {}
  const data = record.data && typeof record.data === 'object' ? record.data as Record<string, unknown> : {}
  const code = typeof data.code === 'string' ? data.code : typeof record.code === 'string' ? record.code : record.code === -32601 ? 'method_not_found' : ''

  return `${errorMessages[code] ?? 'The runtime request failed or is unsupported; inspect the connection and command'}${mutation ? '. Operation outcome is not confirmed; no automatic retry was made' : ''}`
}

export function summarizeArtifactCommand(status: ArtifactControlStatus): string {
  const labels: Record<ArtifactControlStatus['status'], string> = { accepted: 'accepted; preparation pending', claimed: 'active; publication is not confirmed',
    completed: 'completed; inspect committed artifacts', cancelled: 'cancelled; already committed effects are not undone', failed: 'failed; partial effects may remain', blocked: 'blocked' }

  return `Command ${controlId(status.command_id)}: ${labels[status.status]}; owner ${status.owner_live ? 'live' : 'not live'}. Delivery is not confirmed.`
}

export const RESEARCH_HELP = [
  '/runtime research list', '/runtime research inspect <command-id>', '/runtime research resolve <JSON source array>',
  '/runtime research prepare <JSON: project_id, command_id, request_id, artifact_id, parent_version, manifest_ref, request_json>',
  '/runtime research publish <same JSON plus brief_approval_id, brief_approval_digest, manifest_approval_id, manifest_approval_digest>',
  '/runtime research prepare-initial <JSON: project_id, command_id, request_id, artifact_id, parent_version, previous_requests, claims, requests, updates>',
  '/runtime research publish-initial <JSON: command_id plus the four exact approval fields>',
  'Initial baseline derives prior source manifests from owned reads; publish-initial must use the same live session/connection. It refreshes an existing brief artifact.',
  'request_json contains exact requests and claim updates. manifest_ref pins the prior dependency artifact by artifact_id/version/sha256.',
  'Only retained project_artifact and capture_original sources are available. No external search, scheduler or automatic publication.',
  'Session, schema and identity come from the current owned runtime. Preserve request bytes and all version/approval fields.'
].join('\n')

export async function runResearchCommand(argument: string, request: RuntimeRequest, sessionId: string): Promise<string> {
  const match = /^(\S+)(?:\s+([\s\S]*))?$/u.exec(argument.trim())
  const action = match?.[1] ?? '--help', rest = match?.[2] ?? ''
  const mutation = ['prepare', 'publish', 'prepare-initial', 'publish-initial'].includes(action)

  try {
    const session_id = controlId(sessionId)

    const actions: Record<string, () => Promise<string>> = {
      '--help': async () => RESEARCH_HELP, help: async () => RESEARCH_HELP,
      list: async () => 'Supported local source types: project_artifact, capture_original. Exact immutable versions, SHA-256 and byte-range citations are required. External connected search is unavailable.',
      inspect: async () => {
        const status = await request('runtime.artifact.status', { session_id, schema_version: 1, command_id: controlId(rest) })

        return status.result && 'response_json' in status.result ? `${summarizeArtifactCommand(status)}\n${summarizeBrief(status.result)}` : summarizeArtifactCommand(status)
      },
      resolve: async () => {
        validateResearchSources(controlJson(rest))

        return summarizeResearch(await request('runtime.research.resolve', { session_id, schema_version: 1, request_json: rest }))
      },
      prepare: async () => summarizeBrief(await prepareBrief(briefInput(controlJson(rest), false), request, session_id)),
      publish: async () => summarizeBrief(await publishBrief(briefInput(controlJson(rest), true) as OwnedPublish, request, session_id)),
      'prepare-initial': async () => summarizeBrief(await prepareInitialBrief(rest, request, session_id)),
      'publish-initial': async () => summarizeBrief(await publishInitialBrief(rest, request, session_id))
    }

    return await ((Object.hasOwn(actions, action) ? actions[action] : undefined) ?? (async () => `Unsupported research action.\n${RESEARCH_HELP}`))()
  } catch (error) { return controlFailure(error, mutation) }
}
