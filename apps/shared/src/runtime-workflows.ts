import type { RpcMethods, ScheduleRecordResult, WorkflowRecord } from './gateway-contract.generated.js'
import type { RuntimeRequest } from './runtime-control.js'
import {
  artifactProposal, artifactPublication, artifactReference, controlChoice, controlDigest,
  controlFailure, controlId, controlInteger, controlJson, controlList, controlRecord,
  controlRef, controlText, RuntimeInputError, summarizeArtifactCommand
} from './runtime-research.js'

type Owned<M extends keyof RpcMethods> = Omit<RpcMethods[M]['params'], 'session_id' | 'schema_version'>

function identifier(value: unknown): string {
  const text = controlId(value)

  if (!/^[A-Za-z0-9_.:-]{1,128}$/u.test(text)) {throw new RuntimeInputError('Use an exact bounded runtime identifier')}

  return text
}

function words(argument: string, count: number): string[] {
  const result = argument.trim().split(/\s+/u)

  if (result.length !== count || result.some(value => !value)) {throw new RuntimeInputError(`Expected ${count} arguments; use help for the command syntax`)}

  return result
}

function multiline(value: unknown, maximum = 65536): string {
  if (typeof value !== 'string' || !value.trim() || new TextEncoder().encode(value).length > maximum || [...value].some(char => { const code = char.charCodeAt(0);

 return (code < 32 && code !== 9 && code !== 10) || code === 127 })) {throw new RuntimeInputError('Expected bounded text without unsafe control characters')}

  return value
}

function jsonString(value: unknown, maximum = 131072): string {
  if (typeof value !== 'string') {throw new RuntimeInputError('Expected an exact JSON string')}
  controlJson(value, maximum)

  return value
}

function epoch(value: unknown): number {
  if (typeof value !== 'number' || !Number.isFinite(value) || value <= 0 || value >= 253402214400) {throw new RuntimeInputError('Expected a finite positive UTC timestamp')}

  return value
}

function timezone(value: unknown): string {
  const zone = controlText(value, 128)

  try { new Intl.DateTimeFormat('en-US', { timeZone: zone }).format(0) } catch { throw new RuntimeInputError('Use an available IANA timezone') }

  return zone
}

function stamp(value: unknown): string { return value == null ? 'unavailable' : new Date(epoch(value) * 1000).toISOString() }

function checkedBoolean(value: unknown): boolean {
  if (typeof value !== 'boolean') {throw new RuntimeInputError('Expected an explicit boolean')}

  return value
}

function unique<T>(values: T[]): T[] {
  if (new Set(values).size !== values.length) {throw new RuntimeInputError('Duplicate values are unsupported')}

  return values
}

function workflowPin(rest: string): Owned<'runtime.workflow.get'> {
  const [project, workflow, version] = words(rest, 3)

  return { project_id: identifier(project), workflow_id: identifier(workflow), version: controlInteger(Number(version), 1) }
}

function workflowFields(row: Record<string, unknown>): Owned<'runtime.workflow.get'> {
  return { project_id: identifier(row.project_id), workflow_id: identifier(row.workflow_id), version: controlInteger(row.version, 1) }
}

function approvalList(value: unknown): { approval_id: string; approval_digest: string }[] {
  const rows = controlList(value, 2, 65).map(value => {
    const row = controlRecord(value, ['approval_id', 'approval_digest'], [])

    return { approval_id: controlId(row.approval_id), approval_digest: controlDigest(row.approval_digest) }
  })

  unique(rows.map(row => row.approval_id))

  return rows
}

