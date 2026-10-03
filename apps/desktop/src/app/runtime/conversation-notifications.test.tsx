// @vitest-environment jsdom
import { act, cleanup, render, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

import type { HermesGateway } from '@/hermes'
vi.mock('@nanostores/react', () => ({ useStore: () => 'open' }))
vi.mock('@/store/session', () => ({ $gatewayState: {} }))
import { ConversationNotifications } from './conversation-notifications'
afterEach(cleanup)

async function event(session_id = 'owned') {
  const notification_json = JSON.stringify({ schema_version: 1, schedule_id: 's', schedule_version: 1, policy_revision: 1, kind: 'change', items: [{ intent_id: 'n', schedule_id: 's', schedule_version: 1, question: '<script>private source text</script>', observed_at: 100, source_refs: [], previous_source_refs: [], source_scope: 'retained_local_artifacts', live_connection_verified: false }] })
  const sha256 = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(notification_json)))).map(value => value.toString(16).padStart(2, '0')).join('')

  return { session_id, payload: { delivery_id: 'd', attempt_token: 't', sha256, notification_json } }
}

function gateway() {
  let listener!: (value: Awaited<ReturnType<typeof event>>) => void
  const request = vi.fn(async () => ({}))

  const value = { request, on: vi.fn((_name: string, callback: (value: Awaited<ReturnType<typeof event>>) => void) => { listener = callback;

 return () => {} }) } as unknown as HermesGateway

  return { value, request, emit: (data: Awaited<ReturnType<typeof event>>) => listener(data) }
}

it('renders escaped verified text before exact received acknowledgment and deduplicates replay', async () => {
  const rpc = gateway(), view = render(<ConversationNotifications gateway={rpc.value} sessionId="owned" />)
  const data = await event()
  await act(async () => rpc.emit(data))
  await waitFor(() => expect(view.getByText('<script>private source text</script>')).toBeTruthy())
  expect(view.container.querySelector('script')).toBeNull()
  await waitFor(() => expect(rpc.request).toHaveBeenCalledWith('runtime.delivery.ack', expect.objectContaining({ delivery_id: 'd', sha256: data.payload.sha256, text_received: true, artifact_received: true })))
  await act(async () => rpc.emit(data))
  expect(view.getAllByText('<script>private source text</script>')).toHaveLength(1)
  expect(rpc.request).toHaveBeenCalledTimes(1)
})
it('rejects corrupt bytes and foreign-session notices without acknowledgment', async () => {
  const rpc = gateway(), view = render(<ConversationNotifications gateway={rpc.value} sessionId="owned" />)
  await act(async () => rpc.emit(await event('foreign')))
  const data = await event(); data.payload.sha256 = 'f'.repeat(64)
  await act(async () => rpc.emit(data))
  await waitFor(() => expect(view.getByRole('alert')).toBeTruthy())
  expect(rpc.request).not.toHaveBeenCalled()
  expect(view.queryByText('<script>private source text</script>')).toBeNull()
})
it('does not acknowledge or display a payload validated after scope replacement', async () => {
  const rpc = gateway(), view = render(<ConversationNotifications gateway={rpc.value} sessionId="owned" />)
  const data = await event()
  act(() => { rpc.emit(data); view.rerender(<ConversationNotifications gateway={rpc.value} sessionId="other" />) })
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)) })
  expect(view.queryByText('<script>private source text</script>')).toBeNull()
  expect(rpc.request).not.toHaveBeenCalled()
})
