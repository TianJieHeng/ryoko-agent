// @vitest-environment jsdom
import { webcrypto } from 'node:crypto'

import { act, cleanup, fireEvent, render, type RenderResult, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { OutputContextResult, OutputControlParams, OutputControlResult } from '../../../../shared/src/gateway-contract.generated'
import type { RuntimeRequest } from '../../../../shared/src/runtime-control'

vi.mock('@/i18n', () => ({ useI18n: () => ({ locale: 'en', t: { common: { confirm: 'Confirm', cancel: 'Cancel', loading: 'Loading', done: 'Done', close: 'Close' }, errors: { genericFailure: 'Failed' } } }) }))
import { OutputInfluencePanel } from './output-influence-panel'

const context: OutputContextResult = { run_id: 'output-run', backend: 'builtin', namespace_id: 'owned-namespace', project_id: 'p', references: [{ record_id: 'pref', version: 7, source_ref: '<script>unsafe()</script>', namespace_id: 'owned-namespace', deletion_state: 'present', scope: 'project:p' }], context_packet_sha256: 'a'.repeat(64), immutable_prefix_sha256: 'b'.repeat(64), context_sha256: 'c'.repeat(64), coverage: 'fresh_memory_context_only', controls: [], degraded: false, state: 'supplied_to_provider_call', latest: true, causal_explanation: false, historical_context_enumerated: false }

function receipt(p: OutputControlParams): OutputControlResult {
  return { control_id: p.control_id, run_id: p.run_id, action: p.action, scope: p.scope, project_id: p.project_id ?? null, status: p.scope === 'response' ? 'queued_next_turn' : 'memory_acknowledged', acknowledged_version: p.scope === 'response' ? null : 8, applied_run_id: null, current_output_changed: false, current_run_application: 'late_not_applied', deletion_semantics: p.action === 'remove' ? 'tombstone_not_physical_erasure' : 'none' }
}

function gateway() {
  let saved: OutputControlParams | null = null

  return vi.fn(async (method: string, params: unknown): Promise<unknown> => {
    if (method === 'runtime.memory.output.list') { return { outputs: [{ run_id: context.run_id, context_sha256: context.context_sha256 }], backend: 'builtin', complete: false, unavailable_reason: null } }

    if (method === 'runtime.memory.output.get') { return context }

    if (method === 'runtime.memory.output.control') { saved = params as OutputControlParams;

 return receipt(saved) }

    if (!saved) { throw new Error('No saved control') }

    return receipt(saved)
  })
}

async function select(view: RenderResult) {
  fireEvent.click(view.getByRole('button', { name: 'List output receipts' }))
  fireEvent.click(await view.findByRole('button', { name: 'Inspect output: output-run' }))
  fireEvent.click(await view.findByRole('button', { name: 'Select reference: pref@7' }))
}

function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>(done => { resolve = done });

 return { promise, resolve } }

