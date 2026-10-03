import { EventEmitter } from 'node:events'

import type { MissionSnapshot, RuntimeCapabilities } from '@hermes/shared/gateway-events'
import { describe, expect, it, vi } from 'vitest'

import { runtimeCommands } from '../slash/commands/runtime.js'
import type { SlashRunCtx } from '../slash/types.js'

const capabilities: RuntimeCapabilities = {
  schema_versions: [1], operations: [{ operation: 'cancel', accepts_commands: true, executes: true, effects_enabled: false }],
  strict_identity_required: true, durable_replay: true, max_events: 200,
  cursor_policy: 'snapshot_required_on_expired_or_unknown_cursor'
}

const snapshot: MissionSnapshot = {
  schema_version: 1, session_id: 'durable-a', revision: 3,
  state: { status: 'claimed', run_id: 'r', last_command_id: 'c', last_operation: 'submit' },
  outstanding_requests: [], artifacts: [], unresolved_effects: [], last_cursor: 'a', compatibility_status: 'native'
}

function harness() {
  const gw = Object.assign(new EventEmitter(), {
    request: vi.fn(async (method: string) => method === 'runtime.capabilities' ? capabilities : snapshot)
  })

  const sys = vi.fn()
  const context = { sid: 'live-a', gateway: { gw }, transcript: { sys }, stale: () => false, guardedErr: sys } as unknown as SlashRunCtx

  return { gw, sys, context }
}

describe('TUI runtime command', () => {
  it('uses the owned live session and renders work separately from connection failures', async () => {
    const { gw, sys, context } = harness()
    runtimeCommands[0].run('status', context, '/runtime status')
    await vi.waitFor(() => expect(sys).toHaveBeenCalled())
    expect(gw.request).toHaveBeenCalledWith('runtime.snapshot', { session_id: 'live-a', schema_version: 1 })
    expect(sys.mock.calls[0][0]).toContain('Working')
    gw.emit('exit', 1)
    runtimeCommands[0].run('cancel stop', context, '/runtime cancel stop')
    await vi.waitFor(() => expect(sys).toHaveBeenCalledTimes(2))
    expect(String(sys.mock.calls[1][0])).toContain('Refresh')
    expect(gw.request.mock.calls.some(call => call[0] === 'runtime.command')).toBe(false)
  })
  it('does not publish a delayed result into a different foreground conversation', async () => {
    const { sys, context } = harness()
    let stale = false
    context.stale = () => stale
    runtimeCommands[0].run('status', context, '/runtime status')
    stale = true
    await new Promise(resolve => setTimeout(resolve, 10))
    expect(sys).not.toHaveBeenCalled()
  })
  it('preserves exact whitespace inside structured artifact preparation content', async () => {
    const { gw, sys, context } = harness()
    const content = '# Draft\nA  deliberately   spaced line\n'
    gw.request.mockImplementation(async () => ({ request_id: 'req', project_id: 'p', artifact_id: 'a', version: 1, sha256: 'a'.repeat(64), size: content.length, mime: 'text/markdown', approval_id: 'ap', approval_digest: 'b'.repeat(64), expires_at: Date.now() / 1000 + 60 } as never))
    runtimeCommands[0].run(`artifact prepare ${JSON.stringify({ project_id: 'p', command_id: 'cmd', request_id: 'req', content })}`, context, '/runtime artifact prepare')
    await vi.waitFor(() => expect(sys).toHaveBeenCalled())
    expect(gw.request).toHaveBeenCalledWith('runtime.artifact.prepare', expect.objectContaining({ content }))
  })

})
