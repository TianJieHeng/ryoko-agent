import { expect, it, vi } from 'vitest'

import type { RuntimeRequest } from './runtime-control.js'
import { transcribeRuntimePCM } from './runtime-voice.js'
const response = (value: unknown) => ({ response_json: JSON.stringify(value) })
it('feeds bounded ordered mono PCM and leaves the transcript unaccepted', async () => {
  const request = vi.fn(async (method: string, params: Record<string, unknown>) => response(method.endsWith('start') ? { capture_id: 'c', state: 'recording', processing_location: 'local' } : { capture_id: 'c', state: params.final ? 'transcribed' : 'recording', sequence: Number(params.sequence) + 1, text: params.final ? 'Review exactly' : '', final: params.final, accepted_as_task: false, confirmation_required: true, processing_location: 'local' })) as RuntimeRequest
  expect(await transcribeRuntimePCM(request, 'owned', new Uint8Array(64002), () => true)).toBe('Review exactly')
  expect(request).toHaveBeenCalledTimes(3)
  const calls = vi.mocked(request).mock.calls
  expect(calls[1]).toEqual(['runtime.voice.capture.feed', expect.objectContaining({ sequence: 0, final: false })])
  expect(calls[2]).toEqual(['runtime.voice.capture.feed', expect.objectContaining({ sequence: 1, final: true })])
  expect(calls.some(([method]) => method === 'runtime.voice.submit')).toBe(false)
})
it('rejects over-limit or task-accepting transcript responses', async () => {
  const request = vi.fn(async (method: string) => response(method.endsWith('start') ? { capture_id: 'c', state: 'recording', processing_location: 'local' } : { capture_id: 'c', state: 'transcribed', sequence: 1, text: 'unsafe', final: true, accepted_as_task: true, confirmation_required: false, processing_location: 'local' })) as RuntimeRequest
  await expect(transcribeRuntimePCM(request, 'owned', new Uint8Array(1920002), () => true)).rejects.toThrow('sixty seconds')
  expect(request).not.toHaveBeenCalled()
  await expect(transcribeRuntimePCM(request, 'owned', new Uint8Array(2), () => true)).rejects.toThrow('authority')
})
it('cancels only the old owned capture if the session changes during start', async () => {
  let current = true

  const request = vi.fn(async (method: string) => { if (method.endsWith('start')) {current = false;}

 return response({ capture_id: 'c', state: 'recording', processing_location: 'local' }) }) as RuntimeRequest

  expect(await transcribeRuntimePCM(request, 'old', new Uint8Array(2), () => current)).toBeNull()
  expect(request).toHaveBeenLastCalledWith('runtime.voice.capture.cancel', { session_id: 'old', schema_version: 1 })
})
it('uses the exact admitted account/request only on capture start and returns final usage separately', async () => {
  const onBudget = vi.fn()
  const request = vi.fn(async (method: string) => response(method.endsWith('start') ? { capture_id: 'c', state: 'recording', processing_location: 'local' } : { capture_id: 'c', state: 'transcribed', sequence: 1, text: 'Reviewed', final: true, accepted_as_task: false, confirmation_required: true, processing_location: 'local', budget: { unknown_usage: false, consumed: { attempts: 1 } } })) as RuntimeRequest
  expect(await transcribeRuntimePCM(request, 'owned', new Uint8Array(2), () => true, { request_id: 'utterance', budget_account_id: 'account' }, onBudget)).toBe('Reviewed')
  expect(request).toHaveBeenNthCalledWith(1, 'runtime.voice.capture.start', { session_id: 'owned', schema_version: 1, request_id: 'utterance', budget_account_id: 'account' })
  expect(vi.mocked(request).mock.calls[1][1]).not.toHaveProperty('budget_account_id')
  expect(onBudget).toHaveBeenCalledExactlyOnceWith({ unknown_usage: false, consumed: { attempts: 1 } })
})
