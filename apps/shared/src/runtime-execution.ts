import type { MediaResponse, RuntimeEffectRecord, ServicePrepareParams } from './gateway-contract.generated.js'
import type { RuntimeRequest } from './runtime-control.js'

export const EXECUTION_HELP = 'Execution: capabilities | prepare <JSON: project_id,request_id,artifact_id,version> | execute <pipeline-id> <manifest-sha256> | status/output <pipeline-id> | receipts | effects | effect <effect-id>\nOnly declared local document services execute here. Coding/browser evidence is read-only; prepared forms, focused checks and unknown effects are not completion.'

type RecordValue = Record<string, unknown>
interface Executor { executor_id: string, generation: number, capability_digest: string, location: string }
interface Service {
  service_id: string, version: number, capability: string, health?: string,
  processing_location: string, destination: string, authentication: string,
  data_policy: string, max_input_bytes: number, max_output_bytes: number
}
export interface RuntimeExecutionManifest {
  pipeline_id: string, project_id: string, executor: Executor, stages: Service[],
  source: { artifact_id: string, version: number, sha256: string, size: number, mime: string },
  transfer: { input_bytes: number, max_intermediate_bytes: number, max_output_bytes: number, from: string, to: string, remote_bytes: number },
  publication: string
}
interface StageReceipt {
  pipeline_id: string, stage: number, service_id: string, service_version: number,
  input_sha256: string, input_size: number, output_sha256: string, output_size: number,
  output_mime: string, destination: string, acknowledgment_level: string,
  transfer_id: string, executor: Executor
}
interface Preparation { manifest: RuntimeExecutionManifest, digest: string, attempted: boolean }
const preparations = new WeakMap<RuntimeRequest, Map<string, Preparation>>()

function record(value: unknown, label = 'response'): RecordValue {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {throw new Error(`Invalid ${label} object`)}

  return value as RecordValue
}

function text(value: unknown, label: string, max = 256): string {
  if (typeof value !== 'string' || !value.trim() || value.length > max || Array.from(value).some(char => char.charCodeAt(0) < 32 || char.charCodeAt(0) === 127)) {throw new Error(`Invalid ${label}`)}

  return value
}

function number(value: unknown, label: string, min = 0): number {
  if (!Number.isSafeInteger(value) || (value as number) < min) {throw new Error(`Invalid ${label}`)}

  return value as number
}

function digest(value: unknown, label = 'digest'): string {
  if (typeof value !== 'string' || !/^[a-f0-9]{64}$/.test(value)) {throw new Error(`Invalid ${label}; exact SHA-256 required`)}

  return value
}

function rows(value: unknown, max = 16): unknown[] {
  if (!Array.isArray(value) || value.length > max) {throw new Error('Invalid bounded response rows')}

  return value
}

function response(value: MediaResponse): RecordValue {
  if (typeof value.response_json !== 'string' || value.response_json.length > 2 * 1024 * 1024) {throw new Error('Invalid bounded service response')}

  return record(JSON.parse(value.response_json))
}

function executor(value: unknown): Executor {
  const item = record(value, 'executor')

  return { executor_id: text(item.executor_id, 'executor ID'), generation: number(item.generation, 'executor generation'), capability_digest: digest(item.capability_digest), location: text(item.location, 'executor location') }
}

function service(value: unknown): Service {
  const item = record(value, 'service')

  return {
    service_id: text(item.service_id, 'service ID'), version: number(item.version, 'service version', 1), capability: text(item.capability, 'capability'),
    ...(item.health === undefined ? {} : { health: text(item.health, 'service health') }),
    processing_location: text(item.processing_location, 'processing location'), destination: text(item.destination, 'destination'),
    authentication: text(item.authentication, 'authentication'), data_policy: text(item.data_policy, 'data policy'),
    max_input_bytes: number(item.max_input_bytes, 'input bound'), max_output_bytes: number(item.max_output_bytes, 'output bound')
  }
}

function manifest(value: unknown): RuntimeExecutionManifest {
  const item = record(value, 'manifest')

  if (item.schema_version !== 1) {throw new Error('Unsupported service manifest version')}
  const source = record(item.source, 'source'), transfer = record(item.transfer, 'transfer')

  const result: RuntimeExecutionManifest = {
    pipeline_id: text(item.pipeline_id, 'pipeline ID'), project_id: text(item.project_id, 'project ID'), executor: executor(item.executor),
    stages: rows(item.stages).map(service),
    source: { artifact_id: text(source.artifact_id, 'artifact ID'), version: number(source.version, 'artifact version', 1), sha256: digest(source.sha256), size: number(source.size, 'source size'), mime: text(source.mime, 'source MIME') },
    transfer: { input_bytes: number(transfer.input_bytes, 'transfer bytes'), max_intermediate_bytes: number(transfer.max_intermediate_bytes, 'intermediate bound'), max_output_bytes: number(transfer.max_output_bytes, 'output bound'), from: text(transfer.from, 'transfer origin'), to: text(transfer.to, 'transfer destination'), remote_bytes: number(transfer.remote_bytes, 'remote bytes') },
    publication: text(item.publication, 'publication policy')
  }

  if (result.source.size !== result.transfer.input_bytes || !result.stages.length) {throw new Error('Manifest source/transfer mismatch')}

  return result
}

