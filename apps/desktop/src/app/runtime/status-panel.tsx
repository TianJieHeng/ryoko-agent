import type { RuntimeRequest } from '@hermes/shared/runtime-control'
import { useEffect, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { useI18n } from '@/i18n'

import { inspectRuntimeStatus, runStatusCommand, type RuntimeStatusView } from '../../../../shared/src/runtime-status'

import { useRuntimeUiText } from './runtime-ui-copy'

const chrome = {
  en: ['Current work and repair', 'Project ID (optional)', 'Inspect current queues', 'Ready to review', 'Waiting', 'Active', 'Upcoming', 'Exact effect ID', 'Inspect effect', 'Reconcile local evidence', 'Privacy capabilities'],
  de: ['Aktuelle Arbeit und Reparatur', 'Projekt-ID (optional)', 'Aktuellen Status prüfen', 'Zur Prüfung bereit', 'Wartend', 'Aktiv', 'Bevorstehend', 'Exakte Effekt-ID', 'Effekt prüfen', 'Lokale Belege abgleichen', 'Datenschutzfunktionen'],
  es: ['Trabajo actual y reparación', 'ID de proyecto (opcional)', 'Revisar estado actual', 'Listo para revisar', 'En espera', 'Activo', 'Próximo', 'ID exacto de efecto', 'Revisar efecto', 'Conciliar evidencia local', 'Capacidades de privacidad'],
  fr: ['Travail en cours et réparation', 'ID du projet (facultatif)', 'Vérifier les files actuelles', 'Prêt à examiner', 'En attente', 'Actif', 'À venir', 'ID exact de l’effet', 'Examiner l’effet', 'Rapprocher les preuves locales', 'Fonctions de confidentialité'],
  ja: ['現在の作業と修復', 'プロジェクトID（任意）', '現在の状態を確認', 'レビュー待ち', '待機中', '実行中', '今後の予定', '正確な効果ID', '効果を確認', 'ローカル証拠を照合', 'プライバシー機能'],
  zh: ['当前工作与修复', '项目ID（可选）', '检查当前队列', '待审核', '等待中', '进行中', '即将进行', '准确的操作效果ID', '检查操作效果', '核对本地证据', '隐私功能'],
  'zh-hant': ['目前工作與修復', '專案ID（選填）', '檢查目前佇列', '待審核', '等待中', '進行中', '即將進行', '確切操作效果ID', '檢查操作效果', '核對本機證據', '隱私功能'],
  ar: ['العمل الحالي والإصلاح', 'معرّف المشروع (اختياري)', 'فحص الحالة الحالية', 'جاهز للمراجعة', 'قيد الانتظار', 'نشط', 'قادم', 'معرّف الأثر الدقيق', 'فحص الأثر', 'مطابقة الأدلة المحلية', 'قدرات الخصوصية'],
  ru: ['Текущая работа и восстановление', 'ID проекта (необязательно)', 'Проверить очереди', 'Готово к проверке', 'Ожидание', 'Активно', 'Предстоящее', 'Точный ID эффекта', 'Проверить эффект', 'Сопоставить локальные доказательства', 'Возможности приватности']
} as const

export function StatusPanel({ request, sessionId, connected }: { request: RuntimeRequest; sessionId: string; connected: boolean }) {
  const rt = useRuntimeUiText()
  const { locale } = useI18n()
  const c = chrome[locale as keyof typeof chrome] ?? chrome.en
  const [project, setProject] = useState(''), [effect, setEffect] = useState(''), [view, setView] = useState<RuntimeStatusView | null>(null)
  const [output, setOutput] = useState(''), [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null)
  const lifetimeRef = useRef({ request, sessionId, connected, epoch: 0, pending: null as symbol | null })

  if (lifetimeRef.current.request !== request || lifetimeRef.current.sessionId !== sessionId || lifetimeRef.current.connected !== connected) {
    lifetimeRef.current.epoch++
    lifetimeRef.current = { request, sessionId, connected, epoch: 0, pending: null as symbol | null }
  }

  const lifetime = lifetimeRef.current
  useEffect(() => { lifetime.epoch++; lifetime.pending = null; setBusy(false); setView(null); setOutput('');

 return () => { lifetime.epoch++ } }, [lifetime])

  async function run(work: () => Promise<void>) {
    if (lifetime.pending || !connected) {return}
    const token = Symbol(); lifetime.pending = token; setBusy(true); setError(null)

    try { await work() } catch { if (lifetime.pending === token) {setError(rt("Status unavailable; no success or repair was inferred"))} }
    finally { if (lifetime.pending === token) { lifetime.pending = null; setBusy(false) } }
  }

  async function refresh() {
    const flight = lifetime.epoch
    const result = await inspectRuntimeStatus(request, sessionId, project.trim() || undefined)

    if (flight === lifetime.epoch) {setView(result)}
  }

  async function inspect(action: string) {
    const flight = lifetime.epoch
    const result = await runStatusCommand(action, request, sessionId)

    if (flight === lifetime.epoch) {setOutput(result)}
  }

  return <section className="grid gap-3"><h4 className="text-sm font-medium">{c[0]}</h4>
    <label>{c[1]}<Input disabled={busy} onChange={event => { lifetime.epoch++; setProject(event.target.value); setView(null) }} value={project} /></label>
    <Button disabled={busy || !connected} onClick={() => void run(refresh)} size="xs" variant="secondary">{c[2]}</Button>
    {view && <div className="grid gap-3"><p className="text-xs">{view.checkedAt}{" " + rt("· Bounded snapshot; missing data is not an all-clear")}</p>
      {([['ready', c[3]], ['waiting', c[4]], ['active', c[5]]] as const).map(([key, title]) => <div className="grid gap-1" key={key}><h5 className="text-sm font-medium">{title} ({view[key].length})</h5>{view[key].map(item => <div className="text-xs" key={item.mission_id}><strong>{item.outcome}</strong><p>{item.mission_id}{" " + rt("· revision") + " "}{item.revision} · {item.state}</p><p>{item.next_step}</p>{item.blockers.map(blocker => <p key={blocker}>{blocker}</p>)}</div>)}</div>)}
      <h5 className="text-sm font-medium">{c[6]}</h5>{view.upcoming.map((item, index) => <p className="whitespace-pre-wrap text-xs" key={index}>{item}</p>)}{view.commitments.map((item, index) => <p className="whitespace-pre-wrap text-xs" key={index}>{item}</p>)}
      {view.effects.map(item => <p className="text-xs" key={item.effect_id}>{item.effect_id}: {item.state}{rt("; owner") + " "}{item.run_id}</p>)}
      {view.errors.map(item => <p className="text-xs" key={item} role="status">{item}</p>)}
    </div>}
    <label>{c[7]}<Input disabled={busy} onChange={event => { lifetime.epoch++; setEffect(event.target.value); setOutput('') }} value={effect} /></label>
    <div className="flex flex-wrap gap-2"><Button disabled={busy || !connected || !effect.trim()} onClick={() => void run(() => inspect(`effect ${effect.trim()}`))} size="xs" variant="secondary">{c[8]}</Button><Button disabled={busy || !connected || !effect.trim()} onClick={() => void run(() => inspect(`reconcile ${effect.trim()}`))} size="xs" variant="secondary">{c[9]}</Button><Button disabled={busy || !connected} onClick={() => void run(() => inspect('privacy'))} size="xs" variant="ghost">{c[10]}</Button></div>
    {output && <p aria-live="polite" className="whitespace-pre-wrap break-words text-xs">{output}</p>}{error && <p role="alert">{error}</p>}
    <p className="text-xs text-muted-foreground">{rt("No automatic repair, deletion, export or opportunity task is created. Durable dismissals and opportunity discovery await backend operations.")}</p>
  </section>
}
