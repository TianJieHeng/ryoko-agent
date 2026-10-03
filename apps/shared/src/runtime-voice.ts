import type { RuntimeRequest } from './runtime-control.js'
import { controlJson, controlRecord, controlText } from './runtime-research.js'
export const voiceResponse = (value: { response_json: string }) => controlRecord(controlJson(value.response_json, 1500000, false))

/** Local nonstreaming transcription, never task admission. The caller owns mic permission/resources. */
export async function transcribeRuntimePCM(request: RuntimeRequest, sessionId: string, bytes: Uint8Array, current: () => boolean, controls?: { request_id: string; budget_account_id?: string }, onBudget?: (receipt: Record<string, unknown>) => void): Promise<string | null> {
  if (!bytes.length || bytes.length % 2 || bytes.length > 1920000) {throw new Error('Provide at most sixty seconds of mono16k signed PCM')}

  if (!current()) {return null}
  const base = { session_id: sessionId, schema_version: 1 as const }
  const started = voiceResponse(await request('runtime.voice.capture.start', { ...controls, ...base }))

  if (!current()) { await request('runtime.voice.capture.cancel', base).catch(() => {});

 return null }

  if (started.state !== 'recording' || started.processing_location !== 'local') {throw new Error('Configured local capture unavailable')}
  const capture_id = controlText(started.capture_id, 512)

  for (let offset = 0, sequence = 0; offset < bytes.length; offset += 64000, sequence++) {
    if (!current()) { await request('runtime.voice.capture.cancel', base).catch(() => {});

 return null }

    const chunk = bytes.subarray(offset, offset + 64000)
    let raw = ''

    for (let at = 0; at < chunk.length; at += 8192) {raw += String.fromCharCode(...chunk.subarray(at, at + 8192))}
    const final = offset + chunk.length === bytes.length
    const result = voiceResponse(await request('runtime.voice.capture.feed', { ...base, capture_id, sequence, pcm_base64: btoa(raw), final }))

    if (!current()) {return null}

    if (result.capture_id !== capture_id || result.sequence !== sequence + 1 || result.processing_location !== 'local') {throw new Error('Capture response does not match this exact local sequence')}

    if (final) {
      if (result.state !== 'transcribed' || result.final !== true || result.accepted_as_task !== false || result.confirmation_required !== true) {throw new Error('Unexpected transcript authority; no task was confirmed')}

      if (typeof result.text !== 'string' || result.text.length > 65536) {throw new Error('Invalid bounded transcript')}

      if (result.budget) { onBudget?.(controlRecord(result.budget)) }

      return result.text
    }
  }

  return null
}