function receipt(value: unknown, pipeline: string): StageReceipt {
  const item = record(value, 'stage receipt')

  const result = {
    pipeline_id: text(item.pipeline_id, 'pipeline ID'), stage: number(item.stage, 'stage'), service_id: text(item.service_id, 'service ID'), service_version: number(item.service_version, 'service version', 1),
    input_sha256: digest(item.input_sha256), input_size: number(item.input_size, 'input size'), output_sha256: digest(item.output_sha256), output_size: number(item.output_size, 'output size'),
    output_mime: text(item.output_mime, 'output MIME'), destination: text(item.destination, 'destination'), acknowledgment_level: text(item.acknowledgment_level, 'acknowledgment'), transfer_id: digest(item.transfer_id, 'transfer ID'), executor: executor(item.executor)
  }

  if (result.pipeline_id !== pipeline) {throw new Error('Receipt belongs to a different pipeline')}

  return result
}

function placement(item: Executor): string { return `${item.executor_id}; generation ${item.generation}; location ${item.location}; capability digest ${item.capability_digest}` }

function receiptLines(item: StageReceipt): string[] {
  return [`Stage ${item.stage}: ${item.service_id}@${item.service_version}; acknowledgment ${item.acknowledgment_level}`,
    `Input ${item.input_size} bytes / ${item.input_sha256}; output ${item.output_size} bytes / ${item.output_sha256}`,
    `Transfer ${item.transfer_id}; storage ${item.destination}; ${item.output_mime}`, `Executor ${placement(item.executor)}`]
}

function statusLines(item: RecordValue, pipeline: string): string {
  if (item.pipeline_id !== pipeline || !['pending', 'partial', 'completed'].includes(String(item.state))) {throw new Error('Invalid pipeline status')}
  const receipts = rows(item.receipts).map(row => receipt(row, pipeline))
  receipts.forEach((row, index) => {
    if (row.stage !== index || (index > 0 && (row.input_sha256 !== receipts[index - 1].output_sha256 || row.input_size !== receipts[index - 1].output_size))) {throw new Error('Invalid service receipt chain')}
  })
  const blocked = item.blocked_reason === undefined ? '' : text(item.blocked_reason, 'blocked reason')
  const next = item.next_stage === null ? 'none' : number(item.next_stage, 'next stage')
  const coherent = item.state === 'pending' ? receipts.length === 0 && next === 0 : item.state === 'partial' ? receipts.length > 0 && next === receipts.length : receipts.length > 0 && next === 'none'

  if (!coherent) { throw new Error('Pipeline state lacks matching stage receipts') }

  return [`Pipeline ${pipeline}: ${item.state}${blocked ? `; blocked: ${blocked}` : ''}`, `Manifest ${digest(item.manifest_sha256)}; next stage ${next}`,
    ...receipts.flatMap(receiptLines), `Publication: ${text(item.publication, 'publication policy')}`,
    blocked ? 'Inspect status, restore the declared endpoint/grant, then explicitly prepare and review the route again. No service substitution or automatic retry.' : 'Service completion covers these pure local stages only; publication, delivery, coding tests and browser effects need separate evidence.'].join('\n')
}

function cache(request: RuntimeRequest): Map<string, Preparation> {
  let value = preparations.get(request)

  if (!value) { value = new Map(); preparations.set(request, value) }

  return value
}

function key(session: string, pipeline: string) { return `${session.length}:${session}:${pipeline}` }

function words(value: string, count: number): string[] {
  const parts = value.trim().split(/\s+/)

  if (parts.length !== count || !parts[0]) {throw new Error(`Expected ${count} exact argument${count === 1 ? '' : 's'}`)}

  return parts.map(part => text(part, 'argument'))
}

function errorCode(error: unknown): string {
  const data = error && typeof error === 'object' ? (error as { data?: unknown }).data : undefined
  const code = data && typeof data === 'object' ? (data as { code?: unknown }).code : undefined

  if (typeof code === 'string' && /^[a-z][a-z0-9_]{0,100}$/.test(code)) {return code}
  // Known gateway errors may arrive as Error(message); never print arbitrary payloads.
  const message = error instanceof Error ? error.message : ''

  return /^(service_|executor_|project_|identity_|stale_owner|screen_)[a-z_]*$/.test(message) ? message : 'request_unavailable'
}

