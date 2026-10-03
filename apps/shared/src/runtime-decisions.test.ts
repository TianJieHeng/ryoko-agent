import { expect, it, vi } from 'vitest'

import type { DecisionReceipt } from './gateway-contract.generated.js'
import type { RuntimeRequest } from './runtime-control.js'
import { decisionExplanation, inspectRuntimeDecisions } from './runtime-decisions.js'

it('explains only recorded fallback/route without distributing private packets or treating confidence as authority', () => {
  const receipt: DecisionReceipt = { schema_version: 1, contract_digest: 'c'.repeat(64), question_id: 'q', request_id: 'r', input_digest: 'd'.repeat(64), scope_digest: 'e'.repeat(64), model_digest: 'f'.repeat(64), calibration_digest: 'a'.repeat(64), service_digest: 'b'.repeat(64), thresholds: {}, point_gate_digest: null, live_options: ['no_tools', 'needs_tools'], unclear: false, latency_ms: 1, node_latency_ms: null, outcome: null, raw_state_retained: false, point_id: 'DP16', mode: 'shadow', contract_version: 1, actual_route: 'incumbent', incumbent: 'needs_tools', selected: 'no_tools', fallback: 'shadow_observation', receipt_id: 'a'.repeat(64), recorded_at: 1, distribution: { no_tools: 0.99, needs_tools: 0.01 }, classification: 'private' }
  const text = decisionExplanation(receipt).join('\n')
  expect(text).toContain('suggestion did not control')
  expect(text).toContain('never grants permission')
  expect(text).not.toContain('0.99')
  expect(text).not.toContain('private')
})
it('exposes cursor gaps honestly and has no feedback or activation side effect', async () => {
  const request = vi.fn(async () => ({ status: 'snapshot_required', snapshot: null, events: [], last_cursor: 'new:4', has_more: false })) as RuntimeRequest
  const result = await inspectRuntimeDecisions(request, 'owned', 'old:2')
  expect(result.gap).toBe(true)
  expect(result.lines.join('\n')).toContain('Earlier decisions cannot be reconstructed')
  expect(request).toHaveBeenCalledOnce()
  expect(request).toHaveBeenCalledWith('runtime.events.since', { session_id: 'owned', schema_version: 1, cursor: 'old:2', limit: 200 })
})
