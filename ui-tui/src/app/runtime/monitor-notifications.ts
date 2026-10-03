import type { MonitorAvailablePayload } from '@hermes/shared/gateway-events'
import type { RuntimeRequest } from '@hermes/shared/runtime-control'
import { acknowledgeMonitorNotification, monitorNotificationLines, validateMonitorNotification } from '@hermes/shared/runtime-notifications'

/** Terminal queue acceptance is not a paint receipt: acknowledge validated bytes only. */
export function createMonitorNotificationReceiver(request: RuntimeRequest, activeSession: () => string | null, display: (text: string) => void) {
  const shown = new Map<string, string>()

  return async (sessionId: string, payload: MonitorAvailablePayload): Promise<void> => {
    if (activeSession() !== sessionId) {return}

    try {
      const notice = await validateMonitorNotification(payload)

      if (activeSession() !== sessionId) {return}
      const key = `${sessionId}:${notice.deliveryId}`, prior = shown.get(key)

      if (prior && prior !== notice.sha256) {throw new Error('Conflicting immutable notification')}

      if (!prior) {
        display(monitorNotificationLines(notice).join('\n'))
        shown.set(key, notice.sha256)

        if (shown.size > 200) {shown.delete(shown.keys().next().value!)}
      }

      await acknowledgeMonitorNotification(request, sessionId, notice, false)
    } catch {
      if (activeSession() === sessionId) {display('Monitor notification or byte receipt unavailable. Inspect retained monitor/delivery history; no source check or mission was repeated.')}
    }
  }
}
