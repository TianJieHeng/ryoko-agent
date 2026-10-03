import { expect, it } from 'vitest'

import { pcm16Bytes, pcmBase64, validateSpeechPCM } from './voice-audio'
it('encodes signed little-endian PCM and validates complete digest before playback', async () => {
  const bytes = pcm16Bytes(new Float32Array([-1, 0, 1]))
  expect([...bytes]).toEqual([0, 128, 0, 0, 255, 127])
  const sha256 = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', new Uint8Array(bytes).buffer))).map(value => value.toString(16).padStart(2, '0')).join('')
  const value = { state: 'ready', playback: 'client', mission_cancelled: false, audio: { format: 'pcm_s16le', channels: 1, sample_rate: 22050, byte_length: bytes.length, sha256, pcm_base64: pcmBase64(bytes) } }
  expect((await validateSpeechPCM(value)).samples[0]).toBe(-1)
  await expect(validateSpeechPCM({ ...value, audio: { ...value.audio, sha256: 'f'.repeat(64) } })).rejects.toThrow('digest mismatch')
  await expect(validateSpeechPCM({ ...value, audio: { ...value.audio, byte_length: 8 } })).rejects.toThrow('byte count')
})
it('rejects non-finite samples and unsupported output format/rate', async () => {
  expect(() => pcm16Bytes(new Float32Array([NaN]))).toThrow('Invalid PCM')
  await expect(validateSpeechPCM({ state: 'ready', playback: 'client', mission_cancelled: false, audio: { format: 'wav', channels: 1, sample_rate: 192000 } })).rejects.toThrow('Unsupported')
})