async function read<T>(operation: () => Promise<T>): Promise<T> {
  try { return await operation() }
  catch (error) { throw new Error(`Execution inspection unavailable (${errorCode(error)}). Refresh the owned connection or restore its grant; no alternate executor selected.`) }
}

function effectLines(effect: RuntimeEffectRecord): string[] {
  return [`Effect ${effect.effect_id}: ${effect.state}; type ${effect.operation_type}`,
    `Operation ${effect.operation_id}; run ${effect.run_id}; generation ${effect.generation}; policy ${effect.policy_version}`,
    `Action digest ${effect.action_digest}; replay permitted: ${effect.replay_permitted}`,
    effect.state === 'confirmed' ? 'Confirmed only for the operation named in this receipt.' : 'No confirmed final outcome. Prepared/dispatched/unknown effects are not a booking, send, deployment or completed change.']
}

export interface RuntimeExecutionPreparation { manifest: RuntimeExecutionManifest, digest: string, summary: string }

/** Exact typed preparation shared by forms and the expert command surface. */
export async function prepareRuntimeExecution(request: RuntimeRequest, sessionId: string, data: Pick<ServicePrepareParams, 'project_id' | 'request_id' | 'artifact_id' | 'version'>): Promise<RuntimeExecutionPreparation> {
  const base = { session_id: sessionId, schema_version: 1 as const }
  const params: ServicePrepareParams = { ...base, project_id: text(data.project_id, 'project_id'), request_id: text(data.request_id, 'request_id'), artifact_id: text(data.artifact_id, 'artifact_id'), version: number(data.version, 'version', 1) }
  const result = response(await read(() => request('runtime.services.prepare', params)))
  const value = manifest(result.manifest), checksum = digest(result.manifest_sha256)

  if (value.project_id !== params.project_id || value.source.artifact_id !== params.artifact_id || value.source.version !== params.version) {throw new Error('Prepared source does not match the exact requested version')}
  cache(request).set(key(sessionId, value.pipeline_id), { manifest: value, digest: checksum, attempted: false })

  const summary = [`Prepared pipeline ${value.pipeline_id}; no stages executed`, `Project ${value.project_id}; source ${value.source.artifact_id}@${value.source.version}; ${value.source.mime}`,
    `Source ${value.source.size} bytes; digest ${value.source.sha256}`, `Executor ${placement(value.executor)}`,
    ...value.stages.map(item => `Route ${item.service_id}@${item.version}: ${item.processing_location} → ${item.destination}; ${item.data_policy}`),
    `Transfer ${value.transfer.from} → ${value.transfer.to}; ${value.transfer.input_bytes} input bytes; intermediate/output bounds ${value.transfer.max_intermediate_bytes}/${value.transfer.max_output_bytes}; remote ${value.transfer.remote_bytes} bytes`,
    `Publication: ${value.publication}`, `Manifest ${checksum}`, `After reviewing this exact route: execute ${value.pipeline_id} ${checksum}`].join('\n')

  return { manifest: value, digest: checksum, summary }
}

