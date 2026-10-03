import type { ArtifactProposalResult, ArtifactPublishResult, ScheduleOutputReadResult, ScheduleRecordResult, WorkflowRecord } from './gateway-contract.generated.js'
import type { RuntimeRequest } from './runtime-control.js'
import { controlDigest, controlInteger, controlJson, controlList, controlRecord } from './runtime-research.js'

export interface DraftInput { name: string; type: 'string' | 'number' | 'integer' | 'boolean'; required: boolean; schema: Record<string, unknown> }
export interface DraftDefinitionOptions {
  projectId: string; scheduleId: string; timezone: string; anchor: string; expiry: string
  interval: number; maxChecks: number; maxBytes: number; deadlineSeconds: number
  workflow: WorkflowRecord; values: Record<string, string>; bindings: Record<string, string>
}
export interface ScheduledOutput {
  output_index: number; artifact_id: string; version: number; sha256: string; size: number; mime: string; name: string; step_id: string
}
export interface DraftOccurrence {
  occurrence_id: string; state: string; workflow_run_id: string | null; mission_id: string | null
  outputs: ScheduledOutput[]; provenance: Record<string, unknown>
}
export interface ScheduledDraftSnapshot {
  schedule_id: string; project_id: string; version: number; revision: number; state: string
  definition: Record<string, unknown>; occurrences: DraftOccurrence[]; history_truncated: boolean
  health: unknown; last_success: unknown; next_due: unknown; last_error: unknown; remaining_checks: number
}
export interface DraftOutputTarget {
  project_id: string; schedule_id: string; occurrence_id: string; output_index: number; workflow_run_id: string; output: ScheduledOutput
}
export interface VerifiedScheduledOutput { target: DraftOutputTarget; metadata: Omit<ScheduleOutputReadResult, 'data_base64'>; bytes: Uint8Array; text: string }

export function draftIdentifier(value: string): string {
  if (!/^[A-Za-z0-9_.:-]{1,128}$/u.test(value)) { throw new Error('Use an exact bounded runtime identifier') }

  return value
}

export function draftInstant(value: string): number {
  const result = Date.parse(value) / 1000

  if (!/(Z|[+-]\d{2}:\d{2})$/u.test(value) || !Number.isFinite(result) || result <= 0 || result >= 253402214400) { throw new Error('Use an ISO date with an explicit UTC offset') }

  return result
}

export function scheduledDraftInputs(workflow: WorkflowRecord): DraftInput[] {
  if (workflow.state !== 'approved') { throw new Error('Inspect an approved immutable workflow version') }
  draftIdentifier(workflow.workflow_id); draftIdentifier(workflow.project_id); controlInteger(workflow.version, 1); controlDigest(workflow.sha256)
  const definition = controlRecord(controlJson(workflow.definition_json, 131072, false))
  const steps = controlList(definition.steps, 1, 16)

  if (steps.some(value => controlRecord(value).kind !== 'render_markdown')) { throw new Error('Scheduled drafts support only deterministic render_markdown steps') }
  const input = controlRecord(definition.input_schema), properties = controlRecord(input.properties)
  const required = controlList(input.required ?? [], 0, 64)

  if (input.type !== 'object' || input.additionalProperties !== false || Object.keys(properties).length > 64) { throw new Error('Workflow inputs must be a bounded exact object') }

  return Object.entries(properties).map(([name, raw]) => {
    const schema = controlRecord(raw), type = schema.type

    if (!/^[A-Za-z_][A-Za-z0-9_-]{0,63}$/u.test(name) || !['string', 'number', 'integer', 'boolean'].includes(String(type))) { throw new Error('Nested workflow inputs require the advanced workflow controls; no schedule was created') }

    return { name, type: type as DraftInput['type'], required: required.includes(name), schema }
  })
}

function fixedInput(input: DraftInput, value: string): string | boolean | number {
  if (input.type === 'string') {
    if ([...value].length < Number(input.schema.minLength ?? 0) || [...value].length > Number(input.schema.maxLength ?? 65536)) { throw new Error(`${input.name} exceeds its text bounds`) }

    return value
  }

  if (input.type === 'boolean') {
    if (!['true', 'false'].includes(value)) { throw new Error(`${input.name} requires true or false`) }

    return value === 'true'
  }

  const number = Number(value)

  if (!value.trim() || !Number.isFinite(number) || (input.type === 'integer' && !Number.isSafeInteger(number)) || number < Number(input.schema.minimum ?? -Infinity) || number > Number(input.schema.maximum ?? Infinity)) { throw new Error(`${input.name} requires a number within its schema bounds`) }

  return number
}

