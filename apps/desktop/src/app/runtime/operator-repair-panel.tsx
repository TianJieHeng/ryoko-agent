import { useLayoutEffect, useState, useSyncExternalStore } from 'react'

import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorState } from '@/components/ui/error-state'
import { Input } from '@/components/ui/input'
import { Loader } from '@/components/ui/loader'
import { useI18n } from '@/i18n'

import type { RuntimeRequest } from '../../../../shared/src/runtime-control'
import { OperationsSession, type OperatorAction, operatorActions, type OperatorError, type OperatorReview, operatorTargets } from '../../../../shared/src/runtime-operations'

import { type RuntimeUiLocale, runtimeUiLocales } from './runtime-ui-copy'

// Evidence identifiers remain verbatim. All controls and safety explanations have nine authored locales.
type Translation = readonly [string, string, string, string, string, string, string, string, string]
export const operatorCopy = {
  scope: ['Session / revision', 'Sitzung / Revision', 'Sesión / revisión', 'Session / révision', 'セッション／リビジョン', '会话／版本', '工作階段／版本', 'الجلسة / المراجعة', 'Сеанс / ревизия'],
  health: ['SQLite readable; provider and remote memory not probed', 'SQLite lesbar; Anbieter und Remote-Speicher nicht geprüft', 'SQLite legible; proveedor y memoria remota sin comprobar', 'SQLite lisible ; fournisseur et mémoire distante non testés', 'SQLite読取可能・プロバイダーとリモートメモリは未検査', 'SQLite可读；服务商及远程记忆未探测', 'SQLite可讀；服務商及遠端記憶未探測', 'SQLite قابل للقراءة؛ المزود والذاكرة البعيدة لم يُفحصا', 'SQLite доступна для чтения; провайдер и удалённая память не проверены'],
  waiting: ['Waiting reason', 'Wartegrund', 'Motivo de espera', 'Motif d’attente', '待機理由', '等待原因', '等待原因', 'سبب الانتظار', 'Причина ожидания'],
  affected: ['Affected IDs / before-revision', 'Betroffene IDs / Vorher-Revision', 'IDs afectados / revisión previa', 'ID concernés / révision préalable', '対象ID／変更前リビジョン', '受影响ID／修改前版本', '受影響ID／修改前版本', 'المعرفات المتأثرة / المراجعة السابقة', 'Затронутые ID / исходная ревизия'],
  expires: ['Review expires', 'Prüfung läuft ab', 'Caducidad de la revisión', 'Expiration de la vérification', '確認の有効期限', '审核到期时间', '審核到期時間', 'انتهاء المراجعة', 'Срок проверки'],
  title: ['Repair and privacy', 'Reparatur und Datenschutz', 'Reparación y privacidad', 'Réparation et confidentialité', '修復とプライバシー', '修复与隐私', '修復與隱私', 'الإصلاح والخصوصية', 'Восстановление и конфиденциальность'],
  inspect: ['Inspect current session', 'Aktuelle Sitzung prüfen', 'Inspeccionar sesión actual', 'Inspecter la session actuelle', '現在のセッションを確認', '检查当前会话', '檢查目前工作階段', 'فحص الجلسة الحالية', 'Проверить текущий сеанс'],
  audit: ['Inspect redacted audit', 'Redigiertes Audit prüfen', 'Inspeccionar auditoría redactada', 'Inspecter l’audit expurgé', '秘匿済み監査を確認', '检查脱敏审计', '檢查去識別審計', 'فحص التدقيق المنقح', 'Проверить очищенный аудит'],
  more: ['Next audit page', 'Nächste Auditseite', 'Siguiente página de auditoría', 'Page d’audit suivante', '次の監査ページ', '下一页审计', '下一頁審計', 'صفحة التدقيق التالية', 'Следующая страница аудита'],
  checkpoint: ['Check checkpoint eligibility', 'Checkpoint-Eignung prüfen', 'Comprobar punto de control', 'Vérifier le point de contrôle', 'チェックポイントの適格性を確認', '检查检查点资格', '檢查檢查點資格', 'فحص أهلية نقطة الاستعادة', 'Проверить контрольную точку'],
  retention: ['Inspect store retention', 'Speicheraufbewahrung prüfen', 'Inspeccionar retención por almacén', 'Inspecter la conservation par stockage', '保存先別の保持方針を確認', '检查各存储保留策略', '檢查各儲存保留原則', 'فحص الاحتفاظ بكل مخزن', 'Проверить хранение по хранилищам'],
  target: ['Exact inspected target ID', 'Exakte geprüfte Ziel-ID', 'ID exacto del destino inspeccionado', 'ID exact de la cible inspectée', '確認済み対象の正確なID', '已检查目标的确切ID', '已檢查目標的確切ID', 'معرف الهدف المحدد المفحوص', 'Точный ID проверенного объекта'],
  memory: ['Built-in memory record ID (blank selects transcript)', 'ID des integrierten Speichers (leer: Transkript)', 'ID de memoria integrada (vacío: transcripción)', 'ID de mémoire intégrée (vide : transcription)', '内蔵メモリのレコードID（空欄で会話記録）', '内置记忆记录ID（留空选择对话记录）', '內建記憶紀錄ID（留空選擇對話記錄）', 'معرف الذاكرة المدمجة (الفراغ يختار النص)', 'ID встроенной памяти (пусто: переписка)'],
  previewRepair: ['Preview exact repair', 'Exakte Reparatur voranzeigen', 'Previsualizar reparación exacta', 'Prévisualiser la réparation exacte', '正確な修復内容をプレビュー', '预览确切修复', '預覽確切修復', 'معاينة الإصلاح المحدد', 'Предпросмотр точного восстановления'],
  previewDelete: ['Preview logical deletion', 'Logische Löschung voranzeigen', 'Previsualizar borrado lógico', 'Prévisualiser la suppression logique', '論理削除をプレビュー', '预览逻辑删除', '預覽邏輯刪除', 'معاينة الحذف المنطقي', 'Предпросмотр логического удаления'],
  review: ['Review exact plan', 'Exakten Plan prüfen', 'Revisar plan exacto', 'Examiner le plan exact', '正確な計画を確認', '审核确切计划', '審核確切計畫', 'مراجعة الخطة المحددة', 'Проверить точный план'],
  confirm: ['Apply reviewed plan once', 'Geprüften Plan einmal anwenden', 'Aplicar el plan revisado una vez', 'Appliquer une fois le plan vérifié', '確認済み計画を一度適用', '应用已审核计划一次', '套用已審核計畫一次', 'تطبيق الخطة المراجعة مرة واحدة', 'Применить проверенный план один раз'],
  confirmation: ['Authorize only the exact JSON and digest below for this session, listed targets and before-revision. The broker rechecks ownership, policy, expiry and compare-and-swap. Changed inputs, scope or connection require a new review. An unconfirmed reply does not authorize a retry.', 'Nur exakte JSON-Daten und Digest unten für diese Sitzung, Ziele und Vorher-Revision autorisieren. Der Broker prüft Eigentum, Richtlinie, Ablauf und Compare-and-Swap erneut. Geänderte Eingaben, Bereiche oder Verbindungen erfordern eine neue Prüfung. Eine unbestätigte Antwort erlaubt keine Wiederholung.', 'Autoriza solo el JSON y resumen exactos para esta sesión, destinos y revisión previa. El intermediario comprueba titularidad, política, caducidad y comparación de versión. Cambios de entrada, ámbito o conexión requieren otra revisión. Una respuesta no confirmada no autoriza reintentar.', 'Autoriser uniquement le JSON et l’empreinte exacts pour cette session, ses cibles et la révision préalable. Le courtier revérifie propriété, politique, expiration et comparaison de version. Toute modification de saisie, portée ou connexion impose une nouvelle vérification. Une réponse non confirmée n’autorise pas un nouvel envoi.', '下記の正確なJSONとダイジェストだけを、このセッション、対象、変更前リビジョンについて承認します。ブローカーは所有権、ポリシー、有効期限、比較交換を再確認します。入力・範囲・接続が変わった場合は再確認が必要です。応答不明は再試行の許可ではありません。', '仅授权下方确切的JSON和摘要，限于此会话、所列目标和修改前版本。代理会再次检查归属、策略、有效期和版本比较交换。输入、范围或连接变化均需重新审核。未确认的响应不代表允许重试。', '僅授權下方確切的JSON和摘要，限於此工作階段、所列目標和修改前版本。代理會再次檢查歸屬、原則、有效期和版本比較交換。輸入、範圍或連線變化均需重新審核。未確認的回應不代表允許重試。', 'فوّض JSON والبصمة المحددين أدناه لهذه الجلسة والأهداف والمراجعة السابقة فقط. يعيد الوسيط فحص الملكية والسياسة والانتهاء ومقارنة الإصدار. تغير المدخلات أو النطاق أو الاتصال يتطلب مراجعة جديدة. الرد غير المؤكد لا يسمح بإعادة المحاولة.', 'Разрешить только точный JSON и хеш ниже для этого сеанса, объектов и исходной ревизии. Брокер проверит владельца, политику, срок и сравнение версии. Изменение ввода, области или подключения требует новой проверки. Неподтверждённый ответ не разрешает повторный запрос.'],
  privacy: ['Logical deletion is partial. It does not forensically erase SQLite/WAL, backups, provider copies, exports or active process context. Recovery journals, artifacts, session dumps, schedules, delegations and service evidence remain. Remote or primary personal harness deletion is unsupported; export and expiry are not certified here.', 'Logische Löschung ist teilweise. SQLite/WAL, Backups, Anbieterkopien, Exporte und aktiver Prozesskontext werden nicht forensisch gelöscht. Wiederherstellungsjournale, Artefakte, Sitzungsabbilder, Zeitpläne, Delegationen und Dienstbelege bleiben. Remote- oder primärer persönlicher Harness wird nicht gelöscht; Export und Ablauf sind hier nicht zertifiziert.', 'El borrado lógico es parcial. No elimina físicamente SQLite/WAL, copias de seguridad o proveedores, exportaciones ni contexto activo. Se conservan diarios, artefactos, volcados de sesión, programaciones, delegaciones y pruebas de servicio. No se admite borrar el sistema remoto o personal principal; exportación y caducidad no están certificadas aquí.', 'La suppression logique est partielle. Elle n’efface pas physiquement SQLite/WAL, sauvegardes, copies fournisseur, exports ou contexte actif. Journaux de récupération, artefacts, exports de session, calendriers, délégations et preuves restent conservés. La suppression du système distant ou personnel principal n’est pas prise en charge ; export et expiration ne sont pas certifiés ici.', '論理削除は部分的です。SQLite/WAL、バックアップ、プロバイダー側コピー、エクスポート、実行中コンテキストは物理消去されません。復旧記録、成果物、セッションダンプ、予定、委任、サービス証跡は残ります。リモートまたは主要な個人ハーネスの削除は未対応で、エクスポートや保持期限の保証もありません。', '逻辑删除是部分删除，不会彻底擦除SQLite/WAL、备份、服务商副本、导出或活动进程上下文。恢复日志、产物、会话转储、计划、委派和服务证据仍保留。不支持远程或主要个人运行框架删除；此处未认证导出及过期清理能力。', '邏輯刪除是部分刪除，不會徹底抹除SQLite/WAL、備份、服務商副本、匯出或活動程序內容。復原日誌、產物、工作階段傾印、排程、委派和服務證據仍保留。不支援遠端或主要個人執行框架刪除；此處未認證匯出及到期清理能力。', 'الحذف المنطقي جزئي. لا يمحو نهائيًا SQLite/WAL أو النسخ الاحتياطية أو نسخ المزود أو الصادرات أو سياق العملية النشطة. تبقى سجلات الاستعادة والمخرجات وتفريغات الجلسات والجداول والتفويضات وأدلة الخدمة. حذف النظام البعيد أو الشخصي الأساسي غير مدعوم؛ التصدير وانتهاء الاحتفاظ غير معتمدين هنا.', 'Логическое удаление частично. Оно не стирает физически SQLite/WAL, резервные и провайдерские копии, экспорты и активный контекст. Сохраняются журналы восстановления, артефакты, дампы, расписания, делегирования и служебные доказательства. Удаление удалённой или основной персональной среды не поддерживается; экспорт и истечение хранения здесь не сертифицированы.'],
  'reconcile-effect': ['Reconcile effect evidence', 'Effektbelege abgleichen', 'Conciliar pruebas del efecto', 'Rapprocher les preuves d’effet', '作用の証跡を照合', '核对操作证据', '核對操作證據', 'مطابقة أدلة الأثر', 'Сверить доказательства эффекта'],
  'retry-delivery': ['Requeue delivery', 'Zustellung neu einreihen', 'Reencolar entrega', 'Remettre la livraison en attente', '配信を再キュー', '重新排队交付', '重新排隊交付', 'إعادة التسليم إلى الطابور', 'Вернуть доставку в очередь'],
  'revoke-lease': ['Revoke ownership lease', 'Besitz-Lease widerrufen', 'Revocar concesión de control', 'Révoquer le bail de contrôle', '所有リースを失効', '撤销所有权租约', '撤銷所有權租約', 'إلغاء عقد التحكم', 'Отозвать аренду управления'],
  'rebuild-index': ['Rebuild derived index', 'Abgeleiteten Index neu aufbauen', 'Reconstruir índice derivado', 'Reconstruire l’index dérivé', '派生インデックスを再構築', '重建派生索引', '重建衍生索引', 'إعادة بناء الفهرس المشتق', 'Перестроить производный индекс'],
  'restore-checkpoint': ['Reconstruct checkpoint projection', 'Checkpoint-Projektion rekonstruieren', 'Reconstruir proyección del punto de control', 'Reconstruire la projection du point de contrôle', 'チェックポイント投影を再構築', '重构检查点投影', '重建檢查點投影', 'إعادة بناء إسقاط نقطة الاستعادة', 'Восстановить проекцию контрольной точки'],
  reconcileMeaning: ['Inspects evidence only; never redispatches the external effect. Uncertainty may remain.', 'Prüft nur Belege; führt den externen Effekt nie erneut aus. Unsicherheit kann bleiben.', 'Solo inspecciona pruebas; nunca repite el efecto externo. Puede persistir incertidumbre.', 'Inspecte seulement les preuves ; ne réexécute jamais l’effet externe. Une incertitude peut subsister.', '証跡のみ確認し、外部作用を再実行しません。不明な状態が残る場合があります。', '仅检查证据，绝不重新执行外部操作，结果可能仍不确定。', '僅檢查證據，絕不重新執行外部操作，結果可能仍不確定。', 'يفحص الأدلة فقط ولا يعيد تنفيذ الأثر الخارجي. قد يبقى الغموض.', 'Проверяет лишь доказательства, без повторного внешнего действия. Неопределённость может сохраниться.'],
  deliveryMeaning: ['Updates the same local outbox obligation with the same result and attempt budget. This action does not send; exhausted delivery stays dead-lettered.', 'Aktualisiert denselben lokalen Ausgangsauftrag mit gleichem Ergebnis und Versuchslimit. Sendet nicht; ausgeschöpfte Zustellungen bleiben gesperrt.', 'Actualiza la misma obligación local con el mismo resultado y límite de intentos. No envía; las entregas agotadas siguen fallidas.', 'Met à jour la même obligation locale avec le même résultat et quota d’essais. N’envoie rien ; les livraisons épuisées restent en échec définitif.', '同じローカル送信義務を、同じ結果と試行予算で更新します。この操作は送信せず、上限到達時は配信不能のままです。', '用相同结果和尝试预算更新同一本地发件义务。本操作不发送；预算耗尽时仍保持死信状态。', '以相同結果和嘗試預算更新同一本機寄送義務。本操作不傳送；預算耗盡時仍保持死信狀態。', 'يحدّث التزام صندوق الصادر المحلي نفسه بالنتيجة وميزانية المحاولات نفسيهما. لا يرسل؛ يبقى التسليم المستنفد معلقًا نهائيًا.', 'Обновляет ту же локальную доставку с прежним результатом и лимитом попыток. Ничего не отправляет; исчерпанные доставки остаются недоставленными.'],
  revokeMeaning: ['Fences the old owner generation. Remote work may continue; this does not stop it or undo committed effects.', 'Sperrt die alte Besitzergeneration. Remote-Arbeit kann weiterlaufen; stoppt sie nicht und macht erfolgte Effekte nicht rückgängig.', 'Bloquea la generación anterior. El trabajo remoto puede continuar; no lo detiene ni deshace efectos confirmados.', 'Bloque l’ancienne génération du propriétaire. Le travail distant peut continuer ; cela ne l’arrête pas et n’annule pas les effets engagés.', '旧所有者の世代を無効化します。リモート作業は継続し得るため、停止や確定済み作用の取消を意味しません。', '隔离旧所有者代次。远程工作可能继续；这不会停止远程工作或撤销已提交的作用。', '隔離舊擁有者代次。遠端工作可能繼續；這不會停止遠端工作或復原已提交的作用。', 'يعزل جيل المالك السابق. قد يستمر العمل البعيد؛ لا يوقفه ولا يعكس الآثار الملتزمة.', 'Ограждает прежнее поколение владельца. Удалённая работа может продолжаться; это не останавливает её и не отменяет совершённые действия.'],
  indexMeaning: ['Rebuilds bounded, single-actor derived indexes for every listed session. Source records are unchanged.', 'Baut begrenzte abgeleitete Indizes eines Akteurs für alle aufgeführten Sitzungen neu auf. Quelldaten bleiben unverändert.', 'Reconstruye índices derivados acotados de un actor para todas las sesiones listadas. Las fuentes no cambian.', 'Reconstruit les index dérivés bornés d’un acteur pour toutes les sessions listées. Les sources restent inchangées.', '記載された全セッションの、単一主体に限定された派生インデックスを再構築します。元データは変更しません。', '为所列全部会话重建范围有限的单主体派生索引。源记录不变。', '為所列全部工作階段重建範圍有限的單一主體衍生索引。來源紀錄不變。', 'يعيد بناء فهارس مشتقة محدودة لجهة واحدة لكل الجلسات المدرجة. لا تتغير سجلات المصدر.', 'Перестраивает ограниченные производные индексы одного субъекта для всех указанных сеансов. Исходные записи не меняются.'],
  checkpointMeaning: ['Reconstructs derived state from checkpoint plus journal. Never rewinds history, consumed approvals or effects, and never replays external actions. This is not a certified full-profile restore.', 'Rekonstruiert abgeleiteten Zustand aus Checkpoint und Journal. Setzt Verlauf, verbrauchte Freigaben oder Effekte nie zurück und wiederholt keine externen Aktionen. Kein zertifiziertes Vollprofil-Restore.', 'Reconstruye estado derivado del punto de control y diario. No retrocede historial, aprobaciones usadas ni efectos y no repite acciones externas. No es restauración certificada del perfil completo.', 'Reconstruit l’état dérivé depuis le point de contrôle et le journal. Ne rembobine jamais historique, autorisations consommées ou effets, et ne rejoue aucune action externe. Pas de restauration certifiée du profil entier.', 'チェックポイントとジャーナルから派生状態を再構築します。履歴、使用済み承認、作用を巻き戻さず、外部操作も再実行しません。プロファイル全体の認証済み復元ではありません。', '基于检查点和日志重构派生状态。绝不回退历史、已消耗的批准或作用，也不重放外部操作。这不是经过认证的完整配置恢复。', '基於檢查點和日誌重建衍生狀態。絕不回退歷史、已消耗的核准或作用，也不重播外部操作。這不是經過認證的完整設定檔復原。', 'يعيد بناء الحالة المشتقة من نقطة الاستعادة والسجل. لا يعيد التاريخ أو الموافقات المستهلكة أو الآثار إلى الوراء ولا يكرر الإجراءات الخارجية. ليس استعادة معتمدة للملف الكامل.', 'Восстанавливает производное состояние из контрольной точки и журнала. Не откатывает историю, использованные разрешения или эффекты и не повторяет внешние действия. Это не сертифицированное восстановление всего профиля.'],
  offline: ['Offline: reconnect and inspect again before acting', 'Offline: erneut verbinden und prüfen', 'Sin conexión: reconecta e inspecciona otra vez', 'Hors ligne : reconnectez-vous puis inspectez', 'オフライン：再接続後に再確認してください', '离线：重新连接并检查后再操作', '離線：重新連線並檢查後再操作', 'غير متصل: أعد الاتصال والفحص قبل التصرف', 'Нет связи: подключитесь и проверьте заново'],
  empty: ['Inspect before preparing a change', 'Vor Änderungen zuerst prüfen', 'Inspecciona antes de preparar cambios', 'Inspectez avant de préparer une modification', '変更の準備前に状態を確認', '准备更改前请检查状态', '準備變更前請檢查狀態', 'افحص قبل إعداد تغيير', 'Проверьте состояние перед изменением'],
  unavailable: ['Request unavailable or malformed. Inspect current state; no automatic retry was sent.', 'Anfrage nicht verfügbar oder fehlerhaft. Aktuellen Zustand prüfen; keine automatische Wiederholung.', 'Solicitud no disponible o incorrecta. Inspecciona el estado; no hubo reintento automático.', 'Requête indisponible ou invalide. Inspectez l’état ; aucun nouvel envoi automatique.', '要求を利用できないか形式が無効です。現在の状態を確認してください。自動再試行はありません。', '请求不可用或格式无效。请检查当前状态；未自动重试。', '要求無法使用或格式無效。請檢查目前狀態；未自動重試。', 'الطلب غير متاح أو غير صالح. افحص الحالة؛ لم تتم إعادة محاولة تلقائية.', 'Запрос недоступен или некорректен. Проверьте состояние; автоматического повтора не было.'],
  changed: ['Review expired or changed. Inspect and prepare again.', 'Prüfung abgelaufen oder geändert. Erneut prüfen und vorbereiten.', 'Revisión caducada o modificada. Inspecciona y prepara otra vez.', 'Vérification expirée ou modifiée. Inspectez et préparez à nouveau.', '確認内容が変更または期限切れです。再確認して準備してください。', '审核已过期或内容变化。请重新检查并准备。', '審核已到期或內容變化。請重新檢查並準備。', 'المراجعة تغيرت أو انتهت. افحص وأعد الإعداد.', 'Проверка устарела или изменилась. Проверьте и подготовьте заново.'],
  unknown: ['Outcome unknown. Retain the original digest and inspect state and audit only; do not resubmit.', 'Ergebnis unbekannt. Original-Digest behalten und nur Zustand sowie Audit prüfen; nicht erneut senden.', 'Resultado desconocido. Conserva el resumen e inspecciona solo estado y auditoría; no reenvíes.', 'Résultat inconnu. Conservez l’empreinte et inspectez seulement état et audit ; ne renvoyez rien.', '結果不明です。元のダイジェストを保持し、状態と監査のみ確認してください。再送しないでください。', '结果未知。保留原始摘要，仅检查状态和审计；不要重新提交。', '結果未知。保留原始摘要，僅檢查狀態和審計；不要重新提交。', 'النتيجة مجهولة. احتفظ بالبصمة الأصلية وافحص الحالة والتدقيق فقط؛ لا تعِد الإرسال.', 'Результат неизвестен. Сохраните исходный хеш и только проверьте состояние и аудит; не отправляйте повторно.'],
  rejected: ['Exact plan rejected by the broker. Inspect fresh state before preparing a new review.', 'Exakter Plan vom Broker abgelehnt. Vor neuer Prüfung aktuellen Zustand prüfen.', 'El intermediario rechazó el plan. Inspecciona el estado actual antes de otra revisión.', 'Plan exact refusé par le courtier. Inspectez l’état actualisé avant une nouvelle vérification.', '計画がブローカーに拒否されました。新しい確認の準備前に最新状態を確認してください。', '代理拒绝了确切计划。准备新的审核前，请检查最新状态。', '代理拒絕了確切計畫。準備新的審核前，請檢查最新狀態。', 'رفض الوسيط الخطة المحددة. افحص الحالة الجديدة قبل إعداد مراجعة أخرى.', 'Брокер отклонил точный план. Проверьте актуальное состояние перед новой подготовкой.'],
  afterUnavailable: ['Receipt received, but after-state inspection is unavailable. Refresh before drawing conclusions.', 'Beleg empfangen, Nachher-Zustand nicht prüfbar. Vor Schlussfolgerungen aktualisieren.', 'Acuse recibido, pero estado posterior no disponible. Actualiza antes de concluir.', 'Reçu arrivé, mais état après indisponible. Actualisez avant de conclure.', '応答記録は受信しましたが変更後状態を確認できません。判断前に再確認してください。', '已收到回执，但无法检查操作后状态。得出结论前请刷新。', '已收到回執，但無法檢查操作後狀態。下結論前請重新整理。', 'وصل الإيصال لكن فحص الحالة اللاحقة غير متاح. حدّث قبل الاستنتاج.', 'Подтверждение получено, но последующее состояние недоступно. Обновите перед выводами.'],
  receipt: ['Actual receipt', 'Tatsächlicher Beleg', 'Acuse real', 'Reçu réel', '実際の応答記録', '实际回执', '實際回執', 'الإيصال الفعلي', 'Фактическое подтверждение'],
  before: ['Before-state', 'Vorher-Zustand', 'Estado previo', 'État avant', '変更前の状態', '操作前状态', '操作前狀態', 'الحالة السابقة', 'Состояние до'],
  after: ['Observed after-state', 'Beobachteter Nachher-Zustand', 'Estado posterior observado', 'État après observé', '確認された変更後の状態', '观察到的操作后状态', '觀察到的操作後狀態', 'الحالة اللاحقة المرصودة', 'Наблюдаемое состояние после'],
  acknowledged: ['Reply acknowledged; inspect its exact outcome below', 'Antwort bestätigt; genaues Ergebnis unten prüfen', 'Respuesta recibida; inspecciona el resultado exacto', 'Réponse reçue ; examinez le résultat exact', '応答を確認しました。下記の正確な結果をご確認ください', '已确认响应；请检查下方确切结果', '已確認回應；請檢查下方確切結果', 'وصل الرد؛ افحص النتيجة الدقيقة أدناه', 'Ответ получен; проверьте точный результат ниже'],
  partial: ['Partial logical deletion only; acknowledgments do not prove complete erasure', 'Nur teilweise logische Löschung; Bestätigungen belegen keine vollständige Löschung', 'Solo borrado lógico parcial; los acuses no prueban eliminación completa', 'Suppression logique partielle ; les accusés ne prouvent pas l’effacement complet', '部分的な論理削除のみです。確認は完全消去の証明ではありません', '仅部分逻辑删除；确认不能证明完全擦除', '僅部分邏輯刪除；確認不能證明完全抹除', 'حذف منطقي جزئي فقط؛ الإقرارات لا تثبت المحو الكامل', 'Только частичное логическое удаление; подтверждения не доказывают полное стирание'],
  auditLimit: ['Audit pages are bounded and redact payloads. A started or finished event alone does not prove success, delivery or erasure.', 'Auditseiten sind begrenzt und blenden Nutzdaten aus. Start- oder Ende-Ereignisse beweisen allein weder Erfolg, Zustellung noch Löschung.', 'La auditoría es acotada y omite contenidos. Un evento de inicio o fin no prueba éxito, entrega ni borrado.', 'L’audit est borné et expurgé. Un événement de début ou fin ne prouve seul ni réussite, livraison ou effacement.', '監査は範囲が限定され、ペイロードは秘匿されます。開始・終了イベントだけでは成功、配信、消去を証明できません。', '审计页面范围有限且载荷已脱敏。仅凭开始或结束事件不能证明成功、交付或擦除。', '審計頁面範圍有限且承載資料已去識別。僅憑開始或結束事件不能證明成功、交付或抹除。', 'صفحات التدقيق محدودة وتخفي الحمولات. حدث البدء أو الانتهاء وحده لا يثبت النجاح أو التسليم أو المحو.', 'Страницы аудита ограничены, содержимое скрыто. Событие начала или завершения само по себе не доказывает успех, доставку или стирание.'],
  capability: ['Store capabilities and retained counts (backend facts)', 'Speicherfunktionen und Aufbewahrungszahlen (Backend-Fakten)', 'Capacidades y recuentos retenidos (datos del servidor)', 'Capacités et comptes conservés (faits du serveur)', '保存先の機能と保持件数（バックエンドの事実）', '存储能力和保留计数（后端事实）', '儲存能力和保留計數（後端事實）', 'قدرات المخازن والأعداد المحتفظ بها (حقائق الخلفية)', 'Возможности и сохранённые количества (факты бэкенда)'],
  exact: ['Exact JSON, digest, affected IDs, invariants and limitations', 'Exakte JSON-Daten, Digest, betroffene IDs, Invarianten und Grenzen', 'JSON, resumen, IDs afectados, invariantes y límites exactos', 'JSON, empreinte, ID concernés, invariants et limites exacts', '正確なJSON・ダイジェスト・対象ID・不変条件・制限', '确切JSON、摘要、受影响ID、不变量和限制', '確切JSON、摘要、受影響ID、不變量和限制', 'JSON والبصمة والمعرفات والثوابت والقيود المحددة', 'Точный JSON, хеш, затронутые ID, инварианты и ограничения'],
  cancel: ['Cancel review', 'Prüfung abbrechen', 'Cancelar revisión', 'Annuler la vérification', '確認を取消', '取消审核', '取消審核', 'إلغاء المراجعة', 'Отменить проверку'],
  busy: ['Applying reviewed plan', 'Geprüften Plan anwenden', 'Aplicando plan revisado', 'Application du plan vérifié', '確認済み計画を適用中', '正在应用已审核计划', '正在套用已審核計畫', 'تطبيق الخطة المراجعة', 'Применение проверенного плана'],
  noAfter: ['After-state has not been verified', 'Nachher-Zustand nicht verifiziert', 'Estado posterior sin verificar', 'État après non vérifié', '変更後状態は未検証', '尚未验证操作后状态', '尚未驗證操作後狀態', 'لم يتم التحقق من الحالة اللاحقة', 'Состояние после не проверено']
} satisfies Record<string, Translation>