beforeEach(() => { vi.stubGlobal('crypto', webcrypto) })
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('output influence panel', () => {
  it('shows actual supplied references and coverage, then confirms exact scoped identity', async () => {
    const rpc = gateway(), view = render(<OutputInfluencePanel connected request={rpc as RuntimeRequest} sessionId="owned" />)
    await select(view)
    expect(view.getByText(/Coverage: fresh_memory_context_only/).textContent).toContain('not a causal explanation')
    expect(view.getByText(/Source:/).querySelector('script')).toBeNull()
    fireEvent.click(view.getByRole('button', { name: 'Correct' })); fireEvent.click(view.getByRole('button', { name: 'This project' }))
    fireEvent.change(view.getByLabelText('Replacement text'), { target: { value: 'Use metric units' } })
    fireEvent.click(view.getByRole('button', { name: 'Review exact change' }))
    expect(view.getByRole('dialog').textContent).toContain('Record pref@7 · namespace owned-namespace')
    expect(rpc.mock.calls.some(([method]) => method === 'runtime.memory.output.control')).toBe(false)
    fireEvent.click(view.getByRole('button', { name: 'Confirm scoped change' }))
    await waitFor(() => expect(rpc).toHaveBeenCalledWith('runtime.memory.output.control', expect.objectContaining({ session_id: 'owned', run_id: 'output-run', context_sha256: context.context_sha256, record_id: 'pref', expected_version: 7, namespace_id: 'owned-namespace', action: 'correct', scope: 'project', project_id: 'p', content: 'Use metric units' })))
    expect((await view.findByText(/memory_acknowledged:/)).textContent).toContain('model application is not yet confirmed')
    expect(view.getByText(/memory_acknowledged:/).textContent).toContain('Acknowledged version: 8')
  })
  it('invalidates open review after edits and does not promote a response-only correction', async () => {
    const rpc = gateway(), view = render(<OutputInfluencePanel connected request={rpc as RuntimeRequest} sessionId="owned" />)
    await select(view); fireEvent.click(view.getByRole('button', { name: 'Correct' }))
    fireEvent.change(view.getByLabelText('Replacement text'), { target: { value: 'One response' } }); fireEvent.click(view.getByRole('button', { name: 'Review exact change' }))
    fireEvent.change(view.getByLabelText('Replacement text'), { target: { value: 'Revised response' } })
    expect(view.queryByRole('dialog')).toBeNull()
    fireEvent.click(view.getByRole('button', { name: 'Review exact change' })); fireEvent.click(view.getByRole('button', { name: 'Confirm scoped change' }))
    await view.findByText(/queued_next_turn:/)
    expect(rpc).toHaveBeenCalledWith('runtime.memory.output.control', expect.objectContaining({ scope: 'response', project_id: null, content: 'Revised response' }))
  })
  it('retains unknown mutation identity, blocks duplicate writes and recovers only by exact control.get', async () => {
    const rpc = gateway(), base = rpc.getMockImplementation()!
    let original: OutputControlParams
    rpc.mockImplementation((method, params) => {
      if (method === 'runtime.memory.output.control') { original = params as OutputControlParams;

 return Promise.reject(new Error('Connection lost after send')) }

      if (method === 'runtime.memory.output.control.get') { return Promise.resolve({ ...receipt(original), status: 'mutation_pending' }) }

      return base(method, params)
    })
    const view = render(<OutputInfluencePanel connected request={rpc as RuntimeRequest} sessionId="owned" />)
    await select(view); fireEvent.click(view.getByRole('button', { name: 'Review exact change' }))
    const confirm = view.getByRole('button', { name: 'Confirm scoped change' })
    fireEvent.click(confirm); fireEvent.click(confirm)
    await view.findByText(/Outcome unknown/)
    expect(rpc.mock.calls.filter(([method]) => method === 'runtime.memory.output.control')).toHaveLength(1)
    expect(view.getByRole('button', { name: 'Review exact change' }).hasAttribute('disabled')).toBe(true)
    fireEvent.click(view.getByRole('button', { name: 'Inspect control status' }))
    await view.findByText(/mutation_pending:/)
    expect(rpc).toHaveBeenCalledWith('runtime.memory.output.control.get', { session_id: 'owned', schema_version: 1, control_id: original!.control_id })
    expect(view.queryByRole('button', { name: 'Review another change' })).toBeNull()
  })
  it('does not expose built-in mutation controls for opaque personal context', async () => {
    const rpc = vi.fn(async () => ({ outputs: [], backend: 'personal_mcp', complete: false, unavailable_reason: 'Harness not configured' }))
    const view = render(<OutputInfluencePanel connected request={rpc as RuntimeRequest} sessionId="primary" />)
    fireEvent.click(view.getByRole('button', { name: 'List output receipts' }))
    expect((await view.findByText(/personal context harness is opaque/)).textContent).toContain('built-in memory controls do not apply')
    expect(view.queryByRole('button', { name: 'Correct' })).toBeNull()
  })
  it('acknowledges tombstone semantics and never claims physical erasure', async () => {
    const rpc = gateway(), view = render(<OutputInfluencePanel connected request={rpc as RuntimeRequest} sessionId="owned" />)
    await select(view); fireEvent.click(view.getByRole('button', { name: 'Remove record' }))
    expect(view.getByRole('button', { name: 'Review exact change' }).hasAttribute('disabled')).toBe(true)
    fireEvent.click(view.getByRole('button', { name: 'This project' })); fireEvent.click(view.getByRole('button', { name: 'Review exact change' })); fireEvent.click(view.getByRole('button', { name: 'Confirm scoped change' }))
    expect((await view.findByText(/memory_acknowledged:/)).textContent).toContain('not physically erased')
  })
  it.each(['session', 'transport', 'unmount', 'disconnect'] as const)('fences late output reads after %s changes', async mode => {
    const rpc = gateway(), base = rpc.getMockImplementation()!, delayed = deferred<unknown>()
    rpc.mockImplementation((method, params) => method === 'runtime.memory.output.get' ? delayed.promise : base(method, params))
    const view = render(<OutputInfluencePanel connected request={rpc as RuntimeRequest} sessionId="owned" />)
    fireEvent.click(view.getByRole('button', { name: 'List output receipts' })); fireEvent.click(await view.findByRole('button', { name: 'Inspect output: output-run' }))

    if (mode === 'unmount') { view.unmount() }
    else { view.rerender(<OutputInfluencePanel connected={mode !== 'disconnect'} request={(mode === 'transport' ? gateway() : rpc) as RuntimeRequest} sessionId={mode === 'session' ? 'foreign' : 'owned'} />) }

    if (mode === 'disconnect') { view.rerender(<OutputInfluencePanel connected request={rpc as RuntimeRequest} sessionId="owned" />) }
    await act(async () => delayed.resolve(context))
    expect(view.queryByRole('button', { name: 'Select reference: pref@7' })).toBeNull()
  })
  it('rejects a context digest changed since list and offers a fresh inspection', async () => {
    const rpc = gateway(), base = rpc.getMockImplementation()!
    rpc.mockImplementation((method, params) => method === 'runtime.memory.output.get' ? Promise.resolve({ ...context, context_sha256: 'f'.repeat(64) }) : base(method, params))
    const view = render(<OutputInfluencePanel connected request={rpc as RuntimeRequest} sessionId="owned" />)
    fireEvent.click(view.getByRole('button', { name: 'List output receipts' })); fireEvent.click(await view.findByRole('button', { name: 'Inspect output: output-run' }))
    expect((await view.findByRole('alert')).textContent).toContain('refresh the run list')
    expect(view.queryByRole('button', { name: 'Select reference: pref@7' })).toBeNull()
  })
  it('shows next-run supply and expiration only after explicit status acknowledgments', async () => {
    const rpc = gateway(), base = rpc.getMockImplementation()!
    let original: OutputControlParams, inspection = 0
    rpc.mockImplementation((method, params) => {
      if (method === 'runtime.memory.output.control') { original = params as OutputControlParams }

      if (method === 'runtime.memory.output.control.get') { inspection++;

 return Promise.resolve({ ...receipt(original), status: inspection === 1 ? 'context_supplied' : 'expired', applied_run_id: 'later-run' }) }

      return base(method, params)
    })
    const view = render(<OutputInfluencePanel connected request={rpc as RuntimeRequest} sessionId="owned" />)
    await select(view); fireEvent.click(view.getByRole('button', { name: 'Review exact change' })); fireEvent.click(view.getByRole('button', { name: 'Confirm scoped change' }))
    await view.findByText(/queued_next_turn:/)
    fireEvent.click(view.getByRole('button', { name: 'Inspect control status' }))
    expect((await view.findByText(/context_supplied:/)).textContent).toContain('Supplied to run: later-run')
    fireEvent.click(view.getByRole('button', { name: 'Inspect control status' }))
    expect((await view.findByText(/expired:/)).textContent).toContain('not a durable preference')
    expect(rpc.mock.calls.filter(([method]) => method === 'runtime.memory.output.control')).toHaveLength(1)
  })
  it('retains in-flight control across A→B→A without showing the old record in B', async () => {
    const rpc = gateway(), base = rpc.getMockImplementation()!, delayed = deferred<unknown>()
    let original: OutputControlParams
    rpc.mockImplementation((method, params) => {
      if (method === 'runtime.memory.output.control') { original = params as OutputControlParams;

 return delayed.promise }

      if (method === 'runtime.memory.output.control.get') { return Promise.resolve(receipt(original)) }

      return base(method, params)
    })
    const view = render(<OutputInfluencePanel connected request={rpc as RuntimeRequest} sessionId="A" />)
    await select(view); fireEvent.click(view.getByRole('button', { name: 'Review exact change' })); fireEvent.click(view.getByRole('button', { name: 'Confirm scoped change' }))
    view.rerender(<OutputInfluencePanel connected request={rpc as RuntimeRequest} sessionId="B" />)
    expect(view.queryByText(/Source:/)).toBeNull()
    expect(view.queryByText(new RegExp(original!.control_id))).toBeNull()
    expect(view.queryByRole('button', { name: 'Select reference: pref@7' })).toBeNull()
    view.rerender(<OutputInfluencePanel connected request={rpc as RuntimeRequest} sessionId="A" />)
    await act(async () => delayed.resolve(receipt(original!)))
    expect(view.queryByText(/queued_next_turn:/)).toBeNull()
    expect(view.getByText(/Retained control/).textContent).toContain(original!.control_id)
    fireEvent.click(view.getByRole('button', { name: 'Inspect control status' }))
    await view.findByText(/queued_next_turn:/)
    expect(rpc).toHaveBeenCalledWith('runtime.memory.output.control.get', { session_id: 'A', schema_version: 1, control_id: original!.control_id })
  })

})