function schema(value: unknown, depth = 0): void {
  if (depth > 8) {throw new RuntimeInputError('Input schema exceeds the nesting bound')}
  const row = controlRecord(value), kind = controlChoice(row.type, ['object', 'array', 'string', 'integer', 'number', 'boolean'])
  const fields = { object: ['properties', 'required', 'additionalProperties'], array: ['items', 'minItems', 'maxItems'], string: ['minLength', 'maxLength'], integer: ['minimum', 'maximum'], number: ['minimum', 'maximum'], boolean: [] }
  controlRecord(row, ['type'], ['description', ...fields[kind]])

  if (row.description !== undefined) {multiline(row.description, 4096)}

  if (kind === 'object') {
    const properties = controlRecord(row.properties)

    if (Object.keys(properties).length > 64 || row.additionalProperties !== false) {throw new RuntimeInputError('Input object must be bounded and disallow additional properties')}

    for (const [key, child] of Object.entries(properties)) { bindingName(key); schema(child, depth + 1) }
    const required = unique(controlList(row.required ?? [], 0, 64).map(bindingName))

    if (required.some(key => !Object.hasOwn(properties, key))) {throw new RuntimeInputError('Required input must be declared')}
  }

  if (kind === 'array') {schema(row.items, depth + 1)}
  const limits = kind === 'array' ? ['minItems', 'maxItems', 1000] as const : ['minLength', 'maxLength', 65536] as const

  if (kind === 'array' || kind === 'string') {
    const lower = row[limits[0]] === undefined ? 0 : controlInteger(row[limits[0]], 0, limits[2])
    const upper = row[limits[1]] === undefined ? limits[2] : controlInteger(row[limits[1]], 0, limits[2])

    if (lower > upper) {throw new RuntimeInputError('Input bounds conflict')}
  }

  if (kind === 'integer' || kind === 'number') {
    for (const field of ['minimum', 'maximum']) {if (row[field] !== undefined && (typeof row[field] !== 'number' || !Number.isFinite(row[field]))) {throw new RuntimeInputError('Input bounds must be finite')}}

    if (typeof row.minimum === 'number' && typeof row.maximum === 'number' && row.minimum > row.maximum) {throw new RuntimeInputError('Input bounds conflict')}
  }
}

function bindingName(value: unknown): string {
  const name = controlText(value, 64)

  if (!/^[A-Za-z_][A-Za-z0-9_-]{0,63}$/u.test(name)) {throw new RuntimeInputError('Invalid input or step binding name')}

  return name
}

/** The selected authoring slice deliberately exposes deterministic Markdown steps only. */
function workflowDefinition(value: unknown, sessionId: string): Record<string, unknown> {
  const row = controlRecord(value, ['workflow_id', 'version', 'project_id', 'input_schema', 'steps', 'output_schema', 'capability_requirements', 'environment_manifest', 'provenance'], ['predecessor', 'template_ref'])
  identifier(row.workflow_id); identifier(row.project_id); controlInteger(row.version, 1); schema(row.input_schema)
  const input = controlRecord(row.input_schema)

  if (input.type !== 'object') {throw new RuntimeInputError('Workflow input schema must be an object')}
  const inputs = controlRecord(input.properties)

  const steps = controlList(row.steps, 1, 16).map(value => {
    const step = controlRecord(value, ['step_id', 'kind', 'parameters'], ['depends_on'])
    const id = bindingName(step.step_id), dependencies = unique(controlList(step.depends_on ?? [], 0, 16).map(bindingName))
    controlChoice(step.kind, ['render_markdown'])
    const parameters = controlRecord(step.parameters, ['template'], []), template = multiline(parameters.template)
    const pattern = /\$\{(input|steps)\.([A-Za-z_][A-Za-z0-9_-]{0,63})\}/gu
    const refs: string[] = []

    for (const match of template.matchAll(pattern)) {
      if (match[1] === 'input' && !Object.hasOwn(inputs, match[2])) {throw new RuntimeInputError('Template input is undeclared')}

      if (match[1] === 'steps') {refs.push(match[2])}
    }

    if (template.replace(pattern, '').includes('${')) {throw new RuntimeInputError('Unsupported template substitution')}

    return { id, dependencies, refs }
  })

  unique(steps.map(step => step.id))

  function ancestors(id: string, path: string[] = []): Set<string> {
    const step = steps.find(item => item.id === id)

    if (!step || path.includes(id)) {throw new RuntimeInputError('Steps require an acyclic graph of known dependencies')}
    const parents = new Set<string>()

    for (const dependency of step.dependencies) { parents.add(dependency); ancestors(dependency, [...path, id]).forEach(parent => parents.add(parent)) }

    if (step.refs.some(ref => !parents.has(ref))) {throw new RuntimeInputError('Step output binding requires a dependency path')}

    return parents
  }

  steps.forEach(step => ancestors(step.id))
  const output = controlRecord(row.output_schema, ['required_sections', 'min_bytes'], [])
  unique(controlList(output.required_sections, 0, 32).map(item => controlText(item)))
  controlInteger(output.min_bytes, 0, 4 * 1024 * 1024)
  unique(controlList(row.capability_requirements, 0, 2).map(item => controlChoice(item, ['artifact_read', 'artifact_write'])))
  controlChoice(controlRecord(row.environment_manifest, ['adapter'], []).adapter, ['local_deterministic_v1'])
  const provenance = controlRecord(row.provenance, ['kind', 'source_refs', 'private_derived'], ['reference'])
  controlChoice(provenance.kind, ['manual', 'accepted_mission']); checkedBoolean(provenance.private_derived)
  const refs = controlList(provenance.source_refs, 0, 64).map(controlRef)

  if (!provenance.private_derived && (provenance.kind !== 'manual' || refs.length)) {throw new RuntimeInputError('Source-derived workflows must remain private')}
  let reference = provenance.reference

  if (provenance.kind === 'accepted_mission') {
    const ref = controlRecord(reference, ['mission_id', 'revision'], [])
    reference = { mission_id: identifier(ref.mission_id), revision: controlInteger(ref.revision, 1), session_id: sessionId }
  } else if (reference !== undefined) {controlText(reference, 1024)}

  for (const field of ['predecessor', 'template_ref']) {if (row[field] != null) {
    const key = field === 'predecessor' ? 'workflow_id' : 'template_id', ref = controlRecord(row[field], [key, 'version', 'sha256'], [])
    identifier(ref[key]); controlInteger(ref.version, 1); controlDigest(ref.sha256)

    if (field === 'predecessor' && (ref.workflow_id !== row.workflow_id || Number(ref.version) >= Number(row.version))) {throw new RuntimeInputError('Predecessor must pin an earlier version of this workflow')}
  }}

  return { ...row, provenance: { ...provenance, ...(reference === undefined ? {} : { reference }) } }
}

