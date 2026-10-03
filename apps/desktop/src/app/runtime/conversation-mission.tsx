import type { MissionRecord } from '@hermes/shared/gateway-events'
import type { RuntimeRequest } from '@hermes/shared/runtime-control'
import { useStore } from '@nanostores/react'
import { useEffect, useMemo, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import type { HermesGateway } from '@/hermes'
import { useI18n } from '@/i18n'
import { $gatewayState } from '@/store/session'

import { DeliveryPanel } from './delivery-panel'
import { MissionPanel } from './mission-panel'
import { useRuntimeUiText } from './runtime-ui-copy'

const chrome = {
  en: ['Mission', 'Review this conversation’s work and delivery'], de: ['Auftrag', 'Arbeit und Zustellung dieser Unterhaltung prüfen'],
  es: ['Misión', 'Revisar el trabajo y la entrega de esta conversación'], fr: ['Mission', 'Vérifier le travail et la livraison de cette conversation'],
  ja: ['ミッション', 'この会話の作業と配信を確認'], zh: ['任务', '查看此对话的工作与交付状态'], 'zh-hant': ['任務', '查看此對話的工作與交付狀態'],
  ar: ['المهمة', 'مراجعة العمل والتسليم في هذه المحادثة'], ru: ['Задача', 'Проверить работу и доставку в этом разговоре']
} as const

const gatewayKeys = new WeakMap<HermesGateway, number>()
let nextGatewayKey = 0

function gatewayKey(gateway: HermesGateway | null | undefined) {
  if (!gateway) {return 0}

  if (!gatewayKeys.has(gateway)) {gatewayKeys.set(gateway, ++nextGatewayKey)}

  return gatewayKeys.get(gateway)!
}

export function ConversationMissionControl({ gateway, sessionId }: { gateway: HermesGateway | null | undefined; sessionId: string | null }) {
  const rt = useRuntimeUiText()
  const [open, setOpen] = useState(false)
  const [summary, setSummary] = useState<{ scope: string; mission: MissionRecord | null; checked: number } | null>(null)
  const state = useStore($gatewayState)
  const { locale } = useI18n()
  const [label, description] = chrome[locale as keyof typeof chrome] ?? chrome.en
  const scope = JSON.stringify([gatewayKey(gateway), sessionId])

  const request = useMemo<RuntimeRequest>(() => (method, params) => {
    if (!gateway) {return Promise.reject(new Error(rt("Conversation gateway is unavailable")))}

    return gateway.request(method, { ...params })
  }, [gateway, rt])

  useEffect(() => { setOpen(false) }, [scope])
  useEffect(() => {
    if (!gateway || !sessionId || state !== 'open') {return}
    let active = true, flight = 0

    async function refresh() {
      const read = ++flight

      try {
        const result = await request('runtime.mission.get', { session_id: sessionId!, schema_version: 1 })

        if (active && read === flight) {setSummary({ scope, mission: result.mission, checked: Date.now() })}
      } catch { /* Existing sessions may lack a durable identity; no false mission state. */ }
    }

    void refresh()
    const offMessage = gateway.on('message.complete', event => { if (event.session_id === sessionId) {void refresh()} })
    const offResult = gateway.on('runtime.result.available', event => { if (event.session_id === sessionId) {void refresh()} })

    return () => { active = false; offMessage(); offResult() }
  }, [gateway, request, scope, sessionId, state])

  if (!gateway || !sessionId) {return null}
  const current = summary?.scope === scope ? summary : null

  return <div className="flex shrink-0 items-start justify-between gap-2">
    <div className="min-w-0 text-xs text-muted-foreground">{current?.mission && <><p className="truncate">{current.mission.state.replaceAll('_', ' ')}: {current.mission.outcome}</p><p className="truncate">{current.mission.next_step}</p><p>{state === 'open' ? rt("Snapshot") : rt("Stale snapshot")} · {new Date(current.checked).toLocaleTimeString()}</p></>}</div>
    <Button onClick={() => setOpen(true)} size="xs" variant="text">{label}</Button>
    <Dialog onOpenChange={setOpen} open={open}>
      <DialogContent className="max-h-[85vh] overflow-y-auto">
        <DialogHeader><DialogTitle>{label}</DialogTitle><DialogDescription>{description}</DialogDescription></DialogHeader>
        <div className="grid gap-5" key={scope}><MissionPanel connected={state === 'open'} onMission={mission => setSummary({ scope, mission, checked: Date.now() })} request={request} sessionId={sessionId} /><DeliveryPanel connected={state === 'open'} request={request} sessionId={sessionId} /></div>
      </DialogContent>
    </Dialog>
  </div>
}
