import { controlRecord } from '@hermes/shared/runtime-research'

export function pcm16Bytes(samples: Float32Array): Uint8Array {
  if (samples.length > 960000) {throw new Error('Audio exceeds sixty seconds at 16 kHz')}
  const bytes = new Uint8Array(samples.length * 2), view = new DataView(bytes.buffer)
  samples.forEach((sample, index) => {
    if (!Number.isFinite(sample)) {throw new Error('Invalid PCM sample')}
    const value = Math.max(-1, Math.min(1, sample))
    view.setInt16(index * 2, Math.round(value * (value < 0 ? 32768 : 32767)), true)
  })

  return bytes
}

export function pcmBase64(bytes: Uint8Array): string {
  let text = ''

  for (let start = 0; start < bytes.length; start += 8192) {text += String.fromCharCode(...bytes.subarray(start, start + 8192))}

  return btoa(text)
}

/** Reuse the existing explicit recorder; conversion remains on this client. */
export async function recordingPCM(blob: Blob): Promise<Uint8Array> {
  if (blob.size > 16 * 1024 * 1024) {throw new Error('Recorded clip exceeds the local decode limit')}
  const decoder = new AudioContext()

  try {
    const buffer = await decoder.decodeAudioData(await blob.arrayBuffer())

    if (!Number.isFinite(buffer.duration) || buffer.duration <= 0 || buffer.duration > 60) {throw new Error('Record at most sixty seconds')}
    const renderer = new OfflineAudioContext(1, Math.ceil(buffer.duration * 16000), 16000)
    const source = renderer.createBufferSource(); source.buffer = buffer; source.connect(renderer.destination); source.start()
    const mono = await renderer.startRendering()

    return pcm16Bytes(mono.getChannelData(0))
  } finally { await decoder.close() }
}

export async function validateSpeechPCM(value: unknown): Promise<{ samples: Float32Array; sampleRate: number }> {
  const response = controlRecord(value), audio = controlRecord(response.audio)

  if (response.state !== 'ready' || response.playback !== 'client' || response.mission_cancelled !== false || audio.format !== 'pcm_s16le' || audio.channels !== 1 || typeof audio.sample_rate !== 'number' || !Number.isSafeInteger(audio.sample_rate) || audio.sample_rate < 8000 || audio.sample_rate > 48000 || typeof audio.pcm_base64 !== 'string' || audio.pcm_base64.length > 1400000 || typeof audio.sha256 !== 'string') {throw new Error('Unsupported local speech payload')}
  const raw = atob(audio.pcm_base64), bytes = Uint8Array.from(raw, char => char.charCodeAt(0))

  if (!bytes.length || bytes.length % 2 || bytes.length > 1048576 || bytes.length !== audio.byte_length || bytes.length / 2 / audio.sample_rate > 30) {throw new Error('Invalid bounded speech byte count/duration')}
  const digest = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))).map(byte => byte.toString(16).padStart(2, '0')).join('')

  if (digest !== audio.sha256) {throw new Error('Speech digest mismatch; playback was not started')}
  const view = new DataView(bytes.buffer), samples = new Float32Array(bytes.length / 2)

  for (let index = 0; index < samples.length; index++) {samples[index] = view.getInt16(index * 2, true) / 32768}

  return { samples, sampleRate: audio.sample_rate }
}