type CopyKey = keyof typeof operatorCopy
type Copy = (key: CopyKey) => string
const actionMeaning: Record<OperatorAction, CopyKey> = { 'reconcile-effect': 'reconcileMeaning', 'retry-delivery': 'deliveryMeaning', 'revoke-lease': 'revokeMeaning', 'rebuild-index': 'indexMeaning', 'restore-checkpoint': 'checkpointMeaning' }
export interface OperatorRepairPanelProps {
  request: RuntimeRequest; sessionId: string; connected: boolean; connectionGeneration?: string | number
  /** Passive unknown digests from the owning request-scope cache; never approvals. */
  unresolvedOperationIds?: readonly string[]
}

export function OperatorRepairPanel(props: OperatorRepairPanelProps) {
  const [scope, setScope] = useState({ request: props.request, sessionId: props.sessionId, generation: 0 })

  if (scope.request !== props.request || scope.sessionId !== props.sessionId) {
    setScope({ request: props.request, sessionId: props.sessionId, generation: scope.generation + 1 })

    return null
  }

  return <OwnedOperatorPanel key={scope.generation} {...props} />
}

function OwnedOperatorPanel(props: OperatorRepairPanelProps) {
  const [session] = useState(() => new OperationsSession(props.request, props.sessionId))
  const [scope, setScope] = useState({ connected: props.connected, connection: props.connectionGeneration, generation: 0 })
  const pending = JSON.stringify(props.unresolvedOperationIds ?? [])

  if (scope.connected !== props.connected || scope.connection !== props.connectionGeneration) {
    setScope({ connected: props.connected, connection: props.connectionGeneration, generation: scope.generation + 1 })

    return null
  }

  return <OperatorPanelView connected={props.connected} key={scope.generation} pendingJson={pending} session={session} />
}

