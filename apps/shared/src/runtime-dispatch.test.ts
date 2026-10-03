import { expect, it, vi } from 'vitest'

import type { RuntimeRequest } from './runtime-control.js'
import { runtimeFeatures } from './runtime-features.js'
it('never dispatches prototype property names as runtime actions', async () => {
  const request = vi.fn(async () => { throw new Error('No RPC permitted') }) as RuntimeRequest

  for (const feature of Object.values(runtimeFeatures)) {
    for (const action of ['constructor', 'toString', '__proto__']) {expect(typeof await feature.run(action, request, 'owned')).toBe('string')}
  }

  expect(request).not.toHaveBeenCalled()
})
