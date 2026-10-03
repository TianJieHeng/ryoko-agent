import { useEffect, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { useI18n } from '@/i18n'

import type { OutputContextResult, OutputContextsResult, OutputContextSummary, OutputControlParams, OutputControlResult, OutputReference } from '../../../../shared/src/gateway-contract.generated'
import type { RuntimeRequest } from '../../../../shared/src/runtime-control'
import { buildOutputControl, describeOutputControl, outputControlBlock, verifyOutputContext, verifyOutputControl } from '../../../../shared/src/runtime-output-influences'

import { useRuntimeUiText } from './runtime-ui-copy'

const words = {
  en: ['What context was supplied?', 'List output receipts', 'Inspect output', 'Select reference', 'Ignore here', 'Correct', 'Remove record', 'Next response only', 'This project', 'General default', 'Replacement text', 'Review exact change', 'Confirm scoped change', 'Inspect control status', 'Review another change'],
  de: ['Welcher Kontext wurde geliefert?', 'Ausgabebelege auflisten', 'Ausgabe prüfen', 'Referenz wählen', 'Hier ignorieren', 'Korrigieren', 'Datensatz entfernen', 'Nur nächste Antwort', 'Dieses Projekt', 'Allgemeiner Standard', 'Ersatztext', 'Exakte Änderung prüfen', 'Begrenzte Änderung bestätigen', 'Steuerungsstatus prüfen', 'Weitere Änderung prüfen'],
  es: ['¿Qué contexto se proporcionó?', 'Listar recibos de salida', 'Inspeccionar salida', 'Elegir referencia', 'Ignorar aquí', 'Corregir', 'Eliminar registro', 'Solo siguiente respuesta', 'Este proyecto', 'Valor general', 'Texto de reemplazo', 'Revisar cambio exacto', 'Confirmar cambio acotado', 'Inspeccionar estado', 'Revisar otro cambio'],
  fr: ['Quel contexte a été fourni ?', 'Lister les reçus de sortie', 'Inspecter la sortie', 'Choisir la référence', 'Ignorer ici', 'Corriger', 'Supprimer la fiche', 'Prochaine réponse seulement', 'Ce projet', 'Préférence générale', 'Texte de remplacement', 'Vérifier la modification exacte', 'Confirmer la portée', 'Inspecter le statut', 'Vérifier une autre modification'],
  ja: ['提供されたコンテキスト', '出力の記録一覧', '出力を確認', '参照を選択', 'ここで無視', '修正', 'レコードを削除', '次の応答のみ', 'このプロジェクト', '一般設定', '置換テキスト', '変更内容を確認', '範囲を確認して変更', '制御状態を確認', '別の変更を確認'],
  zh: ['提供了哪些上下文？', '列出输出回执', '检查输出', '选择引用', '此处忽略', '更正', '删除记录', '仅下一次回复', '此项目', '通用默认值', '替换文本', '审阅确切更改', '确认范围内更改', '检查控制状态', '审阅另一项更改'],
  'zh-hant': ['提供了哪些上下文？', '列出輸出回執', '檢查輸出', '選擇引用', '此處忽略', '更正', '刪除紀錄', '僅下一次回覆', '此專案', '通用預設值', '替換文字', '審閱確切變更', '確認範圍內變更', '檢查控制狀態', '審閱另一項變更'],
  ar: ['ما السياق الذي تم توفيره؟', 'عرض إيصالات المخرجات', 'فحص المخرج', 'اختيار مرجع', 'تجاهل هنا', 'تصحيح', 'إزالة السجل', 'الرد التالي فقط', 'هذا المشروع', 'افتراضي عام', 'النص البديل', 'مراجعة التغيير المحدد', 'تأكيد التغيير المحدود', 'فحص حالة التحكم', 'مراجعة تغيير آخر'],
  ru: ['Какой контекст был передан?', 'Список квитанций вывода', 'Проверить вывод', 'Выбрать ссылку', 'Игнорировать здесь', 'Исправить', 'Удалить запись', 'Только следующий ответ', 'Этот проект', 'Общий стандарт', 'Текст замены', 'Проверить точное изменение', 'Подтвердить область изменения', 'Проверить статус', 'Проверить другое изменение']
} as const

interface Props { request: RuntimeRequest; sessionId: string; connected: boolean }
interface Pending { params: OutputControlParams; receipt: OutputControlResult | null }
interface Scope { id: number; request: RuntimeRequest; sessionId: string; pending: Pending | null }

export function OutputInfluencePanel(props: Props) {
  const rt = useRuntimeUiText()
  const scopes = useRef<Scope[]>([])
  let scope = scopes.current.find(item => item.request === props.request && item.sessionId === props.sessionId)

  if (!scope) { scope = { id: scopes.current.length, request: props.request, sessionId: props.sessionId, pending: null }; scopes.current.push(scope) }

  return <section className="grid gap-3">
    {scopes.current.filter(item => item !== scope && item.pending).map(item => <p key={item.id} role="status">{rt("Another session or connection retains a pending control. Return there to inspect it; it is never replayed here.")}</p>)}
    <ScopedOutputInfluence {...props} key={scope.id} scope={scope} />
  </section>
}

function ContextEvidence({ context }: { context: OutputContextResult }) {
  const rt = useRuntimeUiText()

  return <div className="grid gap-1 text-xs">
    <p>{rt("Run") + " "}{context.run_id}{" " + rt("· backend") + " "}{context.backend} · {context.latest ? rt("latest") : rt("historical")} · {context.degraded ? rt("degraded") : rt("receipt available")}</p>
    <p>{rt("Namespace") + " "}{context.namespace_id ?? 'opaque'}{" " + rt("· project") + " "}{context.project_id ?? 'none'}</p>
    <p className="break-all">{rt("Context digest") + " "}{context.context_sha256}</p><p className="break-all">{rt("Fresh packet") + " "}{context.context_packet_sha256}</p><p className="break-all">{rt("Frozen prefix") + " "}{context.immutable_prefix_sha256}</p>
    <p>{rt("Coverage: fresh_memory_context_only. These references were supplied to a provider call. They are not a causal explanation. Historical system/transcript context and arbitrary tool results are not exhaustively listed. No references does not mean no other context was used.")}</p>
    {context.controls.map(control => <p key={control.control_id}>{rt("Supplied directive") + " "}{control.control_id}: {control.action} {control.record_id}@{control.version}, {control.scope}{control.replacement !== null ? ` · ${control.replacement}` : ''}</p>)}
  </div>
}

function ScopedOutputInfluence({ request, sessionId, connected, scope }: Props & { scope: Scope }) {
  const rt = useRuntimeUiText()
  const { locale } = useI18n()
  const c = words[locale as keyof typeof words] ?? words.en
  const [catalog, setCatalog] = useState<OutputContextsResult | null>(null), [context, setContext] = useState<OutputContextResult | null>(null), [reference, setReference] = useState<OutputReference | null>(null)
  const [actionKind, setActionKind] = useState<OutputControlParams['action']>('ignore'), [applyScope, setApplyScope] = useState<OutputControlParams['scope']>('response'), [replacement, setReplacement] = useState('')
  const [review, setReview] = useState<OutputControlParams | null>(null), [pending, setPending] = useState(scope.pending), [recovery, setRecovery] = useState('')
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [message, setMessage] = useState('')
  const live = useRef({ mounted: true, connected, token: 0, pending: false })

  if (live.current.connected !== connected) { live.current.connected = connected; live.current.token++; live.current.pending = false }
  useEffect(() => {
    const state = live.current; state.mounted = true

    return () => { state.mounted = false; state.token++; state.pending = false }
  }, [])
  useEffect(() => {
    if (!connected) { setBusy(false); setReview(null); setCatalog(null); setContext(null); setReference(null); setMessage(''); setError('') }
  }, [connected])
  const base = { session_id: sessionId, schema_version: 1 as const }

  function valid(token: number) { return live.current.mounted && live.current.connected && live.current.token === token }

  function retain(value: Pending | null) { scope.pending = value; setPending(value) }

  async function action(work: (token: number) => Promise<void>) {
    if (!connected || !sessionId || !live.current.mounted || live.current.pending) { return }
    live.current.pending = true
    const token = ++live.current.token
    setBusy(true); setError('')

    try { await work(token) }
    catch (caught) { if (valid(token)) { setError(`${caught instanceof Error ? caught.message : rt("Request failed")}. ${scope.pending ? rt("Inspect the original control ID. No automatic retry was sent.") : ''}`); setReview(null) } }
    finally { if (valid(token)) { live.current.pending = false; setBusy(false) } }
  }

  async function list(token: number) {
    setReview(null); setReference(null); setContext(null)
    const result = await request('runtime.memory.output.list', base)

    if (valid(token)) { setCatalog(result) }
  }

  async function inspect(expected: OutputContextSummary, token: number) {
    setReview(null); setReference(null); setContext(null)
    const result = await request('runtime.memory.output.get', { ...base, run_id: expected.run_id })
    verifyOutputContext(result, expected)

    if (valid(token)) { setContext(result); setMessage('') }
  }

  function prepareReview() {
    if (!connected || busy || !context || !reference || scope.pending) { return }

    try { setReview(buildOutputControl(sessionId, context, reference, actionKind, applyScope, replacement, crypto.randomUUID())); setError('') }
    catch (caught) { setError(caught instanceof Error ? caught.message : rt("Invalid scoped change")) }
  }

  async function submit(token: number) {
    if (!review || scope.pending || !context || !reference) { return }
    const params = review
    // Save exact run/context/record/version before dispatch. Transport failures never mint another control.
    retain({ params, receipt: null }); setReview(null); setMessage('')
    const receipt = await request('runtime.memory.output.control', params)
    verifyOutputControl(receipt, params)

    if (valid(token)) { retain({ params, receipt }); setMessage(describeOutputControl(receipt)) }
  }

  async function inspectControl(token: number) {
    const original = scope.pending, control_id = original?.params.control_id ?? recovery.trim()

    if (!control_id) { return }
    const receipt = await request('runtime.memory.output.control.get', { ...base, control_id })

    if (receipt.control_id !== control_id) { throw new Error(rt("Receipt belongs to a different control")) }

    if (original) { verifyOutputControl(receipt, original.params) }

    if (!valid(token)) { return }

    if (original) { retain({ params: original.params, receipt }) }
    setMessage(describeOutputControl(receipt))
  }

  const block = context && reference ? outputControlBlock(context, reference, actionKind, applyScope) : null
  const opaque = catalog?.backend === 'personal_mcp' || context?.backend === 'personal_mcp'

  return <section aria-label={c[0]} className="grid gap-3">
    <h4 className="text-sm font-medium">{c[0]}</h4>
    <p className="text-xs">{rt("Controls change future context at a valid turn boundary. The already-produced output is never rewritten. Response-only changes expire; project/general durable changes require an actual store acknowledgment.")}</p>
    {!connected && <p role="status">{rt("Disconnected. Reconnect and inspect the original receipt.")}</p>}
    <Button disabled={busy || !connected} onClick={() => void action(list)} size="xs" variant="secondary">{c[1]}</Button>
    {catalog && <><p className="text-xs">{rt("Backend") + " "}{catalog.backend}{" " + rt("· bounded list, not complete.")}{catalog.unavailable_reason ? ` Unavailable: ${catalog.unavailable_reason}` : ''}</p>{!catalog.outputs.length && <p>{rt("No output receipt is available. This does not establish that no context was used.")}</p>}{catalog.outputs.map(output => <Button disabled={busy || !connected} key={`${output.run_id}:${output.context_sha256}`} onClick={() => void action(token => inspect(output, token))} size="xs" variant="ghost">{c[2]}: {output.run_id}</Button>)}</>}
    {opaque && <p role="status">{rt("Ryoko’s personal context harness is opaque and read-only here. There is no verified record mutation contract; built-in memory controls do not apply to it.")}</p>}
    {context && <><ContextEvidence context={context} />{context.references.map(ref => <div className="grid gap-1 text-xs" key={`${ref.namespace_id}:${ref.record_id}:${ref.version}`}><p>{ref.record_id}@{ref.version}{" " + rt("· namespace") + " "}{ref.namespace_id}{" " + rt("· scope") + " "}{ref.scope}{" " + rt("· deletion state") + " "}{ref.deletion_state}</p><p className="break-words">{rt("Source:") + " "}{ref.source_ref}</p>{!opaque && <Button aria-pressed={reference === ref} disabled={busy || !connected || !!pending || !context.latest || context.degraded || ref.deletion_state !== 'present'} onClick={() => { setReference(ref); setReview(null); setActionKind('ignore'); setApplyScope('response'); setReplacement('') }} size="xs" variant="ghost">{c[3]}: {ref.record_id}@{ref.version}</Button>}</div>)}</>}
    {context && reference && !opaque && <div className="grid gap-2">
      <p className="text-xs">{rt("Selected") + " "}{reference.record_id}@{reference.version}{rt("; existing scope") + " "}{reference.scope}</p>
      <div aria-label={rt("Control action")} className="flex flex-wrap gap-1">{(['ignore', 'correct', 'remove'] as const).map((value, index) => <Button aria-pressed={actionKind === value} disabled={busy || !!pending} key={value} onClick={() => { setActionKind(value); setReview(null) }} size="xs" variant="ghost">{c[index + 4]}</Button>)}</div>
      <div aria-label={rt("Applicability scope")} className="flex flex-wrap gap-1">{(['response', 'project', 'general'] as const).map((value, index) => <Button aria-pressed={applyScope === value} disabled={busy || !!pending || (value === 'project' && !context.project_id)} key={value} onClick={() => { setApplyScope(value); setReview(null) }} size="xs" variant="ghost">{c[index + 7]}</Button>)}</div>
      {actionKind === 'correct' && <label>{c[10]}<Textarea disabled={busy || !!pending} onChange={event => { setReplacement(event.target.value); setReview(null) }} value={replacement} /></label>}
      {applyScope === 'response' && <p className="text-xs">{rt("Applies only to the next supplied response context, then expires. It does not create a durable preference.")}</p>}
      {actionKind === 'remove' && <p className="text-xs">{rt("Remove tombstones this exact record. It does not physically erase backups, earlier supplied context or transcript history.")}</p>}
      {block && <p role="status">{block}</p>}
      <Button disabled={busy || !connected || !!pending || !!block} onClick={prepareReview} size="xs" variant="secondary">{c[11]}</Button>
    </div>}
    <ConfirmDialog confirmLabel={c[12]} description={rt("Apply this exact scoped control at a future boundary. The current output will remain unchanged. Only a durable acknowledgment establishes a stored preference change.")} destructive={review?.action === 'remove'} onClose={() => setReview(null)} onConfirm={() => action(submit)} open={!!review && connected} title={c[11]}>
      {review && <div className="grid gap-1 break-words text-xs"><p>{review.action}{" " + rt("· scope") + " "}{review.scope}{" " + rt("· project") + " "}{review.project_id ?? 'none'}</p><p>{rt("Run") + " "}{review.run_id}{" " + rt("· context") + " "}{review.context_sha256}</p><p>{rt("Record") + " "}{review.record_id}@{review.expected_version}{" " + rt("· namespace") + " "}{review.namespace_id}</p><p>{rt("Control") + " "}{review.control_id}</p>{review.content !== null && <pre className="whitespace-pre-wrap">{review.content}</pre>}</div>}
    </ConfirmDialog>
    {pending && <><p className="break-all text-xs" role="status">{rt("Retained control") + " "}{pending.params.control_id}{" " + rt("· run") + " "}{pending.params.run_id}{" " + rt("· record") + " "}{pending.params.record_id}@{pending.params.expected_version}{" " + rt("· namespace") + " "}{pending.params.namespace_id}{" " + rt("· context") + " "}{pending.params.context_sha256}{" " + rt("· scope") + " "}{pending.params.scope}{rt(". Keep this ID if leaving the panel.")}</p>{!pending.receipt && <p role="status">{rt("Outcome unknown. Inspect this control; never submit the mutation again blindly.")}</p>}</>}
    {!pending && <label>{rt("Existing control ID for recovery")}<Input disabled={busy} onChange={event => setRecovery(event.target.value)} value={recovery} /></label>}
    {(pending || recovery.trim()) && <Button disabled={busy || !connected} onClick={() => void action(inspectControl)} size="xs" variant="secondary">{c[13]}</Button>}
    {pending?.receipt && pending.receipt.status !== 'mutation_pending' && <Button disabled={busy || !connected} onClick={() => { retain(null); setReference(null); setContext(null); setCatalog(null); setReview(null); setMessage(rt("Refresh output receipts before deciding on another change.")) }} size="xs" variant="text">{c[14]}</Button>}
    {message && <p className="text-xs" role="status">{message}</p>}{error && <p role="alert">{error}</p>}
  </section>
}
