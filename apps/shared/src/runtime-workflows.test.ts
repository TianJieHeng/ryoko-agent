import { describe, expect, it, vi } from 'vitest'

import type { WorkflowRecord } from './gateway-contract.generated.js'
import type { RuntimeRequest } from './runtime-control.js'
import { runScheduleCommand, runWorkflowCommand, summarizeSchedule } from './runtime-workflows.js'

const digest = 'a'.repeat(64), other = 'b'.repeat(64)
const ref = { artifact_id: 'source', version: 1, sha256: digest }

const definition = {
  workflow_id: 'w', project_id: 'p', version: 1,
  input_schema: { type: 'object', properties: { topic: { type: 'string' } }, required: ['topic'], additionalProperties: false },
  steps: [{ step_id: 'render', kind: 'render_markdown', parameters: { template: '# ${input.topic}' } }],
  output_schema: { required_sections: [], min_bytes: 1 }, capability_requirements: ['artifact_write'],
  environment_manifest: { adapter: 'local_deterministic_v1' }, provenance: { kind: 'manual', source_refs: [], private_derived: false }
}

const workflow: WorkflowRecord = { workflow_id: 'w', project_id: 'p', version: 1, sha256: digest, definition_json: JSON.stringify(definition), state: 'approved', revision: 3, evaluation_ref: 'eval', active_version: 1, head_revision: 1 }
const proposal = (id: string) => ({ request_id: id, project_id: 'p', artifact_id: id, version: 1, sha256: digest, size: 10, mime: id === 'manifest' ? 'application/json' : 'text/markdown', parent_version: null, expected_head_version: null, action_digest: digest, approval_id: `approval-${id}`, approval_digest: other, expires_at: 2000000000 })
const published = (id: string) => ({ project_id: 'p', artifact_id: id, version: 1, sha256: digest, size: 10, mime: id === 'manifest' ? 'application/json' : 'text/markdown', parent_version: null, disposition: 'canonical', head_version: 1, validation_status: 'passed', approval_status: 'approved' })
const runInput = (command = 'run-1', topic = 'First') => ({ project_id: 'p', workflow_id: 'w', version: 1, command_id: command, sha256: digest, mission_id: 'm', mission_revision: 2, parameters_json: JSON.stringify({ topic }) })
const scheduleDefinition = { schedule_id: 's', project_id: 'p', version: 1, timezone: 'America/New_York', trigger: { kind: 'calendar', hour: 1, minute: 30, weekdays: [6], fold: 'second', gap: 'skip' }, policy: { missed_run: 'latest', grace_seconds: 60, overlap: 'block' }, budget: { max_checks: 5, max_bytes: 1000, deadline_seconds: 20 }, expires_at: 2000000000, kind: 'monitor', specification: { question: 'What changed?', source_set: ['source'], predicate: { kind: 'normalized_text' }, notify_policy: 'record_only', condition_action: null } }
const schedule = (overrides = {}) => ({ schedule_id: 's', project_id: 'p', version: 1, revision: 4, state: 'active', next_due: 1900000000, remaining_checks: 4, health: 'healthy', last_success: 1800000000, last_error: null, authority: 'hermes_cron', definition: { ...scheduleDefinition, schema_version: 1, specification: { ...scheduleDefinition.specification, predicate: { kind: 'normalized_text', version: 1 } } }, occurrences: [], intents: [], history_truncated: false, ...overrides })
const rpcRecord = (value: unknown) => ({ record_json: JSON.stringify(value) })

