import type { RuntimeRequest } from '@hermes/shared/runtime-control'
// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
const harness = vi.hoisted(() => ({ recording: false, start: vi.fn(), stop: vi.fn(), cancel: vi.fn(), stops: new Set<() => void>() }))
vi.mock('@/i18n', () => ({ useI18n: () => ({ t: { notifications: { voice: {} } } }) }))
vi.mock('../chat/composer/hooks/use-mic-recorder', () => ({ useMicRecorder: () => ({ recording: harness.recording, level: 0.2, handle: { start: harness.start, stop: harness.stop, cancel: harness.cancel } }) }))
vi.mock('@/lib/voice-playback', () => ({ registerOwnedVoiceStop: (stop: () => void) => { harness.stops.add(stop);

 return () => harness.stops.delete(stop) }, stopVoicePlayback: () => { for (const stop of [...harness.stops]) {stop();} harness.stops.clear() } }))
vi.mock('./voice-audio', () => ({ recordingPCM: vi.fn(async () => new Uint8Array(2)), validateSpeechPCM: vi.fn(async () => ({ samples: new Float32Array([0]), sampleRate: 22050 })) }))
vi.mock('@/components/ui/button', () => ({ Button: (props: React.ComponentProps<'button'>) => <button {...props} /> }))
vi.mock('@/components/ui/textarea', () => ({ Textarea: (props: React.ComponentProps<'textarea'>) => <textarea {...props} /> }))
vi.mock('@/components/ui/confirm-dialog', () => ({ ConfirmDialog: ({ open, onConfirm }: { open: boolean; onConfirm: () => Promise<void> }) => open ? <button onClick={() => void onConfirm()}>Confirm voice task</button> : null }))
import { VoicePanel } from './voice-panel'
const response = (value: unknown) => ({ response_json: JSON.stringify(value) })
const caps = response({ voice: { stt: { processing_location: 'local' }, tts: { processing_location: 'local' }, limits: {}, unsupported_reasons: {} } })
beforeEach(() => { harness.recording = false; harness.stops.clear(); harness.start.mockReset().mockImplementation(async () => { harness.recording = true }); harness.stop.mockReset().mockImplementation(async () => { harness.recording = false;

 return { audio: new Blob(['clip']), durationMs: 100 } }); harness.cancel.mockReset().mockImplementation(() => { harness.recording = false }) })
afterEach(() => { cleanup(); vi.unstubAllGlobals() })
it('does not request microphone/backend media at mount and transcribes without submitting a task', async () => {
  const request = vi.fn(async (method: string) => method === 'runtime.media.capabilities' ? caps : response(method.endsWith('start') ? { capture_id: 'c', state: 'recording', processing_location: 'local' } : { capture_id: 'c', sequence: 1, state: 'transcribed', text: 'Check this name', final: true, accepted_as_task: false, confirmation_required: true, processing_location: 'local' })) as RuntimeRequest
  const view = render(<VoicePanel connected request={request} sessionId="owned" />)
  expect(request).not.toHaveBeenCalled(); expect(harness.start).not.toHaveBeenCalled()
  fireEvent.click(view.getByText('Inspect configured local speech'))
  await waitFor(() => expect((view.getByText('Record microphone (max 59 seconds)') as HTMLButtonElement).disabled).toBe(false))
  fireEvent.click(view.getByText('Record microphone (max 59 seconds)'))
  await waitFor(() => expect((view.getByText('Stop and transcribe on local backend') as HTMLButtonElement).disabled).toBe(false))
  fireEvent.click(view.getByText('Stop and transcribe on local backend'))
  await waitFor(() => expect((view.getByLabelText('Reviewed transcript or text') as HTMLTextAreaElement).value).toBe('Check this name'))
  expect(vi.mocked(request).mock.calls.some(([method]) => method === 'runtime.voice.submit')).toBe(false)
})
it('global stop invalidates synthesis before any late PCM starts playback', async () => {
  const createBuffer = vi.fn(), close = vi.fn(async () => {})
  vi.stubGlobal('AudioContext', class { resume = async () => {}; close = close; createBuffer = createBuffer })
  let resolve!: (value: unknown) => void
  const request = vi.fn((method: string) => method === 'runtime.media.capabilities' ? Promise.resolve(caps) : method === 'runtime.voice.speak' ? new Promise(done => { resolve = done }) : Promise.resolve(response({ state: 'stopped' }))) as RuntimeRequest
  const view = render(<VoicePanel connected request={request} sessionId="owned" />)
  fireEvent.click(view.getByText('Inspect configured local speech'))
  fireEvent.change(view.getByLabelText('Reviewed transcript or text'), { target: { value: 'Speak this text' } })
  await waitFor(() => expect((view.getByText('Speak via local backend') as HTMLButtonElement).disabled).toBe(false))
  fireEvent.click(view.getByText('Speak via local backend')); await waitFor(() => expect(resolve).toBeDefined())
  fireEvent.click(view.getByText('Stop speech only'))
  await act(async () => resolve(response({ state: 'ready' })))
  expect(createBuffer).not.toHaveBeenCalled(); expect(close).toHaveBeenCalled()
  expect(vi.mocked(request).mock.calls.some(([method]) => method === 'runtime.command')).toBe(false)
})
it('unmount releases owned client microphone without inventing backend capture or mission cancellation', () => {
  const request = vi.fn(async () => caps) as RuntimeRequest
  const view = render(<VoicePanel connected request={request} sessionId="owned" />)
  view.unmount(); expect(harness.cancel).toHaveBeenCalled(); expect(request).not.toHaveBeenCalled()
})

