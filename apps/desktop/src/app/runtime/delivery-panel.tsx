import type { RuntimeDeliveryReceipt } from '@hermes/shared/gateway-events'
import type { RuntimeRequest } from '@hermes/shared/runtime-control'
import { useEffect, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'

import { useRuntimeUiText } from './runtime-ui-copy'

export function DeliveryPanel({ request, sessionId, connected }: { request: RuntimeRequest; sessionId: string; connected: boolean }) {
  const rt = useRuntimeUiText()
  const [id, setId] = useState(''), [delivery, setDelivery] = useState<RuntimeDeliveryReceipt | null>(null), [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null)
  const lifetimeRef = useRef({ request, sessionId, connected, epoch: 0, pending: false })

  if (lifetimeRef.current.request !== request || lifetimeRef.current.sessionId !== sessionId || lifetimeRef.current.connected !== connected) {
    lifetimeRef.current.epoch++
    lifetimeRef.current = { request, sessionId, connected, epoch: 0, pending: false }
  }

  const lifetime = lifetimeRef.current
  useEffect(() => { lifetime.epoch++; lifetime.pending = false; setBusy(false); setDelivery(null);

 return () => { lifetime.epoch++; lifetime.pending = false } }, [lifetime])

  async function load(retry: boolean) {
    if (lifetime.pending || !connected || !id.trim()) {return}

    if (retry && (!delivery || delivery.delivery_id !== id.trim())) {return}
    const flight = ++lifetime.epoch
    lifetime.pending = true; setBusy(true); setError(null)

    try {
      const result = await request(retry ? 'runtime.delivery.retry' : 'runtime.delivery.status', { session_id: sessionId, schema_version: 1, delivery_id: id.trim() })

      if (flight === lifetime.epoch) {setDelivery(result)}
    } catch (caught) { if (flight === lifetime.epoch) {setError(`${caught instanceof Error ? caught.message : rt("Delivery unavailable")}. Execution was not rerun.`)} }
    finally { if (flight === lifetime.epoch) { lifetime.pending = false; setBusy(false) } }
  }

  return <section className="grid gap-2"><h4 className="text-sm font-medium">{rt("Delivery recovery")}</h4><label>{rt("Existing delivery ID")}<Input disabled={busy} onChange={event => { lifetime.epoch++; setId(event.target.value); setDelivery(null) }} value={id} /></label><div className="flex flex-wrap gap-2"><Button disabled={!connected || busy || !id.trim()} onClick={() => void load(false)} size="xs" variant="secondary">{rt("Inspect delivery")}</Button><Button disabled={!connected || busy || !delivery || delivery.state === 'delivered'} onClick={() => void load(true)} size="xs" variant="secondary">{rt("Retry only this result notification")}</Button></div>
    {delivery && <div aria-live="polite" className="grid gap-1 text-sm"><p>{delivery.delivery_id}: {delivery.state}</p><p>{rt("Receipt level:") + " "}{delivery.acknowledgment_level}{rt("; text") + " "}{delivery.components.text}{rt("; artifact") + " "}{delivery.components.artifact}</p><p>{delivery.artifact_id}@{delivery.version}{rt("; attempts") + " "}{delivery.attempt_count}/{delivery.max_attempts}</p>{delivery.last_error && <p>{delivery.last_error}</p>}</div>}
    <p className="text-xs text-muted-foreground">{rt("Failed/unknown delivery does not mean the mission failed. This control never restarts inference or claims that a person read the result.")}</p>{error && <p role="alert">{error}</p>}
  </section>
}