export function summarizeWorkflow(row: WorkflowRecord, detail = false): string {
  const state = controlChoice(row.state, ['draft', 'tested', 'approved', 'deprecated', 'revoked'])
  const definition = controlRecord(controlJson(row.definition_json, 131072, false))

  const lines = [`Workflow ${identifier(row.workflow_id)} v${controlInteger(row.version, 1)} (${identifier(row.project_id)}): ${state}; revision ${controlInteger(row.revision, 1)}; head revision ${controlInteger(row.head_revision)}`,
    `SHA-256 ${controlDigest(row.sha256)}; active version ${row.active_version ?? 'none'}; evaluation ${row.evaluation_ref ?? 'none'}`]

  if (detail) {
    const input = controlRecord(definition.input_schema), output = controlRecord(definition.output_schema)
    lines.push(`Inputs: ${Object.keys(controlRecord(input.properties)).map(bindingName).join(', ') || 'none'}`,
      `Steps: ${controlList(definition.steps, 1, 16).map(item => { const step = controlRecord(item);

 return `${bindingName(step.step_id)} (${controlText(step.kind)})` }).join(', ')}`,
      `Expected sections: ${controlList(output.required_sections).map(item => controlText(item)).join(', ') || 'none'}; minimum ${controlInteger(output.min_bytes)} bytes`,
      `Capabilities: ${controlList(definition.capability_requirements, 0, 2).map(item => controlText(item)).join(', ') || 'none'}; no live effects`)
  }

  return lines.join('\n')
}

function runInput(rest: string, publish: boolean): Owned<'runtime.workflow.run.prepare'> & { approvals?: { approval_id: string; approval_digest: string }[] } {
  const row = controlRecord(controlJson(rest), ['project_id', 'workflow_id', 'version', 'command_id', 'sha256', 'mission_id', 'mission_revision', 'parameters_json', ...(publish ? ['approvals'] : [])], [])
  const parameters_json = jsonString(row.parameters_json)
  controlRecord(controlJson(parameters_json, 131072))

  return { ...workflowFields(row), command_id: identifier(row.command_id), sha256: controlDigest(row.sha256), mission_id: identifier(row.mission_id), mission_revision: controlInteger(row.mission_revision, 1), parameters_json,
    ...(publish ? { approvals: approvalList(row.approvals) } : {}) }
}

function decisionInput(rest: string, commit: boolean): Owned<'runtime.workflow.decision.prepare'> & { approval_id?: string; approval_digest?: string } {
  const row = controlRecord(controlJson(rest), ['project_id', 'workflow_id', 'version', 'command_id', 'sha256', 'expected_revision', 'expected_head_revision', 'action', ...(commit ? ['approval_id', 'approval_digest'] : [])], [])

  return { ...workflowFields(row), command_id: identifier(row.command_id), sha256: controlDigest(row.sha256), expected_revision: controlInteger(row.expected_revision, 1), expected_head_revision: controlInteger(row.expected_head_revision),
    action: controlChoice(row.action, ['approve', 'deprecate', 'revoke', 'rollback']), ...(commit ? { approval_id: controlId(row.approval_id), approval_digest: controlDigest(row.approval_digest) } : {}) }
}

