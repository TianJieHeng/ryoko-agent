import { describe, expect, it, vi } from 'vitest'

import type { ArtifactProposalResult, ArtifactPublishResult, WorkflowRecord } from './gateway-contract.generated.js'
import type { RuntimeRequest } from './runtime-control.js'
import type { DraftDefinitionOptions, DraftOutputTarget } from './runtime-scheduled-drafts.js'
import { readScheduledDraft, scheduledDraftDefinition, scheduledDraftInputs, scheduledDraftSnapshot, verifyScheduledProposal, verifyScheduledPublication } from './runtime-scheduled-drafts.js'

const content = new TextEncoder().encode('# Draft\nSaved source α\n'), digest = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', content))).map(value => value.toString(16).padStart(2, '0')).join('')
const workflow: WorkflowRecord = { workflow_id: 'wf', project_id: 'p', version: 3, revision: 4, sha256: 'a'.repeat(64), state: 'approved', evaluation_ref: 'evaluated', active_version: 3, head_revision: 4, definition_json: JSON.stringify({ input_schema: { type: 'object', additionalProperties: false, properties: { topic: { type: 'string', minLength: 1 }, source: { type: 'string' }, count: { type: 'integer', minimum: 1, maximum: 5 } }, required: ['topic', 'source'] }, steps: [{ kind: 'render_markdown', step_id: 'render', parameters: { template: '# ${input.topic}\n${input.source}' } }] }) }
const options = (): DraftDefinitionOptions => ({ projectId: 'p', scheduleId: 'scheduled', timezone: 'Etc/UTC', anchor: new Date(Date.now() + 60000).toISOString(), expiry: new Date(Date.now() + 7200000).toISOString(), interval: 3600, maxChecks: 2, maxBytes: 1024, deadlineSeconds: 10, workflow, values: { topic: 'Review', count: '2' }, bindings: { source: 'retained-source' } })
const target: DraftOutputTarget = { project_id: 'p', schedule_id: 'scheduled', occurrence_id: 'occ', output_index: 0, workflow_run_id: 'wrun', output: { output_index: 0, artifact_id: 'private-result', version: 1, sha256: digest, size: content.length, mime: 'text/markdown', name: 'summary', step_id: 'render' } }
const part = (offset = 0, end = content.length) => ({ project_id: 'p', artifact_id: target.output.artifact_id, version: 1, sha256: digest, size: content.length, mime: 'text/markdown', offset, data_base64: btoa(String.fromCharCode(...content.slice(offset, end))), next_offset: end, eof: end === content.length, preview_mode: 'plain_text', draft_only: true, occurrence_id: 'occ', occurrence_state: 'completed', output_index: 0, workflow_run_id: 'wrun' })
const proposal = (): ArtifactProposalResult => ({ project_id: 'p', request_id: 'scheduled-review', artifact_id: 'new-canonical', version: 1, sha256: digest, size: content.length, mime: 'text/markdown', parent_version: null, expected_head_version: null, action_digest: 'd'.repeat(64), approval_id: 'approval', approval_digest: 'e'.repeat(64), expires_at: Date.now() / 1000 + 120 })

