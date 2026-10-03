import type { RuntimeRequest } from '@hermes/shared/runtime-control'
import { useStore } from '@nanostores/react'
import { useEffect, useMemo, useRef, useState } from 'react'

import type { HermesGateway } from '@/hermes'
import { $gatewayState } from '@/store/session'

import { acknowledgeMonitorNotification, monitorNotificationLines, type ValidatedMonitorNotice, validateMonitorNotification } from '../../../../shared/src/runtime-notifications'

import { useRuntimeUiText } from './runtime-ui-copy'

interface Props { gateway: HermesGateway | null | undefined; sessionId: string | null }
interface NoticeView { notice: ValidatedMonitorNotice; receipt: 'received' | 'acknowledging' | 'acknowledged' | 'unknown' }

export function ConversationNotifications({ gateway, sessionId }: Props) {
  const [scope, setScope] = useState({ gateway, sessionId, generation: 0 })

  if (scope.gateway !== gateway || scope.sessionId !== sessionId) {
    setScope({ gateway, sessionId, generation: scope.generation + 1 })

    return null
  }

  if (!gateway || !sessionId) {return null}

  return <OwnedNotifications gateway={gateway} key={scope.generation} sessionId={sessionId} />
}

function OwnedNotifications({ gateway, sessionId }: { gateway: HermesGateway; sessionId: string }) {
  const rt = useRuntimeUiText()
  const connection = useStore($gatewayState)
  const request = useMemo<RuntimeRequest>(() => (method, params) => gateway.request(method, { ...params }), [gateway])
  const [notices, setNotices] = useState<NoticeView[]>([]), [error, setError] = useState<string | null>(null)
  const lifetimeRef = useRef({ request, sessionId, connection, active: true, digests: new Map<string, string>(), ackedAttempts: new Set<string>() })

  if (lifetimeRef.current.request !== request || lifetimeRef.current.sessionId !== sessionId || lifetimeRef.current.connection !== connection) {
    lifetimeRef.current.active = false
    lifetimeRef.current = { request, sessionId, connection, active: true, digests: new Map<string, string>(), ackedAttempts: new Set<string>() }
  }

  const lifetime = lifetimeRef.current
  useEffect(() => {
    lifetime.active = true

    if (connection !== 'open') {return () => { lifetime.active = false }}

    const unsubscribe = gateway.on('runtime.monitor.available', event => {
      if (event.session_id !== sessionId || !event.payload) {return}
      void validateMonitorNotification(event.payload).then(notice => {
        if (!lifetime.active) {return}
        const prior = lifetime.digests.get(notice.deliveryId)

        if (prior && prior !== notice.sha256) {throw new Error(rt("Conflicting immutable notification"))}
        lifetime.digests.set(notice.deliveryId, notice.sha256)

        if (lifetime.digests.size > 200) {lifetime.digests.delete(lifetime.digests.keys().next().value!)}
        setNotices(previous => {
          if (previous.some(row => row.notice.deliveryId === notice.deliveryId && row.notice.attemptToken === notice.attemptToken)) {return previous}

          return [...previous.filter(row => row.notice.deliveryId !== notice.deliveryId), { notice, receipt: 'received' as const }].slice(-8)
        })
      }).catch(() => { if (lifetime.active) {setError(rt("A monitor notification could not be validated. Nothing was acknowledged; inspect the retained notification history."))} })
    })

    return () => { lifetime.active = false; unsubscribe() }
  }, [connection, gateway, lifetime, sessionId, rt])
  // A committed visible React tree is the text-received boundary, never evidence of human reading.
  useEffect(() => {
    if (connection !== 'open') {return}

    for (const row of notices) {
      if (row.receipt !== 'received') {continue}
      const key = `${row.notice.deliveryId}:${row.notice.attemptToken}`

      if (lifetime.ackedAttempts.has(key)) {continue}
      lifetime.ackedAttempts.add(key)

      const mark = (receipt: NoticeView['receipt']) => { if (lifetime.active) {setNotices(previous => previous.map(item => item.notice.deliveryId === row.notice.deliveryId && item.notice.attemptToken === row.notice.attemptToken ? { ...item, receipt } : item))} }
      mark('acknowledging')
      void acknowledgeMonitorNotification(request, sessionId, row.notice, true).then(() => mark('acknowledged')).catch(() => mark('unknown'))
    }
  }, [connection, lifetime, notices, request, sessionId])

  if (!notices.length && !error) {return null}

  return <section aria-label={rt("Monitor notifications")} className="grid max-h-64 gap-2 overflow-y-auto rounded border p-2">
    <h3 className="text-sm font-medium">{rt("Monitor changes ·") + " "}{connection === 'open' ? rt("current connection") : rt("stale connection")}</h3>
    {notices.map(row => <article className="grid gap-1 break-words text-xs" key={row.notice.deliveryId}>
      <div aria-live="polite">{monitorNotificationLines(row.notice).map((line, index) => <p key={index}>{line}</p>)}</div>
      <p>{rt("Client receipt:") + " "}{row.receipt}{rt(". This never means a person read the notice.")}</p>
      {row.receipt === 'unknown' && <p role="status">{rt("Acknowledgment outcome unknown. Inspect delivery") + " "}{row.notice.deliveryId}{rt("; no check or mission was rerun.")}</p>}
    </article>)}
    <p className="text-xs text-muted-foreground">{rt("Showing the latest eight received payloads. Open Monitor health for retained history, quiet hours, snooze and durable dismissal; closing this view does not dismiss a notice.")}</p>
    {error && <p role="alert">{error}</p>}
  </section>
}
