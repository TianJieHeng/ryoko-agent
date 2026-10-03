import type { RuntimeRequest } from '@hermes/shared/runtime-control'
import { controlRecord } from '@hermes/shared/runtime-research'
import { bindRuntimeChannel, runSpecialistCommand } from '@hermes/shared/runtime-specialists'
import { useEffect, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { Textarea } from '@/components/ui/textarea'
import { useI18n } from '@/i18n'
import { registerOwnedVoiceStop, stopVoicePlayback } from '@/lib/voice-playback'

import { transcribeRuntimePCM, voiceResponse } from '../../../../shared/src/runtime-voice'
import { admitRuntimeVoice, strictVoiceBudget, type VoiceAdmission, voiceOperation } from '../../../../shared/src/runtime-voice-budget'
import { useMicRecorder } from '../chat/composer/hooks/use-mic-recorder'

import { runtimeUiTemplates, useRuntimeUiFormat, useRuntimeUiText } from './runtime-ui-copy'
import { recordingPCM, validateSpeechPCM } from './voice-audio'
import { useVoiceBudgetCopy } from './voice-budget-copy'

interface Props { request: RuntimeRequest; sessionId: string; connected: boolean; unresolvedSpeechIds?: readonly string[] }
interface TaskReview { binding_id: string; input_id: string; expected_revision: number; text: string; confirmed_text: string }

export function VoicePanel(props: Props) {
  const [scope, setScope] = useState({ request: props.request, sessionId: props.sessionId, connected: props.connected, generation: 0 })

  if (scope.request !== props.request || scope.sessionId !== props.sessionId || scope.connected !== props.connected) {
    setScope({ request: props.request, sessionId: props.sessionId, connected: props.connected, generation: scope.generation + 1 })

    return null
  }

  return <OwnedVoicePanel key={scope.generation} {...props} />
}

function OwnedVoicePanel({ request, sessionId, connected, unresolvedSpeechIds = [] }: Props) {
  const budgetCopy = useVoiceBudgetCopy()
  const [admission, setAdmission] = useState<VoiceAdmission | null>(null)
  const [admissionId, setAdmissionId] = useState<string | null>(null)
  const [mediaAttempt, setMediaAttempt] = useState<{ request_id: string; budget_account_id?: string } | null>(null)
  const [budgetReceipt, setBudgetReceipt] = useState<Record<string, unknown> | null>(null)
  const format = useRuntimeUiFormat()
  const rt = useRuntimeUiText()
  const { t } = useI18n(), mic = useMicRecorder(t.notifications.voice)
  const [capabilities, setCapabilities] = useState<Record<string, unknown> | null>(null)
  const [text, setText] = useState(''), [output, setOutput] = useState(''), [error, setError] = useState<string | null>(null), [busy, setBusy] = useState(false)
  const [review, setReview] = useState<TaskReview | null>(null), [submitted, setSubmitted] = useState<string | null>(null)
  const lifetimeRef = useRef({ request, sessionId, connected, active: true, busy: false, audioGeneration: 0, captureGeneration: 0, captureOwned: false, mediaControls: null as { request_id: string; budget_account_id?: string } | null, unregisterAudio: null as (() => void) | null, timer: null as ReturnType<typeof setTimeout> | null, stopAudio: null as (() => void) | null, cancelMic: null as (() => void) | null })

  if (lifetimeRef.current.request !== request || lifetimeRef.current.sessionId !== sessionId || lifetimeRef.current.connected !== connected) {
    lifetimeRef.current.active = false
    lifetimeRef.current = { request, sessionId, connected, active: true, busy: false, audioGeneration: 0, captureGeneration: 0, captureOwned: false, mediaControls: null as { request_id: string; budget_account_id?: string } | null, unregisterAudio: null as (() => void) | null, timer: null as ReturnType<typeof setTimeout> | null, stopAudio: null as (() => void) | null, cancelMic: null as (() => void) | null }
  }

  const lifetime = lifetimeRef.current
  lifetime.cancelMic = mic.handle.cancel
  useEffect(() => {
    lifetime.active = true; setBusy(false); setReview(null); setCapabilities(null); setAdmission(null); setAdmissionId(null); setMediaAttempt(null); setBudgetReceipt(null)

    const stop = () => { lifetime.audioGeneration++; lifetime.stopAudio?.(); lifetime.stopAudio = null }

    return () => {
      lifetime.active = false; lifetime.captureGeneration++; stop(); lifetime.unregisterAudio?.(); lifetime.cancelMic?.()

      if (lifetime.timer) {clearTimeout(lifetime.timer)}

      if (lifetime.captureOwned) {void lifetime.request('runtime.voice.capture.cancel', { session_id: lifetime.sessionId, schema_version: 1 }).catch(() => {})}
    }
  }, [lifetime])
  const base = { session_id: sessionId, schema_version: 1 as const }

  async function run(work: () => Promise<void>) {
    if (!connected || lifetime.busy || !lifetime.active) {return}
    lifetime.busy = true; setBusy(true); setError(null)

    try { await work() } catch { if (lifetime.active) {setError(rt("Local voice unavailable or interrupted. Inspect capabilities and retained work; nothing was automatically resubmitted."))} }
    finally { lifetime.busy = false;

 if (lifetime.active) {setBusy(false)} }
  }

  async function inspect() {
    const result = voiceResponse(await request('runtime.media.capabilities', base))
    const voice = controlRecord(result.voice)

    strictVoiceBudget(voice)

    if (lifetime.active) {setCapabilities(voice)}
  }

  const strict = capabilities ? strictVoiceBudget(capabilities) : false
  const budgetReady = !strict || !!admission && admission.deadline * 1000 > Date.now()
  const speechUnknown = unresolvedSpeechIds.length > 0

  async function admit() {
    if (admissionId || speechUnknown) { return }
    const id = crypto.randomUUID(); setAdmissionId(id)
    await run(async () => {
      const value = await admitRuntimeVoice(request, sessionId, id)

      if (lifetime.active) { setAdmission(value); setBudgetReceipt(value.budget) }
    })
  }

  function configured(kind: 'stt' | 'tts'): boolean {
    const item = capabilities?.[kind]

    return !!item && controlRecord(item).processing_location === 'local'
  }

  async function stopCapture() {
    if (lifetime.timer) { clearTimeout(lifetime.timer); lifetime.timer = null }
    const generation = lifetime.captureGeneration
    await run(async () => {
      const clip = await mic.handle.stop(), current = () => lifetime.active && generation === lifetime.captureGeneration

      if (!clip || !current()) {return}
      const pcm = await recordingPCM(clip.audio)

      if (!current()) {return}
      lifetime.captureOwned = true
      const controls = lifetime.mediaControls

      if (!controls) { throw new Error('Missing original speech operation') }
      const result = await transcribeRuntimePCM(request, sessionId, pcm, current, controls, receipt => { if (current()) { setBudgetReceipt(receipt) } })
      lifetime.captureOwned = false

      if (current() && result !== null) { lifetime.mediaControls = null; setMediaAttempt(null); setText(result); setReview(null); setOutput(rt("Local transcript returned for review. It was not accepted as a task. Check decisive names, numbers and intent.")) }
    })
  }

  async function startCapture() {
    if (!configured('stt') || !budgetReady || speechUnknown || mediaAttempt) {return}
    stopVoicePlayback(); lifetime.captureGeneration++
    const generation = lifetime.captureGeneration
    await run(async () => {
      const id = crypto.randomUUID()
      lifetime.mediaControls = strict ? voiceOperation(admission!, id) : { request_id: id }; setMediaAttempt(lifetime.mediaControls)
      await mic.handle.start()

      if (!lifetime.active || lifetime.captureGeneration !== generation) { mic.handle.cancel();

 return }

      setReview(null); setOutput(rt("Recording locally. Stop to obtain a transcript; no work is submitted by recording."))
      lifetime.timer = setTimeout(() => void stopCapture(), 59000)
    })
  }

  function discard() {
    lifetime.captureGeneration++; lifetime.cancelMic?.(); setReview(null); setText('')

    if (!lifetime.captureOwned) { lifetime.mediaControls = null; setMediaAttempt(null) }

    if (lifetime.timer) { clearTimeout(lifetime.timer); lifetime.timer = null }

    if (!lifetime.captureOwned) { setOutput(rt("Client audio discarded. No backend capture or accepted work was cancelled."));

 return }

    lifetime.captureOwned = false
    void request('runtime.voice.capture.cancel', base).then(() => { if (lifetime.active) {lifetime.mediaControls = null; setMediaAttempt(null); setOutput(rt("Captured audio discarded. Accepted mission work was not cancelled."))} }).catch(() => { if (lifetime.active) {setError(rt("Local microphone stopped; backend capture-discard acknowledgment is unavailable."))} })
  }

  async function speak() {
    if (!connected || lifetime.busy || !lifetime.active) {return}

    if (!budgetReady || speechUnknown || mediaAttempt || !configured('tts') || new TextEncoder().encode(text).length > 4096) { setError(rt("Select configured local TTS and at most 4096 UTF-8 text bytes"));

 return }

    stopVoicePlayback()
    lifetime.unregisterAudio?.()
    lifetime.audioGeneration++
    const generation = lifetime.audioGeneration
    lifetime.unregisterAudio = registerOwnedVoiceStop(() => { lifetime.audioGeneration++; lifetime.stopAudio?.(); lifetime.stopAudio = null })
    // Unlock only on this explicit user gesture, before awaiting local synthesis.
    const context = new AudioContext()

    const close = () => { void context.close().catch(() => {}) }
    lifetime.stopAudio = close
    await run(async () => {
      try {
        await context.resume()
        const id = crypto.randomUUID(), controls = strict ? voiceOperation(admission!, id) : { request_id: id }
        setMediaAttempt(controls)
        const result = voiceResponse(await request('runtime.voice.speak', { ...base, text, ...controls }))

        if (lifetime.active) { if (result.budget) { setBudgetReceipt(controlRecord(result.budget)) } setMediaAttempt(null) }
        const audio = await validateSpeechPCM(result)

        if (!lifetime.active || lifetime.audioGeneration !== generation) { close();

 return }

        const buffer = context.createBuffer(1, audio.samples.length, audio.sampleRate); buffer.copyToChannel(new Float32Array(audio.samples), 0)
        const source = context.createBufferSource(); source.buffer = buffer; source.connect(context.destination)

        lifetime.stopAudio = () => { try { source.stop() } catch { /* Already ended. */ } close() }

        source.onended = () => { close();

 if (lifetime.active && lifetime.audioGeneration === generation) {setOutput(rt("Local speech playback ended. Mission state is unchanged."))} }

        source.start(); setOutput(rt("Playing verified local PCM. Stop speech interrupts playback only."))
      } catch (caught) { close(); throw caught }
    })
  }

  function stopSpeech() {
    stopVoicePlayback()
    void request('runtime.voice.stop', base).then(() => { if (lifetime.active) {setOutput(rt("Owned client speech stopped. Accepted work and conversation streaming are unchanged."))} }).catch(() => { if (lifetime.active) {setError(rt("Client playback stopped; backend speech-stop acknowledgment is unavailable."))} })
  }

  async function prepareTask() {
    await run(async () => {
      const binding = await bindRuntimeChannel(request, sessionId, 'voice')

      if (!lifetime.active) {return}
      const snapshot = await request('runtime.snapshot', base)

      if (lifetime.active) {setReview({ binding_id: binding.bindingId, input_id: crypto.randomUUID(), expected_revision: snapshot.revision, text, confirmed_text: text })}
    })
  }

  async function submitTask() {
    if (!review || submitted) {return}
    const exact = review
    await run(async () => {
      setSubmitted(exact.input_id); setReview(null)
      const result = await runSpecialistCommand(`voice-submit ${JSON.stringify(exact)}`, request, sessionId)

      if (lifetime.active) {setOutput(result)}
    })
  }

  return <section className="grid gap-3"><h4 className="text-sm font-medium">{rt("Explicit local voice")}</h4><p className="text-xs">{rt("Microphone access starts only with your Record button. Processing uses the configured gateway backend’s local model, which may run on another computer. Client capture/playback stay in this app. No model download, cloud speech provider or hands-free listener is enabled here.")}</p>
    <Button disabled={!connected || busy || mic.recording} onClick={() => void run(inspect)} size="xs" variant="secondary">{rt("Inspect configured local speech")}</Button>
    {capabilities && <pre className="whitespace-pre-wrap break-words text-xs">{JSON.stringify({ stt: capabilities.stt, tts: capabilities.tts, unsupported_reasons: capabilities.unsupported_reasons, limits: capabilities.limits, budget: capabilities.budget }, null, 2)}</pre>}
    {strict && <div className="grid gap-2"><p className="text-xs">{budgetCopy('explain')}</p><Button disabled={!connected || busy || !!admissionId || speechUnknown} onClick={() => void admit()} size="xs" variant="secondary">{budgetCopy('admit')}</Button></div>}
    {admissionId && !admission && !busy && <p className="break-all text-xs" role="status">{budgetCopy('unknown')} · {admissionId}</p>}
    {budgetReceipt && <details><summary>{budgetCopy('receipt')}</summary><pre className="whitespace-pre-wrap break-words text-xs">{JSON.stringify({ admission_request: admissionId, account_id: admission?.accountId, deadline: admission?.deadline, receipt: budgetReceipt }, null, 2)}</pre></details>}
    {(speechUnknown || mediaAttempt && !mic.recording && !busy) && <p className="break-all text-xs" role="status">{budgetCopy('unknown')} · {JSON.stringify(mediaAttempt)} · {unresolvedSpeechIds.join(', ')}</p>}
    <div className="flex flex-wrap gap-2"><Button disabled={!connected || busy || mic.recording || !budgetReady || speechUnknown || !!mediaAttempt || !configured('stt')} onClick={() => void startCapture()} size="xs" variant="secondary">{rt("Record microphone (max 59 seconds)")}</Button><Button disabled={!connected || busy || !mic.recording} onClick={() => void stopCapture()} size="xs" variant="secondary">{rt("Stop and transcribe on local backend")}</Button><Button disabled={!connected} onClick={discard} size="xs" variant="text">{rt("Discard captured audio")}</Button></div>
    {mic.recording && <p role="status">{rt("Recording · microphone level") + " "}{Math.round(mic.level * 100)}%</p>}
    <label>{rt("Reviewed transcript or text")}<Textarea disabled={busy || mic.recording} onChange={event => { setText(event.target.value); setReview(null) }} value={text} /></label>
    <div className="flex flex-wrap gap-2"><Button disabled={!connected || busy || mic.recording || !text || !budgetReady || speechUnknown || !!mediaAttempt || !configured('tts')} onClick={() => void speak()} size="xs" variant="secondary">{rt("Speak via local backend")}</Button><Button disabled={!connected} onClick={stopSpeech} size="xs" variant="text">{rt("Stop speech only")}</Button><Button disabled={!connected || busy || mic.recording || !text.trim() || !!submitted} onClick={() => void prepareTask()} size="xs" variant="secondary">{rt("Review discussion-to-task transition")}</Button></div>
    {submitted && <p className="break-words text-xs">{rt("Logical input") + " "}{submitted}{" " + rt("retained. Inspect the mission/effect receipt before another submission; reconnect or speech stop does not restart accepted work.")}</p>}
    <ConfirmDialog confirmLabel={rt("Submit this exact reviewed text")} description={format(runtimeUiTemplates.voiceSubmit, { text: review?.text ?? '', revision: review?.expected_revision ?? '' })} onClose={() => setReview(null)} onConfirm={submitTask} open={!!review && connected} title={rt("Turn this reviewed discussion into a task?")} />
    {output && <p aria-live="polite" className="whitespace-pre-wrap break-words text-xs">{output}</p>}{error && <p role="alert">{error}</p>}
  </section>
}