function workflowFailure(error: unknown, mutation: boolean): string {
  const row = error && typeof error === 'object' ? error as Record<string, unknown> : {}, data = row.data && typeof row.data === 'object' ? row.data as Record<string, unknown> : {}
  const code = typeof data.code === 'string' ? data.code : row.code

  const messages: Record<string, string> = {
    workflow_revision_conflict: 'Workflow changed during review; reload its exact version before preparing a new decision',
    workflow_not_approved: 'Workflow is not approved or was revoked; no new run is confirmed', approval_mismatch: 'Approval no longer matches the exact reviewed inputs and outputs',
    workflow_resume_mismatch: 'Run identity is already bound to different content, inputs or state; inspect the original run',
    schedule_revision_conflict: 'Schedule revision changed; reload before another explicit action', schedule_revoked: 'Schedule revocation is terminal',
    schedule_unresolved: 'Resolve accepted or unknown occurrences before resuming or changing ownership', schedule_expired: 'Schedule has expired or exhausted its budget'
  }

  return typeof code === 'string' && messages[code] ? `${messages[code]}. No automatic retry was made.` : controlFailure(error, mutation)
}

export const WORKFLOW_HELP = [
  '/runtime workflow list <project-id>', '/runtime workflow status|cancel <command-id>', '/runtime workflow get|runs <project-id> <workflow-id> <version>',
  '/runtime workflow create <JSON: command_id, definition_json>',
  '/runtime workflow evaluate <JSON: project_id, workflow_id, version, command_id, expected_revision, cases_json>',
  '/runtime workflow decision-prepare|decision-commit <JSON: project_id, workflow_id, version, command_id, sha256, expected_revision, expected_head_revision, action; commit adds approval_id, approval_digest>',
  '/runtime workflow run-prepare|run-publish <JSON: project_id, workflow_id, version, command_id, sha256, mission_id, mission_revision, parameters_json; publish adds ordered approvals>',
  'Decisions: approve, deprecate, revoke, rollback. Preparation is not approval or publication. Reuse the exact prepared command and bindings for commit/publish; a varied run uses a new command_id.',
  'A prepared or uncertain command retains its runtime lease. Inspect status or explicitly cancel it before starting a replacement; cancellation never undoes published effects.',
  'Create supports deterministic Markdown steps with bounded input/output schemas. Accepted-mission provenance omits session_id; this session owns it. Domain-step authoring, demonstration and external export are not exposed.'
].join('\n')

