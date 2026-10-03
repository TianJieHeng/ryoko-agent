import { describe, expect, it, vi } from 'vitest'

import type { RuntimeRequest } from './runtime-control.js'
import { runSpecialistCommand } from './runtime-specialists.js'

const json = (value: unknown) => ({ response_json: JSON.stringify(value) })
const mock = (fn: (method: string, params: unknown) => unknown) => vi.fn(async (method, params) => fn(method, params)) as unknown as RuntimeRequest
const sha = 'a'.repeat(64)
const frame = { frame_id: 'frame-a', sha256: sha, width: 80, height: 60, scope: 'selected_window', window_ref: 'window-a' }
const command = { schema_version: 1, command_id: 'input-a', status: 'accepted', durable_revision: 4, run_id: 'run-a', conflict: null }
const submit = { binding_id: 'binding-a', input_id: 'logical-a', expected_revision: 3, text: 'Find Ada at 12', confirmed_text: 'Find Ada at 12' }

describe('FE10 owned specialists/media/channel controls', () => {
  it('shows actual child and partial failure states without exposing private memory or claiming a specialist manifest', async () => {
    const request = mock(() => ({ subagents: [{ subagent_id: 'child-a', goal: 'Review source', status: 'running', accepting_steer: true, private_memory: 'secret' }], delegations: [{ delegation_id: 'delegate-a', status: 'failed', goal: 'Extract headings', error: 'private error body' }] }))
    const result = await runSpecialistCommand('team', request, 'owned')
    expect(result).toContain('child-a: running')
    expect(result).toContain('delegate-a: failed')
    expect(result).toContain('not exposed by this RPC')
    expect(result).toContain('primary personal memory harness is never read')
    expect(result).not.toContain('secret')
    expect(result).not.toContain('private error body')
    expect(request).toHaveBeenCalledWith('subagent.list', { session_id: 'owned' })
  })

  it('keeps queued steer distinct from delivery and never prints returned text', async () => {
    const request = mock(() => ({ subagent_id: 'child-a', status: 'queued', text: 'sensitive echo' }))
    const result = await runSpecialistCommand(`steer ${JSON.stringify({ subagent_id: 'child-a', text: 'Use exact source' })}`, request, 'owned')
    expect(result).toContain('queued, not delivered')
    expect(result).not.toContain('sensitive echo')
    expect(request).toHaveBeenCalledWith('subagent.steer', { session_id: 'owned', subagent_id: 'child-a', text: 'Use exact source' })
  })

  it('does not treat lineage visibility or a missing interrupt target as cancellation', async () => {
    const rejected = mock(() => ({ subagent_id: 'child-a', status: 'rejected', text: '' }))
    expect(await runSpecialistCommand('steer {"subagent_id":"child-a","text":"hello"}', rejected, 'owned')).toContain('lack current control authority')
    const request = mock(() => ({ subagent_id: 'child-a', found: false }))
    expect(await runSpecialistCommand('interrupt child-a', request, 'owned')).toContain('no interrupt signal acknowledged')
  })

  it('distinguishes speech stop, captured-audio discard, unsupported hangup and durable work cancellation', async () => {
    const request = mock(method => {
      if (method === 'runtime.voice.stop') { return json({ state: 'stopped', mission_cancelled: false, streaming_stopped: false }) }

      if (method === 'runtime.voice.capture.cancel') { return json({ state: 'discarded', mission_cancelled: false }) }

      return json(command)
    })

    expect(await runSpecialistCommand('voice-stop', request, 'owned')).toContain('accepted mission work continue')
    expect(await runSpecialistCommand('voice-discard', request, 'owned')).toContain('Accepted mission work remains active')
    expect(await runSpecialistCommand('voice-hangup', request, 'owned')).toContain('hangup is unavailable')
    expect(request).toHaveBeenCalledTimes(2)
    const result = await runSpecialistCommand('channel-submit {"binding_id":"binding-a","input_id":"cancel-a","expected_revision":4,"operation":"cancel","payload":{"reason":"Explicit user cancellation"}}', request, 'owned')
    expect(result).toContain('explicitly requests accepted-work cancellation')
    expect(request).toHaveBeenLastCalledWith('runtime.channel.submit', expect.objectContaining({ operation: 'cancel', payload: { reason: 'Explicit user cancellation' } }))
  })

  it('reports unconfigured adapters and server freshness without starting capture', async () => {
    const request = mock(() => json({ voice: { push_to_talk: false, stt: null, tts: null, unsupported: ['stt', 'tts'], remote_processing: false, capture: 'explicit_client_pcm_only' }, screen: { capture: 'explicit_client_selected_window_png', inspection: 'dimensions_digest_regions', freshness: 'server_receipt_age_client_acquisition_unverified', max_age_seconds: 15, ocr: false, os_actions: false, act_workflow: 'confirmed_selected_text_to_task' }, channels: ['local_jsonrpc', 'voice', 'screen'], external_channel_adapters: 'unconfigured' }))
    const result = await runSpecialistCommand('media', request, 'owned')
    expect(result).toContain('STT: unconfigured; TTS: unconfigured')
    expect(result).toContain('client acquisition time unverified')
    expect(result).toContain('never starts capture')
    expect(request).toHaveBeenCalledTimes(1)
  })

  it.each(['session_id', 'schema_version', 'identity', 'history', 'profile'])('rejects injected %s and nested payload authority before channel submit', async field => {
    const request = mock(() => json(command))
    const value = { binding_id: 'binding-a', input_id: 'input-a', expected_revision: 0, operation: 'submit', payload: { text: 'hello' } }
    await expect(runSpecialistCommand(`channel-submit ${JSON.stringify({ ...value, [field]: 'bad' })}`, request, 'owned')).rejects.toThrow('Unsupported field')
    await expect(runSpecialistCommand(`channel-submit ${JSON.stringify({ ...value, payload: { text: 'hello', [field]: 'bad' } })}`, request, 'owned')).rejects.toThrow('Unsupported field')
    expect(request).not.toHaveBeenCalled()
  })

  it('requires exact transcript confirmation and numeric revision without silently repairing names/numbers', async () => {
    const request = mock(() => json(command))
    await expect(runSpecialistCommand(`voice-submit ${JSON.stringify({ ...submit, confirmed_text: 'Find Adam at 120' })}`, request, 'owned')).rejects.toThrow('exact complete transcript')
    await expect(runSpecialistCommand(`voice-submit ${JSON.stringify({ ...submit, expected_revision: '3' })}`, request, 'owned')).rejects.toThrow('expected_revision')
    expect(request).not.toHaveBeenCalled()
    expect(await runSpecialistCommand(`voice-submit ${JSON.stringify(submit)}`, request, 'owned')).toContain('Admission is not execution')
    expect(request).toHaveBeenLastCalledWith('runtime.voice.submit', { ...submit, session_id: 'owned', schema_version: 1 })
  })

  it('shows exact owned local binding while refusing external channels', async () => {
    const request = mock(() => json({ binding_id: 'binding-a', mission_id: 'mission-a', project_id: 'project-a', channel: 'voice', verification: 'owned_live_transport_and_stored_identity', history_replayed: false }))
    await expect(runSpecialistCommand('channel-bind slack', request, 'owned')).rejects.toThrow('no external handoff')
    expect(request).not.toHaveBeenCalled()
    const result = await runSpecialistCommand('channel-bind voice', request, 'owned')
    expect(result).toContain('existing mission mission-a')
    expect(result).toContain('no new mission')
  })

  it('keeps duplicate logical inputs and runtime conflicts visible without replaying', async () => {
    const request = mock(() => json({ ...command, status: 'duplicate' }))
    const value = { binding_id: 'binding-a', input_id: 'logical-a', expected_revision: 3, operation: 'submit', payload: { text: 'Find Ada' } }
    const result = await runSpecialistCommand(`channel-submit ${JSON.stringify(value)}`, request, 'owned')
    expect(result).toContain('duplicate; durable revision 4')
    expect(request).toHaveBeenCalledTimes(1)
  })

  it('inspects and annotates an exact owned fresh frame without inventing capture time or OCR', async () => {
    const request = mock(method => json(method === 'runtime.screen.inspect' ? frame : { frame_id: 'frame-a', frame_sha256: sha, region: [0, 0, 20, 20], workflow: 'confirmed_selected_text_to_task', os_action_supported: false }))
    expect(await runSpecialistCommand('screen-inspect frame-a', request, 'owned')).toContain('no capture timestamp')
    expect(await runSpecialistCommand('screen-annotate {"frame_id":"frame-a","region":[0,0,20,20]}', request, 'owned')).toContain('no OS action performed')
    await expect(runSpecialistCommand('screen-annotate {"frame_id":"frame-a","region":[0,0,0,20]}', request, 'owned')).rejects.toThrow('coordinate')
  })

  it('fails closed for stale frames or revoked identities, with safe reasons and no retry', async () => {
    const request = mock(() => { throw Object.assign(new Error('private frame details'), { data: { code: 'screen_frame_stale' } }) })
    await expect(runSpecialistCommand(`screen-submit ${JSON.stringify({ ...submit, frame_id: 'frame-a', region: [0, 0, 20, 20] })}`, request, 'owned')).rejects.toThrow('screen_frame_stale')
    expect(request).toHaveBeenCalledTimes(1)
    const revoked = mock(() => { throw Object.assign(new Error('private principal'), { data: { code: 'identity_mismatch' } }) })
    await expect(runSpecialistCommand('interrupt child-a', revoked, 'owned')).rejects.toThrow('identity_mismatch')
    expect(revoked).toHaveBeenCalledTimes(1)
  })
})