describe('scheduled draft specification', () => {
  it('pins the inspected approved version and keeps local source bindings separate from exact fixed values', () => {
    const definition = scheduledDraftDefinition(options())
    expect(definition).toMatchObject({ schema_version: 1, kind: 'workflow_draft', schedule_id: 'scheduled', version: 1, trigger: { kind: 'interval', seconds: 3600 }, policy: { missed_run: 'skip', overlap: 'block' }, budget: { max_checks: 2, max_bytes: 1024, deadline_seconds: 10 }, specification: { workflow_ref: { workflow_id: 'wf', version: 3, sha256: workflow.sha256 }, parameters: { topic: 'Review', count: 2 }, source_bindings: [{ parameter: 'source', artifact_id: 'retained-source' }], destination: { kind: 'project_artifact_drafts', project_id: 'p' } } })
    expect(definition).not.toHaveProperty('state'); expect(definition).not.toHaveProperty('grant')
  })
  it.each<Partial<DraftDefinitionOptions>>([{ interval: 59 }, { maxChecks: Infinity }, { maxBytes: 2097153 }, { deadlineSeconds: 61 }, { expiry: '2020-01-01T00:00:00Z' }, { anchor: '2030-01-01T12:00:00' }, { projectId: 'other' }, { values: { topic: 'Review', source: 'fixed' } }, { bindings: { count: 'retained-source' } }, { values: { topic: 'Review', count: '2.5' } }, { values: { topic: 'Review', count: '6' } }])('rejects invalid bounds, conflicting bindings or scope %j', patch => {
    expect(() => scheduledDraftDefinition({ ...options(), ...patch })).toThrow()
  })
  it('preserves prototype-shaped parameter names without inheriting bindings or mutating object prototypes', () => {
    const names = ['__proto__', 'constructor', 'toString'], definition = JSON.parse(workflow.definition_json)
    definition.input_schema.properties = Object.fromEntries(names.map(name => [name, { type: 'string' }]))
    definition.input_schema.required = names
    const row = scheduledDraftDefinition({ ...options(), workflow: { ...workflow, definition_json: JSON.stringify(definition) }, values: Object.fromEntries(names.map(name => [name, `value-${name}`])), bindings: {} })
    const specification = JSON.parse(JSON.stringify(row)).specification
    expect(specification.parameters).toEqual(Object.fromEntries(names.map(name => [name, `value-${name}`])))
    expect(specification.source_bindings).toEqual([])
  })
  it('rejects revoked, unsupported and nested workflows', () => {
    expect(() => scheduledDraftInputs({ ...workflow, state: 'revoked' })).toThrow('approved')
    const definition = JSON.parse(workflow.definition_json)
    definition.steps[0].kind = 'browser'
    expect(() => scheduledDraftInputs({ ...workflow, definition_json: JSON.stringify(definition) })).toThrow('render_markdown')
    definition.steps[0].kind = 'render_markdown'; definition.input_schema.properties.source.type = 'object'
    expect(() => scheduledDraftInputs({ ...workflow, definition_json: JSON.stringify(definition) })).toThrow('Nested')
  })
})
describe('actual retained scheduled output', () => {
  it('joins complete bounded bytes, checks their digest, and calls only the no-rerender output reader', async () => {
    const request = vi.fn(async (_method: string, params: { offset: number }) => part(params.offset, Math.min(content.length, params.offset + 7))) as unknown as RuntimeRequest
    const value = await readScheduledDraft(request, 'session', target)
    expect(value.bytes).toEqual(content); expect(value.text).toBe('# Draft\nSaved source α\n'); expect(value.metadata.eof).toBe(true)
    expect(vi.mocked(request).mock.calls.every(([method, params]) => method === 'runtime.schedule.output.get' && 'limit' in params && params.limit === 65536)).toBe(true)
    expect(vi.mocked(request).mock.calls.map(([, params]) => 'offset' in params && params.offset)).toEqual([0, 7, 14, 21])
  })
  it.each([{ eof: true, next_offset: 1, data_base64: 'eA==' }, { sha256: 'f'.repeat(64) }, { size: content.length + 1 }, { occurrence_id: 'other' }, { output_index: 1 }, { workflow_run_id: 'other' }, { draft_only: false }, { next_offset: 0 }, { eof: false }, { preview_mode: 'download_only' }])('does not expose partial or substituted bytes %j', async patch => {
    const request = vi.fn(async () => ({ ...part(), ...patch })) as unknown as RuntimeRequest
    await expect(readScheduledDraft(request, 'session', target)).rejects.toThrow()
  })
  it('detects corrupt actual bytes even when the metadata still carries the approved digest', async () => {
    const request = vi.fn(async () => ({ ...part(), data_base64: btoa('x'.repeat(content.length)) })) as unknown as RuntimeRequest
    await expect(readScheduledDraft(request, 'session', target)).rejects.toThrow('digest mismatch')
  })
  it('aborts before requesting another chunk after the scope is discarded', async () => {
    const abort = new AbortController(), request = vi.fn(async () => { abort.abort();

 return part(0, 7) }) as unknown as RuntimeRequest

    await expect(readScheduledDraft(request, 'session', target, abort.signal)).rejects.toThrow()
    expect(request).toHaveBeenCalledTimes(1)
  })
  it('binds a new canonical proposal and publication to the complete fetched private bytes', async () => {
    const request = vi.fn(async () => part()) as unknown as RuntimeRequest, value = await readScheduledDraft(request, 'session', target), prepared = proposal()
    expect(() => verifyScheduledProposal(prepared, value)).not.toThrow()
    expect(() => verifyScheduledProposal({ ...prepared, artifact_id: target.output.artifact_id }, value)).toThrow()
    expect(() => verifyScheduledProposal({ ...prepared, size: prepared.size - 1 }, value)).toThrow()
    expect(() => verifyScheduledProposal({ ...prepared, parent_version: 1 }, value)).toThrow()
    expect(() => verifyScheduledProposal({ ...prepared, expires_at: 1 }, value)).toThrow()
    const published: ArtifactPublishResult = { ...prepared, disposition: 'canonical', head_version: 1, validation_status: 'passed', approval_status: 'approved' }
    expect(() => verifyScheduledPublication(published, prepared)).not.toThrow()
    expect(() => verifyScheduledPublication({ ...published, artifact_id: target.output.artifact_id }, prepared)).toThrow()
  })
  it('preserves unresolved occurrence evidence as inspectable without claiming a successful production', () => {
    const record = { project_id: 'p', schedule_id: 'scheduled', definition: scheduledDraftDefinition(options()), state: 'paused', revision: 2, version: 1, remaining_checks: 1, history_truncated: true, occurrences: [{ occurrence_id: 'occ', state: 'outcome_unknown', result: { workflow_run_id: 'wrun', draft_only: true, publication_state: 'reconciliation_required', outputs: [{ ...target.output, review_method: 'runtime.schedule.output.get' }] } }] }
    const result = scheduledDraftSnapshot({ record_json: JSON.stringify(record) }, 'p', 'scheduled')
    expect(result.occurrences[0].state).toBe('outcome_unknown'); expect(result.history_truncated).toBe(true)
    expect(() => scheduledDraftSnapshot({ record_json: JSON.stringify(record) }, 'foreign', 'scheduled')).toThrow()
  })
})