export async function runWorkflowCommand(argument: string, request: RuntimeRequest, sessionId: string): Promise<string> {
  const match = /^(\S+)(?:\s+([\s\S]*))?$/u.exec(argument.trim()), action = match?.[1] ?? 'help', rest = match?.[2] ?? ''
  const mutation = ['create', 'evaluate', 'decision-prepare', 'decision-commit', 'run-prepare', 'run-publish', 'cancel'].includes(action)

  try {
    const owned = { session_id: controlId(sessionId), schema_version: 1 as const }

    const actions: Record<string, () => Promise<string>> = {
      help: async () => WORKFLOW_HELP, '--help': async () => WORKFLOW_HELP,
      status: async () => summarizeArtifactCommand(await request('runtime.artifact.status', { ...owned, command_id: identifier(rest) })),
      cancel: async () => summarizeArtifactCommand(await request('runtime.artifact.cancel', { ...owned, command_id: identifier(rest) })),
      list: async () => {
        const result = await request('runtime.workflow.list', { ...owned, project_id: identifier(rest) })

        return `${result.workflows.length ? result.workflows.map(row => summarizeWorkflow(row)).join('\n') : 'No workflows returned'}\nBounded workflow list; completeness is not certified.`
      },
      get: async () => summarizeWorkflow((await request('runtime.workflow.get', { ...owned, ...workflowPin(rest) })).workflow, true),
      runs: async () => {
        const result = await request('runtime.workflow.runs', { ...owned, ...workflowPin(rest) })
        const rows = controlList(controlJson(result.runs_json, 131072, false), 0, 100)

        return `${rows.map(value => { const row = controlRecord(value), pin = controlRecord(row.pin);

 return `${controlId(row.workflow_run_id)}: ${controlText(row.state)}; workflow v${controlInteger(pin.version, 1)}; inputs SHA-256 ${controlDigest(pin.parameters_sha256)}; control ${controlText(row.control_status)}; ${controlList(row.artifact_refs, 0, 65).length} retained artifacts` }).join('\n') || 'No runs returned'}\nBounded run history; publication does not establish mission completion or delivery.`
      },
      create: async () => {
        const row = controlRecord(controlJson(rest), ['command_id', 'definition_json'], [])
        const parsed = controlJson(jsonString(row.definition_json), 131072)
        const source = controlRecord(controlRecord(parsed).provenance)
        const sourceSession = source.kind === 'accepted_mission' ? (await request('runtime.snapshot', owned)).session_id : owned.session_id
        const definition = workflowDefinition(parsed, controlId(sourceSession))
        const result = await request('runtime.workflow.create', { ...owned, command_id: identifier(row.command_id), definition_json: JSON.stringify(definition) })

        return `${summarizeWorkflow(result.workflow, true)}\nDraft creation does not approve or run this workflow.`
      },
      evaluate: async () => {
        const row = controlRecord(controlJson(rest), ['project_id', 'workflow_id', 'version', 'command_id', 'expected_revision', 'cases_json'], [])
        const cases_json = jsonString(row.cases_json), cases = controlList(controlJson(cases_json, 131072), 4, 16)

        const splits = cases.map(item => { const entry = controlRecord(item, ['case_id', 'split', 'parameters', 'expected_sha256', 'generalist_ref'], []); identifier(entry.case_id); controlRecord(entry.parameters); controlDigest(entry.expected_sha256); controlRef(entry.generalist_ref);

 return controlChoice(entry.split, ['tuning', 'held_out']) })

        unique(cases.map(item => controlRecord(item).case_id))

        if (splits.filter(split => split === 'tuning').length < 2 || splits.filter(split => split === 'held_out').length < 2) {throw new RuntimeInputError('Evaluation needs two tuning and two held-out cases')}
        const result = await request('runtime.workflow.evaluate', { ...owned, ...workflowFields(row), command_id: identifier(row.command_id), expected_revision: controlInteger(row.expected_revision, 1), cases_json })
        const evidence = controlRecord(controlJson(result.evaluation_json, 131072, false))

        return `${summarizeWorkflow(result.workflow)}\nEvaluation ${checkedBoolean(evidence.passed) ? 'passed' : 'failed'}; ${controlInteger(evidence.candidate_passes)}/${controlList(evidence.cases, 4, 16).length} expected outputs. Recorded human-supplied baselines; no live generalist attestation. Approval remains a separate decision.`
      },
      'decision-prepare': async () => {
        const input = decisionInput(rest, false), result = await request('runtime.workflow.decision.prepare', { ...owned, ...input })

        return `${summarizeWorkflow(result.workflow)}\nPrepared ${input.action}; awaiting explicit commit. Approval ${controlId(result.approval_id)}; digest ${controlDigest(result.approval_digest)}; expires ${stamp(result.expires_at)}. Preserve this exact command, version, revision and head revision.`
      },
      'decision-commit': async () => {
        const input = decisionInput(rest, true)
        const result = await request('runtime.workflow.decision.commit', { ...owned, ...input, approval_id: input.approval_id!, approval_digest: input.approval_digest! })

        return `${summarizeWorkflow(result.workflow)}\nDecision ${input.action} recorded by the runtime. No run or external send was requested.`
      },
      'run-prepare': async () => {
        const input = runInput(rest, false), result = await request('runtime.workflow.run.prepare', { ...owned, ...input })
        const pin = controlRecord(controlJson(result.pin_json, 131072, false))

        if (pin.workflow_id !== input.workflow_id || pin.version !== input.version || pin.sha256 !== input.sha256 || pin.mission_id !== input.mission_id || pin.mission_revision !== input.mission_revision || result.publication_atomic !== false) {throw new RuntimeInputError('Prepared run differs from the selected immutable version or mission')}
        const proposals = result.proposals.map(artifactProposal)

        if (proposals.length < 2 || proposals.some(proposal => proposal.project_id !== input.project_id)) {throw new RuntimeInputError('Prepared outputs differ from the selected project')}

        return [`Prepared run ${controlId(result.workflow_run_id)}: ${input.workflow_id} v${input.version}; inputs SHA-256 ${controlDigest(pin.parameters_sha256)}`,
          ...proposals.map((proposal, index) => `${index === proposals.length - 1 ? 'Manifest' : 'Output'}: ${artifactReference(proposal)}`),
          `Ordered approvals: ${JSON.stringify(proposals.map(({ approval_id, approval_digest }) => ({ approval_id, approval_digest })))}`,
          'Awaiting explicit publication; nothing published. Reuse the identical command and parameters_json. Publication is not atomic.'].join('\n')
      },
      'run-publish': async () => {
        const input = runInput(rest, true), result = await request('runtime.workflow.run.publish', { ...owned, ...input, approvals: input.approvals! })

        if (result.state !== 'published' || result.publication_atomic !== false || result.mission_completed !== false) {throw new RuntimeInputError('Workflow publication is not confirmed')}
        const outputs = [...result.outputs.map(artifactPublication), artifactPublication(result.manifest)]

        if (outputs.some(output => output.project_id !== input.project_id)) {throw new RuntimeInputError('Published output project differs')}

        return [`Published run ${controlId(result.workflow_run_id)}: ${input.workflow_id} v${input.version}`, ...outputs.map(output => artifactReference(output)), 'Publication was not atomic. Mission completion and delivery are not confirmed.'].join('\n')
      }
    }

    return await ((Object.hasOwn(actions, action) ? actions[action] : undefined) ?? (async () => `Unsupported workflow action.\n${WORKFLOW_HELP}`))()
  } catch (error) { return workflowFailure(error, mutation) }
}