describe('workflow review and immutable runs', () => {
  it('keeps varied runs explicitly pinned and separates preparation from publication', async () => {
    const request = vi.fn(async (_method: string, params: ReturnType<typeof runInput>) => ({ workflow_run_id: params.command_id, pin_json: JSON.stringify({ ...params, parameters_sha256: params.command_id === 'run-1' ? digest : other }), proposals: [proposal('output'), proposal('manifest')], publication_atomic: false })) as unknown as RuntimeRequest
    const first = await runWorkflowCommand(`run-prepare ${JSON.stringify(runInput())}`, request, 'owned')
    const second = await runWorkflowCommand(`run-prepare ${JSON.stringify(runInput('run-2', 'Second'))}`, request, 'owned')
    expect(first).toContain('nothing published')
    expect(second).toContain(other)
    expect(request).toHaveBeenNthCalledWith(2, 'runtime.workflow.run.prepare', { ...runInput('run-2', 'Second'), session_id: 'owned', schema_version: 1 })
    expect(vi.mocked(request).mock.calls.every(([method]) => method === 'runtime.workflow.run.prepare')).toBe(true)
  })
  it('preserves the exact ordered approvals and distinguishes published from delivered', async () => {
    const approvals = [proposal('output'), proposal('manifest')].map(({ approval_id, approval_digest }) => ({ approval_id, approval_digest }))
    const request = vi.fn(async () => ({ workflow_run_id: 'run', state: 'published', outputs: [published('output')], manifest: published('manifest'), publication_atomic: false, mission_completed: false })) as unknown as RuntimeRequest
    const input = { ...runInput(), approvals }
    const result = await runWorkflowCommand(`run-publish ${JSON.stringify(input)}`, request, 'owned')
    expect(request).toHaveBeenCalledExactlyOnceWith('runtime.workflow.run.publish', { ...input, session_id: 'owned', schema_version: 1 })
    expect(result).toContain('Mission completion and delivery are not confirmed')
  })
  it.each(['workflow_revision_conflict', 'workflow_not_approved', 'approval_mismatch', 'workflow_resume_mismatch'])('does not retry a rejected or revoked run (%s)', async code => {
    const request = vi.fn(async () => { throw { data: { code }, message: 'private arbitrary backend data' } }) as RuntimeRequest
    const result = await runWorkflowCommand(`run-publish ${JSON.stringify({ ...runInput(), approvals: [{ approval_id: 'a', approval_digest: digest }, { approval_id: 'b', approval_digest: other }] })}`, request, 'owned')
    expect(request).toHaveBeenCalledTimes(1)
    expect(result).toContain('No automatic retry')
    expect(result).not.toContain('Published run')
    expect(result).not.toContain('private arbitrary')
  })
  it('does not approve when a decision is only prepared', async () => {
    const request = vi.fn(async () => ({ workflow: { ...workflow, state: 'tested', revision: 2, active_version: null, head_revision: 0 }, approval_id: 'approval', approval_digest: digest, expires_at: 2000000000, scope_json: '{}' })) as unknown as RuntimeRequest
    const input = { project_id: 'p', workflow_id: 'w', version: 1, command_id: 'decision', sha256: digest, expected_revision: 2, expected_head_revision: 0, action: 'approve' }
    const result = await runWorkflowCommand(`decision-prepare ${JSON.stringify(input)}`, request, 'owned')
    expect(result).toContain('awaiting explicit commit')
    expect(result).toContain('tested; revision 2')
    expect(request).toHaveBeenCalledExactlyOnceWith('runtime.workflow.decision.prepare', { ...input, session_id: 'owned', schema_version: 1 })
  })
  it('creates only a draft and rejects caller identity or executable steps', async () => {
    const request = vi.fn(async () => ({ workflow: { ...workflow, state: 'draft', evaluation_ref: null } })) as unknown as RuntimeRequest
    const command = (value: unknown) => `create ${JSON.stringify({ command_id: 'new', definition_json: JSON.stringify(value) })}`
    expect(await runWorkflowCommand(command(definition), request, 'owned')).toContain('Draft creation does not approve or run')
    vi.mocked(request).mockClear()

    for (const value of [{ ...definition, identity: { agent_id: 'other' } }, { ...definition, steps: [{ step_id: 'run', kind: 'shell', parameters: { command: 'curl' } }] }]) {
      expect(await runWorkflowCommand(command(value), request, 'owned')).toContain('Invalid input')
    }

    expect(request).not.toHaveBeenCalled()
  })
  it('binds accepted provenance to the owned durable session rather than the transport ID', async () => {
    const request = vi.fn(async (method: string) => method === 'runtime.snapshot' ? { session_id: 'durable' } : { workflow: { ...workflow, state: 'draft' } }) as unknown as RuntimeRequest
    const value = { ...definition, provenance: { kind: 'accepted_mission', source_refs: [ref], private_derived: true, reference: { mission_id: 'm', revision: 4 } } }
    await runWorkflowCommand(`create ${JSON.stringify({ command_id: 'new', definition_json: JSON.stringify(value) })}`, request, 'transport')
    const sent = vi.mocked(request).mock.calls[1][1] as { definition_json: string; session_id: string }
    expect(JSON.parse(sent.definition_json).provenance.reference).toEqual({ mission_id: 'm', revision: 4, session_id: 'durable' })
    expect(sent.session_id).toBe('transport')
  })
})

