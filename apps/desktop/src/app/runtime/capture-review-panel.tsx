import { useEffect, useLayoutEffect, useMemo, useState, useSyncExternalStore } from 'react'

import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorState } from '@/components/ui/error-state'
import { Input } from '@/components/ui/input'
import { SearchField } from '@/components/ui/search-field'
import { useI18n } from '@/i18n'

import type { ArtifactVersionRef, CaptureInspectResult } from '../../../../shared/src/gateway-contract.generated'
import { type DownloadedArtifact } from '../../../../shared/src/runtime-artifacts'
import { type CaptureReview, captureReviewSession, type CaptureReviewSession, captureSequence } from '../../../../shared/src/runtime-capture-review'
import type { RuntimeRequest } from '../../../../shared/src/runtime-control'

import { useRuntimeUiText } from './runtime-ui-copy'

const words = {
  en: ['Review captures', 'Project ID', 'Search captures', 'Search', 'Result limit', 'Scan limit', 'Inspect', 'Select for batch', 'Process original text', 'Process latest supplied text', 'Filing project (blank clears)', 'Consolidate into capture (blank undoes)', 'Remove selection', 'Preview metadata changes', 'Review exact batch', 'Commit reviewed metadata', 'Inspect retained operation', 'Download original', 'Save verified original', 'No matches in this bounded scan', 'Original', 'Acquired', 'Annotation', 'Processing', 'Extraction history', 'Metadata history', 'Unknown outcome; retained identity', 'Offline: reconnect before acting', 'Before → after', 'Capture request unavailable', 'Capture ID'],
  de: ['Erfassungen prüfen', 'Projekt-ID', 'Erfassungen suchen', 'Suchen', 'Ergebnislimit', 'Scanlimit', 'Prüfen', 'Zum Stapel hinzufügen', 'Originaltext verarbeiten', 'Letzten bereitgestellten Text verarbeiten', 'Ablageprojekt (leer löscht)', 'Mit Erfassung bündeln (leer hebt auf)', 'Auswahl entfernen', 'Metadaten voranzeigen', 'Exakten Stapel prüfen', 'Geprüfte Metadaten übernehmen', 'Vorgang prüfen', 'Original herunterladen', 'Geprüftes Original speichern', 'Keine Treffer im begrenzten Scan', 'Original', 'Erfasst', 'Anmerkung', 'Verarbeitung', 'Extraktionsverlauf', 'Metadatenverlauf', 'Unbekanntes Ergebnis; Identität erhalten', 'Offline: erneut verbinden', 'Vorher → nachher', 'Erfassungsanfrage nicht verfügbar', 'Erfassungs-ID'],
  es: ['Revisar capturas', 'ID del proyecto', 'Buscar capturas', 'Buscar', 'Límite de resultados', 'Límite de exploración', 'Inspeccionar', 'Seleccionar para lote', 'Procesar texto original', 'Procesar último texto aportado', 'Proyecto de archivo (vacío borra)', 'Consolidar en captura (vacío revierte)', 'Quitar selección', 'Vista previa de metadatos', 'Revisar lote exacto', 'Confirmar metadatos revisados', 'Inspeccionar operación retenida', 'Descargar original', 'Guardar original verificado', 'Sin coincidencias en esta exploración limitada', 'Original', 'Adquirido', 'Anotación', 'Procesamiento', 'Historial de extracción', 'Historial de metadatos', 'Resultado desconocido; identidad retenida', 'Sin conexión: reconecta para continuar', 'Antes → después', 'Solicitud de captura no disponible', 'ID de captura'],
  fr: ['Examiner les captures', 'ID du projet', 'Rechercher les captures', 'Rechercher', 'Limite de résultats', 'Limite de parcours', 'Inspecter', 'Sélectionner pour le lot', 'Traiter le texte original', 'Traiter le dernier texte fourni', 'Projet de classement (vide efface)', 'Consolider vers la capture (vide annule)', 'Retirer la sélection', 'Aperçu des métadonnées', 'Vérifier le lot exact', 'Appliquer les métadonnées vérifiées', 'Inspecter l’opération conservée', 'Télécharger l’original', 'Enregistrer l’original vérifié', 'Aucun résultat dans ce parcours limité', 'Original', 'Acquis', 'Annotation', 'Traitement', 'Historique des extractions', 'Historique des métadonnées', 'Résultat inconnu ; identité conservée', 'Hors ligne : reconnectez-vous', 'Avant → après', 'Requête de capture indisponible', 'ID de capture'],
  ja: ['キャプチャを確認', 'プロジェクトID', 'キャプチャを検索', '検索', '結果の上限', '走査の上限', '詳細', 'バッチに選択', '元のテキストを処理', '最新の提供テキストを処理', '分類先プロジェクト（空欄で解除）', '統合先キャプチャ（空欄で取消）', '選択を解除', 'メタデータ変更をプレビュー', '正確なバッチを確認', '確認済みメタデータを確定', '保持した操作を確認', '原本をダウンロード', '検証済み原本を保存', '限定走査に一致なし', '原本', '取得日時', '注釈', '処理', '抽出履歴', 'メタデータ履歴', '結果不明・識別子を保持', 'オフライン：再接続が必要', '変更前 → 変更後', 'キャプチャ要求を利用できません', 'キャプチャID'],
  zh: ['审核捕获内容', '项目ID', '搜索捕获内容', '搜索', '结果上限', '扫描上限', '检查', '选择加入批次', '处理原始文本', '处理最新提供文本', '归档项目（留空清除）', '合并到捕获内容（留空撤销）', '取消选择', '预览元数据更改', '审核确切批次', '提交已审核元数据', '检查保留的操作', '下载原件', '保存已验证原件', '此有限扫描没有匹配项', '原件', '获取时间', '注释', '处理', '提取历史', '元数据历史', '结果未知；保留标识', '离线：请重新连接', '之前 → 之后', '捕获请求不可用', '捕获ID'],
  'zh-hant': ['審核擷取內容', '專案ID', '搜尋擷取內容', '搜尋', '結果上限', '掃描上限', '檢查', '選取加入批次', '處理原始文字', '處理最新提供文字', '歸檔專案（留空清除）', '合併至擷取內容（留空復原）', '取消選取', '預覽中繼資料變更', '審核確切批次', '提交已審核中繼資料', '檢查保留的操作', '下載原件', '儲存已驗證原件', '此有限掃描沒有符合項目', '原件', '取得時間', '註解', '處理', '擷取歷史', '中繼資料歷史', '結果未知；保留識別碼', '離線：請重新連線', '之前 → 之後', '擷取要求無法使用', '擷取ID'],
  ar: ['مراجعة الالتقاطات', 'معرف المشروع', 'البحث في الالتقاطات', 'بحث', 'حد النتائج', 'حد الفحص', 'فحص', 'اختيار للدفعة', 'معالجة النص الأصلي', 'معالجة أحدث نص مقدم', 'مشروع الحفظ (الفراغ يمسح)', 'دمج ضمن التقاط (الفراغ يتراجع)', 'إزالة الاختيار', 'معاينة تغييرات البيانات', 'مراجعة الدفعة المحددة', 'تطبيق البيانات المراجعة', 'فحص العملية المحتفظ بها', 'تنزيل الأصل', 'حفظ الأصل المتحقق منه', 'لا نتائج في هذا الفحص المحدود', 'الأصل', 'تاريخ الالتقاط', 'ملاحظة', 'المعالجة', 'سجل الاستخراج', 'سجل البيانات', 'نتيجة مجهولة؛ المعرف محفوظ', 'غير متصل: أعد الاتصال', 'قبل ← بعد', 'طلب الالتقاط غير متاح', 'معرف الالتقاط'],
  ru: ['Проверить записи', 'ID проекта', 'Поиск записей', 'Найти', 'Лимит результатов', 'Лимит просмотра', 'Проверить', 'Выбрать для пакета', 'Обработать исходный текст', 'Обработать последний предоставленный текст', 'Проект хранения (пусто снимает)', 'Объединить с записью (пусто отменяет)', 'Снять выбор', 'Предпросмотр метаданных', 'Проверить точный пакет', 'Применить проверенные метаданные', 'Проверить сохранённую операцию', 'Скачать оригинал', 'Сохранить проверенный оригинал', 'Совпадений в ограниченном просмотре нет', 'Оригинал', 'Получено', 'Аннотация', 'Обработка', 'История извлечения', 'История метаданных', 'Результат неизвестен; идентификатор сохранён', 'Нет связи: подключитесь снова', 'До → после', 'Запрос записи недоступен', 'ID записи']
} as const