export function scheduledDraftDefinition(options: DraftDefinitionOptions): Record<string, unknown> {
  const inputs = scheduledDraftInputs(options.workflow), project = draftIdentifier(options.projectId)

  if (options.workflow.project_id !== project) { throw new Error('Workflow and draft destination must belong to the same project') }
  const known = new Set(inputs.map(input => input.name))

  if ([...Object.keys(options.values), ...Object.keys(options.bindings)].some(name => !known.has(name))) { throw new Error('Unknown workflow input') }
  const parameters: Record<string, string | boolean | number> = Object.create(null), source_bindings: { parameter: string; artifact_id: string }[] = []

  for (const input of inputs) {
    const binding = Object.hasOwn(options.bindings, input.name) ? options.bindings[input.name].trim() : undefined

    if (binding) {
      if (input.type !== 'string' || Object.hasOwn(options.values, input.name)) { throw new Error('Source bindings require separate declared string inputs') }
      source_bindings.push({ parameter: input.name, artifact_id: draftIdentifier(binding) })
    } else if (Object.hasOwn(options.values, input.name)) { parameters[input.name] = fixedInput(input, options.values[input.name]) }
    else if (input.required) { throw new Error(`Provide required input ${input.name}`) }
  }

  if (source_bindings.length > 8) { throw new Error('At most eight local source bindings are supported') }
  new Intl.DateTimeFormat('en', { timeZone: options.timezone }).format(0)
  const anchor = draftInstant(options.anchor), expires_at = draftInstant(options.expiry)

  if (expires_at <= anchor || expires_at * 1000 <= Date.now()) { throw new Error('Expiry must follow the first check and be in the future') }

  const definition = {
    schema_version: 1, schedule_id: draftIdentifier(options.scheduleId), version: 1, project_id: project, timezone: options.timezone,
    trigger: { kind: 'interval', anchor, seconds: controlInteger(options.interval, 60, 31622400) },
    policy: { missed_run: 'skip', grace_seconds: 0, overlap: 'block' },
    budget: { max_checks: controlInteger(options.maxChecks, 1, 10000), max_bytes: controlInteger(options.maxBytes, 1, 2097152), deadline_seconds: controlInteger(options.deadlineSeconds, 1, 60) },
    expires_at, kind: 'workflow_draft', specification: {
      workflow_ref: { workflow_id: options.workflow.workflow_id, version: options.workflow.version, sha256: options.workflow.sha256 }, parameters, source_bindings,
      destination: { kind: 'project_artifact_drafts', project_id: project }
    }
  }

  if (new TextEncoder().encode(JSON.stringify(definition)).length > 131072) { throw new Error('Schedule definition exceeds 128 KiB') }

  return definition
}

export function scheduledDraftSnapshot(result: ScheduleRecordResult, projectId: string, scheduleId: string): ScheduledDraftSnapshot {
  const row = controlRecord(controlJson(result.record_json, 131072, false)), definition = controlRecord(row.definition)

  if (row.project_id !== projectId || row.schedule_id !== scheduleId || definition.project_id !== projectId || definition.kind !== 'workflow_draft') { throw new Error('Response is not the selected project workflow-draft schedule') }

  const occurrences = controlList(row.occurrences, 0, 20).map(value => {
    const occurrence = controlRecord(value), produced = controlRecord(occurrence.result ?? {})

    const outputs = controlList(produced.outputs ?? [], 0, 16).map(raw => {
      const output = controlRecord(raw)

      if (produced.draft_only !== true || !['human_review_required', 'reconciliation_required'].includes(String(produced.publication_state)) || output.review_method !== 'runtime.schedule.output.get') { throw new Error('Output lacks the private draft review contract') }

      return { output_index: controlInteger(output.output_index, 0, 15), artifact_id: draftIdentifier(String(output.artifact_id)), version: controlInteger(output.version, 1), sha256: controlDigest(output.sha256), size: controlInteger(output.size, 0, 2097152), mime: String(output.mime), name: String(output.name), step_id: String(output.step_id) }
    })

    if (new Set(outputs.map(output => output.output_index)).size !== outputs.length) { throw new Error('Duplicate scheduled output index') }

    return { occurrence_id: draftIdentifier(String(occurrence.occurrence_id)), state: String(occurrence.state), workflow_run_id: produced.workflow_run_id ? draftIdentifier(String(produced.workflow_run_id)) : null, mission_id: produced.mission_id ? draftIdentifier(String(produced.mission_id)) : null, outputs, provenance: produced }
  })

  return { schedule_id: scheduleId, project_id: projectId, version: controlInteger(row.version, 1), revision: controlInteger(row.revision, 1), state: String(row.state), definition, occurrences, history_truncated: row.history_truncated === true, health: row.health, last_success: row.last_success, next_due: row.next_due, last_error: row.last_error, remaining_checks: controlInteger(row.remaining_checks, 0) }
}

