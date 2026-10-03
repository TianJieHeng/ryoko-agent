// @vitest-environment jsdom
import { createHash, webcrypto } from 'node:crypto'

import { act, cleanup, fireEvent, render, type RenderResult, waitFor, within } from '@testing-library/react'
import { StrictMode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { RuntimeRequest } from '../../../../shared/src/runtime-control'

vi.mock('@/i18n', () => ({ useI18n: () => ({ locale: 'en', t: { common: { confirm: 'Confirm', cancel: 'Cancel', loading: 'Loading', done: 'Done', close: 'Close' }, errors: { genericFailure: 'Failed' } } }) }))
import { ScheduledDraftPanel } from './scheduled-draft-panel'

const source = '# Exact retained draft\n<script>untrusted()</script>\n', content = new TextEncoder().encode(source), sha256 = createHash('sha256').update(content).digest('hex')
const workflow = { project_id: 'p', workflow_id: 'workflow', version: 3, revision: 4, state: 'approved', sha256: 'a'.repeat(64), active_version: 3, head_revision: 4, evaluation_ref: 'evaluation', definition_json: JSON.stringify({ input_schema: { type: 'object', additionalProperties: false, properties: { topic: { type: 'string' }, source: { type: 'string' } }, required: ['topic', 'source'] }, steps: [{ step_id: 'render', kind: 'render_markdown', parameters: { template: '# ${input.topic}\n${input.source}' } }] }) }
const definition = () => ({ project_id: 'p', schedule_id: 'schedule', version: 1, kind: 'workflow_draft', expires_at: Date.now() / 1000 + 3600, timezone: 'Etc/UTC', trigger: { kind: 'interval', seconds: 60 }, specification: { workflow_ref: { workflow_id: 'workflow', version: 3, sha256: workflow.sha256 }, parameters: { topic: 'Fixed topic' }, source_bindings: [{ parameter: 'source', artifact_id: 'local-source' }], destination: { kind: 'project_artifact_drafts', project_id: 'p' } } })
const occurrence = { occurrence_id: 'occ', state: 'completed', result: { draft_only: true, publication_state: 'human_review_required', workflow_run_id: 'wrun', mission_id: 'mission', source_refs: [{ artifact_id: 'local-source', version: 2, sha256: 'b'.repeat(64) }], parameters_sha256: 'c'.repeat(64), bytes_read: 8, bytes_produced: content.length, outputs: [{ output_index: 0, artifact_id: 'private-result', version: 1, sha256, size: content.length, mime: 'text/markdown', name: 'summary', step_id: 'render', review_method: 'runtime.schedule.output.get' }] } }
const snapshot = () => ({ schedule_id: 'schedule', project_id: 'p', version: 1, revision: 1, state: 'paused', definition: definition(), health: 'healthy', last_success: null, next_due: Date.now() / 1000 + 60, last_error: null, remaining_checks: 2, occurrences: [occurrence], history_truncated: true })
const proposal = () => ({ request_id: 'scheduled-review', project_id: 'p', artifact_id: 'new-artifact', version: 1, sha256, size: content.length, mime: 'text/markdown', parent_version: null, expected_head_version: null, action_digest: 'd'.repeat(64), approval_id: 'approval', approval_digest: 'e'.repeat(64), expires_at: Date.now() / 1000 + 120 })

function gateway() {
  let schedule = snapshot()

  return vi.fn(async (method: string, raw: unknown): Promise<unknown> => {
    const params = raw as Record<string, unknown>

    if (method === 'runtime.workflow.get') { return { workflow } }

    if (method === 'runtime.schedule.create') { schedule = { ...schedule, definition: JSON.parse(String(params.definition_json)), occurrences: [] };

 return { record_json: JSON.stringify(schedule) } }

    if (method === 'runtime.schedule.get') { return { record_json: JSON.stringify(schedule) } }

    if (method === 'runtime.schedule.update') { schedule = { ...schedule, state: String(params.state), revision: Number(params.expected_revision) + 1 };

 return { record_json: JSON.stringify(schedule) } }

    if (method === 'runtime.schedule.grant') { return { record_json: JSON.stringify({ grant_id: 'grant', target_digest: 'f'.repeat(64), expires_at: params.expires_at, remaining: params.max_fires, external_actions: false }) } }

    if (method === 'runtime.schedule.output.get') {
      const offset = Number(params.offset), end = Math.min(content.length, offset + 11)

      return { project_id: 'p', artifact_id: 'private-result', version: 1, sha256, size: content.length, mime: 'text/markdown', offset, next_offset: end, data_base64: btoa(String.fromCharCode(...content.slice(offset, end))), eof: end === content.length, preview_mode: 'plain_text', draft_only: true, occurrence_id: 'occ', occurrence_state: 'completed', output_index: 0, workflow_run_id: 'wrun' }
    }

    if (method === 'runtime.schedule.output.prepare') { return proposal() }

    if (method === 'runtime.schedule.output.publish') { return { ...proposal(), disposition: 'canonical', head_version: 1, validation_status: 'passed', approval_status: 'approved' } }

    if (['runtime.artifact.status', 'runtime.artifact.cancel'].includes(method)) { return { command_id: params.command_id, run_id: 'control', status: method.endsWith('cancel') ? 'cancelled' : 'claimed', owner_live: false, expires_at: null, result: null } }
    throw new Error(`Unexpected ${method}`)
  })
}

function enter(view: RenderResult, label: string, value: string) { fireEvent.change(view.getByLabelText(label), { target: { value } }) }

async function inspect(view: RenderResult) {
  enter(view, 'Project ID', 'p'); enter(view, 'Schedule ID', 'schedule'); fireEvent.click(view.getByRole('button', { name: 'Inspect schedule' }))
  await view.findByText(/Schedule schedule v1:/)
}

async function read(view: RenderResult) {
  fireEvent.click(view.getByRole('button', { name: /Read exact output:/ }))
  await view.findByLabelText('Complete scheduled output')
}

async function prepare(view: RenderResult) {
  fireEvent.click(view.getByRole('button', { name: 'Prepare output for approval' }))
  await view.findByRole('button', { name: 'Publish exact reviewed bytes' })
}

function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>(done => { resolve = done });

 return { promise, resolve } }