function reviewSpecification(value: unknown): void {
  const row = controlRecord(value, ['purpose', 'source_refs', 'workflow_ref'], [])
  controlChoice(row.purpose, ['memory_review', 'skill_review']); controlList(row.source_refs, 1, 16).forEach(controlRef)

  if (row.workflow_ref != null) {
    const ref = controlRecord(row.workflow_ref, ['workflow_id', 'version', 'sha256'], [])
    identifier(ref.workflow_id); controlInteger(ref.version, 1); controlDigest(ref.sha256)
  } else if (row.purpose === 'skill_review') {throw new RuntimeInputError('Skill review requires an immutable workflow reference')}
}

function scheduleDefinition(value: unknown): Record<string, unknown> {
  const row = controlRecord(value, ['schedule_id', 'version', 'project_id', 'timezone', 'trigger', 'policy', 'budget', 'expires_at', 'kind', 'specification'], [])
  identifier(row.schedule_id); identifier(row.project_id); controlInteger(row.version, 1); timezone(row.timezone); epoch(row.expires_at)
  const trigger = controlRecord(row.trigger), kind = controlChoice(trigger.kind, ['at', 'interval', 'calendar'])

  const triggers = {
    at: () => { controlRecord(trigger, ['kind', 'at'], []); epoch(trigger.at) },
    interval: () => { controlRecord(trigger, ['kind', 'anchor', 'seconds'], []); epoch(trigger.anchor); controlInteger(trigger.seconds, 60, 366 * 86400) },
    calendar: () => { controlRecord(trigger, ['kind', 'hour', 'minute', 'weekdays', 'fold', 'gap'], []); controlInteger(trigger.hour, 0, 23); controlInteger(trigger.minute, 0, 59); unique(controlList(trigger.weekdays, 1, 7).map(day => controlInteger(day, 0, 6))); controlChoice(trigger.fold, ['first', 'second']); controlChoice(trigger.gap, ['skip']) }
  }

  triggers[kind]()
  const policy = controlRecord(row.policy, ['missed_run', 'grace_seconds', 'overlap'], [])
  controlChoice(policy.missed_run, ['skip', 'latest']); controlInteger(policy.grace_seconds, 0, 86400); controlChoice(policy.overlap, ['block'])
  const budget = controlRecord(row.budget, ['max_checks', 'max_bytes', 'deadline_seconds'], [])
  controlInteger(budget.max_checks, 1, 10000); controlInteger(budget.max_bytes, 1, 2097152); controlInteger(budget.deadline_seconds, 1, 60)
  const spec = controlRecord(row.specification), adapter = controlChoice(row.kind, ['monitor', 'review', 'weekly_review'])

  if (adapter === 'weekly_review') {controlRecord(spec, [], [])}

  if (adapter === 'review') {reviewSpecification(spec)}

  if (adapter === 'monitor') {
    controlRecord(spec, ['question', 'source_set', 'predicate', 'notify_policy', 'condition_action'], [])
    controlText(spec.question, 2048); unique(controlList(spec.source_set, 1, 8).map(identifier)); controlChoice(spec.notify_policy, ['record_only', 'local_runtime'])
    const predicate = controlRecord(spec.predicate), kind = controlChoice(predicate.kind, ['normalized_text', 'json_fields', 'threshold'])
    // Version is runtime-owned in this nested predicate too.
    controlRecord(predicate, ['kind', ...(kind === 'json_fields' ? ['fields'] : kind === 'threshold' ? ['field', 'operator', 'value'] : [])], [])

    if (kind === 'json_fields') {unique(controlList(predicate.fields, 1, 32).map(identifier))}

    if (kind === 'threshold') {
      identifier(predicate.field); controlChoice(predicate.operator, ['gt', 'gte', 'lt', 'lte', 'eq'])

      if (typeof predicate.value !== 'number' || !Number.isFinite(predicate.value)) {throw new RuntimeInputError('Threshold must be finite')}
    }

    if (spec.condition_action != null) {reviewSpecification(spec.condition_action)}

    return { ...row, schema_version: 1, specification: { ...spec, predicate: { ...predicate, version: 1 } } }
  }

  return { ...row, schema_version: 1 }
}

function scheduleRecord(result: ScheduleRecordResult): Record<string, unknown> { return controlRecord(controlJson(result.record_json, 131072, false)) }

