import type { MonitorAvailablePayload } from './gateway-contract.generated.js'
import type { RuntimeRequest } from './runtime-control.js'

export interface MonitorNoticeItem {
  intentId: string
  question: string
  observedAt: number
  sourceRefs: string[]
  previousSourceRefs: string[]
}
export interface ValidatedMonitorNotice {
  deliveryId: string
  attemptToken: string
  sha256: string
  scheduleId: string
  scheduleVersion: number
  policyRevision: number
  kind: 'change' | 'digest'
  items: MonitorNoticeItem[]
}

const object = (value: unknown): Record<string, unknown> => {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {throw new Error('Invalid monitor notification record')}

  return value as Record<string, unknown>
}

const text = (value: unknown, max = 512): string => {
  if (typeof value !== 'string' || !value || value.length > max) {throw new Error('Invalid monitor notification text')}

  return value
}

const integer = (value: unknown): number => {
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < 1) {throw new Error('Invalid monitor notification version')}

  return value
}

const refs = (value: unknown): string[] => {
  if (!Array.isArray(value) || value.length > 64) {throw new Error('Invalid monitor source references')}

  return value.map(raw => {
    const ref = object(raw)
    const digest = text(ref.sha256, 64)

    if (!/^[a-f0-9]{64}$/.test(digest)) {throw new Error('Invalid source digest')}

    return `${text(ref.artifact_id)}@${integer(ref.version)} · SHA256 ${digest}`
  })
}

/** Validate the exact complete UTF-8 notification before any received-byte acknowledgment. */
export async function validateMonitorNotification(payload: MonitorAvailablePayload): Promise<ValidatedMonitorNotice> {
  const bytes = new TextEncoder().encode(payload.notification_json)

  if (bytes.length > 131072 || !/^[a-f0-9]{64}$/.test(payload.sha256)) {throw new Error('Notification exceeds its bounded contract')}
  const actual = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))).map(value => value.toString(16).padStart(2, '0')).join('')

  if (actual !== payload.sha256) {throw new Error('Notification digest mismatch; receipt was not acknowledged')}
  const record = object(JSON.parse(payload.notification_json))

  if (record.schema_version !== 1 || !['change', 'digest'].includes(String(record.kind)) || !Array.isArray(record.items) || record.items.length < 1 || record.items.length > 8) {throw new Error('Unsupported monitor notification schema')}
  const scheduleId = text(record.schedule_id), scheduleVersion = integer(record.schedule_version)

  const items = record.items.map(raw => {
    const item = object(raw)

    if (item.source_scope !== 'retained_local_artifacts' || item.live_connection_verified !== false || item.schedule_id !== scheduleId || item.schedule_version !== scheduleVersion || typeof item.observed_at !== 'number' || !Number.isFinite(item.observed_at)) {throw new Error('Notification source/schedule scope is invalid')}

    return { intentId: text(item.intent_id), question: text(item.question, 8192), observedAt: item.observed_at, sourceRefs: refs(item.source_refs), previousSourceRefs: refs(item.previous_source_refs) }
  })

  return { deliveryId: text(payload.delivery_id), attemptToken: text(payload.attempt_token), sha256: actual, scheduleId, scheduleVersion, policyRevision: integer(record.policy_revision), kind: record.kind as 'change' | 'digest', items }
}

export function monitorNotificationLines(notice: ValidatedMonitorNotice): string[] {
  return [`Monitor ${notice.scheduleId}@${notice.scheduleVersion} · ${notice.kind} · policy ${notice.policyRevision}`,
    ...notice.items.flatMap(item => [item.question, `Observed ${new Date(item.observedAt * 1000).toISOString()} · notice ${item.intentId}`, ...item.sourceRefs.map(ref => `Current source ${ref}`), ...item.previousSourceRefs.map(ref => `Previous source ${ref}`)]),
    'Retained local artifact comparison; no live external connection is certified. Receipt acknowledgment means received bytes/text, never human read.']
}

export async function acknowledgeMonitorNotification(request: RuntimeRequest, sessionId: string, notice: ValidatedMonitorNotice, textRendered: boolean): Promise<void> {
  await request('runtime.delivery.ack', { session_id: sessionId, schema_version: 1, delivery_id: notice.deliveryId, attempt_token: notice.attemptToken, sha256: notice.sha256, artifact_received: true, text_received: textRendered })
}
