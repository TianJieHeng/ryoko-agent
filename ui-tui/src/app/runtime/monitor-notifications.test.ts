import type { RuntimeRequest } from '@hermes/shared/runtime-control'
import { expect, it, vi } from 'vitest'

import { createMonitorNotificationReceiver } from './monitor-notifications.js'

async function payload() {
  const notification_json = JSON.stringify({ schema_version: 1, schedule_id: 's', schedule_version: 1, policy_revision: 1, kind: 'digest', items: [{ intent_id: 'n', schedule_id: 's', schedule_version: 1, question: 'Changed retained source', observed_at: 100, source_refs: [], previous_source_refs: [], source_scope: 'retained_local_artifacts', live_connection_verified: false }] })
  const sha256 = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(notification_json)))).map(value => value.toString(16).padStart(2, '0')).join('')

  return { delivery_id: 'd', attempt_token: 't', sha256, notification_json }
}

it('deduplicates terminal text while acknowledging exact validated bytes without claiming paint or read', async () => {
  const request = vi.fn(async () => ({})) as RuntimeRequest, display = vi.fn()
  const receive = createMonitorNotificationReceiver(request, () => 'owned', display), data = await payload()
  await receive('owned', data); await receive('owned', data)
  expect(display).toHaveBeenCalledTimes(1)
  expect(request).toHaveBeenCalledWith('runtime.delivery.ack', expect.objectContaining({ text_received: false, artifact_received: true }))
})
it('suppresses late payloads and acknowledgments after active conversation changes', async () => {
  const request = vi.fn(async () => ({})) as RuntimeRequest, display = vi.fn()
  let active = 'owned'
  const receive = createMonitorNotificationReceiver(request, () => active, display), data = await payload()
  const pending = receive('owned', data); active = 'other'; await pending
  expect(display).not.toHaveBeenCalled(); expect(request).not.toHaveBeenCalled()
})