interface Props { request: RuntimeRequest; sessionId: string; connected: boolean }
type Copy = typeof words[keyof typeof words]
const reference = (value: ArtifactVersionRef | null): string => value ? `${value.artifact_id}@${value.version}` : '—'
const date = (value: number): string => new Date(value * 1000).toLocaleString()

export function CaptureReviewPanel({ request, sessionId, connected }: Props) {
  const rt = useRuntimeUiText()
  const { locale } = useI18n(), c = words[locale as keyof typeof words] ?? words.en
  const session = useMemo(() => captureReviewSession(request, sessionId), [request, sessionId])
  const state = useSyncExternalStore(session.subscribe, session.getState)
  const [confirming, setConfirming] = useState<CaptureReview | null>(null)
  useLayoutEffect(() => session.attach(connected), [session, connected])
  const disabled = !connected || !sessionId || state.busy, locked = disabled || !!state.pending
  const pendingId = state.pending?.batch?.params.batch_id ?? state.pending?.process?.capture_id

  return <section aria-label={c[0]} className="grid gap-4">
    <h4 className="text-sm font-medium">{c[0]}</h4>
    <p className="text-xs text-muted-foreground">{rt("Local UTF-8 or supplied-text processing only. Search uses lexical overlap and limited spelling similarity; no semantic understanding, OCR or image analysis. Results cover a bounded scan, never the entire inbox.")}</p>
    {!connected && <p role="status">{c[27]}</p>}
    <form className="grid gap-3" onSubmit={event => { event.preventDefault(); void session.search() }}>
      <label>{c[1]}<Input disabled={disabled} onChange={event => session.edit({ project: event.target.value })} value={state.project} /></label>
      <SearchField aria-label={c[2]} loading={state.busy} onChange={value => session.edit({ query: [...value].slice(0, 512).join('') })} placeholder={c[2]} value={state.query} />
      <div className="grid gap-2 sm:grid-cols-2">
        <label>{c[4]}<Input disabled={disabled} max={50} min={1} onChange={event => session.edit({ limit: Number(event.target.value) })} type="number" value={state.limit} /></label>
        <label>{c[5]}<Input disabled={disabled} max={500} min={1} onChange={event => session.edit({ scanLimit: Number(event.target.value) })} type="number" value={state.scanLimit} /></label>
      </div>
      <Button disabled={disabled || !state.project || !state.query} size="xs" type="submit" variant="secondary">{c[3]}</Button>
    </form>
    <form className="flex flex-wrap items-end gap-2" onSubmit={event => { event.preventDefault(); void session.inspect(state.captureId) }}>
      <label className="min-w-0 flex-1">{c[30]}<Input disabled={disabled} onChange={event => session.edit({ captureId: event.target.value })} value={state.captureId} /></label>
      <Button disabled={disabled || !state.project || !state.captureId} size="xs" type="submit" variant="secondary">{c[6]}</Button>
    </form>
    {state.result && <div className="grid gap-3">
      <p className="text-xs">{state.result.scanned}{" " + rt("scanned ·") + " "}{state.result.matches.length}{" " + rt("matches · complete: false · truncated:") + " "}{String(state.result.truncated)}</p>
      {!state.result.matches.length && <EmptyState title={c[19]} />}
      {state.result.matches.map(match => <div className="grid gap-1" key={match.capture.capture_id}>
        <Button disabled={disabled} onClick={() => void session.inspect(match.capture.capture_id)} size="xs" variant="textStrong">{c[6]}: {match.capture.capture_id}</Button>
        <p className="break-words text-xs">{reference(match.capture.original_ref)} · {date(match.capture.acquired_at)}{" " + rt("· revision") + " "}{match.capture.revision} · {match.processing.status}</p>
        <p className="break-words text-xs">{match.matched_terms.slice(0, 32).join(', ')} · {match.score}</p>
        <pre className="max-h-32 overflow-auto whitespace-pre-wrap break-words text-xs">{[...match.excerpt].slice(0, 320).join('')}</pre>
      </div>)}
      {state.result.limitations.map((line, index) => <p className="text-xs text-muted-foreground" key={index}>{line.slice(0, 1024)}</p>)}
    </div>}
    {state.inspected && <CaptureDetails c={c} disabled={disabled} locked={locked} session={session} value={state.inspected} />}
    {state.download && connected && <OriginalDownload c={c} value={state.download} />}
    {!!state.drafts.length && <section className="grid gap-3">
      <p className="text-xs">{state.drafts.length}{rt("/25 selected. Filing is a metadata label; it does not move bytes or grant access through the destination project. Consolidation links exact-byte duplicates and preserves every original, date, annotation and extraction. Clear either target to review an undo.")}</p>
      {state.drafts.map(({ before, item }) => <fieldset className="grid min-w-0 gap-2" disabled={locked} key={item.capture_id}>
        <legend className="text-sm">{item.capture_id}{" " + rt("· revision") + " "}{item.expected_revision}</legend>
        <p className="text-xs">{reference(before.capture.original_ref)} · {date(before.capture.acquired_at)}</p>
        <label>{c[10]}<Input aria-label={`${c[10]}: ${item.capture_id}`} onChange={event => session.change(item.capture_id, { filed_project_id: event.target.value || null })} value={item.filed_project_id ?? ''} /></label>
        <label>{c[11]}<Input aria-label={`${c[11]}: ${item.capture_id}`} onChange={event => session.change(item.capture_id, { consolidated_into: event.target.value || null })} value={item.consolidated_into ?? ''} /></label>
        <Button onClick={() => session.change(item.capture_id, null)} size="xs" variant="text">{c[12]}: {item.capture_id}</Button>
      </fieldset>)}
      <Button disabled={locked} onClick={() => void session.preview()} size="xs" variant="secondary">{c[13]}</Button>
    </section>}
    {state.review && <section className="grid gap-2">
      <BatchPreview c={c} review={state.review} />
      <Button disabled={locked} onClick={() => setConfirming(state.review)} size="xs" variant="secondary">{c[14]}</Button>
    </section>}
    {state.pending && <div className="grid gap-2">
      <p className="break-all text-xs" role="status">{c[26]}: {pendingId} · {state.pending.project}{state.pending.batch ? ` · ${state.pending.batch.preview.preview_digest}` : ` · expected extraction ${state.pending.process?.expected_extraction_sequence} · ${state.pending.process?.source}`}</p>
      {state.pending.batch && <BatchPreview c={c} review={state.pending.batch} />}
      <Button disabled={disabled} onClick={() => void session.reconcile()} size="xs" variant="secondary">{c[16]}</Button>
    </div>}
    <ConfirmDialog confirmLabel={c[15]} description={rt("Apply only the exact metadata changes below. The backend checks current revisions and live project grants. Original bytes, dates, annotations and extraction history remain retained. This does not move or delete files.")} onClose={() => setConfirming(null)} onConfirm={() => confirming ? session.commit(confirming) : undefined} open={connected && !!confirming && confirming === state.review} title={c[14]}>
      {confirming && <BatchPreview c={c} review={confirming} />}
    </ConfirmDialog>
    {state.message && <p aria-live="polite" className="break-words text-xs">{state.message}</p>}
    {state.error && <div role="alert"><ErrorState description={state.error} title={c[29]} /></div>}
  </section>
}