beforeEach(() => { vi.stubGlobal('crypto', webcrypto); URL.createObjectURL = vi.fn(() => 'blob:verified'); URL.revokeObjectURL = vi.fn() })
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('scheduled workflow draft consumer', () => {
  it('keeps complete-byte reads usable through the app StrictMode mount cleanup cycle', async () => {
    const rpc = gateway(), view = render(<StrictMode><ScheduledDraftPanel connected request={rpc as RuntimeRequest} sessionId="owned" /></StrictMode>)
    await inspect(view); await read(view)
    expect((await view.findByRole('link', { name: 'Save verified draft' })).getAttribute('href')).toBe('blob:verified')
    view.unmount()
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:verified')
  })
  it('inspects an actual workflow pin, confirms paused creation, then separately grants and activates', async () => {
    const rpc = gateway(), view = render(<ScheduledDraftPanel connected request={rpc as RuntimeRequest} sessionId="owned" />)
    expect(rpc).not.toHaveBeenCalled()
    enter(view, 'Project ID', 'p'); enter(view, 'Workflow ID', 'workflow'); enter(view, 'Version', '3'); enter(view, 'Schedule ID', 'schedule')
    fireEvent.click(view.getByRole('button', { name: 'Inspect workflow' }))
    await view.findByLabelText('topic (string, required)')
    enter(view, 'topic (string, required)', 'Fixed topic'); enter(view, 'source local source artifact ID (optional)', 'local-source')
    fireEvent.click(view.getByText('Finite schedule and input binding review'))
    enter(view, 'First check ISO with offset', new Date(Date.now() + 60000).toISOString()); enter(view, 'Schedule expiry ISO with offset', new Date(Date.now() + 7200000).toISOString())
    enter(view, 'Maximum checks', '2')
    fireEvent.click(view.getByRole('button', { name: 'Review paused draft schedule' }))
    expect(view.getByLabelText('Exact scheduled action review').textContent).toContain(workflow.sha256)
    expect(rpc).toHaveBeenCalledTimes(1)
    fireEvent.click(within(view.getByRole('dialog')).getByRole('button', { name: 'Create paused schedule' }))
    await view.findByText(/Created paused/)
    const create = rpc.mock.calls.find(([method]) => method === 'runtime.schedule.create')![1] as { definition_json: string }
    expect(JSON.parse(create.definition_json)).toMatchObject({ kind: 'workflow_draft', specification: { workflow_ref: { workflow_id: 'workflow', version: 3, sha256: workflow.sha256 }, parameters: { topic: 'Fixed topic' }, source_bindings: [{ parameter: 'source', artifact_id: 'local-source' }] } })
    expect(view.getByRole('button', { name: 'Activate schedule' }).hasAttribute('disabled')).toBe(true)
    enter(view, 'Grant expiry ISO with offset', new Date(Date.now() + 3600000).toISOString()); enter(view, 'Grant maximum fires', '2')
    fireEvent.click(view.getByRole('button', { name: 'Review bounded grant' }))
    expect(rpc).toHaveBeenCalledTimes(2)
    fireEvent.click(within(view.getByRole('dialog')).getByRole('button', { name: 'Grant draft production' }))
    await view.findByText(/Draft-only grant grant/)
    expect(rpc.mock.calls.some(([method]) => method === 'runtime.schedule.update')).toBe(false)
    fireEvent.click(view.getByRole('button', { name: 'Activate schedule' }))
    fireEvent.click(within(view.getByRole('dialog')).getByRole('button', { name: 'Activate schedule' }))
    await view.findByText(/Schedule active\./)
    expect(rpc.mock.calls.map(([method]) => method)).toEqual(['runtime.workflow.get', 'runtime.schedule.create', 'runtime.schedule.grant', 'runtime.schedule.update'])
  })
  it('reads complete real bytes before download, prepares separately, and confirms exact publication without source or mission reruns', async () => {
    const rpc = gateway(), view = render(<ScheduledDraftPanel connected request={rpc as RuntimeRequest} sessionId="owned" />)
    await inspect(view); await read(view)
    const preview = view.getByLabelText('Complete scheduled output')
    expect(preview.textContent).toBe(source); expect(preview.querySelector('script')).toBeNull()
    expect((await view.findByRole('link', { name: 'Save verified draft' })).getAttribute('href')).toBe('blob:verified')
    expect(view.getByText(/history is truncated/)).toBeTruthy()
    expect(rpc.mock.calls.some(([method]) => method.includes('prepare'))).toBe(false)
    await prepare(view)
    const params = rpc.mock.calls.find(([method]) => method === 'runtime.schedule.output.prepare')![1]
    expect(params).toMatchObject({ session_id: 'owned', project_id: 'p', schedule_id: 'schedule', occurrence_id: 'occ', output_index: 0, expected_sha256: sha256 })
    expect(rpc.mock.calls.some(([method]) => method.includes('publish'))).toBe(false)
    fireEvent.click(view.getByRole('button', { name: 'Publish exact reviewed bytes' }))
    expect(view.getByLabelText('Publication confirmation bytes').textContent).toBe(source)
    const confirm = within(view.getByRole('dialog')).getByRole('button', { name: 'Publish exact reviewed bytes' })
    fireEvent.click(confirm); fireEvent.click(confirm)
    await view.findByText(/Published new-artifact@1/)
    expect(rpc.mock.calls.filter(([method]) => method === 'runtime.schedule.output.publish')).toHaveLength(1)
    expect(rpc).toHaveBeenCalledWith('runtime.schedule.output.publish', { ...(params as object), approval_id: 'approval', approval_digest: 'e'.repeat(64) })
    expect(rpc.mock.calls.every(([method]) => ['runtime.schedule.get', 'runtime.schedule.output.get', 'runtime.schedule.output.prepare', 'runtime.schedule.output.publish'].includes(method))).toBe(true)
  })
  it('rejects truncation without offering partial previews, downloads or approval', async () => {
    const rpc = gateway(), base = rpc.getMockImplementation()!
    rpc.mockImplementation(async (method, params) => method === 'runtime.schedule.output.get' ? { ...(await base(method, params) as object), eof: true } : base(method, params))
    const view = render(<ScheduledDraftPanel connected request={rpc as RuntimeRequest} sessionId="owned" />)
    await inspect(view); fireEvent.click(view.getByRole('button', { name: /Read exact output:/ }))
    await view.findByRole('alert')
    expect(view.queryByLabelText('Complete scheduled output')).toBeNull(); expect(view.queryByRole('link')).toBeNull(); expect(URL.createObjectURL).not.toHaveBeenCalled()
  })
  it('keeps unknown publication on its original command and prevents duplicate preparation or retry', async () => {
    const rpc = gateway(), base = rpc.getMockImplementation()!
    rpc.mockImplementation(async (method, params) => { if (method === 'runtime.schedule.output.publish') { throw new Error('connection lost') };

 return base(method, params) })
    const view = render(<ScheduledDraftPanel connected request={rpc as RuntimeRequest} sessionId="owned" />)
    await inspect(view); await read(view); await prepare(view)
    const original = rpc.mock.calls.find(([method]) => method === 'runtime.schedule.output.prepare')![1] as { command_id: string }
    fireEvent.click(view.getByRole('button', { name: 'Publish exact reviewed bytes' })); fireEvent.click(within(view.getByRole('dialog')).getByRole('button', { name: 'Publish exact reviewed bytes' }))
    await view.findByRole('alert')
    expect(view.queryByRole('button', { name: 'Publish exact reviewed bytes' })).toBeNull()
    expect(view.getByRole('button', { name: 'Prepare output for approval' }).hasAttribute('disabled')).toBe(true)
    fireEvent.click(view.getByRole('button', { name: 'Inspect original command' }))
    await view.findByText(/Original command .*claimed/)
    expect(rpc).toHaveBeenCalledWith('runtime.artifact.status', { session_id: 'owned', schema_version: 1, command_id: original.command_id })
    fireEvent.click(view.getByRole('button', { name: 'Cancel original command' }))
    await view.findByText(/Original command .*cancelled/)
    expect(rpc.mock.calls.filter(([method]) => method === 'runtime.schedule.output.prepare')).toHaveLength(1)
    expect(rpc.mock.calls.filter(([method]) => method === 'runtime.schedule.output.publish')).toHaveLength(1)
  })
  it.each(['session', 'transport', 'disconnect', 'unmount'] as const)('fences a late preparation after %s changes and privately retains the original identity', async mode => {
    const rpc = gateway(), base = rpc.getMockImplementation()!, late = deferred<unknown>(), fresh = gateway()
    rpc.mockImplementation(async (method, params) => method === 'runtime.schedule.output.prepare' ? late.promise : base(method, params))
    let view = render(<ScheduledDraftPanel connected request={rpc as RuntimeRequest} sessionId="owned" />)
    await inspect(view); await read(view)
    const button = view.getByRole('button', { name: 'Prepare output for approval' }); fireEvent.click(button); fireEvent.click(button)
    const original = rpc.mock.calls.find(([method]) => method === 'runtime.schedule.output.prepare')![1] as { command_id: string }
    expect(rpc.mock.calls.filter(([method]) => method === 'runtime.schedule.output.prepare')).toHaveLength(1)

    if (mode === 'unmount') { view.unmount(); view = render(<ScheduledDraftPanel connected request={fresh as RuntimeRequest} sessionId="other" />) }
    else { view.rerender(<ScheduledDraftPanel connected={mode !== 'disconnect'} request={(mode === 'transport' ? fresh : rpc) as RuntimeRequest} sessionId={mode === 'session' ? 'other' : 'owned'} />) }

    await act(async () => late.resolve(proposal()))
    expect(view.queryByLabelText('Complete scheduled output')).toBeNull(); expect(view.queryByRole('button', { name: 'Publish exact reviewed bytes' })).toBeNull()

    if (mode !== 'disconnect') { expect(view.container.textContent).not.toContain(original.command_id); expect((view.getByLabelText('Project ID') as HTMLInputElement).value).toBe('') }
    view.rerender(<ScheduledDraftPanel connected request={rpc as RuntimeRequest} sessionId="owned" />)
    expect(view.container.textContent).toContain(original.command_id)
    fireEvent.click(view.getByRole('button', { name: 'Inspect original command' }))
    await waitFor(() => expect(rpc).toHaveBeenCalledWith('runtime.artifact.status', { session_id: 'owned', schema_version: 1, command_id: original.command_id }))
    expect(fresh).not.toHaveBeenCalled()
  })
  it('invalidates visible approval after input changes and hides original IDs from another project', async () => {
    const rpc = gateway(), view = render(<ScheduledDraftPanel connected request={rpc as RuntimeRequest} sessionId="owned" />)
    await inspect(view); await read(view); await prepare(view)
    const original = rpc.mock.calls.find(([method]) => method === 'runtime.schedule.output.prepare')![1] as { command_id: string }
    fireEvent.click(view.getByRole('button', { name: 'Publish exact reviewed bytes' }))
    enter(view, 'Project ID', 'foreign')
    expect(view.queryByRole('dialog')).toBeNull(); expect(view.queryByLabelText('Complete scheduled output')).toBeNull()
    expect(view.container.textContent).not.toContain(original.command_id); expect(view.container.textContent).not.toContain('local-source')
    expect(view.queryByRole('button', { name: 'Cancel original command' })).toBeNull()
    enter(view, 'Project ID', 'p')
    expect(view.container.textContent).toContain(original.command_id)
    expect(rpc.mock.calls.some(([method]) => method === 'runtime.schedule.output.publish')).toBe(false)
  })
  it('closes an unsubmitted confirmation without creating or granting anything', async () => {
    const rpc = gateway(), view = render(<ScheduledDraftPanel connected request={rpc as RuntimeRequest} sessionId="owned" />)
    await inspect(view); enter(view, 'Grant expiry ISO with offset', new Date(Date.now() + 600000).toISOString())
    fireEvent.click(view.getByRole('button', { name: 'Review bounded grant' }))
    fireEvent.click(within(view.getByRole('dialog')).getByRole('button', { name: 'Cancel' }))
    expect(view.queryByRole('dialog')).toBeNull(); expect(rpc).toHaveBeenCalledTimes(1)
  })
  it('allows retained unknown output inspection but disables preparation until completed evidence exists', async () => {
    const rpc = gateway(), base = rpc.getMockImplementation()!
    rpc.mockImplementation(async (method, params) => {
      if (method === 'runtime.schedule.get') { const row = snapshot(); row.occurrences = [{ ...occurrence, state: 'outcome_unknown', result: { ...occurrence.result, publication_state: 'reconciliation_required' } }];

 return { record_json: JSON.stringify(row) } }

      const value = await base(method, params)

      return method === 'runtime.schedule.output.get' ? { ...(value as object), occurrence_state: 'outcome_unknown' } : value
    })
    const view = render(<ScheduledDraftPanel connected request={rpc as RuntimeRequest} sessionId="owned" />)
    await inspect(view); await read(view)
    expect(view.getByRole('button', { name: 'Prepare output for approval' }).hasAttribute('disabled')).toBe(true)
    expect(rpc.mock.calls.some(([method]) => method.includes('prepare'))).toBe(false)
  })
})
