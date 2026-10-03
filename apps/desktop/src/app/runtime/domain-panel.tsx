import { type ReactNode, useEffect, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { controlVariants } from '@/components/ui/control'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { useI18n } from '@/i18n'

import type { ArtifactControlStatus, ArtifactProposalResult, DomainPublishResult } from '../../../../shared/src/gateway-contract.generated'
import type { RuntimeRequest } from '../../../../shared/src/runtime-control'
import {
  type DataOptions, type DataRecipe, prepareDomain, publishDomain, readDomainManifest,
  type SelectedDomainJob, summarizeDomainManifest, summarizeDomainPrepared, summarizeDomainPublished, validateDomainJob
} from '../../../../shared/src/runtime-domains'
import { controlFailure, controlId, controlInteger, RuntimeInputError, summarizeArtifactCommand } from '../../../../shared/src/runtime-research'

// Feature-local chrome covers every bundled locale; protocol values and validated
// helper reports deliberately retain the runtime contract's terminology.
const words = {
  en: ['Data and creative packages', 'Project ID', 'Package', 'Data', 'Creative', 'Retained artifact ID', 'Exact version', 'SHA-256', 'Source ID', 'Encoding', 'Delimiter', 'Date format', 'Currency (ISO code, blank for none)', 'Null markers (one per line)', 'Empty cells are null', 'Duplicate keys', 'Key columns (one per line)', 'Unit column', 'Unit', 'Worksheet (optional)', 'Aggregation', 'Group columns (one per line)', 'Value column', 'Result column', 'Aggregate nulls', 'Chart', 'Category column', 'Export format', 'Creative brief', 'Prompts (one per line)', 'Continuity notes (one per line)', 'Supplied reference', 'Reference name', 'Declared rights', 'Prepare for review', 'Review exact outputs', 'Reviewed', 'Approve publication', 'Publish exact package', 'Discard proposal', 'Inspect command', 'Inspect verified manifest', 'Result row (zero-based)', 'Show lineage', 'Manifest (last)', 'Command', 'Expires', 'No selection', 'Disconnected; reconnect to inspect the original command', 'Inputs changed or connection was interrupted. Approval is invalid; inspect or discard the original command before preparing again.', 'Closing this panel does not cancel its 300-second control lease. Discard explicitly and wait for a terminal receipt.', 'Another session or connection has a retained command. Return there to inspect or discard it.', 'Only retained CSV/XLSX references are supported. Formula caches are preserved, not recalculated.', 'Prompt-only package: zero image, audio or video generation. Supplied rights are declarations, not verified ownership.', 'Publish the exact reviewed outputs in order, with the manifest last. Publication is not atomic. No external production, sharing or delivery is performed.', 'None', 'Publication is uncertain. Inspect this command; do not retry automatically.', 'Proposal expired; discard it and prepare again', 'Prepared inputs', 'Add unit', 'Remove'],
  zh: ['数据与创意包', '项目 ID', '包类型', '数据', '创意', '已保留制品 ID', '精确版本', 'SHA-256', '来源 ID', '编码', '分隔符', '日期格式', '货币（ISO 代码，留空表示无）', '空值标记（每行一个）', '空单元格视为空值', '重复键', '键列（每行一个）', '单位列', '单位', '工作表（可选）', '聚合', '分组列（每行一个）', '值列', '结果列', '聚合空值策略', '图表', '类别列', '导出格式', '创意简述', '提示词（每行一个）', '连贯性说明（每行一个）', '已提供参考', '参考名称', '声明的权利', '准备审核', '审核精确输出', '已审核', '批准发布', '发布精确包', '丢弃提案', '检查命令', '检查已验证清单', '结果行（从零开始）', '显示来源链', '清单（最后）', '命令', '到期时间', '未选择', '已断开；重新连接以检查原命令', '输入已更改或连接中断。批准已失效；再次准备前请检查或丢弃原命令。', '关闭面板不会取消 300 秒控制租约。请明确丢弃并等待终态回执。', '其他会话或连接保留了命令。请返回原位置检查或丢弃。', '仅支持已保留的 CSV/XLSX 引用。保留公式缓存，不重新计算。', '仅提示词包：不生成图像、音频或视频。参考权利仅为声明，未验证所有权。', '按顺序发布已审核的精确输出，清单最后发布。发布非原子操作；不进行外部制作、共享或交付。', '无', '发布结果不确定。请检查此命令，不要自动重试。', '提案已过期；请丢弃并重新准备', '已准备输入', '添加单位', '移除'],
  'zh-hant': ['資料與創意套件', '專案 ID', '套件類型', '資料', '創意', '已保留成品 ID', '精確版本', 'SHA-256', '來源 ID', '編碼', '分隔符號', '日期格式', '貨幣（ISO 代碼，留空表示無）', '空值標記（每行一個）', '空儲存格視為空值', '重複鍵', '鍵欄（每行一個）', '單位欄', '單位', '工作表（選填）', '彙總', '分組欄（每行一個）', '數值欄', '結果欄', '彙總空值策略', '圖表', '類別欄', '匯出格式', '創意簡述', '提示詞（每行一個）', '連貫性說明（每行一個）', '已提供參考', '參考名稱', '聲明的權利', '準備審核', '審核精確輸出', '已審核', '批准發佈', '發佈精確套件', '捨棄提案', '檢查指令', '檢查已驗證清單', '結果列（從零開始）', '顯示來源鏈', '清單（最後）', '指令', '到期時間', '未選擇', '已中斷連線；重新連線以檢查原指令', '輸入已更改或連線中斷。批准已失效；再次準備前請檢查或捨棄原指令。', '關閉面板不會取消 300 秒控制租約。請明確捨棄並等待終態回執。', '其他工作階段或連線保留了指令。請返回原處檢查或捨棄。', '僅支援已保留的 CSV/XLSX 參考。保留公式快取，不重新計算。', '僅提示詞套件：不產生影像、音訊或影片。參考權利僅為聲明，未驗證所有權。', '按順序發佈已審核的精確輸出，清單最後發佈。發佈非不可分割操作；不進行外部製作、分享或交付。', '無', '發佈結果不確定。請檢查此指令，不要自動重試。', '提案已過期；請捨棄並重新準備', '已準備輸入', '新增單位', '移除'],
  ja: ['データとクリエイティブのパッケージ', 'プロジェクト ID', 'パッケージ', 'データ', 'クリエイティブ', '保存済み成果物 ID', '正確なバージョン', 'SHA-256', 'ソース ID', '文字コード', '区切り文字', '日付形式', '通貨（ISO コード、なしは空欄）', '欠損値マーカー（1 行に 1 つ）', '空セルを欠損値にする', '重複キー', 'キー列（1 行に 1 つ）', '単位の列', '単位', 'ワークシート（任意）', '集計', 'グループ列（1 行に 1 つ）', '値の列', '結果の列', '集計の欠損値処理', 'グラフ', 'カテゴリ列', '出力形式', '制作概要', 'プロンプト（1 行に 1 つ）', '一貫性のメモ（1 行に 1 つ）', '提供済み参照', '参照名', '申告された権利', 'レビューを準備', '正確な出力をレビュー', '確認済み', '公開を承認', '正確なパッケージを公開', '提案を破棄', 'コマンドを確認', '検証済みマニフェストを確認', '結果行（0 から開始）', '来歴を表示', 'マニフェスト（最後）', 'コマンド', '有効期限', '選択なし', '切断中。再接続して元のコマンドを確認してください', '入力の変更または接続の中断により承認は無効です。再準備の前に元のコマンドを確認または破棄してください。', 'パネルを閉じても 300 秒の制御リースは取り消されません。明示的に破棄し、終了応答を待ってください。', '別のセッションまたは接続にコマンドが残っています。元に戻って確認または破棄してください。', '保存済み CSV/XLSX 参照のみ対応します。数式キャッシュは保持され、再計算されません。', 'プロンプトのみのパッケージです。画像・音声・動画は生成しません。権利は申告のみで所有権は未検証です。', '確認済みの正確な出力を順番に公開し、マニフェストを最後にします。公開は非アトミックです。外部制作・共有・納品は行いません。', 'なし', '公開結果は不明です。このコマンドを確認し、自動再試行しないでください。', '提案の期限切れです。破棄して再準備してください', '準備済み入力', '単位を追加', '削除'],
  ar: ['حزم البيانات والإبداع', 'معرّف المشروع', 'الحزمة', 'البيانات', 'الإبداع', 'معرّف الملف المحفوظ', 'الإصدار الدقيق', 'SHA-256', 'معرّف المصدر', 'الترميز', 'الفاصل', 'تنسيق التاريخ', 'العملة (رمز ISO، فارغ لعدم التحديد)', 'علامات القيم الخالية (واحدة بكل سطر)', 'الخلايا الفارغة قيم خالية', 'المفاتيح المكررة', 'أعمدة المفاتيح (واحد بكل سطر)', 'عمود الوحدة', 'الوحدة', 'ورقة العمل (اختياري)', 'التجميع', 'أعمدة التجميع (واحد بكل سطر)', 'عمود القيمة', 'عمود النتيجة', 'القيم الخالية في التجميع', 'الرسم البياني', 'عمود الفئة', 'صيغة التصدير', 'موجز إبداعي', 'التوجيهات (واحد بكل سطر)', 'ملاحظات الاستمرارية (واحدة بكل سطر)', 'مرجع مقدّم', 'اسم المرجع', 'الحقوق المعلنة', 'التحضير للمراجعة', 'مراجعة المخرجات الدقيقة', 'تمت المراجعة', 'الموافقة على النشر', 'نشر الحزمة الدقيقة', 'تجاهل المقترح', 'فحص الأمر', 'فحص البيان المتحقق منه', 'صف النتيجة (يبدأ من الصفر)', 'عرض سلسلة المصدر', 'البيان (أخيرًا)', 'الأمر', 'تنتهي الصلاحية', 'لا يوجد اختيار', 'الاتصال مقطوع؛ أعد الاتصال لفحص الأمر الأصلي', 'تغيرت المدخلات أو انقطع الاتصال. الموافقة غير صالحة؛ افحص الأمر الأصلي أو ألغِه قبل التحضير مجددًا.', 'إغلاق اللوحة لا يلغي مهلة التحكم البالغة 300 ثانية. ألغِ المقترح صراحة وانتظر إيصال حالة نهائية.', 'توجد جلسة أو وصلة أخرى تحتفظ بأمر. ارجع إليها لفحصه أو إلغائه.', 'تُدعم مراجع CSV/XLSX المحفوظة فقط. تُحفظ ذاكرة الصيغ دون إعادة حساب.', 'حزمة توجيهات فقط: لا توليد صور أو صوت أو فيديو. الحقوق تصريحات لا إثبات ملكية.', 'انشر المخرجات الدقيقة المراجعة بالترتيب، والبيان أخيرًا. النشر غير ذري. لا يتم إنتاج خارجي أو مشاركة أو تسليم.', 'لا شيء', 'نتيجة النشر غير مؤكدة. افحص هذا الأمر ولا تعاود المحاولة تلقائيًا.', 'انتهت صلاحية المقترح؛ ألغِه وحضّره مجددًا', 'المدخلات المعدّة', 'إضافة وحدة', 'إزالة'],
  ru: ['Пакеты данных и творчества', 'ID проекта', 'Пакет', 'Данные', 'Творчество', 'ID сохранённого артефакта', 'Точная версия', 'SHA-256', 'ID источника', 'Кодировка', 'Разделитель', 'Формат даты', 'Валюта (код ISO, пусто — нет)', 'Маркеры пропусков (по одному на строку)', 'Пустые ячейки — пропуски', 'Повторяющиеся ключи', 'Столбцы ключей (по одному на строку)', 'Столбец единицы', 'Единица', 'Лист (необязательно)', 'Агрегация', 'Столбцы группировки (по одному на строку)', 'Столбец значений', 'Столбец результата', 'Пропуски при агрегации', 'Диаграмма', 'Столбец категорий', 'Формат экспорта', 'Творческий бриф', 'Промпты (по одному на строку)', 'Заметки о согласованности (по одной на строку)', 'Предоставленный референс', 'Имя референса', 'Заявленные права', 'Подготовить к проверке', 'Проверить точные результаты', 'Проверено', 'Одобрить публикацию', 'Опубликовать точный пакет', 'Отменить предложение', 'Проверить команду', 'Проверить подтверждённый манифест', 'Строка результата (с нуля)', 'Показать происхождение', 'Манифест (последний)', 'Команда', 'Срок действия', 'Не выбрано', 'Нет связи; подключитесь для проверки исходной команды', 'Входные данные изменены или связь прервана. Одобрение недействительно; проверьте или отмените исходную команду до новой подготовки.', 'Закрытие панели не отменяет 300-секундную аренду управления. Отмените явно и дождитесь конечного статуса.', 'В другом сеансе или подключении осталась команда. Вернитесь туда, чтобы проверить или отменить её.', 'Поддерживаются только сохранённые CSV/XLSX. Кэш формул сохраняется без пересчёта.', 'Пакет только с промптами: без генерации изображений, аудио или видео. Права заявлены, владение не проверено.', 'Опубликуйте проверенные точные результаты по порядку, манифест последним. Публикация неатомарна. Внешнее производство, передача и доставка не выполняются.', 'Нет', 'Результат публикации неизвестен. Проверьте команду; автоматический повтор запрещён.', 'Срок предложения истёк; отмените и подготовьте заново', 'Подготовленные входные данные', 'Добавить единицу', 'Удалить'],
  fr: ['Paquets de données et de création', 'ID du projet', 'Paquet', 'Données', 'Création', 'ID du fichier conservé', 'Version exacte', 'SHA-256', 'ID de la source', 'Encodage', 'Délimiteur', 'Format de date', 'Devise (code ISO, vide si aucune)', 'Marqueurs nuls (un par ligne)', 'Les cellules vides sont nulles', 'Clés en double', 'Colonnes clés (une par ligne)', 'Colonne de l’unité', 'Unité', 'Feuille (facultative)', 'Agrégation', 'Colonnes de groupe (une par ligne)', 'Colonne de valeur', 'Colonne de résultat', 'Valeurs nulles agrégées', 'Graphique', 'Colonne de catégorie', 'Format d’export', 'Brief créatif', 'Prompts (un par ligne)', 'Notes de continuité (une par ligne)', 'Référence fournie', 'Nom de référence', 'Droits déclarés', 'Préparer la révision', 'Vérifier les sorties exactes', 'Vérifié', 'Approuver la publication', 'Publier le paquet exact', 'Abandonner la proposition', 'Inspecter la commande', 'Inspecter le manifeste vérifié', 'Ligne du résultat (depuis zéro)', 'Afficher la provenance', 'Manifeste (en dernier)', 'Commande', 'Expiration', 'Aucune sélection', 'Déconnecté ; reconnectez-vous pour inspecter la commande initiale', 'Les entrées ont changé ou la connexion a été interrompue. L’approbation est invalide ; inspectez ou abandonnez la commande initiale avant de préparer à nouveau.', 'Fermer ce panneau n’annule pas son bail de contrôle de 300 secondes. Abandonnez explicitement et attendez un reçu terminal.', 'Une autre session ou connexion conserve une commande. Revenez-y pour l’inspecter ou l’abandonner.', 'Seules les références CSV/XLSX conservées sont prises en charge. Les caches des formules sont préservés, sans recalcul.', 'Paquet de prompts uniquement : aucune génération d’image, d’audio ou de vidéo. Les droits sont déclarés, sans vérification de propriété.', 'Publiez les sorties exactes vérifiées dans l’ordre, le manifeste en dernier. La publication n’est pas atomique. Aucune production externe, aucun partage ni livraison.', 'Aucun', 'Publication incertaine. Inspectez cette commande ; aucune relance automatique.', 'Proposition expirée ; abandonnez-la et préparez à nouveau', 'Entrées préparées', 'Ajouter une unité', 'Supprimer'],
  de: ['Daten- und Kreativpakete', 'Projekt-ID', 'Paket', 'Daten', 'Kreativ', 'ID des gespeicherten Artefakts', 'Exakte Version', 'SHA-256', 'Quell-ID', 'Kodierung', 'Trennzeichen', 'Datumsformat', 'Währung (ISO-Code, leer für keine)', 'Nullwert-Marker (einer pro Zeile)', 'Leere Zellen sind Nullwerte', 'Doppelte Schlüssel', 'Schlüsselspalten (eine pro Zeile)', 'Einheitsspalte', 'Einheit', 'Arbeitsblatt (optional)', 'Aggregation', 'Gruppenspalten (eine pro Zeile)', 'Wertespalte', 'Ergebnisspalte', 'Nullwerte bei Aggregation', 'Diagramm', 'Kategoriespalte', 'Exportformat', 'Kreativbriefing', 'Prompts (einer pro Zeile)', 'Kontinuitätsnotizen (eine pro Zeile)', 'Bereitgestellte Referenz', 'Referenzname', 'Angegebene Rechte', 'Zur Prüfung vorbereiten', 'Exakte Ausgaben prüfen', 'Geprüft', 'Veröffentlichung genehmigen', 'Exaktes Paket veröffentlichen', 'Vorschlag verwerfen', 'Befehl prüfen', 'Verifiziertes Manifest prüfen', 'Ergebniszeile (ab null)', 'Herkunft anzeigen', 'Manifest (zuletzt)', 'Befehl', 'Ablauf', 'Keine Auswahl', 'Getrennt; zur Prüfung des ursprünglichen Befehls erneut verbinden', 'Eingaben geändert oder Verbindung unterbrochen. Die Freigabe ist ungültig; vor erneuter Vorbereitung den ursprünglichen Befehl prüfen oder verwerfen.', 'Das Schließen beendet die 300-Sekunden-Steuerungslease nicht. Explizit verwerfen und auf eine abschließende Bestätigung warten.', 'Eine andere Sitzung oder Verbindung hält einen Befehl. Dort prüfen oder verwerfen.', 'Nur gespeicherte CSV/XLSX-Referenzen werden unterstützt. Formelcaches bleiben erhalten, ohne Neuberechnung.', 'Nur Prompts: keine Bild-, Audio- oder Videogenerierung. Rechte sind Angaben, kein Eigentumsnachweis.', 'Die exakt geprüften Ausgaben in Reihenfolge veröffentlichen, das Manifest zuletzt. Die Veröffentlichung ist nicht atomar. Keine externe Produktion, Freigabe oder Lieferung.', 'Keine', 'Veröffentlichung ungewiss. Diesen Befehl prüfen; nicht automatisch wiederholen.', 'Vorschlag abgelaufen; verwerfen und neu vorbereiten', 'Vorbereitete Eingaben', 'Einheit hinzufügen', 'Entfernen'],
  es: ['Paquetes de datos y creatividad', 'ID del proyecto', 'Paquete', 'Datos', 'Creatividad', 'ID del archivo conservado', 'Versión exacta', 'SHA-256', 'ID de origen', 'Codificación', 'Delimitador', 'Formato de fecha', 'Moneda (código ISO, vacío si ninguna)', 'Marcadores nulos (uno por línea)', 'Las celdas vacías son nulas', 'Claves duplicadas', 'Columnas clave (una por línea)', 'Columna de unidad', 'Unidad', 'Hoja (opcional)', 'Agregación', 'Columnas de grupo (una por línea)', 'Columna de valor', 'Columna de resultado', 'Nulos en agregación', 'Gráfico', 'Columna de categoría', 'Formato de exportación', 'Brief creativo', 'Prompts (uno por línea)', 'Notas de continuidad (una por línea)', 'Referencia aportada', 'Nombre de referencia', 'Derechos declarados', 'Preparar para revisión', 'Revisar resultados exactos', 'Revisado', 'Aprobar publicación', 'Publicar paquete exacto', 'Descartar propuesta', 'Inspeccionar comando', 'Inspeccionar manifiesto verificado', 'Fila del resultado (desde cero)', 'Mostrar procedencia', 'Manifiesto (último)', 'Comando', 'Caduca', 'Sin selección', 'Desconectado; vuelve a conectar para inspeccionar el comando original', 'Las entradas cambiaron o se interrumpió la conexión. La aprobación no es válida; inspecciona o descarta el comando original antes de volver a preparar.', 'Cerrar el panel no cancela su concesión de control de 300 segundos. Descártala explícitamente y espera una confirmación terminal.', 'Otra sesión o conexión conserva un comando. Vuelve allí para inspeccionarlo o descartarlo.', 'Solo se admiten referencias CSV/XLSX conservadas. Se mantienen las cachés de fórmulas sin recalcular.', 'Paquete solo de prompts: no genera imágenes, audio ni vídeo. Los derechos son declaraciones, no propiedad verificada.', 'Publica los resultados exactos revisados en orden, con el manifiesto al final. La publicación no es atómica. Sin producción externa, uso compartido ni entrega.', 'Ninguno', 'Publicación incierta. Inspecciona este comando; no se reintentará automáticamente.', 'Propuesta caducada; descártala y prepara de nuevo', 'Entradas preparadas', 'Añadir unidad', 'Eliminar']
} satisfies Record<string, readonly string[]>

const keys = ['title', 'project', 'kind', 'data', 'creative', 'artifact', 'version', 'digest', 'source', 'encoding', 'delimiter', 'date', 'currency', 'nulls', 'emptyNull', 'duplicates', 'keys', 'unitColumn', 'unit', 'sheet', 'aggregate', 'group', 'value', 'result', 'aggregateNulls', 'chart', 'category', 'export', 'brief', 'prompts', 'continuity', 'reference', 'referenceName', 'rights', 'prepare', 'review', 'reviewed', 'approve', 'publish', 'discard', 'inspect', 'manifest', 'row', 'lineage', 'manifestLast', 'command', 'expires', 'unselected', 'disconnected', 'invalidated', 'lease', 'prior', 'dataNotice', 'creativeNotice', 'publicationNotice', 'none', 'uncertain', 'expired', 'inputs', 'addUnit', 'remove'] as const
type Copy = Record<typeof keys[number], string>

function copyFor(locale: string): Copy {
  const entries = words[locale as keyof typeof words] ?? words.en

  return Object.fromEntries(keys.map((key, index) => [key, entries[index]])) as Copy
}

interface DomainPanelProps { request: RuntimeRequest; sessionId: string; connected: boolean }
interface Lease { command_id: string; job_json: string; job: SelectedDomainJob; cancelRequested?: boolean }
interface Scope { id: number; request: RuntimeRequest; sessionId: string; lease: Lease | null }
interface Review { lease: Lease; proposals: ArtifactProposalResult[]; summary: string }
interface Form {
  project: string; adapter: 'data' | 'creative'; artifact: string; version: string; digest: string; source: string
  encoding: DataOptions['encoding']; delimiter: DataOptions['delimiter']; date: NonNullable<DataOptions['date_format']> | ''
  currency: string; nulls: string; emptyNull: boolean; duplicates: DataOptions['duplicate_keys']; keys: string
  units: { column: string; unit: string }[]; sheet: string; export: 'csv' | 'xlsx' | 'ipynb'
  aggregate: 'none' | 'sum' | 'mean' | 'min' | 'max' | 'count'; group: string; value: string; result: string; aggregateNulls: 'reject' | 'skip'
  chart: 'none' | 'bar' | 'line'; category: string; chartValue: string
  brief: string; prompts: string; continuity: string; reference: boolean; referenceName: string
  rights: 'owned' | 'licensed' | 'permission_recorded' | 'unknown'
}

const initialForm: Form = {
  project: '', adapter: 'data', artifact: '', version: '1', digest: '', source: 'source', encoding: 'utf-8', delimiter: ',', date: '',
  currency: '', nulls: '', emptyNull: true, duplicates: 'reject', keys: '', units: [], sheet: '', export: 'xlsx',
  aggregate: 'none', group: '', value: '', result: '', aggregateNulls: 'reject', chart: 'none', category: '', chartValue: '',
  brief: '', prompts: '', continuity: '', reference: false, referenceName: '', rights: 'unknown'
}

const lines = (value: string) => value.split('\n').filter(line => line.length > 0)
const terminal = new Set<ArtifactControlStatus['status']>(['cancelled', 'completed', 'failed', 'blocked'])

function buildJob(form: Form): SelectedDomainJob {
  const base = { job_id: crypto.randomUUID(), project_id: form.project }
  const ref = { artifact_id: form.artifact, version: Number(form.version), sha256: form.digest }

  if (form.adapter === 'creative') {
    return validateDomainJob({ ...base, adapter: 'creative', arguments: { brief: form.brief, prompts: lines(form.prompts), continuity: lines(form.continuity),
      assets: form.reference ? [{ name: form.referenceName, ref, rights: form.rights, stage: 'supplied' }] : [] } })
  }

  const units = Object.fromEntries(form.units.map(row => [row.column, row.unit]))

  if (Object.keys(units).length !== form.units.length) {throw new RuntimeInputError('Unit columns must be unique')}

  const options: DataOptions = { encoding: form.encoding, delimiter: form.delimiter, date_format: form.date || null, currency: form.currency || null,
    null_values: [...(form.emptyNull ? [''] : []), ...lines(form.nulls)], units, duplicate_keys: form.duplicates, keys: lines(form.keys), ...(form.sheet ? { sheet: form.sheet } : {}) }

  const recipe: DataRecipe = { base: form.source, exports: [form.export],
    ...(form.aggregate !== 'none' ? { aggregate: { group_by: lines(form.group), aggregations: { [form.result]: { operation: form.aggregate, column: form.value } }, nulls: form.aggregateNulls } } : {}),
    ...(form.chart !== 'none' ? { charts: [{ kind: form.chart, category: form.category, value: form.chartValue }] } : {}) }

  return validateDomainJob({ ...base, adapter: 'data', arguments: { inputs: [{ source_id: form.source, ref, options }], recipe } })
}

/** Scope identity includes the exact transport, not just a session's display ID. */
export function DomainPanel(props: DomainPanelProps) {
  const scopes = useRef<Scope[]>([])
  let scope = scopes.current.find(item => item.request === props.request && item.sessionId === props.sessionId)

  if (!scope) {
    scope = { id: scopes.current.length, request: props.request, sessionId: props.sessionId, lease: null }
    scopes.current.push(scope)
  }

  const { locale } = useI18n()
  const c = copyFor(locale)
  const prior = scopes.current.filter(item => item !== scope && item.lease)

  return <section aria-label={c.title} className="grid gap-3">
    {prior.length > 0 && <p className="break-words text-xs" role="status">{c.prior} {prior.map(item => item.lease!.command_id).join(', ')}</p>}
    <ScopedDomainPanel {...props} key={scope.id} scope={scope} />
  </section>
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return <label className="grid gap-1 text-xs">{label}{children}</label>
}

function ScopedDomainPanel({ request, sessionId, connected, scope }: DomainPanelProps & { scope: Scope }) {
  const { locale } = useI18n()
  const c = copyFor(locale)
  const [form, setForm] = useState<Form>(initialForm)
  const [lease, setLease] = useState<Lease | null>(scope.lease)
  const [review, setReview] = useState<Review | null>(null)
  const [reviewed, setReviewed] = useState<number[]>([])
  const [invalid, setInvalid] = useState(!!scope.lease)
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [published, setPublished] = useState<DomainPublishResult | null>(null)
  const [manifest, setManifest] = useState<Record<string, unknown> | null>(null)
  const [manifestText, setManifestText] = useState('')
  const [row, setRow] = useState('0')
  const live = useRef({ mounted: true, connected, token: 0, pending: false })
  live.current.connected = connected
  useEffect(() => {
    const lifetime = live.current
    lifetime.mounted = true

    return () => { lifetime.mounted = false; lifetime.token++; lifetime.pending = false }
  }, [])
  useEffect(() => {
    if (!connected) {
      live.current.token++; live.current.pending = false
      setBusy(false); setConfirming(false); setReviewed([]); setInvalid(true)
      setManifest(null); setManifestText(''); setMessage(''); setError(null)
    }
  }, [connected])

  function retain(next: Lease | null) { scope.lease = next; setLease(next) }

  function edit<K extends keyof Form>(key: K, value: Form[K]) {
    setForm(previous => ({ ...previous, [key]: value }))
    setConfirming(false); setReviewed([])

    if (lease) {setInvalid(true)}
  }

  function valid(token: number) { return live.current.mounted && live.current.connected && live.current.token === token }

  async function action(mutation: boolean, work: (token: number) => Promise<void>, propagate = false) {
    if (!connected || !live.current.mounted || live.current.pending || !sessionId) {return}
    live.current.pending = true
    const token = ++live.current.token
    setBusy(true); setError(null)

    try { await work(token) }
    catch (caught) {
      if (valid(token)) {
        setError(controlFailure(caught, mutation && !!scope.lease))

        if (mutation && scope.lease) { setInvalid(true); setReviewed([]);

 if (!propagate) {setConfirming(false)} }

        if (propagate) {throw new Error(controlFailure(caught, mutation && !!scope.lease))}
      }
    } finally {
      if (valid(token)) { live.current.pending = false; setBusy(false) }
    }
  }

  async function prepare(token: number) {
    if (scope.lease) {return}
    controlId(sessionId)
    const job = buildJob(form)
    const job_json = JSON.stringify(job)

    if (new TextEncoder().encode(job_json).length > 65536) {throw new RuntimeInputError('Domain job exceeds the supported 65536-byte request bound')}
    const next: Lease = { command_id: crypto.randomUUID(), job_json, job }
    // Save before dispatch: a rejected or disconnected mutation may still hold the lease.
    retain(next); setInvalid(true); setPublished(null); setManifest(null); setManifestText(''); setMessage('')
    const result = await prepareDomain({ command_id: next.command_id, job_json: next.job_json }, request, sessionId)

    if (!valid(token)) {return}
    const summary = summarizeDomainPrepared(result, job)

    if (new Set(result.proposals.map(item => item.approval_id)).size !== result.proposals.length) {throw new RuntimeInputError('Proposal approvals are not distinct')}
    setReview({ lease: next, proposals: result.proposals, summary }); setReviewed([]); setInvalid(false)
  }

  async function publish(token: number) {
    if (!review || invalid || !confirming || scope.lease !== review.lease || reviewed.length !== review.proposals.length) {throw new RuntimeInputError(c.invalidated)}

    if (review.proposals.some(item => item.expires_at * 1000 <= Date.now())) {throw new RuntimeInputError(c.expired)}

    const result = await publishDomain({ command_id: review.lease.command_id, job_json: review.lease.job_json,
      approvals: review.proposals.map(({ approval_id, approval_digest }) => ({ approval_id, approval_digest })) }, request, sessionId)

    if (!valid(token)) {return}
    const summary = summarizeDomainPublished(result, review.lease.job)
    const outputs = [...result.outputs, result.manifest]

    if (outputs.length !== review.proposals.length || outputs.some((item, index) => {
      const expected = review.proposals[index]

      return item.artifact_id !== expected.artifact_id || item.version !== expected.version || item.sha256 !== expected.sha256 || item.mime !== expected.mime || item.size !== expected.size || item.parent_version !== expected.parent_version
    })) {throw new RuntimeInputError('Published outputs differ from the exact reviewed proposal')}

    setPublished(result); setMessage(summary); setReviewed([]); setInvalid(true); retain(null)
  }

  async function inspectOrDiscard(discard: boolean, token: number) {
    const original = scope.lease

    if (!original || (discard && original.cancelRequested)) {return}

    if (discard) {original.cancelRequested = true}
    setInvalid(true); setReviewed([]); setConfirming(false)
    const receipt = await request(discard ? 'runtime.artifact.cancel' : 'runtime.artifact.status', { session_id: sessionId, schema_version: 1, command_id: original.command_id })

    if (!valid(token)) {return}

    if (receipt.command_id !== original.command_id) {throw new RuntimeInputError('Command receipt differs from the retained command')}
    const summary = summarizeArtifactCommand(receipt)

    if (!['accepted', 'claimed', 'completed', 'cancelled', 'failed', 'blocked'].includes(receipt.status)) {throw new RuntimeInputError('Unknown command status')}
    setMessage(summary)

    if (receipt.result && 'outputs' in receipt.result) {
      const result = receipt.result
      setMessage(`${summary}\n${summarizeDomainPublished(result, original.job)}`)
      setPublished(result)
    }

    if (terminal.has(receipt.status)) { retain(null); setReview(null); setInvalid(false) }
  }

  async function inspectManifest(token: number) {
    if (!published) {return}
    const result = await readDomainManifest(published.project_id, { artifact_id: published.manifest.artifact_id, version: published.manifest.version, sha256: published.manifest.sha256 }, request, sessionId)

    if (!valid(token)) {return}
    const summary = summarizeDomainManifest(result)
    setManifest(result); setManifestText(summary)
  }

  function showLineage() {
    if (!manifest || !connected) {return}

    try { setManifestText(summarizeDomainManifest(manifest, controlInteger(Number(row)))); setError(null) }
    catch (caught) { setError(controlFailure(caught, false)) }
  }

  const disabled = busy || !connected
  const input = (key: keyof Form, label: string, type = 'text') => <Field label={label}><Input disabled={disabled} onChange={event => edit(key, event.target.value as never)} type={type} value={String(form[key])} /></Field>
  const textarea = (key: keyof Form, label: string) => <Field label={label}><Textarea disabled={disabled} onChange={event => edit(key, event.target.value as never)} value={String(form[key])} /></Field>
  const select = (key: keyof Form, label: string, options: readonly (readonly [string, string])[]) => <Field label={label}><select className={controlVariants()} disabled={disabled} onChange={event => edit(key, event.target.value as never)} value={String(form[key])}>{options.map(([value, name]) => <option key={value} value={value}>{name}</option>)}</select></Field>
  const choices = (values: string[]) => values.map(value => [value, value] as const)
  const refs = <>{input('artifact', c.artifact)}{input('version', c.version, 'number')}{input('digest', c.digest)}</>
  const canApprove = connected && !busy && !invalid && !!review && reviewed.length === review.proposals.length

  return <div className="grid gap-3">
    <h4 className="text-sm font-medium">{c.title}</h4>
    {!connected && <p role="status">{c.disconnected}</p>}
    <div className="grid gap-2 md:grid-cols-2">
      {input('project', c.project)}
      {select('adapter', c.kind, [['data', c.data], ['creative', c.creative]])}
    </div>
    <p className="text-xs text-muted-foreground">{form.adapter === 'data' ? c.dataNotice : c.creativeNotice}</p>
    {form.adapter === 'data' ? <div className="grid gap-2 md:grid-cols-2">
      {refs}{input('source', c.source)}
      {select('encoding', c.encoding, choices(['utf-8', 'utf-8-sig', 'latin-1']))}
      {select('delimiter', c.delimiter, [[',', ','], [';', ';'], ['\t', 'TAB'], ['|', '|']])}
      {select('date', c.date, [['', c.none], ...choices(['YYYY-MM-DD', 'DD/MM/YYYY', 'MM/DD/YYYY', 'excel_serial'])])}
      {input('currency', c.currency)}{textarea('nulls', c.nulls)}
      <Button aria-pressed={form.emptyNull} disabled={disabled} onClick={() => edit('emptyNull', !form.emptyNull)} size="xs" variant="secondary">{c.emptyNull}</Button>
      {select('duplicates', c.duplicates, choices(['allow', 'reject', 'keep_first']))}{textarea('keys', c.keys)}{input('sheet', c.sheet)}
      {form.units.map((unit, index) => <div className="grid gap-2" key={index}>
        <Field label={`${c.unitColumn} ${index + 1}`}><Input disabled={disabled} onChange={event => edit('units', form.units.map((entry, i) => i === index ? { ...entry, column: event.target.value } : entry))} value={unit.column} /></Field>
        <Field label={`${c.unit} ${index + 1}`}><Input disabled={disabled} onChange={event => edit('units', form.units.map((entry, i) => i === index ? { ...entry, unit: event.target.value } : entry))} value={unit.unit} /></Field>
        <Button disabled={disabled} onClick={() => edit('units', form.units.filter((_, i) => i !== index))} size="xs" variant="text">{c.remove}</Button>
      </div>)}
      <Button disabled={disabled || form.units.length >= 256} onClick={() => edit('units', [...form.units, { column: '', unit: '' }])} size="xs" variant="secondary">{c.addUnit}</Button>
      {select('aggregate', c.aggregate, [['none', c.none], ...choices(['sum', 'mean', 'min', 'max', 'count'])])}
      {form.aggregate !== 'none' && <>{textarea('group', c.group)}{input('value', c.value)}{input('result', c.result)}{select('aggregateNulls', c.aggregateNulls, choices(['reject', 'skip']))}</>}
      {select('chart', c.chart, [['none', c.none], ...choices(['bar', 'line'])])}
      {form.chart !== 'none' && <>{input('category', c.category)}{input('chartValue', `${c.chart}: ${c.value}`)}</>}
      {select('export', c.export, choices(['csv', 'xlsx', 'ipynb']))}
    </div> : <div className="grid gap-2">
      {textarea('brief', c.brief)}{textarea('prompts', c.prompts)}{textarea('continuity', c.continuity)}
      <Button aria-pressed={form.reference} disabled={disabled} onClick={() => edit('reference', !form.reference)} size="xs" variant="secondary">{c.reference}</Button>
      {form.reference && <>{input('referenceName', c.referenceName)}{refs}{select('rights', c.rights, choices(['owned', 'licensed', 'permission_recorded', 'unknown']))}</>}
    </div>}
    <Button disabled={disabled || !!lease || !sessionId} onClick={() => void action(true, prepare)} size="xs" variant="secondary">{c.prepare}</Button>
    {lease && <div className="grid gap-2">
      <p className="break-all text-xs">{c.command}: {lease.command_id}</p>
      <p className="text-xs text-muted-foreground">{c.lease}</p>
      {invalid && <p role="status">{c.invalidated}</p>}
      {review && connected && <>
        <h5 className="text-sm font-medium">{c.review}</h5>
        <details><summary>{c.inputs}</summary><pre className="whitespace-pre-wrap break-all text-xs">{review.lease.job_json}</pre></details>
        <pre className="whitespace-pre-wrap break-words text-xs">{review.summary}</pre>
        <ol className="grid gap-3">
          {review.proposals.map((proposal, index) => <li className="grid gap-1 break-all text-xs" key={proposal.approval_id}>
            <span>{index + 1}. {index === review.proposals.length - 1 ? c.manifestLast : proposal.mime} · {proposal.artifact_id} · v{proposal.version} · {proposal.size} B</span>
            <span>{c.digest}: {proposal.sha256}</span>
            <span>{c.expires}: {new Date(proposal.expires_at * 1000).toLocaleString(locale)}</span>
            <Button aria-pressed={reviewed.includes(index)} disabled={disabled || invalid} onClick={() => setReviewed(previous => previous.includes(index) ? previous.filter(value => value !== index) : [...previous, index])} size="xs" variant="secondary">{c.reviewed} {index + 1}</Button>
          </li>)}
        </ol>
        <Button disabled={!canApprove} onClick={() => { if (canApprove && !live.current.pending) {setConfirming(true)} }} size="xs" variant="secondary">{c.approve}</Button>
      </>}
      <div className="flex flex-wrap gap-2">
        <Button disabled={disabled} onClick={() => void action(false, token => inspectOrDiscard(false, token))} size="xs" variant="secondary">{c.inspect}</Button>
        <Button disabled={disabled || !!scope.lease?.cancelRequested} onClick={() => void action(true, token => inspectOrDiscard(true, token))} size="xs" variant="text">{c.discard}</Button>
      </div>
    </div>}
    <ConfirmDialog confirmLabel={c.publish} description={`${c.publicationNotice}\n${c.command}: ${review?.lease.command_id ?? ''}\n${review?.proposals.map((proposal, index) => `${index + 1}. ${proposal.artifact_id}@${proposal.version} · ${proposal.sha256}`).join('\n') ?? ''}`} dismissOnConfirm onClose={() => { setConfirming(false);

 if (!scope.lease) {setReview(null)} }} onConfirm={() => action(true, publish, true)} open={confirming && connected && !!review} title={c.publish} />
    {connected && message && <pre aria-live="polite" className="whitespace-pre-wrap break-words text-xs">{message}</pre>}
    {connected && published && <Button disabled={disabled} onClick={() => void action(false, inspectManifest)} size="xs" variant="secondary">{c.manifest}</Button>}
    {connected && manifestText && <pre className="whitespace-pre-wrap break-words text-xs">{manifestText}</pre>}
    {connected && manifest?.adapter === 'data' && <div className="grid gap-2">
      <Field label={c.row}><Input disabled={disabled} min={0} onChange={event => { setRow(event.target.value); setManifestText(summarizeDomainManifest(manifest)) }} type="number" value={row} /></Field>
      <Button disabled={disabled || row === ''} onClick={showLineage} size="xs" variant="secondary">{c.lineage}</Button>
    </div>}
    {connected && error && <p role="alert">{error}</p>}
  </div>
}
