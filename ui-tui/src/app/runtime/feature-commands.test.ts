import { describe, expect, it, vi } from 'vitest'

import type { SlashRunCtx } from '../slash/types.js'

import { runRuntimeFeature } from './feature-commands.js'

describe('runtime feature command routing', () => {
  it('uses the owned session and suppresses delayed memory from an older selection', async () => {
    let resolve!: (value: unknown) => void
    const request = vi.fn(() => new Promise(done => { resolve = done }))
    const sys = vi.fn()
    let stale = false
    const ctx = { sid: 'a', gateway: { gw: { request } }, stale: () => stale, transcript: { sys } } as unknown as SlashRunCtx
    expect(runRuntimeFeature('memory', 'list project-a', ctx)).toBe(true)
    expect(request).toHaveBeenCalledWith('runtime.memory.records.list', { session_id: 'a', schema_version: 1, project_id: 'project-a' })
    stale = true
    resolve({ revision: 2, records: [], has_more: false })
    await new Promise(done => setTimeout(done, 0))
    expect(sys).not.toHaveBeenCalled()
  })
})

it('rejects inherited property names without RPC or transcript output', () => {
  const request = vi.fn(), sys = vi.fn()
  const ctx = { sid: 'a', gateway: { gw: { request } }, stale: () => false, transcript: { sys } } as unknown as SlashRunCtx

  for (const name of ['constructor', 'toString', '__proto__']) {expect(runRuntimeFeature(name, 'help', ctx)).toBe(false)}
  expect(request).not.toHaveBeenCalled()
  expect(sys).not.toHaveBeenCalled()
})
