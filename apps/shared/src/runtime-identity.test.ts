import { describe, expect, it, vi } from 'vitest'

import type { RuntimeRequest } from './runtime-control.js'
import { inspectRuntimeIdentity, runtimeIdentityLines } from './runtime-identity.js'

describe('runtime identity inspection', () => {
  it('reports actual failed probes without leaking error payloads or fabricating connected state', async () => {
    const request = vi.fn(async () => { throw new Error('secret transport payload') }) as RuntimeRequest
    const view = await inspectRuntimeIdentity(request, 'live-a')
    expect(view.capabilities).toBeNull()
    expect(view.memory).toBeNull()
    expect(view.errors).toHaveLength(2)
    expect(runtimeIdentityLines(view).join('\n')).not.toContain('secret transport payload')
  })
  it('keeps primary outage and isolated built-in memory distinct without a fallback selector', async () => {
    const request = vi.fn(async (method: string) => {
      if (method === 'runtime.capabilities') {throw new Error('unsupported')}

      return {
        health: { backend: 'personal_mcp', status: 'unconfigured', reason_code: 'binding_missing', supported_operations: [] },
        capabilities: { backend: 'personal_mcp', recall: false, write: false, supersede: false, delete: false, export: false, session_ingest: false }
      }
    }) as RuntimeRequest

    const lines = runtimeIdentityLines(await inspectRuntimeIdentity(request, 'live-a')).join('\n')
    expect(lines).toContain('primary personal MCP harness')
    expect(lines).toContain('unconfigured')
    expect(lines).toContain('no fallback')
    expect(lines).not.toContain('isolated built-in agent memory')
  })
})
