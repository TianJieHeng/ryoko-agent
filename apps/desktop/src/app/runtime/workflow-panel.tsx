import type { RuntimeRequest } from '@hermes/shared/runtime-control'
import { artifactProposal, controlDigest, controlFailure, controlInteger, controlJson, controlRecord, RuntimeInputError, summarizeArtifactCommand } from '@hermes/shared/runtime-research'
import { runScheduleCommand, runWorkflowCommand, summarizeSchedule, summarizeWorkflow } from '@hermes/shared/runtime-workflows'
import { useEffect, useId, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { ErrorState } from '@/components/ui/error-state'
import { Field } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { Loader } from '@/components/ui/loader'
import { useI18n } from '@/i18n'
import { confirm } from '@/store/confirm'

import type { WorkflowRecord, WorkflowRunParams, WorkflowRunPrepareResult } from '../../../../shared/src/gateway-contract.generated.js'

const discardLabels: Record<string, string> = { en: 'Discard prepared run', de: 'Vorbereiteten Lauf verwerfen', es: 'Descartar ejecución preparada', fr: 'Abandonner cette préparation', ja: '準備済み実行を破棄', zh: '丢弃已准备运行', 'zh-hant': '捨棄已準備執行', ar: 'إلغاء التشغيل المحضر', ru: 'Отменить подготовленный запуск' }

const complexInputLabels: Record<string, string> = { en: 'Nested inputs require the advanced workflow command. No run has started.', de: 'Verschachtelte Eingaben erfordern den erweiterten Ablaufbefehl. Kein Lauf wurde gestartet.', es: 'Las entradas anidadas requieren el comando avanzado. No se inició ninguna ejecución.', fr: 'Les entrées imbriquées exigent la commande avancée. Aucune exécution lancée.', ja: '入れ子の入力には高度なコマンドが必要です。実行は開始していません。', zh: '嵌套输入需要高级工作流命令。尚未开始运行。', 'zh-hant': '巢狀輸入需要進階工作流程指令。尚未開始執行。', ar: 'المدخلات المتداخلة تتطلب الأمر المتقدم. لم يبدأ تشغيل.', ru: 'Вложенные параметры требуют расширенной команды. Запуск не начат.' }

const labels = {
  en: ['Workflows and monitors', 'Project ID', 'Workflow ID', 'Version', 'Inspect workflow', 'List workflows', 'Mission ID', 'Mission revision', 'Prepare run', 'Publish reviewed outputs', 'Schedule ID', 'Inspect schedule', 'Pause schedule', 'Resume schedule', 'Local sources only. Preparation does not publish. Quiet hours, digest and snooze are unavailable.'],
  de: ['Abläufe und Monitore', 'Projekt-ID', 'Ablauf-ID', 'Version', 'Ablauf prüfen', 'Abläufe auflisten', 'Auftrags-ID', 'Auftragsrevision', 'Lauf vorbereiten', 'Geprüfte Ausgaben veröffentlichen', 'Zeitplan-ID', 'Zeitplan prüfen', 'Zeitplan pausieren', 'Zeitplan fortsetzen', 'Nur lokale Quellen. Vorbereitung veröffentlicht nichts. Ruhezeiten, Zusammenfassung und Schlummern sind nicht verfügbar.'],
  es: ['Flujos y monitores', 'ID del proyecto', 'ID del flujo', 'Versión', 'Inspeccionar flujo', 'Listar flujos', 'ID de misión', 'Revisión de misión', 'Preparar ejecución', 'Publicar resultados revisados', 'ID del programa', 'Inspeccionar programa', 'Pausar programa', 'Reanudar programa', 'Solo fuentes locales. Preparar no publica. Horas silenciosas, resúmenes y aplazamiento no disponibles.'],
  fr: ['Procédures et suivis', 'ID du projet', 'ID de procédure', 'Version', 'Inspecter la procédure', 'Lister les procédures', 'ID de mission', 'Révision de mission', 'Préparer une exécution', 'Publier les résultats examinés', 'ID du programme', 'Inspecter le programme', 'Suspendre le programme', 'Reprendre le programme', 'Sources locales uniquement. La préparation ne publie rien. Plages silencieuses, résumés et report indisponibles.'],
  ja: ['ワークフローと監視', 'プロジェクトID', 'ワークフローID', 'バージョン', 'ワークフローを確認', '一覧を表示', 'ミッションID', 'ミッション改訂', '実行を準備', '確認済み出力を公開', 'スケジュールID', 'スケジュールを確認', '一時停止', '再開', 'ローカルソースのみ。準備では公開しません。通知休止時間、ダイジェスト、スヌーズは未対応です。'],
  zh: ['工作流与监控', '项目 ID', '工作流 ID', '版本', '检查工作流', '列出工作流', '任务 ID', '任务修订', '准备运行', '发布已审阅输出', '计划 ID', '检查计划', '暂停计划', '恢复计划', '仅本地来源。准备不会发布。不支持静默时段、摘要和暂缓。'],
  'zh-hant': ['工作流程與監控', '專案 ID', '工作流程 ID', '版本', '檢查工作流程', '列出工作流程', '任務 ID', '任務修訂', '準備執行', '發布已審閱輸出', '排程 ID', '檢查排程', '暫停排程', '恢復排程', '僅本機來源。準備不會發布。不支援靜默時段、摘要與稍後提醒。'],
  ar: ['سير العمل والمراقبة', 'معرّف المشروع', 'معرّف سير العمل', 'الإصدار', 'فحص سير العمل', 'عرض سير العمل', 'معرّف المهمة', 'مراجعة المهمة', 'تحضير التشغيل', 'نشر المخرجات المراجعة', 'معرّف الجدول', 'فحص الجدول', 'إيقاف الجدول مؤقتًا', 'استئناف الجدول', 'مصادر محلية فقط. التحضير لا ينشر. ساعات الهدوء والملخص والتأجيل غير متاحة.'],
  ru: ['Процессы и наблюдение', 'ID проекта', 'ID процесса', 'Версия', 'Проверить процесс', 'Список процессов', 'ID задачи', 'Ревизия задачи', 'Подготовить запуск', 'Опубликовать проверенные результаты', 'ID расписания', 'Проверить расписание', 'Приостановить расписание', 'Возобновить расписание', 'Только локальные источники. Подготовка не публикует. Тихие часы, дайджест и отсрочка недоступны.']
} as const

const notificationCopy = {
  en: 'Local retained sources only. Preparation does not publish. Local notifications require a separate explicit finite policy; quiet hours, digest and snooze are available under Monitor health.',
  de: 'Nur lokal gespeicherte Quellen. Vorbereitung veröffentlicht nichts. Lokale Benachrichtigungen benötigen eine eigene begrenzte Freigabe im Monitor.',
  es: 'Solo fuentes locales conservadas. Preparar no publica. Las notificaciones locales requieren una política explícita y limitada en el monitor.',
  fr: 'Sources locales conservées uniquement. La préparation ne publie rien. Les notifications locales nécessitent une politique explicite et limitée dans le moniteur.',
  ja: '保持されたローカルソースのみ。準備は公開しません。ローカル通知にはモニターで明示的な有限ポリシーが必要です。',
  zh: '仅使用留存的本地来源。准备不等于发布。本地通知需在监控设置中明确授权有限策略。',
  'zh-hant': '僅使用保留的本機來源。準備不等於發布。本機通知需在監控設定中明確授權有限策略。',
  ar: 'المصادر المحلية المحفوظة فقط. التحضير لا ينشر. تتطلب الإشعارات المحلية سياسة صريحة ومحدودة في المراقب.',
  ru: 'Только сохранённые локальные источники. Подготовка не публикует. Локальным уведомлениям нужна отдельная явная ограниченная политика.'
} as const

export interface WorkflowPanelProps { request: RuntimeRequest; sessionId: string; connected: boolean }

export function WorkflowPanel(props: WorkflowPanelProps) {
  const [scope, setScope] = useState({ request: props.request, sessionId: props.sessionId, connected: props.connected, generation: 0 })

  if (scope.request !== props.request || scope.sessionId !== props.sessionId || scope.connected !== props.connected) {
    setScope({ request: props.request, sessionId: props.sessionId, connected: props.connected, generation: scope.generation + 1 })

    return null
  }

  return <WorkflowPanelBody key={scope.generation} {...props} />
}

function WorkflowPanelBody({ request, sessionId, connected }: WorkflowPanelProps) {
  const { locale, t } = useI18n(), copy = labels[locale as keyof typeof labels] ?? labels.en, id = useId()
  const [project, setProject] = useState(''), [workflowId, setWorkflowId] = useState(''), [version, setVersion] = useState('1')
  const [mission, setMission] = useState(''), [missionRevision, setMissionRevision] = useState('1'), [scheduleId, setScheduleId] = useState('')
  const [workflow, setWorkflow] = useState<WorkflowRecord | null>(null), [parameters, setParameters] = useState<Record<string, string>>({})
  const [schedule, setSchedule] = useState<Record<string, unknown> | null>(null)
  const [prepared, setPrepared] = useState<{ input: WorkflowRunParams; result: WorkflowRunPrepareResult | null } | null>(null)
  const [reviewCurrent, setReviewCurrent] = useState(false)
  const [output, setOutput] = useState(''), [error, setError] = useState(''), [busy, setBusy] = useState(false)
  const lifetime = useRef({ active: true, busy: false, generation: 0 })
  useEffect(() => { const scope = lifetime.current; scope.active = true;

 return () => { scope.active = false; scope.generation++ } }, [])

  function reset() { lifetime.current.generation++; setWorkflow(null); setSchedule(null); setReviewCurrent(false); setOutput(''); setError(''); setParameters({}) }

  async function perform(action: (current: () => boolean) => Promise<void>) {
    const scope = lifetime.current

    if (!connected || scope.busy || !scope.active) {return}
    const generation = ++scope.generation, current = () => scope.active && scope.generation === generation
    scope.busy = true; setBusy(true); setError('')

    try { await action(current) } catch (error) { if (current()) {setError(controlFailure(error, true))} }
    finally { scope.busy = false;

 if (current()) {setBusy(false)} }
  }

  const definition = workflow ? controlRecord(controlJson(workflow.definition_json, 131072, false)) : null
  const inputs = definition ? controlRecord(controlRecord(definition.input_schema).properties) : {}
  const scalar = Object.values(inputs).every(value => ['string', 'integer', 'number', 'boolean'].includes(String(controlRecord(value).type)))
  const field = (key: string, label: string, value: string, change: (value: string) => void, type = 'text') => <Field htmlFor={`${id}-${key}`} label={label}><Input disabled={busy || !connected} id={`${id}-${key}`} onChange={event => change(event.target.value)} type={type} value={value} /></Field>

  async function inspectWorkflow() {
    await perform(async current => {
      const result = await request('runtime.workflow.get', { session_id: sessionId, schema_version: 1, project_id: project, workflow_id: workflowId, version: controlInteger(Number(version), 1) })
      const summary = summarizeWorkflow(result.workflow, true)

      if (result.workflow.project_id !== project || result.workflow.workflow_id !== workflowId || result.workflow.version !== Number(version)) {throw new RuntimeInputError('Workflow selection changed')}

      if (current()) { setWorkflow(result.workflow); setReviewCurrent(false); setParameters({}); setOutput(summary) }
    })
  }

  async function prepare() {
    await perform(async current => {
      if (prepared) {throw new RuntimeInputError('Inspect or explicitly discard the previous preparation before starting another')}

      if (!workflow || !scalar) {throw new RuntimeInputError('Inspect a supported workflow before preparing')}
      const values: Record<string, unknown> = {}, required = controlRecord(definition!.input_schema).required as string[] | undefined

      for (const [name, value] of Object.entries(inputs)) {
        const schema = controlRecord(value), entered = parameters[name]

        if (entered === undefined && !required?.includes(name)) {continue}

        if (schema.type === 'boolean' && !['true', 'false'].includes(entered ?? '')) {throw new RuntimeInputError(`Input ${name} requires true or false`)}
        values[name] = schema.type === 'string' ? entered ?? '' : schema.type === 'boolean' ? entered === 'true' : Number(entered)

        if ((schema.type === 'integer' || schema.type === 'number') && (entered === undefined || entered === '' || !Number.isFinite(values[name]))) {throw new RuntimeInputError(`Input ${name} requires a finite number`)}
      }

      const input: WorkflowRunParams = { session_id: sessionId, schema_version: 1, project_id: project, workflow_id: workflow.workflow_id, version: workflow.version, sha256: workflow.sha256, command_id: crypto.randomUUID(), mission_id: mission, mission_revision: controlInteger(Number(missionRevision), 1), parameters_json: JSON.stringify(controlRecord(controlJson(JSON.stringify(values), 131072))) }
      setPrepared({ input, result: null }); setReviewCurrent(false)
      const result = await request('runtime.workflow.run.prepare', input), pin = controlRecord(controlJson(result.pin_json, 131072, false))
      const proposals = result.proposals.map(artifactProposal)

      if (pin.workflow_id !== input.workflow_id || pin.version !== input.version || pin.sha256 !== input.sha256 || pin.mission_id !== mission || pin.mission_revision !== input.mission_revision || result.publication_atomic !== false || proposals.length < 2 || proposals.some(proposal => proposal.project_id !== project)) {throw new RuntimeInputError('Prepared run does not match reviewed scope')}

      if (current()) { setPrepared({ input, result }); setReviewCurrent(true); setOutput(`Prepared run ${result.workflow_run_id}; workflow ${workflow.workflow_id} v${workflow.version}; input SHA-256 ${controlDigest(pin.parameters_sha256)}. ${proposals.length} outputs await explicit publication. Nothing published.`) }
    })
  }

  async function publish() {
    const selected = prepared

    if (!selected?.result || !reviewCurrent) {return}
    const reviewed = selected.result
    await perform(async current => {
      const approved = await confirm({ title: copy[9], description: `${selected.input.workflow_id} v${selected.input.version}; ${selected.input.parameters_json}`, details: reviewed.proposals.map(proposal => ({ label: `${proposal.artifact_id} v${proposal.version}`, value: `${proposal.mime}; SHA-256 ${proposal.sha256}` })), confirmLabel: t.common.confirm, cancelLabel: t.common.cancel })

      if (!approved || !current()) {return}
      setReviewCurrent(false)
      const { session_id: _session, schema_version: _schema, ...input } = selected.input
      const result = await runWorkflowCommand(`run-publish ${JSON.stringify({ ...input, approvals: reviewed.proposals.map(({ approval_id, approval_digest }) => ({ approval_id, approval_digest })) })}`, request, sessionId)

      if (current()) {setOutput(result)}
    })
  }

  async function inspectPrepared(cancel: boolean) {
    const selected = prepared

    if (!selected) {return}
    await perform(async current => {
      if (cancel) {setReviewCurrent(false)}
      const result = await request(cancel ? 'runtime.artifact.cancel' : 'runtime.artifact.status', { session_id: selected.input.session_id, schema_version: 1, command_id: selected.input.command_id })

      if (result.command_id !== selected.input.command_id) {throw new RuntimeInputError('Cancellation command differs')}

      if (current()) {
        setOutput(summarizeArtifactCommand(result))

        if (['cancelled', 'completed', 'failed', 'blocked'].includes(result.status)) { setPrepared(null); setReviewCurrent(false) }
      }
    })
  }

  async function inspectSchedule() {
    await perform(async current => {
      const result = await request('runtime.schedule.get', { session_id: sessionId, schema_version: 1, project_id: project, schedule_id: scheduleId })
      const row = controlRecord(controlJson(result.record_json, 131072, false)), summary = summarizeSchedule(row)

      if (row.project_id !== project || row.schedule_id !== scheduleId) {throw new RuntimeInputError('Schedule selection changed')}

      if (current()) { setSchedule(row); setOutput(summary) }
    })
  }

  async function scheduleAction(action: 'pause' | 'resume') {
    const selected = schedule

    if (!selected) {return}
    await perform(async current => {
      const approved = await confirm({ title: action === 'pause' ? copy[12] : copy[13], description: `${project} / ${selected.schedule_id}; revision ${selected.revision}`, confirmLabel: t.common.confirm, cancelLabel: t.common.cancel })

      if (!approved || !current()) {return}
      setSchedule(null)
      const result = await runScheduleCommand(`${action} ${project} ${selected.schedule_id} ${selected.revision} ${crypto.randomUUID()}`, request, sessionId)

      if (current()) {setOutput(result)}
    })
  }

  return <section aria-label={copy[0]} className="grid gap-4">
    <p className="text-xs text-muted-foreground">{notificationCopy[locale as keyof typeof notificationCopy] ?? notificationCopy.en}</p>
    {field('project', copy[1], project, value => { reset(); setProject(value) })}
    <div className="grid gap-4 sm:grid-cols-2">{field('workflow', copy[2], workflowId, value => { reset(); setWorkflowId(value) })}{field('version', copy[3], version, value => { reset(); setVersion(value) }, 'number')}</div>
    <div className="flex flex-wrap gap-2"><Button disabled={busy || !connected || !project || !workflowId} onClick={() => void inspectWorkflow()} size="xs" variant="secondary">{copy[4]}</Button><Button disabled={busy || !connected || !project} onClick={() => void perform(async current => { const result = await runWorkflowCommand(`list ${project}`, request, sessionId);

 if (current()) { setReviewCurrent(false); setOutput(result) } })} size="xs" variant="ghost">{copy[5]}</Button></div>
    {workflow && !scalar && <p className="text-xs text-muted-foreground">{complexInputLabels[locale] ?? complexInputLabels.en}</p>}
    {workflow && scalar && <><div className="grid gap-4 sm:grid-cols-2">{Object.keys(inputs).map(name => <div key={name}>{field(`input-${name}`, `${name} (${controlRecord(inputs[name]).type})`, parameters[name] ?? '', value => { setReviewCurrent(false); setParameters(previous => ({ ...previous, [name]: value })) })}</div>)}</div>
      <div className="grid gap-4 sm:grid-cols-2">{field('mission', copy[6], mission, value => { setReviewCurrent(false); setMission(value) })}{field('mission-revision', copy[7], missionRevision, value => { setReviewCurrent(false); setMissionRevision(value) }, 'number')}</div>
      <div className="flex gap-2"><Button disabled={busy || !connected || workflow.state !== 'approved' || !mission || !!prepared} onClick={() => void prepare()} size="xs" variant="secondary">{copy[8]}</Button></div></>}
    {prepared && <div className="grid gap-2"><p className="break-words text-xs text-muted-foreground">{prepared.input.command_id}</p><div className="flex flex-wrap gap-2"><Button disabled={busy || !connected || !reviewCurrent || !prepared.result} onClick={() => void publish()} size="xs" variant="secondary">{copy[9]}</Button><Button disabled={busy || !connected} onClick={() => void inspectPrepared(false)} size="xs" variant="ghost">{t.common.refresh}</Button><Button disabled={busy || !connected} onClick={() => void inspectPrepared(true)} size="xs" variant="text">{discardLabels[locale] ?? discardLabels.en}</Button></div></div>}
    {field('schedule', copy[10], scheduleId, value => { setSchedule(null); setOutput(''); setScheduleId(value) })}
    <div className="flex flex-wrap gap-2"><Button disabled={busy || !connected || !project || !scheduleId} onClick={() => void inspectSchedule()} size="xs" variant="secondary">{copy[11]}</Button><Button disabled={busy || !connected || !schedule || !!prepared || schedule.state !== 'active'} onClick={() => void scheduleAction('pause')} size="xs" variant="ghost">{copy[12]}</Button><Button disabled={busy || !connected || !schedule || !!prepared || schedule.state !== 'paused'} onClick={() => void scheduleAction('resume')} size="xs" variant="ghost">{copy[13]}</Button></div>
    {busy && <Loader label={copy[0]} />}{error && <ErrorState description={error} title={t.common.error} />}
    <div aria-busy={busy} aria-live="polite" className="whitespace-pre-wrap break-words text-sm">{output}</div>
  </section>
}
