import { describe, expect, it, vi } from 'vitest'

import type { MissionSnapshot, RuntimeCapabilities } from './gateway-contract.generated.js'
import { RuntimeControl, runtimeOperationBlock, type RuntimeRequest, runtimeWorkLabel } from './runtime-control.js'

const capabilities: RuntimeCapabilities = {
  schema_versions: [1], operations: [{ operation: 'cancel', accepts_commands: true, executes: true, effects_enabled: false }],
  strict_identity_required: true, durable_replay: true, max_events: 200,
  cursor_policy: 'snapshot_required_on_expired_or_unknown_cursor'
}

const snapshot: MissionSnapshot = {
  schema_version: 1, session_id: 'durable-a', revision: 3,
  state: { status: 'claimed', run_id: 'r', last_command_id: 'c', last_operation: 'submit' },
  outstanding_requests: [], artifacts: [], unresolved_effects: [], last_cursor: 'cursor-a', compatibility_status: 'native'
}

function harness() {
  const request = vi.fn(async (method: string) => {
    if (method === 'runtime.capabilities') {return capabilities}

    if (method === 'runtime.snapshot') {return snapshot}

    return { schema_version: 1, command_id: 'test', status: 'accepted', durable_revision: 4, run_id: 'r' }
  })

  return { request, control: new RuntimeControl(request as RuntimeRequest, () => 'test') }
}

describe('runtime controls', () => {
  it('isolates selection, preserves work on disconnect, and rejects stale capability responses', async () => {
    const { request, control } = harness()
    control.select('profile-a', 'live-a')
    await control.refresh()
    expect(runtimeWorkLabel(control.getState().snapshot)).toBe('Working')
    control.disconnected()
    expect(control.getState().transport).toBe('stale')
    expect(runtimeOperationBlock(control.getState(), 'cancel')).not.toBeNull()
    let resolve!: (value: RuntimeCapabilities) => void
    request.mockImplementationOnce(() => new Promise(done => { resolve = done }))
    const stale = control.refresh()
    control.select('profile-b', 'live-b')
    resolve(capabilities)
    await stale
    expect(control.getState().snapshot).toBeNull()
    expect(control.getState().capabilities).toBeNull()
  })

  it('deduplicates double clicks and retries unknown outcomes with identical revision and operation identity', async () => {
    const { request, control } = harness()
    control.select('profile-a', 'live-a')
    await control.refresh()
    request.mockImplementationOnce(async () => { throw new Error('Connection closed') })
    await control.command('cancel', { reason: 'Stop' })
    const pending = control.getState().pending
    expect(pending?.expected_revision).toBe(3)
    await expect(control.command('cancel', { reason: 'Stop' })).rejects.toThrow('pending command')
    await control.retry()
    const commands = request.mock.calls.filter(call => call[0] === 'runtime.command')
    expect(commands).toHaveLength(2)
    expect(commands[0]).toEqual(commands[1])
    expect(control.getState().pending).toBeNull()
    expect(control.getState().receipt?.status).toBe('accepted')
    expect(runtimeWorkLabel(control.getState().snapshot)).toBe('Working')
  })
})