export function summarizeSchedule(value: unknown): string {
  const row = controlRecord(value), definition = controlRecord(row.definition), trigger = controlRecord(definition.trigger), policy = controlRecord(definition.policy)
  const state = controlChoice(row.state, ['active', 'paused', 'revoked', 'expired']), health = controlChoice(row.health, ['healthy', 'unhealthy', 'unknown'])

  const lines = [`Schedule ${identifier(row.schedule_id)} v${controlInteger(row.version, 1)} (${identifier(row.project_id)}): ${state}; revision ${controlInteger(row.revision, 1)}`,
    `Health ${health}; last successful check ${stamp(row.last_success)}; next due ${stamp(row.next_due)}; expiry ${stamp(definition.expires_at)}`,
    `Timezone ${timezone(definition.timezone)}; trigger ${controlText(trigger.kind)}${trigger.kind === 'calendar' ? ` ${controlInteger(trigger.hour, 0, 23)}:${String(controlInteger(trigger.minute, 0, 59)).padStart(2, '0')}; DST fold ${controlChoice(trigger.fold, ['first', 'second'])}, gap ${controlChoice(trigger.gap, ['skip'])}` : ''}; missed runs ${controlChoice(policy.missed_run, ['skip', 'latest'])}; overlap ${controlChoice(policy.overlap, ['block'])}`,
    `Remaining checks ${controlInteger(row.remaining_checks)}; authority ${controlChoice(row.authority, ['hermes_cron'])}; last error ${row.last_error == null ? 'none' : controlText(row.last_error)}`]

  const spec = controlRecord(definition.specification)

  if (definition.kind === 'monitor') {
    const predicate = controlRecord(spec.predicate)
    lines.push(`Question: ${controlText(spec.question, 2048)}`, `Sources: ${controlList(spec.source_set, 1, 8).map(identifier).join(', ')}; criterion ${controlText(predicate.kind)}; notification policy ${controlChoice(spec.notify_policy, ['record_only', 'local_runtime'])}`,
      `Conditional action: ${spec.condition_action == null ? 'none' : `${controlText(controlRecord(spec.condition_action).purpose)}; requires separate exact-version bounded grant`}`)
  }

  const occurrences = controlList(row.occurrences, 0, 20), seen = new Set<string>()

  for (const value of occurrences) {
    const occurrence = controlRecord(value), id = controlId(occurrence.occurrence_id), result = controlRecord(occurrence.result)

    if (seen.has(id)) {continue}
    seen.add(id)
    let observed = 'check outcome not established'

    if (result.error || occurrence.state === 'failed') {observed = `check failed${result.error ? ` (${controlText(result.error)})` : ''}`}
    else if (occurrence.state === 'completed') {
      if (result.baseline === true) {observed = 'baseline established; no comparison yet'}
      else if (result.changed === false) {observed = 'successful check; no meaningful change'}
      else if (result.changed === true) {observed = result.matched === true ? 'meaningful change matched' : 'changed; condition did not match'}
      else {observed = 'execution completed; change comparison unavailable'}
    }

    lines.push(`Occurrence ${id}: ${controlText(occurrence.state)}; ${observed}; delivery ${controlText(occurrence.delivery_state)}`)
  }

  const intents = controlList(row.intents, 0, 20), intentIds = new Set<string>()

  for (const value of intents) {
    const intent = controlRecord(value), id = controlId(intent.intent_id)

    if (intentIds.has(id)) {continue}
    intentIds.add(id)
    lines.push(`Intent ${id}: ${controlText(intent.kind)} ${controlText(intent.state)}${intent.delivery ? `; ${controlText(intent.delivery)}` : ''}`)
  }

  if (row.history_truncated === true) {lines.push('Recent occurrence/intent history is truncated')}

  if (state !== 'active') {lines.push('Stopped admissions do not establish the outcome of already claimed work; inspect occurrence receipts')}
  lines.push('Local retained sources only. No external notification delivery. Local delivery policy, quiet hours, digest, snooze and dismissal have separate exact-session monitor controls. Delivery retry never rechecks sources.')

  return lines.join('\n')
}

export const SCHEDULE_HELP = [
  '/runtime schedule list <project-id>', '/runtime schedule get <project-id> <schedule-id>',
  '/runtime schedule pause|resume|revoke <project-id> <schedule-id> <expected-revision> <command-id>',
  '/runtime schedule create <JSON: command_id, definition_json, expected_revision?>',
  '/runtime schedule grant <JSON: project_id, schedule_id, command_id, expected_revision, expires_at, max_age_seconds, max_fires>',
  '/runtime schedule reconcile <JSON: project_id, schedule_id, command_id, occurrence, evidence_ref_json>',
  'Definition excludes schema_version; monitor predicate excludes version. These are runtime-owned. Creation is paused; resume is explicit. Revised versions require a paused exact revision.',
  'Only finite local monitor/review/weekly_review jobs are supported. Notifications are record-only unless local_runtime is declared and a separate bounded same-session delivery policy is explicitly authorized. No client timer, live connector or external sends.'
].join('\n')

