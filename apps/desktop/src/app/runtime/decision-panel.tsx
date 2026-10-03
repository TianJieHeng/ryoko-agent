import type { RuntimeRequest } from '@hermes/shared/runtime-control'
import { useEffect, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { useI18n } from '@/i18n'

import { type DecisionInspection, inspectRuntimeDecisions } from '../../../../shared/src/runtime-decisions'

import { useRuntimeUiText } from './runtime-ui-copy'

const chrome = {
  en: ['Recorded decisions', 'Inspect receipt history', 'Next retained page'], de: ['Aufgezeichnete Entscheidungen', 'Belege prüfen', 'Nächste gespeicherte Seite'],
  es: ['Decisiones registradas', 'Revisar historial', 'Siguiente página conservada'], fr: ['Décisions enregistrées', 'Examiner les reçus', 'Page conservée suivante'],
  ja: ['記録された判断', '記録を確認', '次の保持ページ'], zh: ['已记录的决策', '检查决策记录', '下一页留存记录'], 'zh-hant': ['已記錄的決策', '檢查決策紀錄', '下一頁保留紀錄'],
  ar: ['القرارات المسجلة', 'فحص سجل الإيصالات', 'الصفحة المحفوظة التالية'], ru: ['Записанные решения', 'Проверить историю', 'Следующая сохранённая страница']
} as const

export function DecisionPanel({ request, sessionId, connected }: { request: RuntimeRequest; sessionId: string; connected: boolean }) {
  const rt = useRuntimeUiText()
  const { locale } = useI18n(), c = chrome[locale as keyof typeof chrome] ?? chrome.en
  const [pages, setPages] = useState<DecisionInspection[]>([]), [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null)
  const lifetimeRef = useRef({ request, sessionId, connected, epoch: 0, pending: null as symbol | null })

  if (lifetimeRef.current.request !== request || lifetimeRef.current.sessionId !== sessionId || lifetimeRef.current.connected !== connected) {
    lifetimeRef.current.epoch++
    lifetimeRef.current = { request, sessionId, connected, epoch: 0, pending: null as symbol | null }
  }

  const lifetime = lifetimeRef.current
  useEffect(() => { lifetime.epoch++; lifetime.pending = null; setBusy(false); setPages([]); setError(null);

 return () => { lifetime.epoch++ } }, [lifetime])

  async function inspect(next = false) {
    if (!connected || lifetime.pending) {return}
    const token = Symbol(), flight = lifetime.epoch; lifetime.pending = token; setBusy(true); setError(null)

    try {
      const result = await inspectRuntimeDecisions(request, sessionId, next ? pages.at(-1)?.cursor : undefined)

      if (flight === lifetime.epoch) {setPages(previous => next ? [...previous, result].slice(-5) : [result])}
    } catch { if (flight === lifetime.epoch) {setError(rt("Decision receipts unavailable; no reason or service status was invented"))} }
    finally { if (lifetime.pending === token) { lifetime.pending = null; setBusy(false) } }
  }

  return <section className="grid gap-3"><h4 className="text-sm font-medium">{c[0]}</h4><div className="flex flex-wrap gap-2"><Button disabled={!connected || busy} onClick={() => void inspect()} size="xs" variant="secondary">{c[1]}</Button><Button disabled={!connected || busy || !pages.at(-1)?.hasMore} onClick={() => void inspect(true)} size="xs" variant="secondary">{c[2]}</Button></div>
    {pages.map((page, index) => <div aria-live="polite" className="grid gap-1 break-words text-xs" key={`${page.cursor}:${index}`}>{page.lines.map((line, at) => <p key={at}>{line}</p>)}</div>)}
    {error && <p role="alert">{error}</p>}<p className="text-xs text-muted-foreground">{rt("Read-only. This cannot activate LAYA, change per-channel policy, reduce an approval floor or send private packets. Feedback/override APIs and production evaluation gates remain unavailable.")}</p>
  </section>
}
