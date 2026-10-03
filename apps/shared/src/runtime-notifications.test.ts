import { expect, it, vi } from 'vitest'

import type { RuntimeRequest } from './runtime-control.js'
import { acknowledgeMonitorNotification, monitorNotificationLines, validateMonitorNotification } from './runtime-notifications.js'

async function payload(overrides: Record<string, unknown> = {}) {
  const notification_json = JSON.stringify({ schema_version: 1, schedule_id: 's', schedule_version: 1, policy_revision: 2, kind: 'change', items: [{ intent_id: 'n', schedule_id: 's', schedule_version: 1, question: '<script>untrusted</script>', observed_at: 100, source_refs: [{ artifact_id: 'a', version: 2, sha256: 'a'.repeat(64) }], previous_source_refs: [], source_scope: 'retained_local_artifacts', live_connection_verified: false, ...overrides }] })
  const sha256 = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(notification_json)))).map(value => value.toString(16).padStart(2, '0')).join('')

  return { delivery_id: 'd', attempt_token: 't', sha256, notification_json }
}

it('acknowledges only verified complete bytes and explicitly separates text rendering', async () => {
  const notice = await validateMonitorNotification(await payload())
  const request = vi.fn(async () => ({})) as RuntimeRequest
  await acknowledgeMonitorNotification(request, 'owned', notice, false)
  expect(request).toHaveBeenCalledWith('runtime.delivery.ack', expect.objectContaining({ artifact_received: true, text_received: false, session_id: 'owned', attempt_token: 't' }))
  expect(monitorNotificationLines(notice).join('\n')).toContain('no live external connection')
})
it('rejects digest corruption and foreign source/schedule claims', async () => {
  await expect(validateMonitorNotification({ ...await payload(), sha256: 'f'.repeat(64) })).rejects.toThrow('digest mismatch')
  await expect(validateMonitorNotification(await payload({ live_connection_verified: true }))).rejects.toThrow('scope')
  await expect(validateMonitorNotification(await payload({ schedule_id: 'foreign' }))).rejects.toThrow('scope')
})