describe('finite schedule controls', () => {
  it('shows check failure separately from actual no-change and baseline receipts', () => {
    const occurrence = (id: string, result: unknown, state = 'completed') => ({ occurrence_id: id, state, delivery_state: 'not_requested', result })
    const result = summarizeSchedule(schedule({ health: 'unhealthy', last_error: 'source_unavailable', occurrences: [occurrence('failed', { error: 'source_unavailable' }, 'failed'), occurrence('baseline', { baseline: true, changed: false }), occurrence('nochange', { baseline: false, changed: false, matched: false }), occurrence('pending', {}, 'outcome_unknown')] }))
    expect(result).toContain('failed: failed; check failed (source_unavailable)')
    expect(result).toContain('baseline established; no comparison yet')
    expect(result).toContain('successful check; no meaningful change')
    expect(result).toContain('outcome_unknown; check outcome not established')
    expect(result).toContain('last successful check 2027-01-15')
  })
  it('deduplicates repeated receipt identities without claiming external delivery', () => {
    const occurrence = { occurrence_id: 'occ', state: 'completed', delivery_state: 'not_requested', result: { changed: true, matched: true } }
    const intent = { intent_id: 'notice', kind: 'notification', state: 'recorded', delivery: 'record_only' }
    const result = summarizeSchedule(schedule({ occurrences: [occurrence, occurrence], intents: [intent, intent] }))
    expect(result.match(/Occurrence occ:/gu)).toHaveLength(1)
    expect(result.match(/Intent notice:/gu)).toHaveLength(1)
    expect(result).toContain('No external notification delivery')
  })
  it('preserves pause revision and displays an already-claimed occurrence honestly', async () => {
    const request = vi.fn(async () => rpcRecord(schedule({ revision: 5, state: 'paused', occurrences: [{ occurrence_id: 'pending', state: 'claimed', delivery_state: 'not_requested', result: {} }] }))) as unknown as RuntimeRequest
    const result = await runScheduleCommand('pause p s 4 cmd', request, 'owned')
    expect(request).toHaveBeenCalledExactlyOnceWith('runtime.schedule.update', { session_id: 'owned', schema_version: 1, project_id: 'p', schedule_id: 's', expected_revision: 4, command_id: 'cmd', state: 'paused' })
    expect(result).toContain('already claimed work')
    expect(result).toContain('claimed; check outcome not established')
  })
  it('renders expiry and DST policy and directs notification changes to exact-session policy controls', async () => {
    const request = vi.fn(async () => rpcRecord(schedule({ state: 'expired' }))) as unknown as RuntimeRequest
    const result = await runScheduleCommand('get p s', request, 'owned')
    expect(result).toContain('expired; revision')
    expect(result).toContain('America/New_York')
    expect(result).toContain('DST fold second, gap skip')
    vi.mocked(request).mockClear()
    expect(await runScheduleCommand('snooze p s tomorrow', request, 'owned')).toContain('typed Monitor health')
    expect(request).not.toHaveBeenCalled()
  })
  it('creates a paused bounded monitor with owned schema and no implicit action grant', async () => {
    const request = vi.fn(async () => rpcRecord(schedule({ state: 'paused', revision: 1, health: 'unknown', last_success: null }))) as unknown as RuntimeRequest
    const result = await runScheduleCommand(`create ${JSON.stringify({ command_id: 'new', definition_json: JSON.stringify(scheduleDefinition) })}`, request, 'owned')
    expect(result).toContain('paused; revision 1')
    const params = vi.mocked(request).mock.calls[0][1] as { definition_json: string }
    expect(JSON.parse(params.definition_json)).toEqual({ ...scheduleDefinition, schema_version: 1, specification: { ...scheduleDefinition.specification, predicate: { kind: 'normalized_text', version: 1 } } })
    expect(request).toHaveBeenCalledTimes(1)
  })
  it('does not fabricate empty state on failed reads or retry revision conflict', async () => {
    const request = vi.fn(async () => { throw { data: { code: 'schedule_revision_conflict' } } }) as RuntimeRequest
    expect(await runScheduleCommand('resume p s 4 cmd', request, 'owned')).toContain('revision changed')
    expect(request).toHaveBeenCalledTimes(1)
    const unavailable = vi.fn(async () => { throw new Error('credentials SECRET') }) as RuntimeRequest
    const result = await runScheduleCommand('list p', unavailable, 'owned')
    expect(result).not.toContain('No schedules')
    expect(result).not.toContain('SECRET')
  })
})
it('inspects and explicitly cancels the exact retained workflow command without starting a replacement', async () => {
  const request = vi.fn(async (_method: string, params: { command_id: string }) => ({ command_id: params.command_id, run_id: 'run', status: 'cancelled', owner_live: false, expires_at: null, result: { cancel_requested: true, effects_undone: false } })) as unknown as RuntimeRequest
  const result = await runWorkflowCommand('cancel retained-command', request, 'owned')
  expect(request).toHaveBeenCalledExactlyOnceWith('runtime.artifact.cancel', { session_id: 'owned', schema_version: 1, command_id: 'retained-command' })
  expect(result).toContain('cancelled; already committed effects are not undone')
})
