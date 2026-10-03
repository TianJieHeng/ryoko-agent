import { describe, expect, it, vi } from 'vitest'

import type { RuntimeRequest } from './runtime-control.js'
import { RuntimeFeatureSession } from './runtime-feature-session.js'

describe('feature inspector lifecycle', () => {
  it('blocks double submission, preserves unknown outcome, and does not retry a write', async () => {
    let reject!: (error: Error) => void
    const run = vi.fn(() => new Promise<string>((_, fail) => { reject = fail }))
    const session = new RuntimeFeatureSession(vi.fn() as RuntimeRequest, 'a')
    const feature = { label: 'test', help: '', run }
    const pending = session.run(feature, 'write')
    await session.run(feature, 'write')
    expect(run).toHaveBeenCalledTimes(1)
    reject(new Error('Connection lost'))
    await pending
    expect(session.getState().error).toContain('No automatic retry')
  })
  it('closing the view clears private output and ignores late replies without claiming backend cancellation', async () => {
    let resolve!: (value: string) => void
    const session = new RuntimeFeatureSession(vi.fn() as RuntimeRequest, 'a')
    const pending = session.run({ label: 'memory', help: '', run: () => new Promise(done => { resolve = done }) }, 'list')
    session.close()
    resolve('Private agent A record')
    await pending
    expect(session.getState()).toEqual({ busy: false, output: '', error: null })
  })
})

it('rejects malformed nontext results without leaking an object into the renderer', async () => {
  const session = new RuntimeFeatureSession(vi.fn() as RuntimeRequest, 'a')
  await session.run({ label: 'malformed', help: '', run: async () => ({ private: 'data' } as unknown as string) }, 'inspect')
  expect(session.getState().output).toBe('')
  expect(session.getState().error).toContain('Invalid runtime display response')
})
