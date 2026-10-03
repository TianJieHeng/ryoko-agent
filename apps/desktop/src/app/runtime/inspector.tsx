import { RuntimeControl, type RuntimeRequest, runtimeWorkLabel } from '@hermes/shared/runtime-control'
import { inspectRuntimeIdentity, runtimeIdentityLines, type RuntimeIdentityView } from '@hermes/shared/runtime-identity'
import { useStore } from '@nanostores/react'
import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react'

import { Button } from '@/components/ui/button'
import { useI18n } from '@/i18n'
import { $activeGatewayRoute, $gateway } from '@/store/gateway'
import { $activeSessionId, $gatewayState } from '@/store/session'

import { runtimeCopy } from './copy'
import { RuntimeFeaturePanel } from './feature-panel'
import { ownedRuntimeScope } from './owned-runtime-scope'
import { RecoveryReferences } from './recovery-references'
import { useRuntimeUiText } from './runtime-ui-copy'

const gatewayViewIds = new WeakMap<object, number>()
let nextGatewayViewId = 0

export function RuntimeInspector() {
  const rt = useRuntimeUiText()
  const gateway = useStore($gateway)
  const route = useStore($activeGatewayRoute)
  const sessionId = useStore($activeSessionId)
  const connectionState = useStore($gatewayState)
  const { locale } = useI18n()
  const copy = runtimeCopy(locale)

  if (!gateway || !sessionId) {return <p className="text-sm text-muted-foreground">{copy.noSession}</p>}

  if (!gatewayViewIds.has(gateway)) {gatewayViewIds.set(gateway, ++nextGatewayViewId)}

  try { ownedRuntimeScope(gateway, String(route), sessionId) } catch { return <p role="alert">{rt("Inspect pending recovery references in existing sessions before opening another runtime view.")}</p> }

  // Keying the view prevents even one paint of another profile's cached projection.
  return <OwnedRuntimeInspector connected={connectionState === 'open'} gateway={gateway} key={`${route}:${sessionId}:${gatewayViewIds.get(gateway)}`} route={String(route)} sessionId={sessionId} />
}

interface OwnedRuntimeInspectorProps {
  connected: boolean
  route?: string
  gateway: NonNullable<ReturnType<typeof $gateway.get>>
  sessionId: string
}

export function OwnedRuntimeInspector({ connected, gateway, sessionId, route = 'current' }: OwnedRuntimeInspectorProps) {
  const rt = useRuntimeUiText()
  const { locale } = useI18n()
  const copy = runtimeCopy(locale)
  // Capture this exact socket. A reconnect never silently retargets a mutation.
  const scope = ownedRuntimeScope(gateway, route, sessionId)
  scope.setConnected(connected)
  useSyncExternalStore(scope.subscribe, scope.getGeneration, scope.getGeneration)
  const references = useSyncExternalStore(scope.subscribe, scope.getSnapshot, scope.getSnapshot)
  const request: RuntimeRequest = scope.request
  const [, refreshBinding] = useState(0)
  useEffect(() => {
    scope.setConnected(connected)
    refreshBinding(value => value + 1)

    return () => { scope.setConnected(false) }
  }, [scope, connected])

  const control = useMemo(() => {
    const value = new RuntimeControl(request)
    value.select('owned-transport', sessionId)

    return value
  }, [request, sessionId])

  const state = useSyncExternalStore(control.subscribe, control.getState, control.getState)
  const [identity, setIdentity] = useState<{ request: RuntimeRequest; view: RuntimeIdentityView } | null>(null)
  const [checking, setChecking] = useState(false)
  const lifetimeRef = useRef({ request, sessionId, connected, control, generation: 0 })

  if (lifetimeRef.current.request !== request || lifetimeRef.current.sessionId !== sessionId || lifetimeRef.current.connected !== connected || lifetimeRef.current.control !== control) {
    lifetimeRef.current.generation++
    lifetimeRef.current = { request, sessionId, connected, control, generation: 0 }
  }

  const lifetime = lifetimeRef.current

  async function refresh() {
    const flight = ++lifetime.generation
    setChecking(true)
    await control.refresh()
    const result = await inspectRuntimeIdentity(request, sessionId)

    if (flight !== lifetime.generation) {return}
    setIdentity({ request, view: result })
    setChecking(false)
  }

  useEffect(() => {
    if (!connected) {
      lifetime.generation++
      control.disconnected()
      setChecking(false)
    }

    return () => { lifetime.generation++ }
  }, [connected, control, lifetime])

  return (
    <section aria-label={copy.title} className="grid gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-medium">{copy.title}</h3>
        <Button disabled={!connected || checking} onClick={() => void refresh()} size="xs" variant="secondary">{copy.refresh}</Button>
      </div>
      <div aria-busy={checking} aria-live="polite" className="grid gap-1 text-sm text-muted-foreground">
        <p>{state.transport}: {runtimeWorkLabel(state.snapshot)}</p>
        {state.error && <p role="status">{state.error}</p>}
        {identity?.request === request && runtimeIdentityLines(identity.view).map((line, index) => <p className="break-words" key={index}>{line}</p>)}
        {state.snapshot && <p>{rt("Revision") + " "}{state.snapshot.revision}{rt("; pending decisions") + " "}{state.snapshot.outstanding_requests.length}{rt("; unresolved effects") + " "}{state.snapshot.unresolved_effects.length}</p>}
      </div>
      <RecoveryReferences connected={connected} key={scope.generation} scope={scope} />
      <RuntimeFeaturePanel connected={connected} connectionGeneration={scope.generation} recoveryReferences={references} request={request} sessionId={sessionId} />
      {state.pending && <Button disabled={!connected || state.transport !== 'ready'} onClick={() => void control.retry()} size="xs" variant="secondary">{copy.retry}</Button>}
    </section>
  )
}