it('requires explicit strict speech admission before microphone and binds exact account/request without task acceptance', async () => {
  const strictCaps = response({ voice: { stt: { processing_location: 'local' }, tts: { processing_location: 'local' }, budget: { mode: 'existing_run_tree', admission_rpc: 'runtime.voice.admit' } } })
  const request = vi.fn(async (method: string) => method === 'runtime.media.capabilities' ? strictCaps : method === 'runtime.voice.admit' ? response({ status: 'admitted', budget_account_id: 'finite-account', root_id: 'root', deadline: Date.now() / 1000 + 90, budget: { state: 'open' }, capture_started: false, model_dispatched: false, mission_accepted: false, reservation_created: false }) : response(method.endsWith('start') ? { capture_id: 'capture', state: 'recording', processing_location: 'local' } : { capture_id: 'capture', sequence: 1, state: 'transcribed', text: 'Review strict speech', final: true, accepted_as_task: false, confirmation_required: true, processing_location: 'local', budget: { consumed: { attempts: 1 }, unknown_usage: false } })) as RuntimeRequest
  const view = render(<VoicePanel connected request={request} sessionId="owned" />)
  fireEvent.click(view.getByText('Inspect configured local speech')); await view.findByText('Admit finite speech budget')
  expect((view.getByText('Record microphone (max 59 seconds)') as HTMLButtonElement).disabled).toBe(true)
  expect(harness.start).not.toHaveBeenCalled()
  fireEvent.click(view.getByText('Admit finite speech budget'))
  await waitFor(() => expect((view.getByText('Record microphone (max 59 seconds)') as HTMLButtonElement).disabled).toBe(false))
  expect(harness.start).not.toHaveBeenCalled()
  fireEvent.click(view.getByText('Record microphone (max 59 seconds)'))
  await waitFor(() => expect((view.getByText('Stop and transcribe on local backend') as HTMLButtonElement).disabled).toBe(false))
  fireEvent.click(view.getByText('Stop and transcribe on local backend'))
  await waitFor(() => expect((view.getByLabelText('Reviewed transcript or text') as HTMLTextAreaElement).value).toBe('Review strict speech'))
  expect(request).toHaveBeenCalledWith('runtime.voice.capture.start', expect.objectContaining({ session_id: 'owned', budget_account_id: 'finite-account', request_id: expect.any(String) }))
  expect(vi.mocked(request).mock.calls.filter(([method]) => method === 'runtime.voice.admit')).toHaveLength(1)
})
it('does not retry unknown strict admission or start capture while prior speech remains unresolved', async () => {
  const request = vi.fn(async (method: string) => { if (method === 'runtime.media.capabilities') { return response({ voice: { stt: { processing_location: 'local' }, budget: { mode: 'existing_run_tree', admission_rpc: 'runtime.voice.admit' } } }) } throw new Error('unknown') }) as RuntimeRequest
  const view = render(<VoicePanel connected request={request} sessionId="owned" />)
  fireEvent.click(view.getByText('Inspect configured local speech')); await view.findByText('Admit finite speech budget')
  fireEvent.click(view.getByText('Admit finite speech budget')); await view.findByRole('alert')
  expect((view.getByText('Admit finite speech budget') as HTMLButtonElement).disabled).toBe(true)
  expect((view.getByText('Record microphone (max 59 seconds)') as HTMLButtonElement).disabled).toBe(true)
  expect(harness.start).not.toHaveBeenCalled()
  expect(vi.mocked(request).mock.calls.filter(([method]) => method === 'runtime.voice.admit')).toHaveLength(1)
})
it('strict speech controls have authored nine-locale coverage', async () => {
  const { voiceBudgetCopy } = await import('./voice-budget-copy')

  for (const row of Object.values(voiceBudgetCopy)) { expect(row).toHaveLength(9); expect(row.every(value => value.trim().length > 0)).toBe(true) }
})