export async function runExecutionCommand(argument: string, request: RuntimeRequest, sessionId: string): Promise<string> {
  const trimmed = argument.trim(), split = trimmed.search(/\s/)
  const action = split < 0 ? trimmed : trimmed.slice(0, split), rest = split < 0 ? '' : trimmed.slice(split + 1).trim()
  const base = { session_id: sessionId, schema_version: 1 as const }

  const handlers: Record<string, () => Promise<string>> = {
    capabilities: async () => {
      if (rest) {throw new Error('capabilities takes no arguments')}
      const result = response(await read(() => request('runtime.services.capabilities', base)))
      const services = rows(result.services).map(service)

      return ['Declared services (fresh backend inspection):', ...services.flatMap(item => [
        `${item.service_id}@${item.version}: ${item.health ?? 'unknown'}; ${item.capability}`,
        `Processing ${item.processing_location}; storage ${item.destination}; input/output bounds ${item.max_input_bytes}/${item.max_output_bytes} bytes`,
        `Authentication ${item.authentication}; policy ${item.data_policy}`]),
      'Device selection and remote execution are unavailable in this contract. Prepare discloses the exact authenticated local executor before any transfer.'].join('\n')
    },
    prepare: async () => {
      if (rest.length > 8192) {throw new Error('Preparation input is too large')}
      const data = record(JSON.parse(rest), 'preparation')

      if (Object.keys(data).some(field => !['project_id', 'request_id', 'artifact_id', 'version'].includes(field))) {throw new Error('Unsupported preparation field; session, schema and identity are owned by this view')}

      return (await prepareRuntimeExecution(request, sessionId, {
        project_id: text(data.project_id, 'project_id'), request_id: text(data.request_id, 'request_id'), artifact_id: text(data.artifact_id, 'artifact_id'), version: number(data.version, 'version', 1)
      })).summary
    },
    execute: async () => {
      const [pipeline, checksum] = words(rest, 2)
      digest(checksum)
      const item = cache(request).get(key(sessionId, pipeline))

      if (!item || item.digest !== checksum) {throw new Error('Prepare and review this exact manifest in the current session before execution')}

      if (item.attempted) {throw new Error('Execution was already attempted. Inspect status first; explicitly prepare and review again for bounded recovery. Never retry an entire mission.')}
      item.attempted = true

      try {
        return statusLines(response(await request('runtime.services.execute', { ...base, pipeline_id: pipeline, manifest_sha256: checksum })), pipeline)
      } catch (error) {
        throw new Error(`Execution outcome unconfirmed (${errorCode(error)}). Inspect status ${pipeline}; do not infer failure or replay the mission. Restore the same endpoint/grant before explicit preparation.`)
      }
    },
    status: async () => {
      const [pipeline] = words(rest, 1)

      return statusLines(response(await read(() => request('runtime.services.status', { ...base, pipeline_id: pipeline }))), pipeline)
    },
    output: async () => {
      const [pipeline] = words(rest, 1)
      const result = response(await read(() => request('runtime.services.output', { ...base, pipeline_id: pipeline })))

      if (result.pipeline_id !== pipeline || typeof result.content_base64 !== 'string' || result.content_base64.length > 87384) {throw new Error('Invalid bounded service output')}
      const row = receipt(result.receipt, pipeline)
      const bytes = Uint8Array.from(atob(result.content_base64), char => char.charCodeAt(0))
      const checksum = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))).map(value => value.toString(16).padStart(2, '0')).join('')

      if (bytes.length !== row.output_size || checksum !== row.output_sha256) {throw new Error('Service output digest/size mismatch; content discarded')}

      return [`Pipeline ${pipeline}: private staged output bytes verified`, ...receiptLines(row), 'Contents withheld from the status transcript. Publication and delivery have not been performed; use separate artifact approval controls.'].join('\n')
    },
    receipts: async () => {
      if (rest) {throw new Error('receipts takes no arguments')}
      const result = await read(() => request('runtime.mission.receipts.list', { ...base, limit: 30 }))

      return ['Mission validation receipts (bounded snapshot, not full history):', ...result.receipts.flatMap(item => [
        `${item.receipt_id}: ${item.result}; verifier ${item.verifier}; criterion ${item.criterion_id}; mission revision ${item.mission_revision ?? 'unreported'}`,
        `Evidence ${item.evidence_ref}; observed ${new Date(item.observed_at * 1000).toISOString()}`,
        `Artifacts ${item.artifact_refs.map(ref => `${ref.artifact_id}@${ref.version}`).join(', ') || 'none'}; checks ${item.details.checks?.join(', ') || 'not reported'}; reasons ${item.details.reason_codes?.join(', ') || 'none reported'}`]),
      result.receipts.length ? 'Only the named verifier/check scope is evidenced. A focused pass is not a full-suite pass; commit/push/merge/deploy and browser submits are separate effects.' : 'No validation evidence returned; no tests, changes or deployments claimed.',
      `Limit ${result.limit}; limit reached ${result.limit_reached}; complete history ${result.complete}`].join('\n')
    },
    effects: async () => {
      if (rest) {throw new Error('effects takes no arguments')}
      const result = await read(() => request('runtime.effects.list', { ...base, limit: 30 }))

      return ['Effect evidence (bounded snapshot):', ...result.effects.flatMap(effectLines), `Limit ${result.limit}; truncated ${result.truncated}; complete history ${result.complete}`, 'Use effect <effect-id> for evidence sequence/digests. No automatic reconciliation or mutation replay.'].join('\n')
    },
    effect: async () => {
      const [effectId] = words(rest, 1)
      const result = await read(() => request('runtime.effect.get', { ...base, effect_id: effectId }))

      if (result.effect.effect_id !== effectId) {throw new Error('Effect response does not match requested operation')}

      return [...effectLines(result.effect), ...result.evidence.map(item => `Evidence ${item.sequence}; generation ${item.generation}; ${item.from_state} → ${item.state}; receipt ${item.receipt_available ? item.receipt_sha256 ?? 'digest unavailable' : 'unavailable'}`)].join('\n')
    }
  }

  return Object.hasOwn(handlers, action) ? handlers[action]() : EXECUTION_HELP
}
