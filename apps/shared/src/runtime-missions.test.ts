import { expect, it, vi } from 'vitest'

import type { RuntimeRequest } from './runtime-control.js'
import { runApprovalCommand, runDeliveryCommand, runMissionCommand } from './runtime-missions.js'

const mission = { mission_id: 'm', revision: 4, state: 'partially_completed', outcome: 'Two outputs', execution_status: 'completed', acceptance_status: 'pending', delivery_status: 'failed', next_step: 'Repair delivery', deliverables: [], blockers: ['second output missing'], missed_steer: [{ revision: 3, reason: 'effect_already_dispatched' }], effect_refs: [{ effect_id: 'e', state: 'outcome_unknown' }], delivery_refs: [{ delivery_id: 'd', state: 'failed' }], turns_used: 2, max_turns: 10, verification_current: false }

it('keeps partial execution, acceptance, missed steer and unknown effects distinct', async () => {
  const request = vi.fn(async () => ({ mission })) as RuntimeRequest
  const result = await runMissionCommand('get', request, 'owned')
  expect(result).toContain('partially completed')
  expect(result).toContain('delivery: failed')
  expect(result).toContain('outcome_unknown')
  expect(result).toContain('effect_already_dispatched')
  await runMissionCommand('revise 4 Keep the good output', request, 'owned')
  expect(request).toHaveBeenLastCalledWith('runtime.mission.revise', { session_id: 'owned', schema_version: 1, expected_revision: 4, contract: { outcome: 'Keep the good output' } })
})
it('does not approve unseen actions and retries only existing result delivery', async () => {
  const request = vi.fn(async () => ({ delivery_id: 'd', artifact_id: 'a', version: 1, state: 'outcome_unknown', acknowledgment_level: 'none', components: { text: 'not_sent', artifact: 'not_sent' }, attempt_count: 1, max_attempts: 3, result_available: true })) as RuntimeRequest
  const refusal = await runApprovalCommand('approve unseen digest', request, 'owned')
  expect(refusal).toContain('originating exact-content review')
  expect(request).not.toHaveBeenCalled()
  const result = await runDeliveryCommand('retry d', request, 'owned')
  expect(request).toHaveBeenCalledOnce()
  expect(request).toHaveBeenCalledWith('runtime.delivery.retry', { session_id: 'owned', schema_version: 1, delivery_id: 'd' })
  expect(result).toContain('outcome_unknown')
  expect(result).toContain('not rerun')
})