function OperatorPanelView({ session, connected, pendingJson }: { session: OperationsSession; connected: boolean; pendingJson: string }) {
  const { locale } = useI18n()
  const index = Math.max(0, runtimeUiLocales.indexOf(locale as RuntimeUiLocale))
  const c: Copy = key => operatorCopy[key][index]
  const state = useSyncExternalStore(session.subscribe, session.getState)
  const [confirming, setConfirming] = useState<OperatorReview | null>(null)
  useLayoutEffect(() => session.attach(connected), [session, connected])
  useLayoutEffect(() => session.setUnresolved(JSON.parse(pendingJson) as string[]), [session, pendingJson])
  const pending = JSON.parse(pendingJson) as string[]
  const disabled = !connected || state.busy, locked = disabled || state.attempt?.status === 'unknown' || pending.some(id => id !== state.attempt?.review.digest)
  const inspection = state.inspection
  const targets = inspection ? operatorTargets(inspection, state.action) : []
  const review = state.review
  const exactConfirmation = confirming && confirming === review ? confirming : null
  const meaning = review?.kind === 'deletion' ? c('privacy') : c(actionMeaning[state.action])
  const errors: Record<OperatorError, CopyKey> = { unavailable: 'unavailable', changed: 'changed', unknown: 'unknown', rejected: 'rejected', afterUnavailable: 'afterUnavailable' }

  return <section aria-label={c('title')} className="grid gap-4">
    <h4 className="text-sm font-medium">{c('title')}</h4>
    {!connected && <p role="status">{c('offline')}</p>}
    <div className="flex flex-wrap gap-2">
      <Button disabled={disabled} onClick={() => void session.inspect()} size="xs" variant="secondary">{c('inspect')}</Button>
      <Button disabled={disabled} onClick={() => void session.inspectAudit()} size="xs" variant="secondary">{c('audit')}</Button>
      <Button disabled={disabled} onClick={() => void session.inspectCheckpoint()} size="xs" variant="secondary">{c('checkpoint')}</Button>
      <Button disabled={disabled} onClick={() => void session.inspectPrivacy()} size="xs" variant="secondary">{c('retention')}</Button>
    </div>
    {state.busy && <Loader />}
    {!inspection && !state.busy && <EmptyState title={c('empty')} />}
    {inspection && <>
      <p className="break-words text-xs">{c('scope')}: {inspection.session_id} / {inspection.revision}</p>
      <p className="text-xs">{c('health')}</p>
      <p className="text-xs">{c('waiting')}: {inspection.waiting_reason}</p>
      <Evidence label={c('inspect')} value={inspection} />
      <div className="flex flex-wrap gap-2">{operatorActions.map(action => <Button aria-pressed={state.action === action} disabled={locked} key={action} onClick={() => session.edit({ action, target: operatorTargets(inspection, action)[0] ?? '' })} size="xs" variant={state.action === action ? 'secondary' : 'ghost'}>{c(action)}</Button>)}</div>
      <p className="text-xs">{c(actionMeaning[state.action])}</p>
      <label className="grid gap-1 text-xs">{c('target')}<Input disabled={locked} onChange={event => session.edit({ target: event.target.value })} value={state.target} /></label>
      <Button disabled={locked || !targets.includes(state.target) || state.action === 'restore-checkpoint' && (!state.checkpoint?.restore_allowed || state.checkpoint.through_revision !== inspection.revision)} onClick={() => void session.prepare('repair')} size="xs" variant="secondary">{c('previewRepair')}</Button>
    </>}
    {state.checkpoint && <Evidence label={c('checkpoint')} value={state.checkpoint} />}
    <p className="text-xs">{c('privacy')}</p>
    {state.retention && <>
      <Evidence label={c('capability')} value={state.retention} />
      <label className="grid gap-1 text-xs">{c('memory')}<Input disabled={locked || !inspection || inspection.memory_backend !== 'builtin'} onChange={event => session.edit({ memoryRecordId: event.target.value })} value={state.memoryRecordId} /></label>
      <Button disabled={locked || !inspection || !!state.memoryRecordId && inspection.memory_backend !== 'builtin'} onClick={() => void session.prepare('deletion')} size="xs" variant="secondary">{c('previewDelete')}</Button>
    </>}
    {review && <>
      <p className="text-xs">{meaning}</p>
      <p className="break-words text-xs">{c('affected')}: {('affected_ids' in review.plan ? review.plan.affected_ids : review.plan.record_refs).join(', ')} / {review.plan.before_revision}</p>
      <p className="text-xs">{c('expires')}: {new Date(review.plan.expires_at * 1000).toLocaleString()}</p>
      <Evidence exact label={c('exact')} value={review.json} />
      <Button disabled={locked} onClick={() => setConfirming(review)} size="xs" variant="secondary">{c('review')}</Button>
    </>}
    <ConfirmDialog busyLabel={c('busy')} cancelLabel={c('cancel')} confirmLabel={c('confirm')} description={`${c('confirmation')}\n\n${meaning}`} destructive dismissOnConfirm onClose={() => { setConfirming(null); session.dismissReview() }} onConfirm={() => exactConfirmation ? session.apply(exactConfirmation) : undefined} open={!!exactConfirmation && !locked} title={c('review')}>
      {exactConfirmation && <Evidence exact label={c('exact')} value={exactConfirmation.json} />}
    </ConfirmDialog>
    {state.attempt && <div className="grid gap-3" role="status">
      <p className="break-words text-xs">{c(state.attempt.status)}: {state.attempt.review.digest}</p>
      {state.attempt.review.kind === 'deletion' && state.attempt.receipt && <p className="text-xs">{c('partial')}</p>}
      <Evidence label={c('before')} value={state.attempt.review.before} />
      {state.attempt.receipt && <Evidence label={c('receipt')} value={state.attempt.receipt} />}
      {state.attempt.after ? <Evidence label={c('after')} value={state.attempt.after} /> : <p className="text-xs">{c('noAfter')}</p>}
    </div>}
    {pending.length > 0 && <div role="status"><p>{c('unknown')}</p><pre className="whitespace-pre-wrap break-words text-xs">{pending.join('\n')}</pre></div>}
    {state.audit && <>
      <p className="text-xs">{c('auditLimit')}</p>
      <Evidence label={c('audit')} value={state.audit} />
      <Button disabled={disabled || !state.audit.has_more} onClick={() => void session.inspectAudit(true)} size="xs" variant="ghost">{c('more')}</Button>
    </>}
    {state.error && <div role="alert"><ErrorState title={c(errors[state.error])} /></div>}
  </section>
}

function Evidence({ label, value, exact = false }: { label: string; value: unknown; exact?: boolean }) {
  return <div className="grid gap-1"><p className="text-xs font-medium">{label}</p><pre className="whitespace-pre-wrap break-words text-xs">{exact ? String(value) : JSON.stringify(value, null, 2)}</pre></div>
}