function CaptureDetails({ c, value, session, disabled, locked }: { c: Copy; value: CaptureInspectResult; session: CaptureReviewSession; disabled: boolean; locked: boolean }) {
  const rt = useRuntimeUiText()
  const capture = value.capture, processing = value.processing

  return <section aria-label={`${c[6]}: ${capture.capture_id}`} className="grid gap-2">
    <h5 className="text-sm font-medium">{capture.capture_id}{" " + rt("· revision") + " "}{capture.revision}</h5>
    <p className="break-words text-xs">{c[20]}: {reference(capture.original_ref)} · {c[21]}: {date(capture.acquired_at)}</p>
    {capture.source_url && <p className="break-words text-xs">{capture.source_url.slice(0, 4096)}</p>}
    <pre aria-label={c[22]} className="max-h-48 overflow-auto whitespace-pre-wrap break-words text-xs">{capture.annotation.slice(0, 8192)}</pre>
    <p className="text-xs">{c[23]}: {processing.status} · {processing.method}{" " + rt("· extraction") + " "}{captureSequence(value)}{" " + rt("· indexed") + " "}{processing.indexed_characters}{" " + rt("characters · truncated:") + " "}{String(processing.truncated)}</p>
    <p className="break-words text-xs">{reference(processing.source_ref)} · {processing.failure_code ?? '—'} · {processing.indexed_at === null ? '—' : date(processing.indexed_at)}</p>
    <details><summary>{c[24]}</summary>{capture.extractions.map(extraction => <p className="break-words text-xs" key={extraction.sequence}>{extraction.sequence}: {extraction.status} · {reference(extraction.extracted_ref)} · {extraction.failure_code ?? '—'} · {date(extraction.created_at)}</p>)}</details>
    <details><summary>{c[25]}</summary><p className="text-xs">{rt("Filed:") + " "}{capture.filed_project_id ?? '—'}{" " + rt("· consolidated:") + " "}{value.consolidated_into ?? '—'}{" " + rt("· suggested:") + " "}{capture.suggested_project_id ?? '—'}</p>{value.filing_history.map(item => <p className="text-xs" key={`f${item.revision}`}>{rt("Filing revision") + " "}{item.revision}: {item.filed_project_id ?? '—'} · {date(item.created_at)}</p>)}{value.consolidation_history.map(item => <p className="text-xs" key={`c${item.revision}`}>{rt("Consolidation revision") + " "}{item.revision}: {item.consolidated_into ?? '—'} · {date(item.created_at)}</p>)}</details>
    <div className="flex flex-wrap gap-2">
      <Button disabled={locked || captureSequence(value) >= 32} onClick={() => void session.process('original')} size="xs" variant="secondary">{c[8]}</Button>
      <Button disabled={locked || captureSequence(value) >= 32 || capture.extractions.at(-1)?.status !== 'succeeded'} onClick={() => void session.process('latest_extraction')} size="xs" variant="secondary">{c[9]}</Button>
      <Button disabled={locked || capture.revision >= 128} onClick={() => session.select()} size="xs" variant="secondary">{c[7]}</Button>
      <Button disabled={disabled} onClick={() => void session.downloadOriginal()} size="xs" variant="ghost">{c[17]}</Button>
    </div>
  </section>
}

