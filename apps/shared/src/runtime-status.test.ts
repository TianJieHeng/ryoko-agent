import { expect, it, vi } from 'vitest'

import type { RuntimeRequest } from './runtime-control.js'
import { inspectRuntimeStatus, runStatusCommand } from './runtime-status.js'

it('keeps queues scoped and reports outage instead of inventing an all-clear', async () => {
  const request = vi.fn(async (method: string) => {
    if (method === 'runtime.mission.list') {return { missions: [{ mission_id: 'a', project_id: 'p', state: 'ready_to_review' }, { mission_id: 'other', project_id: 'foreign', state: 'working' }, { mission_id: 'done', project_id: 'p', state: 'completed' }], limit_reached: false }}

    if (method === 'runtime.effects.list') {return { effects: [{ effect_id: 'e', state: 'outcome_unknown' }] }}
    throw new Error('Unavailable provider secret diagnostic')
  }) as RuntimeRequest

  const result = await inspectRuntimeStatus(request, 'owned', 'p')
  expect(result.ready.map(item => item.mission_id)).toEqual(['a'])
  expect(result.active).toEqual([])
  expect(result.effects[0].state).toBe('outcome_unknown')
  expect(result.errors).toContain('Upcoming schedules unavailable for this project; do not infer that none exist')
  expect(JSON.stringify(result)).not.toContain('secret diagnostic')
})
it('reconciliation inspects the exact effect without dispatching or marking all work successful', async () => {
  const request = vi.fn(async () => ({ effect: { effect_id: 'e', state: 'outcome_unknown', generation: 2, run_id: 'r', operation_type: 'unsupported', approval_id: null }, evidence: [] })) as RuntimeRequest
  const result = await runStatusCommand('reconcile e', request, 'owned')
  expect(request).toHaveBeenCalledOnce()
  expect(request).toHaveBeenCalledWith('runtime.effect.reconcile', { session_id: 'owned', schema_version: 1, effect_id: 'e' })
  expect(result).toContain('outcome_unknown')
  expect(result).toContain('never replays')
})
