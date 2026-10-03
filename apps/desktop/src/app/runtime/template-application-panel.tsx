import { useEffect, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { useI18n } from '@/i18n'

import type { TemplatePrepareParams, TemplatePrepareResult, TemplatePreviewResult, TemplateRecord } from '../../../../shared/src/gateway-contract.generated'
import type { RuntimeRequest } from '../../../../shared/src/runtime-control'
import { markdownSections } from '../../../../shared/src/runtime-markdown'
import { templateApplicationInput, verifyTemplatePrepared, verifyTemplatePreview, verifyTemplatePublished } from '../../../../shared/src/runtime-template-application'

import { useRuntimeUiText } from './runtime-ui-copy'

const words = {
  en: ['Apply an approved template', 'Project ID', 'List saved templates', 'Inspect exact version', 'Preview new topic', 'Prepare for approval', 'Approve exact artifact', 'Publish reviewed artifact', 'Discard command', 'Inspect command', 'Complete inert preview', 'Lock section', 'Close'],
  de: ['Freigegebene Vorlage anwenden', 'Projekt-ID', 'Vorlagen auflisten', 'Exakte Version prüfen', 'Neues Thema voranzeigen', 'Zur Freigabe vorbereiten', 'Exaktes Artefakt freigeben', 'Geprüftes Artefakt veröffentlichen', 'Befehl verwerfen', 'Befehl prüfen', 'Vollständige inaktive Vorschau', 'Abschnitt sperren', 'Schließen'],
  es: ['Aplicar una plantilla aprobada', 'ID del proyecto', 'Listar plantillas guardadas', 'Inspeccionar versión exacta', 'Vista previa del tema nuevo', 'Preparar para aprobación', 'Aprobar archivo exacto', 'Publicar archivo revisado', 'Descartar comando', 'Inspeccionar comando', 'Vista previa completa e inerte', 'Bloquear sección', 'Cerrar'],
  fr: ['Appliquer un modèle approuvé', 'ID du projet', 'Lister les modèles', 'Inspecter la version exacte', 'Aperçu du nouveau sujet', 'Préparer la validation', 'Approuver le fichier exact', 'Publier le fichier vérifié', 'Abandonner la commande', 'Inspecter la commande', 'Aperçu complet et inerte', 'Verrouiller la section', 'Fermer'],
  ja: ['承認済みテンプレートを適用', 'プロジェクトID', '保存済みテンプレート一覧', '正確なバージョンを確認', '新しい主題をプレビュー', '承認の準備', '正確な成果物を承認', '確認済み成果物を公開', 'コマンドを破棄', 'コマンドを確認', '完全な非実行プレビュー', 'セクションを固定', '閉じる'],
  zh: ['应用已批准模板', '项目ID', '列出已保存模板', '检查确切版本', '预览新主题', '准备审批', '批准确切文件', '发布已审阅文件', '放弃命令', '检查命令', '完整静态预览', '锁定章节', '关闭'],
  'zh-hant': ['套用已核准範本', '專案ID', '列出已儲存範本', '檢查確切版本', '預覽新主題', '準備審核', '核准確切檔案', '發佈已審閱檔案', '放棄指令', '檢查指令', '完整靜態預覽', '鎖定章節', '關閉'],
  ar: ['تطبيق قالب معتمد', 'معرف المشروع', 'عرض القوالب المحفوظة', 'فحص الإصدار المحدد', 'معاينة الموضوع الجديد', 'التحضير للموافقة', 'اعتماد الملف المحدد', 'نشر الملف المراجع', 'إلغاء الأمر', 'فحص الأمر', 'معاينة كاملة غير تنفيذية', 'قفل القسم', 'إغلاق'],
  ru: ['Применить одобренный шаблон', 'ID проекта', 'Список шаблонов', 'Проверить точную версию', 'Предпросмотр новой темы', 'Подготовить одобрение', 'Одобрить точный файл', 'Опубликовать проверенный файл', 'Отменить команду', 'Проверить команду', 'Полный безопасный предпросмотр', 'Закрепить раздел', 'Закрыть']
} as const

interface Props { request: RuntimeRequest; sessionId: string; connected: boolean }
interface Pending { params: TemplatePrepareParams; cancelRequested?: boolean }
interface Scope { id: number; request: RuntimeRequest; sessionId: string; pending: Pending | null }
const terminal = new Set(['completed', 'cancelled', 'failed', 'blocked'])

export function TemplateApplicationPanel(props: Props) {
  const rt = useRuntimeUiText()
  const scopes = useRef<Scope[]>([])
  let scope = scopes.current.find(item => item.request === props.request && item.sessionId === props.sessionId)

  if (!scope) { scope = { id: scopes.current.length, request: props.request, sessionId: props.sessionId, pending: null }; scopes.current.push(scope) }

  return <section className="grid gap-3">
    {scopes.current.filter(item => item !== scope && item.pending).map(item => <p key={item.id} role="status">{rt("Another session or connection retains a pending command. Return there to inspect or cancel it.")}</p>)}
    <ScopedTemplateApplication {...props} key={scope.id} scope={scope} />
  </section>
}

function TemplatePreview({ value, label }: { value: TemplatePreviewResult; label: string }) {
  const rt = useRuntimeUiText()

  return <div className="grid gap-2 text-xs">
    <p>{value.size}{" " + rt("complete bytes · SHA-256") + " "}{value.sha256}</p>
    <p>{rt("Applied deterministic style:") + " "}{value.applied_style_keys.join(', ') || 'none'}{rt(". Advisory only:") + " "}{value.advisory_style_keys.join(', ') || 'none'}.</p>
    <p>{rt("Assets: lineage_only (")}{value.assets.map(ref => `${ref.artifact_id}@${ref.version}`).join(', ') || 'none'}{rt("). No embedded media. Baseline bytes are not copied. Exclusions checked.")}</p>
    {value.locked_sections.map(lock => <p className="break-all" key={lock.anchor}>{rt("Locked") + " "}{lock.anchor}: {lock.sha256}</p>)}
    <pre aria-label={label} className="max-h-96 overflow-auto whitespace-pre-wrap break-words">{value.content}</pre>
  </div>
}

function ScopedTemplateApplication({ request, sessionId, connected, scope }: Props & { scope: Scope }) {
  const rt = useRuntimeUiText()
  const { locale } = useI18n()
  const c = words[locale as keyof typeof words] ?? words.en
  const [project, setProject] = useState(''), [templates, setTemplates] = useState<TemplateRecord[]>([]), [selected, setSelected] = useState<TemplateRecord | null>(null)
  const [slots, setSlots] = useState<Record<string, string>>({}), [locks, setLocks] = useState<string[]>([])
  const [preview, setPreview] = useState<TemplatePreviewResult | null>(null), [review, setReview] = useState<TemplatePrepareResult | null>(null)
  const [pending, setPending] = useState(scope.pending), [invalid, setInvalid] = useState(!!scope.pending), [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false), [message, setMessage] = useState(''), [error, setError] = useState(''), [recoveryId, setRecoveryId] = useState('')
  const live = useRef({ mounted: true, connected, token: 0, pending: false })

  if (live.current.connected !== connected) { live.current.connected = connected; live.current.token++; live.current.pending = false }
  useEffect(() => {
    const state = live.current; state.mounted = true

    return () => { state.mounted = false; state.token++; state.pending = false }
  }, [])
  useEffect(() => {
    if (!connected) { setBusy(false); setConfirming(false); setInvalid(true); setPreview(null); setSelected(null); setTemplates([]); setMessage(''); setError('') }
  }, [connected])
  const base = { session_id: sessionId, schema_version: 1 as const }

  function valid(token: number) { return live.current.mounted && live.current.connected && live.current.token === token }

  function retain(value: Pending | null) { scope.pending = value; setPending(value) }

  function invalidate() { setInvalid(true); setConfirming(false); setPreview(null); setMessage('') }

  function sourceChanged(value: string) { invalidate(); setProject(value); setTemplates([]); setSelected(null); setSlots({}); setLocks([]) }

  async function action(work: (token: number) => Promise<void>) {
    if (!connected || !sessionId || !live.current.mounted || live.current.pending) { return }
    live.current.pending = true
    const token = ++live.current.token
    setBusy(true); setError('')

    try { await work(token) }
    catch (caught) {
      if (valid(token)) { setError(`${caught instanceof Error ? caught.message : rt("Request failed")}. ${scope.pending ? rt("Keep the original command; inspect or cancel it. No mutation retry was sent.") : ''}`); setInvalid(true); setConfirming(false) }
    } finally { if (valid(token)) { live.current.pending = false; setBusy(false) } }
  }

  async function list(token: number) {
    const result = await request('runtime.template.list', { ...base, project_id: project.trim() })

    if (!valid(token)) { return }

    if (result.templates.some(item => item.project_id !== project.trim())) { throw new Error(rt("Template list contains another project")) }
    setTemplates(result.templates); setMessage(`Bounded saved-template list${result.limit_reached ? rt("; limit reached") : ''}. The catalog does not claim completeness.`)
  }

  async function inspect(template: TemplateRecord, token: number) {
    invalidate()
    const result = await request('runtime.template.get', { ...base, template_id: template.template_id, version: template.version })

    if (!valid(token)) { return }
    const value = result.template

    if (value.template_id !== template.template_id || value.version !== template.version || value.sha256 !== template.sha256 || value.project_id !== project.trim()) { throw new Error(rt("Saved template identity changed; refresh the list")) }
    setSelected(value); setSlots({}); setLocks([])
  }

  function input() {
    if (!selected) { throw new Error(rt("Inspect a saved template version first")) }

    return templateApplicationInput(sessionId, project.trim(), selected, slots, locks)
  }

  async function previewTopic(token: number) {
    const params = input(), result = await request('runtime.template.preview', params)
    await verifyTemplatePreview(result, params)

    if (valid(token)) { setPreview(result); setMessage(rt("Preview only. Nothing has been published or shared.")) }
  }

  async function prepare(token: number) {
    if (scope.pending) { return }
    const params = { ...input(), command_id: crypto.randomUUID(), request_id: crypto.randomUUID() }
    retain({ params }); setInvalid(true); setReview(null); setMessage('')
    const result = await request('runtime.template.prepare', params)
    await verifyTemplatePrepared(result, params)

    if (valid(token)) { setReview(result); setPreview(result.preview); setInvalid(false) }
  }

  async function publish(token: number) {
    const original = scope.pending

    if (!confirming || invalid || !review || !original || review.proposal.expires_at * 1000 <= Date.now()) { throw new Error(rt("Approval is invalid or expired; discard and prepare a fresh review")) }
    setInvalid(true); setConfirming(false)
    const result = await request('runtime.template.publish', { ...original.params, approval_id: review.proposal.approval_id, approval_digest: review.proposal.approval_digest })
    verifyTemplatePublished(result, review)

    if (!valid(token)) { return }
    retain(null); setReview(null); setMessage(`Published ${result.artifact_id}@${result.version}, ${result.disposition}, validation ${result.validation_status}. Previous versions remain. No external sharing or delivery.`)
  }

  async function recover(cancel: boolean, token: number) {
    const original = scope.pending, command_id = original?.params.command_id ?? recoveryId.trim()

    if (!command_id || (cancel && original?.cancelRequested)) { return }

    if (cancel && original) { original.cancelRequested = true }
    setInvalid(true); setConfirming(false)
    const result = await request(cancel ? 'runtime.artifact.cancel' : 'runtime.artifact.status', { ...base, command_id })

    if (!valid(token)) { return }

    if (result.command_id !== command_id) { throw new Error(rt("Receipt belongs to another command")) }
    setMessage(`Command ${command_id}: ${result.status}; owner ${result.owner_live ? rt("live") : rt("not live")}. Cancellation never undoes committed effects.${result.result && 'artifact_id' in result.result ? ` Artifact ${result.result.artifact_id}@${result.result.version}.` : ''}`)

    if (terminal.has(result.status)) { retain(null); setReview(null); setInvalid(false) }
  }

  return <section aria-label={c[0]} className="grid gap-3">
    <h4 className="text-sm font-medium">{c[0]}</h4>
    {!connected && <p role="status">{rt("Disconnected. Reconnect to inspect the original command.")}</p>}
    <label>{c[1]}<Input disabled={busy} onChange={event => sourceChanged(event.target.value)} value={project} /></label>
    <Button disabled={busy || !connected || !project.trim()} onClick={() => void action(list)} size="xs" variant="secondary">{c[2]}</Button>
    {templates.map(template => <Button disabled={busy || !connected} key={`${template.template_id}:${template.version}`} onClick={() => void action(token => inspect(template, token))} size="xs" variant="ghost">{c[3]}: {template.template_id}@{template.version}</Button>)}
    {selected && <div className="grid gap-2">
      <p className="break-all text-xs">artifact_templates · {selected.template_id}@{selected.version} · {selected.sha256}</p>
      <p className="text-xs">{rt("Approved baseline") + " "}{selected.baseline_ref.artifact_id}@{selected.baseline_ref.version}{" " + rt("is provenance only. Structure uses literal slot substitutions, without copying original subject matter.")}</p>
      <details><summary>{rt("Saved reusable fields and exclusions")}</summary><pre className="whitespace-pre-wrap break-words text-xs">{selected.structure.join('\n\n')}</pre><p>{rt("Style:") + " "}{Object.entries(selected.style).map(([key, value]) => `${key}: ${value}`).join('; ') || 'none'}</p><p>{rt("Exclusions:") + " "}{selected.exclusions.join('; ') || 'none declared'}</p><p>{rt("Assets:") + " "}{selected.assets.map(ref => `${ref.artifact_id}@${ref.version}`).join(', ') || 'none'}</p></details>
      {selected.slots.map(slot => <label className="grid gap-1 text-xs" key={slot.name}>{slot.name}{slot.required ? rt(" (required)") : rt(" (optional)")} · {slot.purpose}<Textarea disabled={busy} onChange={event => { invalidate(); setLocks([]); setSlots(previous => Object.fromEntries([...Object.entries(previous).filter(([name]) => name !== slot.name), ...(event.target.value ? [[slot.name, event.target.value]] : [])])) }} value={Object.hasOwn(slots, slot.name) ? slots[slot.name] : ''} /></label>)}
      <Button disabled={busy || !connected} onClick={() => void action(previewTopic)} size="xs" variant="secondary">{c[4]}</Button>
    </div>}
    {preview && <><TemplatePreview label={c[10]} value={preview} /><div className="flex flex-wrap gap-1">{markdownSections(preview.content).map(section => <Button aria-pressed={locks.includes(section.anchor)} disabled={busy} key={section.anchor} onClick={() => { setInvalid(true); setConfirming(false); setLocks(previous => previous.includes(section.anchor) ? previous.filter(item => item !== section.anchor) : [...previous, section.anchor]) }} size="xs" variant="ghost">{c[11]}: {section.anchor}</Button>)}</div><p className="text-xs">{rt("Requested locks:") + " "}{locks.join(', ') || 'none'}{rt(". Prepare again to bind changed locks to the actual preview.")}</p></>}
    <Button disabled={busy || !connected || !selected || !!pending} onClick={() => void action(prepare)} size="xs" variant="secondary">{c[5]}</Button>
    {pending && <p className="break-all text-xs" role="status">{rt("Retained command") + " "}{pending.params.command_id}{rt("; request") + " "}{pending.params.request_id}{rt(". Closing this panel does not cancel the command. Retain these IDs for recovery.")}</p>}
    {review && <div className="grid gap-2"><p>{rt("Review artifact") + " "}{review.proposal.artifact_id}@{review.proposal.version}{rt("; approval") + " "}{review.proposal.approval_id}{rt("; expires") + " "}{new Date(review.proposal.expires_at * 1000).toLocaleString()}</p>{invalid && <p role="status">{rt("Inputs or connection changed, or publication was attempted. This old approval is disabled; inspect or discard the original command.")}</p>}<TemplatePreview label="Exact prepared preview" value={review.preview} /><Button disabled={busy || !connected || invalid} onClick={() => setConfirming(true)} size="xs" variant="secondary">{c[6]}</Button></div>}
    <ConfirmDialog confirmLabel={c[7]} description={rt("Publish only this exact reviewed Markdown artifact to this project. Baseline content is not copied, assets remain lineage only, and no external sharing occurs.")} onClose={() => setConfirming(false)} onConfirm={() => action(publish)} open={confirming && !invalid && connected} title={c[6]}>
      {review && pending && <div className="grid gap-2 break-words text-xs"><p>{rt("Project") + " "}{review.proposal.project_id}{" " + rt("· artifact") + " "}{review.proposal.artifact_id}@{review.proposal.version}</p><p className="break-all">SHA-256 {review.proposal.sha256} · {review.proposal.size}{" " + rt("bytes")}</p><p>{rt("Template") + " "}{pending.params.template_ref.template_id}@{pending.params.template_ref.version}{" " + rt("· store artifact_templates")}</p><p>{rt("Approval") + " "}{review.proposal.approval_id}{" " + rt("· request") + " "}{pending.params.request_id}</p><pre aria-label={rt("Publication confirmation bytes")} className="max-h-64 overflow-auto whitespace-pre-wrap">{review.preview.content}</pre></div>}
    </ConfirmDialog>
    {!pending && <label>{rt("Existing command ID for recovery")}<Input disabled={busy} onChange={event => setRecoveryId(event.target.value)} value={recoveryId} /></label>}
    {(pending || recoveryId.trim()) && <div className="flex gap-2"><Button disabled={busy || !connected} onClick={() => void action(token => recover(false, token))} size="xs" variant="secondary">{c[9]}</Button><Button disabled={busy || !connected || pending?.cancelRequested} onClick={() => void action(token => recover(true, token))} size="xs" variant="text">{c[8]}</Button></div>}
    {message && <p className="text-xs" role="status">{message}</p>}{error && <p role="alert">{error}</p>}
  </section>
}
