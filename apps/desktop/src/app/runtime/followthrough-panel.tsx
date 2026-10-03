import type { RuntimeRequest } from '@hermes/shared/runtime-control'
import { runCommitmentCommand, runCorrespondenceCommand, summarizeCommitment, summarizeCorrespondence } from '@hermes/shared/runtime-followthrough'
import { controlFailure, controlId, controlInteger, controlJson, controlRecord, controlRef, RuntimeInputError, summarizeArtifactCommand } from '@hermes/shared/runtime-research'
import { useEffect, useId, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { ErrorState } from '@/components/ui/error-state'
import { Field } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { Loader } from '@/components/ui/loader'
import { Textarea } from '@/components/ui/textarea'
import { useI18n } from '@/i18n'
import { confirm } from '@/store/confirm'

import { useRuntimeUiText } from './runtime-ui-copy'

const recoveryLabels: Record<string, readonly [string, string]> = { en: ['Inspect command', 'Cancel pending command'], de: ['Befehl prüfen', 'Ausstehenden Befehl abbrechen'], es: ['Inspeccionar comando', 'Cancelar comando pendiente'], fr: ['Inspecter la commande', 'Annuler la commande en attente'], ja: ['コマンドを確認', '保留中のコマンドを取消'], zh: ['检查命令', '取消待处理命令'], 'zh-hant': ['檢查指令', '取消待處理指令'], ar: ['فحص الأمر', 'إلغاء الأمر المعلق'], ru: ['Проверить команду', 'Отменить ожидающую команду'] }

const labels = {
  en: ['Reviewed follow-through', 'Project ID', 'Review active commitments', 'Candidate ID', 'Inspect candidate', 'Owner identity', 'Exact outcome', 'Check at (ISO timestamp with offset)', 'IANA timezone', 'Accept reviewed commitment', 'Leave unaccepted', 'New draft identifier', 'Recipient identities (one per line)', 'Exact draft content', 'Source artifact ID', 'Source version', 'Source SHA-256', 'Create draft only', 'Returned correspondence ID', 'Inspect correspondence', 'Imported evidence requires review. Drafts do not send. Candidate dismissal and terminal reopen are unavailable.'],
  de: ['Geprüfte Folgeaufgaben', 'Projekt-ID', 'Aktive Zusagen prüfen', 'Kandidaten-ID', 'Kandidat prüfen', 'Verantwortliche Identität', 'Genaues Ergebnis', 'Prüfzeit (ISO mit Zeitzonenversatz)', 'IANA-Zeitzone', 'Geprüfte Zusage annehmen', 'Nicht annehmen', 'Neue Entwurfs-ID', 'Empfänger (einer pro Zeile)', 'Genauer Entwurfstext', 'Quellartefakt-ID', 'Quellversion', 'Quell-SHA-256', 'Nur Entwurf erstellen', 'Zurückgegebene Korrespondenz-ID', 'Korrespondenz prüfen', 'Importierte Belege müssen geprüft werden. Entwürfe senden nichts. Dauerhaftes Verwerfen und Wiederöffnen sind nicht verfügbar.'],
  es: ['Seguimiento revisado', 'ID del proyecto', 'Revisar compromisos activos', 'ID del candidato', 'Inspeccionar candidato', 'Identidad responsable', 'Resultado exacto', 'Revisar a las (ISO con desfase)', 'Zona horaria IANA', 'Aceptar compromiso revisado', 'Dejar sin aceptar', 'Nuevo ID de borrador', 'Destinatarios (uno por línea)', 'Contenido exacto', 'ID del artefacto fuente', 'Versión de fuente', 'SHA-256 de fuente', 'Crear solo borrador', 'ID devuelto de correspondencia', 'Inspeccionar correspondencia', 'Revisa la evidencia importada. Los borradores no envían. No se pueden descartar candidatos ni reabrir compromisos finalizados.'],
  fr: ['Suivi après examen', 'ID du projet', 'Examiner les engagements actifs', 'ID du candidat', 'Inspecter le candidat', 'Identité responsable', 'Résultat exact', 'Vérification (ISO avec décalage)', 'Fuseau IANA', 'Accepter cet engagement', 'Laisser non accepté', 'Nouvel ID de brouillon', 'Destinataires (un par ligne)', 'Contenu exact', 'ID du document source', 'Version source', 'SHA-256 source', 'Créer seulement un brouillon', 'ID de correspondance retourné', 'Inspecter la correspondance', 'Les preuves importées exigent un examen. Les brouillons ne sont pas envoyés. Rejet durable et réouverture indisponibles.'],
  ja: ['確認済みのフォローアップ', 'プロジェクトID', '有効な約束を確認', '候補ID', '候補を確認', '担当者の識別情報', '正確な成果', '確認日時（オフセット付きISO）', 'IANAタイムゾーン', '確認した約束を受諾', '未受諾のままにする', '新規下書きID', '宛先（1行に1つ）', '正確な下書き本文', 'ソース成果物ID', 'ソースバージョン', 'ソースSHA-256', '下書きのみ作成', '返された通信ID', '通信を確認', '取り込んだ証拠は確認が必要です。下書きは送信しません。候補の永続的な却下と完了後の再開は未対応です。'],
  zh: ['审阅后的跟进', '项目 ID', '审阅有效承诺', '候选 ID', '检查候选', '负责人身份', '确切结果', '检查时间（含偏移的 ISO）', 'IANA 时区', '接受已审阅承诺', '保持未接受', '新草稿标识', '收件人（每行一个）', '确切草稿内容', '来源文件 ID', '来源版本', '来源 SHA-256', '仅创建草稿', '返回的通信 ID', '检查通信', '导入证据需要审阅。草稿不会发送。不支持永久驳回候选或重新打开已结束承诺。'],
  'zh-hant': ['審閱後的跟進', '專案 ID', '審閱有效承諾', '候選 ID', '檢查候選', '負責人身分', '確切結果', '檢查時間（含偏移的 ISO）', 'IANA 時區', '接受已審閱承諾', '保持未接受', '新草稿識別碼', '收件者（每行一位）', '確切草稿內容', '來源文件 ID', '來源版本', '來源 SHA-256', '僅建立草稿', '傳回的通信 ID', '檢查通信', '匯入證據需要審閱。草稿不會寄送。不支援永久駁回候選或重新開啟已結束承諾。'],
  ar: ['متابعة بعد المراجعة', 'معرّف المشروع', 'مراجعة الالتزامات النشطة', 'معرّف المرشح', 'فحص المرشح', 'هوية المسؤول', 'النتيجة الدقيقة', 'وقت الفحص (ISO مع الإزاحة)', 'المنطقة الزمنية IANA', 'قبول الالتزام المراجع', 'تركه دون قبول', 'معرّف المسودة الجديدة', 'المستلمون (واحد في كل سطر)', 'محتوى المسودة الدقيق', 'معرّف مستند المصدر', 'إصدار المصدر', 'بصمة المصدر SHA-256', 'إنشاء مسودة فقط', 'معرّف المراسلة المُعاد', 'فحص المراسلة', 'تحتاج الأدلة المستوردة إلى مراجعة. المسودات لا ترسل. رفض المرشح نهائيًا وإعادة الفتح غير متاحين.'],
  ru: ['Проверенные обязательства', 'ID проекта', 'Проверить активные обязательства', 'ID кандидата', 'Проверить кандидата', 'Идентификатор ответственного', 'Точный результат', 'Время проверки (ISO со смещением)', 'Часовой пояс IANA', 'Принять проверенное обязательство', 'Оставить непринятым', 'ID нового черновика', 'Получатели (по одному в строке)', 'Точный текст черновика', 'ID исходного документа', 'Версия источника', 'SHA-256 источника', 'Только создать черновик', 'Полученный ID переписки', 'Проверить переписку', 'Импортированные доказательства требуют проверки. Черновики не отправляются. Отклонение навсегда и повторное открытие недоступны.']
} as const

export interface FollowthroughPanelProps { request: RuntimeRequest; sessionId: string; connected: boolean }

export function FollowthroughPanel(props: FollowthroughPanelProps) {
  const [scope, setScope] = useState({ request: props.request, sessionId: props.sessionId, connected: props.connected, generation: 0 })

  if (scope.request !== props.request || scope.sessionId !== props.sessionId || scope.connected !== props.connected) {
    setScope({ request: props.request, sessionId: props.sessionId, connected: props.connected, generation: scope.generation + 1 })

    return null
  }

  return <FollowthroughPanelBody key={scope.generation} {...props} />
}

function FollowthroughPanelBody({ request, sessionId, connected }: FollowthroughPanelProps) {
  const rt = useRuntimeUiText()
  const { locale, t } = useI18n(), copy = labels[locale as keyof typeof labels] ?? labels.en, id = useId()
  const [project, setProject] = useState(''), [candidateId, setCandidateId] = useState(''), [candidate, setCandidate] = useState<Record<string, unknown> | null>(null)
  const [owner, setOwner] = useState(''), [outcome, setOutcome] = useState(''), [when, setWhen] = useState(''), [timezone, setTimezone] = useState('')
  const [draftId, setDraftId] = useState(''), [recipients, setRecipients] = useState(''), [content, setContent] = useState(''), [correspondenceId, setCorrespondenceId] = useState('')
  const [sourceId, setSourceId] = useState(''), [sourceVersion, setSourceVersion] = useState('1'), [sourceDigest, setSourceDigest] = useState('')
  const [declineReason, setDeclineReason] = useState('')
  const [pendingCommand, setPendingCommand] = useState<string | null>(null)
  const [output, setOutput] = useState(''), [error, setError] = useState(''), [busy, setBusy] = useState(false)
  const lifetime = useRef({ active: true, busy: false, generation: 0 })
  useEffect(() => { const scope = lifetime.current; scope.active = true;

 return () => { scope.active = false; scope.generation++ } }, [])

  async function perform(action: (current: () => boolean) => Promise<void>) {
    const scope = lifetime.current

    if (!connected || scope.busy || !scope.active) {return}
    const generation = ++scope.generation, current = () => scope.active && scope.generation === generation
    scope.busy = true; setBusy(true); setError('')

    try { await action(current) } catch (error) { if (current()) {setError(controlFailure(error, true))} }
    finally { scope.busy = false;

 if (current()) {setBusy(false)} }
  }

  const field = (key: string, label: string, value: string, change: (value: string) => void, type = 'text') => <Field htmlFor={`${id}-${key}`} label={label}><Input disabled={busy || !connected} id={`${id}-${key}`} onChange={event => change(event.target.value)} type={type} value={value} /></Field>
  const prose = (key: string, label: string, value: string, change: (value: string) => void) => <Field htmlFor={`${id}-${key}`} label={label}><Textarea disabled={busy || !connected} id={`${id}-${key}`} onChange={event => change(event.target.value)} value={value} /></Field>

  function changeProject(value: string) {
    setProject(value); setCandidate(null); setCandidateId(''); setOwner(''); setOutcome(''); setWhen(''); setTimezone(''); setDraftId(''); setRecipients(''); setContent(''); setCorrespondenceId(''); setSourceId(''); setSourceVersion('1'); setSourceDigest(''); setOutput(''); setError('')
  }

  async function inspectCandidate() {
    await perform(async current => {
      const response = await request('runtime.commitment.candidate', { session_id: sessionId, schema_version: 1, project_id: project, candidate_id: candidateId })
      const row = controlRecord(controlJson(response.record_json, 131072, false))

      if (row.project_id !== project || row.candidate_id !== candidateId) {throw new RuntimeInputError(rt("Candidate selection differs from the returned scope"))}
      const summary = row.review_state === 'declined' ? `Candidate declined: ${String(row.decline_reason)}. No obligation created.` : row.accepted === true ? `Candidate already accepted as ${controlId(row.commitment_id)}. Inspect that accepted obligation.` : summarizeCommitment(row, true)

      if (current()) { setCandidate(row.accepted === false && row.review_state !== 'declined' ? row : null); setOwner(''); setOutcome(String(row.outcome ?? '')); setWhen(''); setTimezone(''); setOutput(summary) }
    })
  }

  async function decline() {
    const selected = candidate

    if (!selected || !declineReason.trim() || pendingCommand) {return}
    await perform(async current => {
      const approved = await confirm({ title: rt("Decline this exact candidate?"), details: [{ label: copy[3], value: `${selected.candidate_id}; revision ${selected.revision}` }, { label: rt("Reason"), value: declineReason }], confirmLabel: t.common.confirm, cancelLabel: t.common.cancel })

      if (!approved || !current()) {return}
      const command_id = crypto.randomUUID()
      setPendingCommand(command_id); setCandidate(null)
      const result = await runCommitmentCommand(`decline ${JSON.stringify({ project_id: project, candidate_id: selected.candidate_id, command_id, expected_revision: selected.revision, reason: declineReason })}`, request, sessionId)

      if (current()) {setOutput(result)}
    })
  }

  async function accept() {
    const selected = candidate

    if (!selected) {return}
    const input = { project_id: project, candidate_id: selected.candidate_id, command_id: crypto.randomUUID(), expected_revision: selected.revision, owner, outcome, due_or_check_at: when ? { at: when, timezone, kind: 'check' } : null }
    await perform(async current => {
      const approved = await confirm({ title: copy[9], details: [{ label: copy[3], value: `${selected.candidate_id}; revision ${selected.revision}` }, { label: copy[5], value: owner }, { label: copy[6], value: outcome }, { label: copy[7], value: when ? `${when} (${timezone})` : t.common.notSet }], confirmLabel: t.common.confirm, cancelLabel: t.common.cancel })

      if (!approved || !current()) {return}
      setCandidate(null)

      const capture: RuntimeRequest = async (method, params) => {
        if (current() && 'command_id' in params) {setPendingCommand(String(params.command_id))}

        return request(method, params)
      }

      const result = await runCommitmentCommand(`accept ${JSON.stringify(input)}`, capture, sessionId)

      if (current()) {setOutput(result)}
    })
  }

  async function createDraft() {
    await perform(async current => {
      const ref = controlRef({ artifact_id: sourceId, version: controlInteger(Number(sourceVersion), 1), sha256: sourceDigest })
      const input = { project_id: project, correspondence_id: draftId, command_id: crypto.randomUUID(), recipients: recipients.split('\n').map(value => value.trim()).filter(Boolean), content, source_refs_json: JSON.stringify([ref]) }
      // Shared validation runs before the actual RPC; capture only its verified canonical response.
      let resultId: string | null = null

      const capture: RuntimeRequest = async (method, params) => {
        if (current() && 'command_id' in params) {setPendingCommand(String(params.command_id))}
        const response = await request(method, params)

        if (method === 'runtime.correspondence.draft') {
          const row = controlRecord(controlJson((response as { record_json: string }).record_json, 131072, false))
          summarizeCorrespondence(row, true)

          if (row.project_id !== project) {throw new RuntimeInputError(rt("Draft project differs"))}
          resultId = controlId(row.correspondence_id)
        }

        return response
      }

      const result = await runCorrespondenceCommand(`draft ${JSON.stringify(input)}`, capture, sessionId)

      if (current()) { if (resultId) {setCorrespondenceId(resultId);} setOutput(result) }
    })
  }

  async function recover(cancel: boolean) {
    const command = pendingCommand

    if (!command) {return}
    await perform(async current => {
      const result = await request(cancel ? 'runtime.artifact.cancel' : 'runtime.artifact.status', { session_id: sessionId, schema_version: 1, command_id: command })

      if (result.command_id !== command) {throw new RuntimeInputError(rt("Command receipt differs"))}

      if (current()) {
        setOutput(summarizeArtifactCommand(result))

        if (['cancelled', 'completed', 'failed', 'blocked'].includes(result.status)) {setPendingCommand(null)}
      }
    })
  }

  return <section aria-label={copy[0]} className="grid gap-4">
    <p className="text-xs text-muted-foreground">{rt("Imported evidence requires review. Drafts do not send. Decline records a durable decision without creating an obligation; terminal/superseded obligations stay terminal.")}</p>
    {field('project', copy[1], project, changeProject)}
    <Button disabled={busy || !connected || !project} onClick={() => void perform(async current => { const result = await runCommitmentCommand(`review ${project}`, request, sessionId);

 if (current()) {setOutput(result)} })} size="xs" variant="secondary">{copy[2]}</Button>
    {field('candidate', copy[3], candidateId, value => { setCandidate(null); setCandidateId(value); setOwner(''); setOutcome(''); setWhen(''); setTimezone(''); setOutput('') })}
    <Button disabled={busy || !connected || !project || !candidateId} onClick={() => void inspectCandidate()} size="xs" variant="secondary">{copy[4]}</Button>
    {candidate && <>{prose('decline-reason', rt("Reason for declining"), declineReason, setDeclineReason)}<Button disabled={busy || !connected || !!pendingCommand || !declineReason.trim()} onClick={() => void decline()} size="xs" variant="text">{rt("Decline reviewed candidate")}</Button>{field('owner', copy[5], owner, setOwner)}{prose('outcome', copy[6], outcome, setOutcome)}
      <div className="grid gap-4 sm:grid-cols-2">{field('when', copy[7], when, setWhen)}{field('timezone', copy[8], timezone, setTimezone)}</div>
      <div className="flex flex-wrap gap-2"><Button disabled={busy || !connected || !!pendingCommand || !owner || !outcome || (!!when && !timezone)} onClick={() => void accept()} size="xs" variant="secondary">{copy[9]}</Button><Button disabled={busy} onClick={() => { setCandidate(null); setOutput(rt("Candidate left unaccepted. This does not record a durable decline.")) }} size="xs" variant="text">{copy[10]}</Button></div></>}
    {field('draft', copy[11], draftId, setDraftId)}{prose('recipients', copy[12], recipients, setRecipients)}{prose('content', copy[13], content, setContent)}
    <div className="grid gap-4 sm:grid-cols-2">{field('source', copy[14], sourceId, setSourceId)}{field('source-version', copy[15], sourceVersion, setSourceVersion, 'number')}</div>
    {field('digest', copy[16], sourceDigest, setSourceDigest)}
    <Button disabled={busy || !connected || !!pendingCommand || !project || !draftId || !recipients || !content || !sourceId || !sourceDigest} onClick={() => void createDraft()} size="xs" variant="secondary">{copy[17]}</Button>
    {field('correspondence', copy[18], correspondenceId, value => { setCorrespondenceId(value); setOutput('') })}
    <Button disabled={busy || !connected || !project || !correspondenceId} onClick={() => void perform(async current => { const result = await runCorrespondenceCommand(`get ${project} ${correspondenceId}`, request, sessionId);

 if (current()) {setOutput(result)} })} size="xs" variant="secondary">{copy[19]}</Button>
    {pendingCommand && <div className="grid gap-2"><p className="break-words text-xs text-muted-foreground">{pendingCommand}</p><div className="flex gap-2"><Button disabled={busy || !connected} onClick={() => void recover(false)} size="xs" variant="ghost">{(recoveryLabels[locale] ?? recoveryLabels.en)[0]}</Button><Button disabled={busy || !connected} onClick={() => void recover(true)} size="xs" variant="text">{(recoveryLabels[locale] ?? recoveryLabels.en)[1]}</Button></div></div>}
    {busy && <Loader label={copy[0]} />}{error && <ErrorState description={error} title={t.common.error} />}
    <div aria-busy={busy} aria-live="polite" className="whitespace-pre-wrap break-words text-sm">{output}</div>
  </section>
}