/** Retained private bytes only: never starts a mission, rereads sources, or renders a workflow. */
export async function readScheduledDraft(request: RuntimeRequest, sessionId: string, target: DraftOutputTarget, signal?: AbortSignal): Promise<VerifiedScheduledOutput> {
  const { output } = target, chunks: Uint8Array[] = []
  let offset = 0, first: ScheduleOutputReadResult | null = null
  controlInteger(output.size, 0, 2097152); controlDigest(output.sha256)

  for (;;) {
    signal?.throwIfAborted()
    const part = await request('runtime.schedule.output.get', { session_id: sessionId, schema_version: 1, project_id: target.project_id, schedule_id: target.schedule_id, occurrence_id: target.occurrence_id, output_index: target.output_index, offset, limit: 65536 })
    signal?.throwIfAborted()

    if (part.project_id !== target.project_id || part.occurrence_id !== target.occurrence_id || part.output_index !== target.output_index || part.workflow_run_id !== target.workflow_run_id || part.draft_only !== true || part.artifact_id !== output.artifact_id || part.version !== output.version || part.sha256 !== output.sha256 || part.size !== output.size || part.mime !== output.mime || part.offset !== offset || (first && part.occurrence_state !== first.occurrence_state)) { throw new Error('Scheduled output identity changed; no bytes exposed') }
    const bytes = Uint8Array.from(atob(part.data_base64), char => char.charCodeAt(0))

    if (bytes.length > 65536 || part.next_offset !== offset + bytes.length || part.next_offset > output.size || (!part.eof && (bytes.length === 0 || part.next_offset === output.size))) { throw new Error('Invalid scheduled output cursor; no partial file exposed') }
    first ??= part; chunks.push(bytes); offset = part.next_offset

    if (part.eof) {
      if (offset !== output.size) { throw new Error('Scheduled output is truncated') }

      break
    }
  }

  const bytes = new Uint8Array(offset)
  let position = 0

  for (const chunk of chunks) { bytes.set(chunk, position); position += chunk.length }
  const digest = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))).map(value => value.toString(16).padStart(2, '0')).join('')
  signal?.throwIfAborted()

  if (digest !== output.sha256) { throw new Error('Scheduled output digest mismatch; bytes discarded') }

  if (output.mime !== 'text/markdown' || first!.preview_mode !== 'plain_text') { throw new Error('This draft review supports inert Markdown text only') }
  const { data_base64: _data, ...metadata } = first!

  return { target, bytes, metadata: { ...metadata, eof: true, next_offset: offset }, text: new TextDecoder('utf-8', { fatal: true }).decode(bytes) }
}

export function verifyScheduledProposal(proposal: ArtifactProposalResult, value: VerifiedScheduledOutput): void {
  if (proposal.project_id !== value.target.project_id || proposal.sha256 !== value.metadata.sha256 || proposal.size !== value.bytes.length || proposal.mime !== value.metadata.mime || proposal.parent_version !== null || proposal.expected_head_version !== null || proposal.artifact_id === value.metadata.artifact_id || proposal.version !== 1 || !Number.isFinite(proposal.expires_at) || proposal.expires_at * 1000 <= Date.now()) { throw new Error('Preparation does not match the complete reviewed bytes and new-artifact destination') }
  draftIdentifier(proposal.artifact_id); controlInteger(proposal.version, 1); draftIdentifier(proposal.approval_id); controlDigest(proposal.approval_digest); controlDigest(proposal.action_digest)
}

export function verifyScheduledPublication(result: ArtifactPublishResult, proposal: ArtifactProposalResult): void {
  if (result.project_id !== proposal.project_id || result.artifact_id !== proposal.artifact_id || result.version !== proposal.version || result.sha256 !== proposal.sha256 || result.size !== proposal.size || result.mime !== proposal.mime || result.approval_status !== 'approved' || result.validation_status !== 'passed') { throw new Error('Publication receipt differs from the exact preparation; inspect the original command') }
}
