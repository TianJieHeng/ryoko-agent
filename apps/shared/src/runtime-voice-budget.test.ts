import { expect, it, vi } from 'vitest'

import type { RuntimeRequest } from './runtime-control.js'
import { admitRuntimeVoice, strictVoiceBudget, voiceOperation } from './runtime-voice-budget.js'

const result = (changes: Record<string, unknown> = {}) => ({ response_json: JSON.stringify({ status: 'admitted', budget_account_id: 'account', root_id: 'root', deadline: 200, capture_started: false, model_dispatched: false, mission_accepted: false, reservation_created: false, budget: { state: 'active', unknown_usage: false }, ...changes }) })
it('admits the exact owned finite speech account without starting media or accepting a mission', async () => {
  const request = vi.fn(async () => result()) as RuntimeRequest
  const admission = await admitRuntimeVoice(request, 'session', 'stable-admission', 100000)
  expect(request).toHaveBeenCalledExactlyOnceWith('runtime.voice.admit', { session_id: 'session', schema_version: 1, request_id: 'stable-admission' })
  expect(voiceOperation(admission, 'stable-utterance', 100000)).toEqual({ request_id: 'stable-utterance', budget_account_id: 'account' })
  expect(() => voiceOperation(admission, 'new-utterance', 200000)).toThrow('expired')
})
it.each([{ mission_accepted: true }, { capture_started: true }, { model_dispatched: true }, { reservation_created: true }, { deadline: 0 }, { status: 'not_required' }])('rejects unexpected or expired admission %j', async changes => {
  await expect(admitRuntimeVoice(vi.fn(async () => result(changes)) as RuntimeRequest, 'session', 'request', 100000)).rejects.toThrow()
})
it('fails closed on unfamiliar budget protocols and supports declared legacy mode', () => {
  expect(strictVoiceBudget(null)).toBe(false)
  expect(strictVoiceBudget({ budget: { mode: 'not_configured' } })).toBe(false)
  expect(strictVoiceBudget({ budget: { mode: 'existing_run_tree', admission_rpc: 'runtime.voice.admit' } })).toBe(true)
  expect(() => strictVoiceBudget({ budget: { mode: 'unknown' } })).toThrow('Unsupported')
})
it('never automatically retries an unknown admission', async () => {
  const request = vi.fn(async () => { throw new Error('lost reply') }) as RuntimeRequest
  await expect(admitRuntimeVoice(request, 'session', 'stable')).rejects.toThrow('lost reply')
  expect(request).toHaveBeenCalledTimes(1)
})
