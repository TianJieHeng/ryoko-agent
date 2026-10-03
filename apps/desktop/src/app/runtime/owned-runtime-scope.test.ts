import { expect, it, vi } from 'vitest'

import type { RuntimeGatewayTransport } from './owned-runtime-scope'
import { ownedRuntimeScope, OwnedRuntimeScope } from './owned-runtime-scope'
it('retains only the original owned reference across same-object reconnect and never restores old write authority', async () => {
  const gateway = { request: vi.fn(async (method: string, params: Record<string, unknown>) => method.endsWith('.status') ? { command_id: params.command_id, status: 'cancelled' } : { approval_id: 'ap' }) as RuntimeGatewayTransport['request'] }
  const scope = ownedRuntimeScope(gateway, 'profile-a', 'session-a'); scope.setConnected(true)
  const old = scope.request
  await old('runtime.artifact.prepare', { session_id: 'session-a', schema_version: 1, project_id: 'p', command_id: 'c', request_id: 'r', content: '# Review' })
  expect(scope.getSnapshot()).toMatchObject([{ id: 'c', unknown: false }])
  scope.setConnected(false); scope.setConnected(true)
  expect(scope.request).not.toBe(old)
  await expect(old('runtime.artifact.cancel', { session_id: 'session-a', schema_version: 1, command_id: 'c' })).rejects.toThrow('transport changed')
  expect(gateway.request).toHaveBeenCalledOnce()
  await scope.request('runtime.artifact.status', { session_id: 'session-a', schema_version: 1, command_id: 'c' })
  expect(scope.getSnapshot()).toEqual([])
})
it('isolates route/session references, fences foreign sessions and keeps unknown writes without replay', async () => {
  const gateway = { request: vi.fn(async () => { throw new Error('lost reply') }) as RuntimeGatewayTransport['request'] }
  const a = ownedRuntimeScope(gateway, 'profile', 'a'), b = ownedRuntimeScope(gateway, 'profile', 'b'); a.setConnected(true); b.setConnected(true)
  await expect(a.request('runtime.artifact.cancel', { session_id: 'a', schema_version: 1, command_id: 'unknown' })).rejects.toThrow()
  expect(a.getSnapshot()).toMatchObject([{ id: 'unknown', unknown: true }]); expect(b.getSnapshot()).toEqual([])
  await expect(a.request('runtime.artifact.cancel', { session_id: 'b', schema_version: 1, command_id: 'foreign' })).rejects.toThrow('owned session')
  expect(gateway.request).toHaveBeenCalledOnce()
  expect(ownedRuntimeScope(gateway, 'profile', 'a')).toBe(a)
})
it('bounds recovery references without evicting an unresolved identity to admit new work', async () => {
  const gateway = { request: vi.fn(async () => { throw new Error('unknown') }) as RuntimeGatewayTransport['request'] }, scope = new OwnedRuntimeScope(gateway, 's'); scope.setConnected(true)

  for (let index = 0; index < 100; index++) {await scope.request('runtime.artifact.cancel', { session_id: 's', schema_version: 1, command_id: `c-${index}` }).catch(() => {})}
  await expect(scope.request('runtime.artifact.cancel', { session_id: 's', schema_version: 1, command_id: 'overflow' })).rejects.toThrow('Inspect retained')
  expect(scope.getSnapshot()).toHaveLength(100); expect(gateway.request).toHaveBeenCalledTimes(100)
})
it('fences same-object socket transitions even when a UI could batch closed and open renders', async () => {
  let stateChanged!: (state: string) => void

  const gateway = { request: vi.fn(async () => ({})) as RuntimeGatewayTransport['request'], connectionState: 'open', onState: (listener: (state: string) => void) => { stateChanged = listener; listener('open');

 return vi.fn() } }

  const scope = new OwnedRuntimeScope(gateway, 's'), original = scope.request
  stateChanged('closed'); stateChanged('open')
  await expect(original('runtime.snapshot', { session_id: 's', schema_version: 1 })).rejects.toThrow('transport changed')
  expect(gateway.request).not.toHaveBeenCalled()
  await scope.request('runtime.snapshot', { session_id: 's', schema_version: 1 })
  expect(gateway.request).toHaveBeenCalledOnce()
})
it('evicts only idle scopes and releases their direct socket subscriptions', async () => {
  const releases: ReturnType<typeof vi.fn>[] = []

  const gateway = { request: vi.fn(async () => ({})) as RuntimeGatewayTransport['request'], onState: (listener: (state: string) => void) => { const release = vi.fn(); releases.push(release); listener('open');

 return release } }

  const first = ownedRuntimeScope(gateway, 'profile', 'old'), request = first.request

  for (let index = 0; index < 32; index++) {ownedRuntimeScope(gateway, 'profile', `s-${index}`)}
  expect(releases[0]).toHaveBeenCalledOnce()
  await expect(request('runtime.snapshot', { session_id: 'old', schema_version: 1 })).rejects.toThrow('transport changed')
})
it('keeps lost operator authorization digests and speech request IDs through reconnect without replay', async () => {
  const gateway = { request: vi.fn(async () => { throw new Error('lost') }) as RuntimeGatewayTransport['request'] }
  const scope = new OwnedRuntimeScope(gateway, 's'); scope.setConnected(true)
  await scope.request('runtime.operations.repair.apply', { session_id: 's', schema_version: 1, plan_json: '{}', authorization_digest: 'a'.repeat(64) }).catch(() => {})
  await scope.request('runtime.voice.admit', { session_id: 's', schema_version: 1, request_id: 'admission' }).catch(() => {})
  scope.setConnected(false); scope.setConnected(true)
  expect(scope.getSnapshot()).toMatchObject([{ kind: 'operator', id: 'a'.repeat(64), unknown: true }, { kind: 'speech', id: 'admission', unknown: true }])
  expect(gateway.request).toHaveBeenCalledTimes(2)
})
it('retains the original speech identity when final transcription reply is lost; discard is not budget receipt', async () => {
  const gateway = { request: vi.fn(async (method: string) => {
    if (method.endsWith('.start')) { return { response_json: JSON.stringify({ state: 'recording', capture_id: 'capture' }) } }

    if (method.endsWith('.cancel')) { return { response_json: JSON.stringify({ state: 'discarded', mission_cancelled: false }) } }
    throw new Error('final reply lost')
  }) as RuntimeGatewayTransport['request'] }

  const scope = new OwnedRuntimeScope(gateway, 's'); scope.setConnected(true)
  await scope.request('runtime.voice.capture.start', { session_id: 's', schema_version: 1, request_id: 'original', budget_account_id: 'account' })
  expect(scope.getSnapshot()).toMatchObject([{ id: 'original', kind: 'speech', unknown: false }])
  await scope.request('runtime.voice.capture.feed', { session_id: 's', schema_version: 1, capture_id: 'capture', sequence: 0, pcm_base64: 'AAA=', final: true }).catch(() => {})
  await scope.request('runtime.voice.capture.cancel', { session_id: 's', schema_version: 1 })
  expect(scope.getSnapshot()).toMatchObject([{ id: 'original', unknown: true }])
})
it('specialist status has its own read-only recovery authority rather than artifact status', async () => {
  const gateway = { request: vi.fn(async () => ({ status: 'completed' })) as RuntimeGatewayTransport['request'] }
  const scope = new OwnedRuntimeScope(gateway, 's'); scope.setConnected(true)
  await scope.request('runtime.specialist.status', { session_id: 's', schema_version: 1, command_id: 'child-command' })
  expect(scope.getSnapshot()).toEqual([])
  expect(gateway.request).toHaveBeenCalledExactlyOnceWith('runtime.specialist.status', { session_id: 's', schema_version: 1, command_id: 'child-command' })
})
it('cached source preview reads cannot erase the prepared command recovery identity', async () => {
  const gateway = { request: vi.fn(async () => ({})) as RuntimeGatewayTransport['request'] }
  const scope = new OwnedRuntimeScope(gateway, 's'); scope.setConnected(true)
  await scope.request('runtime.sources.prepare', { session_id: 's', schema_version: 1, project_id: 'p', command_id: 'source-command', request_id: 'request', selection: { kind: 'gmail_thread', account_id: 'account', mailbox: 'owner@example.invalid', thread_id: 'thread' } })
  await scope.request('runtime.sources.preview', { session_id: 's', schema_version: 1, project_id: 'p', command_id: 'source-command', preparation_id: 'preparation', part: 'original', offset: 0, limit: 65536 })
  expect(scope.getSnapshot()).toMatchObject([{ id: 'source-command', kind: 'artifact', unknown: false }])
})
it('malformed speech acknowledgments remain unknown instead of clearing the original request', async () => {
  const gateway = { request: vi.fn(async () => ({ response_json: '{}' })) as RuntimeGatewayTransport['request'] }
  const scope = new OwnedRuntimeScope(gateway, 's'); scope.setConnected(true)
  await expect(scope.request('runtime.voice.speak', { session_id: 's', schema_version: 1, text: 'Exact text', request_id: 'original', budget_account_id: 'account' })).rejects.toThrow('did not acknowledge')
  expect(scope.getSnapshot()).toMatchObject([{ id: 'original', kind: 'speech', unknown: true }])
})
