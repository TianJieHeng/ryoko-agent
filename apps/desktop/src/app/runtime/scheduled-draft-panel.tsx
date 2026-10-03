import { useEffect, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { Input } from '@/components/ui/input'
import { useI18n } from '@/i18n'

import type { ArtifactProposalResult, ScheduleGrantParams, ScheduleOutputPrepareParams, ScheduleUpdateParams, WorkflowRecord } from '../../../../shared/src/gateway-contract.generated'
import type { RuntimeRequest } from '../../../../shared/src/runtime-control'
import { controlDigest, controlInteger, controlJson, controlRecord } from '../../../../shared/src/runtime-research'
import type { DraftDefinitionOptions, DraftInput, DraftOutputTarget, ScheduledDraftSnapshot, VerifiedScheduledOutput } from '../../../../shared/src/runtime-scheduled-drafts'
import { draftIdentifier, draftInstant, readScheduledDraft, scheduledDraftDefinition, scheduledDraftInputs, scheduledDraftSnapshot, verifyScheduledProposal, verifyScheduledPublication } from '../../../../shared/src/runtime-scheduled-drafts'

import { useRuntimeUiText } from './runtime-ui-copy'

const labels = {
  en: ['Scheduled workflow drafts', 'Project ID', 'Workflow ID', 'Version', 'Inspect workflow', 'Schedule ID', 'Review paused draft schedule', 'Create paused schedule', 'Inspect schedule', 'Review bounded grant', 'Grant draft production', 'Activate schedule', 'Pause schedule', 'Revoke schedule', 'Read exact output', 'Prepare output for approval', 'Publish exact reviewed bytes', 'Inspect original command', 'Cancel original command', 'Save verified draft'],
  de: ['Geplante Ablaufentwürfe', 'Projekt-ID', 'Ablauf-ID', 'Version', 'Ablauf prüfen', 'Zeitplan-ID', 'Pausierten Entwurf prüfen', 'Pausierten Zeitplan erstellen', 'Zeitplan prüfen', 'Begrenzte Freigabe prüfen', 'Entwurfserstellung freigeben', 'Zeitplan aktivieren', 'Zeitplan pausieren', 'Zeitplan widerrufen', 'Exakte Ausgabe lesen', 'Ausgabe zur Freigabe vorbereiten', 'Geprüfte Bytes veröffentlichen', 'Originalbefehl prüfen', 'Originalbefehl abbrechen', 'Geprüften Entwurf speichern'],
  es: ['Borradores de flujos programados', 'ID del proyecto', 'ID del flujo', 'Versión', 'Inspeccionar flujo', 'ID del programa', 'Revisar programa pausado', 'Crear programa pausado', 'Inspeccionar programa', 'Revisar permiso limitado', 'Autorizar producción de borradores', 'Activar programa', 'Pausar programa', 'Revocar programa', 'Leer salida exacta', 'Preparar salida para aprobación', 'Publicar bytes revisados', 'Inspeccionar comando original', 'Cancelar comando original', 'Guardar borrador verificado'],
  fr: ['Brouillons de processus planifiés', 'ID du projet', 'ID du processus', 'Version', 'Inspecter le processus', 'ID du calendrier', 'Vérifier le calendrier suspendu', 'Créer le calendrier suspendu', 'Inspecter le calendrier', 'Vérifier l’autorisation limitée', 'Autoriser les brouillons', 'Activer le calendrier', 'Suspendre le calendrier', 'Révoquer le calendrier', 'Lire la sortie exacte', 'Préparer la validation', 'Publier les octets vérifiés', 'Inspecter la commande originale', 'Annuler la commande originale', 'Enregistrer le brouillon vérifié'],
  ja: ['定期ワークフローの下書き', 'プロジェクトID', 'ワークフローID', 'バージョン', 'ワークフローを確認', 'スケジュールID', '停止中の下書き予定を確認', '停止中の予定を作成', '予定を確認', '限定的な許可を確認', '下書き作成を許可', '予定を有効化', '予定を一時停止', '予定を取り消す', '正確な出力を読む', '出力の承認を準備', '確認済みバイトを公開', '元のコマンドを確認', '元のコマンドを中止', '検証済み下書きを保存'],
  zh: ['定时工作流草稿', '项目ID', '工作流ID', '版本', '检查工作流', '计划ID', '审阅暂停的草稿计划', '创建暂停计划', '检查计划', '审阅有限授权', '授权生成草稿', '启动计划', '暂停计划', '撤销计划', '读取确切输出', '准备输出审批', '发布已审阅字节', '检查原命令', '取消原命令', '保存已验证草稿'],
  'zh-hant': ['定時工作流程草稿', '專案ID', '工作流程ID', '版本', '檢查工作流程', '排程ID', '審閱暫停的草稿排程', '建立暫停排程', '檢查排程', '審閱有限授權', '授權產生草稿', '啟動排程', '暫停排程', '撤銷排程', '讀取確切輸出', '準備輸出審核', '發佈已審閱位元組', '檢查原始指令', '取消原始指令', '儲存已驗證草稿'],
  ar: ['مسودات سير العمل المجدولة', 'معرف المشروع', 'معرف سير العمل', 'الإصدار', 'فحص سير العمل', 'معرف الجدول', 'مراجعة جدول المسودات الموقوف', 'إنشاء جدول موقوف', 'فحص الجدول', 'مراجعة الإذن المحدود', 'السماح بإنتاج المسودات', 'تفعيل الجدول', 'إيقاف الجدول', 'إلغاء الجدول', 'قراءة الناتج المحدد', 'تحضير الناتج للموافقة', 'نشر البايتات المراجعة', 'فحص الأمر الأصلي', 'إلغاء الأمر الأصلي', 'حفظ المسودة المتحققة'],
  ru: ['Черновики по расписанию', 'ID проекта', 'ID процесса', 'Версия', 'Проверить процесс', 'ID расписания', 'Проверить приостановленное расписание', 'Создать приостановленное расписание', 'Проверить расписание', 'Проверить ограниченное разрешение', 'Разрешить создание черновиков', 'Активировать расписание', 'Приостановить расписание', 'Отозвать расписание', 'Прочитать точный результат', 'Подготовить результат к одобрению', 'Опубликовать проверенные байты', 'Проверить исходную команду', 'Отменить исходную команду', 'Сохранить проверенный черновик']
} as const

interface Props { request: RuntimeRequest; sessionId: string; connected: boolean }
interface Pending { projectId: string; commandId: string; kind: string; outputParams?: ScheduleOutputPrepareParams; cancelRequested?: boolean }
interface Scope { pending: Pending | null }
const retained = new WeakMap<RuntimeRequest, Map<string, Scope>>()
const terminal = new Set(['completed', 'cancelled', 'failed', 'blocked'])
type Review = { kind: 'create'; definition: Record<string, unknown> } | { kind: 'grant'; params: ScheduleGrantParams } | { kind: 'state'; params: ScheduleUpdateParams } | { kind: 'publish'; proposal: ArtifactProposalResult }

function originalScope(request: RuntimeRequest, sessionId: string): Scope {
  let sessions = retained.get(request)

  if (!sessions) { sessions = new Map(); retained.set(request, sessions) }
  let scope = sessions.get(sessionId)

  if (!scope) { scope = { pending: null }; sessions.set(sessionId, scope) }

  return scope
}

export function ScheduledDraftPanel(props: Props) {
  const [view, setView] = useState({ request: props.request, sessionId: props.sessionId, connected: props.connected, generation: 0 })

  if (view.request !== props.request || view.sessionId !== props.sessionId || view.connected !== props.connected) {
    setView({ request: props.request, sessionId: props.sessionId, connected: props.connected, generation: view.generation + 1 })

    return null
  }

  return <OwnedScheduledDraftPanel {...props} key={view.generation} scope={originalScope(props.request, props.sessionId)} />
}

function VerifiedDraft({ value, label }: { value: VerifiedScheduledOutput; label: string }) {
  const rt = useRuntimeUiText()
  const [download, setDownload] = useState<{ value: VerifiedScheduledOutput; url: string } | null>(null)
  useEffect(() => {
    const url = URL.createObjectURL(new Blob([new Uint8Array(value.bytes).buffer], { type: 'application/octet-stream' }))
    setDownload({ value, url })

    return () => URL.revokeObjectURL(url)
  }, [value])

  return <div className="grid gap-2 text-xs"><p className="break-all">{value.bytes.length}{" " + rt("complete bytes · SHA-256") + " "}{value.metadata.sha256}</p><pre aria-label={rt("Complete scheduled output")} className="max-h-96 overflow-auto whitespace-pre-wrap break-words">{value.text}</pre>{download?.value === value && <a download={`scheduled-${value.target.occurrence_id}-${value.target.output_index}.md`} href={download.url}>{label}</a>}</div>
}

function OwnedScheduledDraftPanel({ request, sessionId, connected, scope }: Props & { scope: Scope }) {
  const rt = useRuntimeUiText()
  const { locale } = useI18n(), c = labels[locale as keyof typeof labels] ?? labels.en
  const [project, setProject] = useState(scope.pending?.projectId ?? ''), [workflowId, setWorkflowId] = useState(''), [version, setVersion] = useState('1'), [scheduleId, setScheduleId] = useState('')
  const [workflow, setWorkflow] = useState<WorkflowRecord | null>(null), [inputs, setInputs] = useState<DraftInput[]>([]), [values, setValues] = useState<Record<string, string>>({}), [bindings, setBindings] = useState<Record<string, string>>({})
  const [settings, setSettings] = useState({ timezone: 'Etc/UTC', anchor: '', expiry: '', interval: '3600', maxChecks: '24', maxBytes: '262144', deadlineSeconds: '10', grantExpiry: '', maxAge: '60', maxFires: '1' })
  const [schedule, setSchedule] = useState<ScheduledDraftSnapshot | null>(null), [granted, setGranted] = useState(false), [verified, setVerified] = useState<VerifiedScheduledOutput | null>(null)
  const [proposal, setProposal] = useState<ArtifactProposalResult | null>(null), [review, setReview] = useState<Review | null>(null), [pending, setPending] = useState(scope.pending)
  const [busy, setBusy] = useState(false), [message, setMessage] = useState(''), [error, setError] = useState('')
  const lifetime = useRef({ active: true, busy: false, token: 0, abort: new AbortController() })
  useEffect(() => { const live = lifetime.current; live.active = true; live.abort = new AbortController();

 return () => { live.active = false; live.token++; live.abort.abort() } }, [])
  const base = { session_id: sessionId, schema_version: 1 as const }
  const blocked = busy || !connected || !sessionId

  function current(token: number) { return lifetime.current.active && connected && lifetime.current.token === token }

  function retain(value: Pending | null) { scope.pending = value; setPending(value) }

  function invalidate() { setReview(null); setProposal(null); setVerified(null); setMessage(''); setError(''); setGranted(false) }

  function change(work: () => void) { if (!lifetime.current.busy) { invalidate(); work() } }

  function selectProject(value: string) { setProject(value); setWorkflow(null); setInputs([]); setValues({}); setBindings({}); setSchedule(null) }

  async function action(work: (token: number) => Promise<void>) {
    const live = lifetime.current

    if (blocked || live.busy || !live.active) { return }
    live.busy = true; const token = ++live.token; setBusy(true); setError('')

    try { await work(token) }
    catch (caught) { if (current(token)) { setError(`${caught instanceof Error ? caught.message : rt("Request unavailable")}.${scope.pending ? rt(" Outcome may be unknown. Inspect or cancel the original command; no mutation was retried.") : ''}`); setReview(null); setProposal(null) } }
    finally { if (current(token)) { live.busy = false; setBusy(false) } }
  }

  async function inspectWorkflow(token: number) {
    invalidate(); setWorkflow(null); setInputs([])
    const result = await request('runtime.workflow.get', { ...base, project_id: draftIdentifier(project.trim()), workflow_id: draftIdentifier(workflowId.trim()), version: controlInteger(Number(version), 1) })

    if (!current(token)) { return }

    if (result.workflow.project_id !== project.trim() || result.workflow.workflow_id !== workflowId.trim() || result.workflow.version !== Number(version)) { throw new Error(rt("Workflow selection changed")) }
    const fields = scheduledDraftInputs(result.workflow)
    setWorkflow(result.workflow); setInputs(fields); setValues({}); setBindings({})
  }

  async function inspectSchedule(token: number) {
    invalidate(); setSchedule(null)
    const result = await request('runtime.schedule.get', { ...base, project_id: draftIdentifier(project.trim()), schedule_id: draftIdentifier(scheduleId.trim()) })

    if (current(token)) { setSchedule(scheduledDraftSnapshot(result, project.trim(), scheduleId.trim())) }
  }

  function reviewCreate() {
    try {
      if (!workflow || scope.pending || blocked) { return }
      const options: DraftDefinitionOptions = { projectId: project.trim(), scheduleId: scheduleId.trim(), workflow, values, bindings, timezone: settings.timezone, anchor: settings.anchor, expiry: settings.expiry, interval: Number(settings.interval), maxChecks: Number(settings.maxChecks), maxBytes: Number(settings.maxBytes), deadlineSeconds: Number(settings.deadlineSeconds) }
      setReview({ kind: 'create', definition: scheduledDraftDefinition(options) }); setError('')
    } catch (caught) { setError(caught instanceof Error ? caught.message : rt("Invalid schedule")) }
  }

  function reviewGrant() {
    try {
      if (!schedule || scope.pending || blocked) { return }
      const expires_at = draftInstant(settings.grantExpiry)

      if (expires_at * 1000 <= Date.now() || expires_at * 1000 > Date.now() + 30 * 86400000 || expires_at > Number(schedule.definition.expires_at)) { throw new Error(rt("Grant expiry must be future, within 30 days and no later than the schedule expiry")) }
      setReview({ kind: 'grant', params: { ...base, project_id: schedule.project_id, schedule_id: schedule.schedule_id, expected_revision: schedule.revision, command_id: crypto.randomUUID(), expires_at, max_age_seconds: controlInteger(Number(settings.maxAge), 1, 3600), max_fires: controlInteger(Number(settings.maxFires), 1, 100) } }); setError('')
    } catch (caught) { setError(caught instanceof Error ? caught.message : rt("Invalid grant")) }
  }

  function reviewState(state: ScheduleUpdateParams['state']) {
    if (!schedule || scope.pending || blocked || (state === 'active' && !granted)) { return }
    setReview({ kind: 'state', params: { ...base, project_id: schedule.project_id, schedule_id: schedule.schedule_id, expected_revision: schedule.revision, command_id: crypto.randomUUID(), state } })
  }

  async function commitReview(token: number) {
    const original = review

    if (!original || (scope.pending && original.kind !== 'publish')) { return }
    setReview(null)

    if (original.kind === 'publish') { await publish(original.proposal, token);

 return }

    const commandId = original.kind === 'create' ? crypto.randomUUID() : original.params.command_id
    retain({ projectId: project.trim(), kind: original.kind, commandId })

    if (original.kind === 'create') {
      const result = await request('runtime.schedule.create', { ...base, command_id: commandId, definition_json: JSON.stringify(original.definition) })

      if (!current(token)) { return }
      const value = scheduledDraftSnapshot(result, project.trim(), scheduleId.trim())

      if (value.state !== 'paused') { throw new Error(rt("Creation did not return a paused schedule")) }
      setSchedule(value); setGranted(false); setMessage(rt("Created paused. A separate finite draft-production grant and explicit activation are required."))
    } else if (original.kind === 'grant') {
      const result = controlRecord(controlJson((await request('runtime.schedule.grant', original.params)).record_json, 131072, false))

      if (!current(token)) { return }

      if (result.external_actions !== false || result.expires_at !== original.params.expires_at || result.remaining !== original.params.max_fires) { throw new Error(rt("Grant receipt differs from the exact review")) }
      controlDigest(result.target_digest); draftIdentifier(String(result.grant_id)); setGranted(true); setMessage(`Draft-only grant ${result.grant_id}; target ${result.target_digest}; ${result.remaining} fires. Activation is separate. No publication or external delivery authorized.`)
    } else {
      const result = await request('runtime.schedule.update', original.params)

      if (!current(token)) { return }
      const value = scheduledDraftSnapshot(result, original.params.project_id, original.params.schedule_id)

      if (value.state !== original.params.state || value.revision <= original.params.expected_revision) { throw new Error(rt("Schedule state receipt differs from the reviewed revision")) }
      setSchedule(value); setMessage(`Schedule ${value.state}. Already admitted work may still finish; inspect occurrence receipts.`)
    }

    retain(null)
  }

  async function read(target: DraftOutputTarget, token: number) {
    setVerified(null); setProposal(null); setReview(null)
    const result = await readScheduledDraft(request, sessionId, target, lifetime.current.abort.signal)

    if (current(token)) { setVerified(result) }
  }

  async function prepare(token: number) {
    if (!verified || scope.pending || verified.metadata.occurrence_state !== 'completed') { return }
    const target = verified.target, params = { ...base, project_id: target.project_id, schedule_id: target.schedule_id, occurrence_id: target.occurrence_id, output_index: target.output_index, expected_sha256: verified.metadata.sha256, command_id: crypto.randomUUID() }
    retain({ projectId: params.project_id, commandId: params.command_id, kind: 'output', outputParams: params }); setProposal(null)
    const result = await request('runtime.schedule.output.prepare', params)
    verifyScheduledProposal(result, verified)

    if (current(token)) { setProposal(result) }
  }

  async function publish(exact: ArtifactProposalResult, token: number) {
    const original = scope.pending

    if (!verified || !original?.outputParams || proposal !== exact || exact.expires_at * 1000 <= Date.now()) { throw new Error(rt("Review changed or expired; inspect or cancel the original preparation")) }
    verifyScheduledProposal(exact, verified); setProposal(null)
    const result = await request('runtime.schedule.output.publish', { ...original.outputParams, approval_id: exact.approval_id, approval_digest: exact.approval_digest })
    verifyScheduledPublication(result, exact)

    if (current(token)) { retain(null); setMessage(`Published ${result.artifact_id}@${result.version}; ${result.disposition}; validation ${result.validation_status}. No mission completion, sharing or external delivery is implied.`) }
  }

  async function recover(cancel: boolean, token: number) {
    const original = scope.pending

    if (!original || original.projectId !== project.trim() || (cancel && original.cancelRequested)) { return }
    setProposal(null); setReview(null)

    if (cancel) { original.cancelRequested = true }
    const result = await request(cancel ? 'runtime.artifact.cancel' : 'runtime.artifact.status', { ...base, command_id: original.commandId })

    if (!current(token)) { return }

    if (result.command_id !== original.commandId) { throw new Error(rt("Receipt belongs to another command")) }
    setMessage(`Original command ${result.command_id}: ${result.status}. Cancellation does not undo committed effects. Inspect the schedule and retained output before further action.`)

    if (terminal.has(result.status)) { retain(null) }
  }

  const field = (label: string, value: string, update: (value: string) => void, type = 'text') => <label className="grid gap-1 text-xs">{label}<Input disabled={blocked} onChange={event => change(() => update(event.target.value))} type={type} value={value} /></label>
  const setting = (key: keyof typeof settings, label: string, type = 'text') => field(label, settings[key], value => setSettings(old => ({ ...old, [key]: value })), type)
  const confirmLabel = review?.kind === 'create' ? c[7] : review?.kind === 'grant' ? c[10] : review?.kind === 'publish' ? c[16] : ({ active: c[11], paused: c[12], revoked: c[13] }[review?.kind === 'state' ? review.params.state : 'paused'])

  return <section aria-label={c[0]} className="grid gap-3">
    <h4 className="text-sm font-medium">{c[0]}</h4><p className="text-xs">{rt("Local retained sources only. Deterministic Markdown drafts require human review. No automatic publication or external delivery. The backend owns the timer.")}</p>
    {!connected && <p role="status">{rt("Disconnected. Reconnect in the original session to inspect unresolved commands.")}</p>}
    {field(c[1], project, selectProject)}{field(c[2], workflowId, value => { setWorkflowId(value); setWorkflow(null); setInputs([]) })}{field(c[3], version, value => { setVersion(value); setWorkflow(null); setInputs([]) }, 'number')}
    <Button disabled={blocked || !!pending || !project || !workflowId} onClick={() => void action(inspectWorkflow)} size="xs" variant="secondary">{c[4]}</Button>
    {workflow && <><p className="break-all text-xs">{rt("Approved pin:") + " "}{workflow.workflow_id}@{workflow.version} · SHA-256 {workflow.sha256}</p><details><summary>{rt("Exact workflow steps, input schema and template pins")}</summary><pre className="whitespace-pre-wrap break-words text-xs">{workflow.definition_json}</pre></details></>}
    {inputs.map(input => <div className="grid gap-2" key={input.name}>{field(`${input.name} (${input.type}${input.required ? rt(", required") : rt(", optional")})`, Object.hasOwn(values, input.name) ? values[input.name] : '', value => { setBindings(old => Object.fromEntries(Object.entries(old).filter(([name]) => name !== input.name))); setValues(old => ({ ...old, [input.name]: value })) })}{input.type === 'string' && field(`${input.name} local source artifact ID (optional)`, Object.hasOwn(bindings, input.name) ? bindings[input.name] : '', value => { setValues(old => Object.fromEntries(Object.entries(old).filter(([name]) => name !== input.name))); setBindings(old => ({ ...old, [input.name]: value })) })}</div>)}
    {field(c[5], scheduleId, value => { setScheduleId(value); setSchedule(null) })}
    <details><summary>{rt("Finite schedule and input binding review")}</summary><div className="grid gap-2">{setting('timezone', 'Schedule timezone')}{setting('anchor', 'First check ISO with offset')}{setting('expiry', 'Schedule expiry ISO with offset')}{setting('interval', 'Interval seconds', 'number')}{setting('maxChecks', 'Maximum checks', 'number')}{setting('maxBytes', 'Maximum bytes per occurrence', 'number')}{setting('deadlineSeconds', 'Deadline seconds', 'number')}</div><p className="text-xs">{rt("Fixed UTC intervals do not shift with daylight saving. Missed checks are skipped; overlap is blocked. Bound sources sample their current retained local text heads at each admitted fire.")}</p></details>
    <div className="flex flex-wrap gap-2"><Button disabled={blocked || !!pending || !workflow} onClick={reviewCreate} size="xs" variant="secondary">{c[6]}</Button><Button disabled={blocked || !project || !scheduleId} onClick={() => void action(inspectSchedule)} size="xs" variant="ghost">{c[8]}</Button></div>
    {schedule && <div className="grid gap-2 text-xs"><p>{rt("Schedule") + " "}{schedule.schedule_id} v{schedule.version}: {schedule.state}{rt("; revision") + " "}{schedule.revision}{rt(". Health") + " "}{String(schedule.health)}{rt("; remaining checks") + " "}{schedule.remaining_checks}{rt("; last success") + " "}{String(schedule.last_success ?? 'unavailable')}{rt("; next due") + " "}{String(schedule.next_due ?? 'unavailable')}{rt("; last error") + " "}{String(schedule.last_error ?? 'none')}</p><details><summary>{rt("Exact saved definition and destination")}</summary><pre className="whitespace-pre-wrap break-words">{JSON.stringify(schedule.definition, null, 2)}</pre></details>
      {setting('grantExpiry', 'Grant expiry ISO with offset')}{setting('maxAge', 'Grant maximum execution age seconds', 'number')}{setting('maxFires', 'Grant maximum fires', 'number')}
      <div className="flex flex-wrap gap-2"><Button disabled={blocked || !!pending || ['revoked', 'expired'].includes(schedule.state)} onClick={reviewGrant} size="xs" variant="secondary">{c[9]}</Button><Button disabled={blocked || !!pending || !granted || schedule.state !== 'paused'} onClick={() => reviewState('active')} size="xs" variant="secondary">{c[11]}</Button><Button disabled={blocked || !!pending || schedule.state !== 'active'} onClick={() => reviewState('paused')} size="xs" variant="ghost">{c[12]}</Button><Button disabled={blocked || !!pending || ['revoked', 'expired'].includes(schedule.state)} onClick={() => reviewState('revoked')} size="xs" variant="ghost">{c[13]}</Button></div>
      {schedule.occurrences.map(occurrence => <div className="grid gap-2" key={occurrence.occurrence_id}><p>{rt("Occurrence") + " "}{occurrence.occurrence_id}: {occurrence.state}{rt("; workflow run") + " "}{occurrence.workflow_run_id ?? 'unavailable'}{rt("; mission") + " "}{occurrence.mission_id ?? 'unavailable'}</p><details><summary>{rt("Retained source versions, parameters digest and byte receipts")}</summary><pre className="whitespace-pre-wrap break-words">{JSON.stringify(occurrence.provenance, null, 2)}</pre></details>{occurrence.outputs.map(output => <Button disabled={blocked || !!pending || !occurrence.workflow_run_id} key={output.output_index} onClick={() => void action(token => read({ project_id: schedule.project_id, schedule_id: schedule.schedule_id, occurrence_id: occurrence.occurrence_id, output_index: output.output_index, workflow_run_id: occurrence.workflow_run_id!, output }, token))} size="xs" variant="secondary">{c[14]}: {output.name} · {output.size}{" " + rt("bytes")}</Button>)}</div>)}
      {schedule.history_truncated && <p role="status">{rt("Recent occurrence history is truncated. This view does not claim completeness.")}</p>}
    </div>}
    {verified && <><VerifiedDraft label={c[19]} value={verified} /><Button disabled={blocked || !!pending || verified.metadata.occurrence_state !== 'completed'} onClick={() => void action(prepare)} size="xs" variant="secondary">{c[15]}</Button></>}
    {proposal && <><p className="break-all text-xs">{rt("New project artifact") + " "}{proposal.artifact_id}@{proposal.version}{rt("; approval") + " "}{proposal.approval_id}{rt("; expires") + " "}{new Date(proposal.expires_at * 1000).toISOString()}</p><Button disabled={blocked} onClick={() => setReview({ kind: 'publish', proposal })} size="xs" variant="secondary">{c[16]}</Button></>}
    <ConfirmDialog confirmLabel={confirmLabel} description={review?.kind === 'publish' ? rt("Publish these complete verified bytes as a new artifact in this project. No rerender, source reread, external delivery or automatic mission completion.") : rt("Review the exact pin, inputs, local sources, destination and finite limits below. Creation stays paused. A production grant, activation and publication are separate actions.")} onClose={() => setReview(null)} onConfirm={() => action(commitReview)} open={!!review && !blocked} title={confirmLabel}>
      {review?.kind === 'create' && <p className="text-xs">{rt("Project") + " "}{String(review.definition.project_id)}{rt("; timezone") + " "}{String(review.definition.timezone)}{rt("; expiry") + " "}{new Date(Number(review.definition.expires_at) * 1000).toISOString()}{rt(". Draft-only destination; create paused. First check and cadence are pinned below.")}</p>}
      {review?.kind === 'grant' && <p className="text-xs">{rt("Authorize at most") + " "}{review.params.max_fires}{" " + rt("draft-production fires until") + " "}{new Date(review.params.expires_at * 1000).toISOString()}{rt("; each admitted execution must finish within") + " "}{review.params.max_age_seconds}{" " + rt("seconds. Activation and publication remain separate.")}</p>}
      <pre aria-label={rt("Exact scheduled action review")} className="max-h-64 overflow-auto whitespace-pre-wrap break-words text-xs">{review ? JSON.stringify(review, null, 2) : ''}</pre>{review && review.kind !== 'create' && schedule && <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words text-xs">{JSON.stringify(schedule.definition, null, 2)}</pre>}{review?.kind === 'publish' && verified && <pre aria-label={rt("Publication confirmation bytes")} className="max-h-64 overflow-auto whitespace-pre-wrap break-words text-xs">{verified.text}</pre>}
    </ConfirmDialog>
    {pending && pending.projectId !== project.trim() && <p role="status">{rt("Another project retains an unresolved command. Return to its project to inspect or cancel it.")}</p>}
    {pending && pending.projectId === project.trim() && <div className="grid gap-2"><p className="break-all text-xs" role="status">{rt("Retained") + " "}{pending.kind}{" " + rt("command") + " "}{pending.commandId}{rt(". Closing this panel does not cancel it.")}</p><div className="flex gap-2"><Button disabled={blocked} onClick={() => void action(token => recover(false, token))} size="xs" variant="secondary">{c[17]}</Button><Button disabled={blocked || pending.cancelRequested} onClick={() => void action(token => recover(true, token))} size="xs" variant="text">{c[18]}</Button></div></div>}
    {busy && <p role="status">{rt("Waiting for the authoritative receipt…")}</p>}{message && <p className="text-xs" role="status">{message}</p>}{error && <p role="alert">{error}</p>}
  </section>
}