function BatchPreview({ c, review }: { c: Copy; review: CaptureReview }) {
  const rt = useRuntimeUiText()

  return <div aria-label={c[28]} className="grid gap-2 text-xs">
    <p className="break-all">{review.params.batch_id} · SHA-256 {review.preview.preview_digest}</p>
    {review.preview.items.map(item => <div className="grid gap-1" key={item.capture_id}>
      <p>{item.capture_id}{" " + rt("· revision") + " "}{item.expected_revision} → {item.expected_revision + 1}</p>
      <p>{rt("Filing:") + " "}{item.previous_filed_project_id ?? '∅'} → {item.filed_project_id ?? '∅'}{" " + rt("· Consolidation:") + " "}{item.previous_consolidated_into ?? '∅'} → {item.consolidated_into ?? '∅'}</p>
      <p className="break-all">{rt("Original SHA-256:") + " "}{item.original_sha256}</p>
    </div>)}
  </div>
}

function OriginalDownload({ value, c }: { value: DownloadedArtifact; c: Copy }) {
  const rt = useRuntimeUiText()
  const [url, setUrl] = useState('')
  useEffect(() => {
    const next = URL.createObjectURL(new Blob([new Uint8Array(value.bytes).buffer], { type: 'application/octet-stream' }))
    setUrl(next)

    return () => URL.revokeObjectURL(next)
  }, [value])

  return url ? <a className="text-xs underline" download={`capture-original-${value.metadata.version}`} href={url}>{c[18]} ({value.bytes.length}{" " + rt("bytes)")}</a> : null
}