export async function runScheduleCommand(argument: string, request: RuntimeRequest, sessionId: string): Promise<string> {
  const match = /^(\S+)(?:\s+([\s\S]*))?$/u.exec(argument.trim()), action = match?.[1] ?? 'help', rest = match?.[2] ?? ''
  const mutation = ['create', 'pause', 'resume', 'revoke', 'grant', 'reconcile'].includes(action)

  try {
    const owned = { session_id: controlId(sessionId), schema_version: 1 as const }

    const change = async (state: 'active' | 'paused' | 'revoked'): Promise<string> => {
      const [project, schedule, revision, command] = words(rest, 4)

      return summarizeSchedule(scheduleRecord(await request('runtime.schedule.update', { ...owned, project_id: identifier(project), schedule_id: identifier(schedule), expected_revision: controlInteger(Number(revision), 1), command_id: identifier(command), state })))
    }

    const actions: Record<string, () => Promise<string>> = {
      help: async () => SCHEDULE_HELP, '--help': async () => SCHEDULE_HELP,
      list: async () => {
        const result = scheduleRecord(await request('runtime.schedule.list', { ...owned, project_id: identifier(rest) })), rows = controlList(result.schedules, 0, 100)

        return `${rows.map(summarizeSchedule).join('\n\n') || 'No schedules returned'}\nBounded schedule list; completeness is not certified.`
      },
      get: async () => { const [project, schedule] = words(rest, 2);

 return summarizeSchedule(scheduleRecord(await request('runtime.schedule.get', { ...owned, project_id: identifier(project), schedule_id: identifier(schedule) }))) },
      pause: () => change('paused'), resume: () => change('active'), revoke: () => change('revoked'),
      create: async () => {
        const row = controlRecord(controlJson(rest), ['command_id', 'definition_json'], ['expected_revision'])
        const definition = scheduleDefinition(controlJson(jsonString(row.definition_json), 131072))

        return summarizeSchedule(scheduleRecord(await request('runtime.schedule.create', { ...owned, command_id: identifier(row.command_id), definition_json: JSON.stringify(definition), expected_revision: row.expected_revision == null ? null : controlInteger(row.expected_revision, 1) })))
      },
      grant: async () => {
        const row = controlRecord(controlJson(rest), ['project_id', 'schedule_id', 'command_id', 'expected_revision', 'expires_at', 'max_age_seconds', 'max_fires'], [])
        const result = scheduleRecord(await request('runtime.schedule.grant', { ...owned, project_id: identifier(row.project_id), schedule_id: identifier(row.schedule_id), command_id: identifier(row.command_id), expected_revision: controlInteger(row.expected_revision, 1), expires_at: epoch(row.expires_at), max_age_seconds: controlInteger(row.max_age_seconds, 1, 3600), max_fires: controlInteger(row.max_fires, 1, 100) }))

        if (result.external_actions !== false) {throw new RuntimeInputError('Unexpected external action authority')}

        return `Bounded local review grant ${controlId(result.grant_id)}; exact target SHA-256 ${controlDigest(result.target_digest)}; ${controlInteger(result.remaining, 0, 100)} remaining; expires ${stamp(result.expires_at)}. No external actions authorized.`
      },
      reconcile: async () => {
        const row = controlRecord(controlJson(rest), ['project_id', 'schedule_id', 'command_id', 'occurrence', 'evidence_ref_json'], [])
        const evidence_ref_json = jsonString(row.evidence_ref_json, 2048); controlRef(controlJson(evidence_ref_json, 2048))

        return `${summarizeSchedule(scheduleRecord(await request('runtime.schedule.reconcile', { ...owned, project_id: identifier(row.project_id), schedule_id: identifier(row.schedule_id), command_id: identifier(row.command_id), occurrence: identifier(row.occurrence), evidence_ref_json })))}\nEvidence retained; the occurrence was not replayed and external effects were not undone.`
      }
    }

    return await ((Object.hasOwn(actions, action) ? actions[action] : undefined) ?? (async () => `Unsupported schedule action. Use the typed Monitor health delivery-policy controls for quiet hours, digest, snooze and dismissal.\n${SCHEDULE_HELP}`))()
  } catch (error) { return workflowFailure(error, mutation) }
}
