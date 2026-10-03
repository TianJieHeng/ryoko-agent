// Feature-local chrome is complete for all bundled locales. Protocol identifiers and
// validated runtime projections below deliberately retain their diagnostic wording.
const locales = ['en', 'de', 'es', 'fr', 'ja', 'zh', 'zh-hant', 'ar', 'ru'] as const

const messages = {
  title: [
    'Retained-source research',
    'Recherche in gespeicherten Quellen',
    'Investigación de fuentes conservadas',
    'Recherche sur sources conservées',
    '保持済みソースの調査',
    '留存来源研究',
    '保留來源研究',
    'بحث المصادر المحفوظة',
    'Исследование сохранённых источников'
  ],
  scope: [
    'Only retained project artifacts and original captures. Source authority is declared; claims are not independently verified. No connected search or monitoring.',
    'Nur gespeicherte Projektartefakte und Originalaufnahmen. Quellenautorität ist eine Angabe; Aussagen sind nicht unabhängig geprüft. Keine externe Suche oder Überwachung.',
    'Solo artefactos conservados y capturas originales. La autoridad es declarada; las afirmaciones no se verifican de forma independiente. Sin búsqueda conectada ni seguimiento.',
    'Uniquement les artefacts conservés et captures originales. Autorité déclarée, affirmations non vérifiées indépendamment. Aucune recherche connectée ni surveillance.',
    '保持済み成果物と元のキャプチャのみ。権威は申告値であり、主張の独立検証は行いません。外部検索や監視はありません。',
    '仅支持留存的项目制品和原始采集。来源权威由用户声明，未独立验证论点。没有外部搜索或监控。',
    '僅支援保留的專案成品和原始擷取。來源權威由使用者聲明，未獨立驗證論點。沒有外部搜尋或監控。',
    'الملفات المحفوظة واللقطات الأصلية فقط. موثوقية المصدر معلنة والادعاءات غير متحققة بشكل مستقل. لا بحث متصل أو مراقبة.',
    'Только сохранённые артефакты и исходные записи. Авторитетность заявлена; утверждения независимо не проверены. Без внешнего поиска и мониторинга.'
  ],
  current: [
    'Current source references',
    'Aktuelle Quellenreferenzen',
    'Referencias actuales',
    'Références actuelles',
    '現在のソース参照',
    '当前来源引用',
    '目前來源參照',
    'مراجع المصادر الحالية',
    'Текущие ссылки на источники'
  ],
  previous: [
    'Previous source references',
    'Vorherige Quellenreferenzen',
    'Referencias anteriores',
    'Références précédentes',
    '以前のソース参照',
    '先前来源引用',
    '先前來源參照',
    'مراجع المصادر السابقة',
    'Предыдущие ссылки на источники'
  ],
  source: [
    'Source ID',
    'Quellen-ID',
    'ID de fuente',
    'ID de source',
    'ソース ID',
    '来源 ID',
    '來源 ID',
    'معرف المصدر',
    'ID источника'
  ],
  project: [
    'Project ID',
    'Projekt-ID',
    'ID del proyecto',
    'ID du projet',
    'プロジェクト ID',
    '项目 ID',
    '專案 ID',
    'معرف المشروع',
    'ID проекта'
  ],
  version: [
    'Exact version',
    'Exakte Version',
    'Versión exacta',
    'Version exacte',
    '正確なバージョン',
    '确切版本',
    '確切版本',
    'الإصدار المحدد',
    'Точная версия'
  ],
  artifactSource: [
    'Project artifact',
    'Projektartefakt',
    'Artefacto del proyecto',
    'Artefact du projet',
    'プロジェクト成果物',
    '项目制品',
    '專案成品',
    'ملف المشروع',
    'Артефакт проекта'
  ],
  captureSource: [
    'Original capture',
    'Originalaufnahme',
    'Captura original',
    'Capture originale',
    '元のキャプチャ',
    '原始采集',
    '原始擷取',
    'اللقطة الأصلية',
    'Исходная запись'
  ],
  authority: [
    'Declared source authority',
    'Angegebene Quellenautorität',
    'Autoridad declarada',
    'Autorité déclarée',
    '申告されたソースの権威',
    '声明的来源权威',
    '聲明的來源權威',
    'موثوقية المصدر المعلنة',
    'Заявленная авторитетность'
  ],
  evidence: [
    'Optional exact evidence range',
    'Optionaler exakter Belegbereich',
    'Rango exacto opcional',
    'Plage exacte facultative',
    '任意の正確な証拠範囲',
    '可选的确切证据范围',
    '選填的確切證據範圍',
    'نطاق الأدلة المحدد الاختياري',
    'Необязательный точный диапазон'
  ],
  start: [
    'Start byte (inclusive)',
    'Startbyte (inklusive)',
    'Byte inicial (incluido)',
    'Octet initial (inclus)',
    '開始バイト（含む）',
    '起始字节（包含）',
    '起始位元組（包含）',
    'بايت البداية (شامل)',
    'Начальный байт (включительно)'
  ],
  end: [
    'End byte (exclusive)',
    'Endbyte (exklusive)',
    'Byte final (excluido)',
    'Octet final (exclu)',
    '終了バイト（含まない）',
    '结束字节（不含）',
    '結束位元組（不含）',
    'بايت النهاية (غير شامل)',
    'Конечный байт (исключительно)'
  ],
  quote: [
    'Exact UTF-8 quote',
    'Exaktes UTF-8-Zitat',
    'Cita exacta UTF-8',
    'Citation exacte UTF-8',
    '正確な UTF-8 引用',
    '确切 UTF-8 引文',
    '確切 UTF-8 引文',
    'اقتباس UTF-8 المحدد',
    'Точная цитата UTF-8'
  ],
  rangeDigest: [
    'Range SHA-256',
    'Bereich SHA-256',
    'SHA-256 del rango',
    'SHA-256 de la plage',
    '範囲 SHA-256',
    '范围 SHA-256',
    '範圍 SHA-256',
    'SHA-256 للنطاق',
    'SHA-256 диапазона'
  ],
  freshness: [
    'Fresh until (UTC epoch seconds; optional)',
    'Frisch bis (UTC-Epochensekunden; optional)',
    'Vigente hasta (segundos UTC; opcional)',
    'Valide jusqu’au (secondes UTC ; facultatif)',
    '有効期限（UTC 秒、任意）',
    '有效截止时间（UTC 秒，可选）',
    '有效截止時間（UTC 秒，選填）',
    'صالح حتى (ثواني UTC، اختياري)',
    'Актуально до (секунды UTC; необязательно)'
  ],
  add: [
    'Add source',
    'Quelle hinzufügen',
    'Añadir fuente',
    'Ajouter une source',
    'ソースを追加',
    '添加来源',
    '新增來源',
    'إضافة مصدر',
    'Добавить источник'
  ],
  remove: [
    'Remove source',
    'Quelle entfernen',
    'Quitar fuente',
    'Retirer la source',
    'ソースを削除',
    '移除来源',
    '移除來源',
    'إزالة المصدر',
    'Удалить источник'
  ],
  resolve: [
    'Check exact source coverage',
    'Exakte Quellenabdeckung prüfen',
    'Comprobar cobertura exacta',
    'Vérifier la couverture exacte',
    '正確なソース範囲を確認',
    '检查确切来源覆盖',
    '檢查確切來源涵蓋',
    'فحص تغطية المصادر المحددة',
    'Проверить охват точных источников'
  ],
  coverage: [
    'Source coverage and citations',
    'Quellenabdeckung und Zitate',
    'Cobertura y citas',
    'Couverture et citations',
    'ソースの網羅性と引用',
    '来源覆盖及引文',
    '來源涵蓋及引文',
    'تغطية المصادر والاستشهادات',
    'Охват источников и цитаты'
  ],
  refresh: [
    'Refresh from saved manifest',
    'Mit gespeichertem Manifest aktualisieren',
    'Actualizar desde manifiesto',
    'Actualiser depuis le manifeste',
    '保存済みマニフェストから更新',
    '从已存清单刷新',
    '從已存清單更新',
    'تحديث من البيان المحفوظ',
    'Обновить по манифесту'
  ],
  initial: [
    'Establish first dependency baseline',
    'Erste Abhängigkeitsbasis erstellen',
    'Establecer dependencias iniciales',
    'Établir les dépendances initiales',
    '最初の依存関係を設定',
    '建立首次依赖基线',
    '建立首次相依基準',
    'إنشاء خط أساس التبعيات الأول',
    'Создать исходные зависимости'
  ],
  brief: [
    'Existing brief artifact ID',
    'ID des bestehenden Briefings',
    'ID del informe existente',
    'ID du document existant',
    '既存ブリーフの成果物 ID',
    '现有简报制品 ID',
    '現有簡報成品 ID',
    'معرف ملف الملخص الحالي',
    'ID существующего отчёта'
  ],
  parent: [
    'Brief parent version',
    'Elternversion des Briefings',
    'Versión anterior del informe',
    'Version parente du document',
    'ブリーフの親バージョン',
    '简报父版本',
    '簡報父版本',
    'الإصدار الأب للملخص',
    'Родительская версия отчёта'
  ],
  manifest: [
    'Prior dependency manifest ID',
    'ID des vorherigen Abhängigkeitsmanifests',
    'ID del manifiesto anterior',
    'ID du manifeste précédent',
    '以前の依存マニフェスト ID',
    '先前依赖清单 ID',
    '先前相依清單 ID',
    'معرف بيان التبعيات السابق',
    'ID предыдущего манифеста'
  ],
  manifestVersion: [
    'Manifest exact version',
    'Exakte Manifestversion',
    'Versión exacta del manifiesto',
    'Version exacte du manifeste',
    'マニフェストの正確なバージョン',
    '清单确切版本',
    '清單確切版本',
    'إصدار البيان المحدد',
    'Точная версия манифеста'
  ],
  manifestDigest: [
    'Manifest SHA-256',
    'Manifest SHA-256',
    'SHA-256 del manifiesto',
    'SHA-256 du manifeste',
    'マニフェスト SHA-256',
    '清单 SHA-256',
    '清單 SHA-256',
    'SHA-256 للبيان',
    'SHA-256 манифеста'
  ],
  command: [
    'Command ID (new for each preparation)',
    'Befehls-ID (neu pro Vorbereitung)',
    'ID del comando (nuevo por preparación)',
    'ID de commande (nouveau à chaque préparation)',
    'コマンド ID（準備ごとに新規）',
    '命令 ID（每次准备使用新值）',
    '指令 ID（每次準備使用新值）',
    'معرف الأمر (جديد لكل إعداد)',
    'ID команды (новый для подготовки)'
  ],
  request: [
    'Request ID',
    'Anfrage-ID',
    'ID de solicitud',
    'ID de requête',
    'リクエスト ID',
    '请求 ID',
    '請求 ID',
    'معرف الطلب',
    'ID запроса'
  ],
  claim: [
    'Claim ID to update',
    'Zu ändernde Aussage-ID',
    'ID de afirmación a actualizar',
    'ID de l’affirmation à modifier',
    '更新する主張 ID',
    '待更新论点 ID',
    '待更新論點 ID',
    'معرف الادعاء المطلوب تحديثه',
    'ID изменяемого утверждения'
  ],
  replacement: [
    'Replacement claim text',
    'Ersatztext der Aussage',
    'Texto de reemplazo',
    'Texte de remplacement',
    '置き換える主張の文',
    '替换论点文本',
    '替換論點文字',
    'النص البديل للادعاء',
    'Новый текст утверждения'
  ],
  section: [
    'Original section heading',
    'Ursprüngliche Abschnittsüberschrift',
    'Encabezado original',
    'Titre de section d’origine',
    '元のセクション見出し',
    '原始章节标题',
    '原始章節標題',
    'عنوان القسم الأصلي',
    'Исходный заголовок раздела'
  ],
  sectionDigest: [
    'Original section SHA-256',
    'SHA-256 des Originalabschnitts',
    'SHA-256 de sección original',
    'SHA-256 de section d’origine',
    '元のセクション SHA-256',
    '原始章节 SHA-256',
    '原始章節 SHA-256',
    'SHA-256 للقسم الأصلي',
    'SHA-256 исходного раздела'
  ],
  original: [
    'Original claim text',
    'Ursprünglicher Aussagetext',
    'Afirmación original',
    'Affirmation d’origine',
    '元の主張の文',
    '原始论点文本',
    '原始論點文字',
    'نص الادعاء الأصلي',
    'Исходный текст утверждения'
  ],
  kind: [
    'Claim kind',
    'Art der Aussage',
    'Tipo de afirmación',
    'Type d’affirmation',
    '主張の種類',
    '论点类型',
    '論點類型',
    'نوع الادعاء',
    'Тип утверждения'
  ],
  citation: [
    'Prior citation source ID (range 0)',
    'Vorherige Zitatquellen-ID (Bereich 0)',
    'ID de fuente anterior (rango 0)',
    'ID de source précédente (plage 0)',
    '以前の引用ソース ID（範囲 0）',
    '先前引文来源 ID（范围 0）',
    '先前引文來源 ID（範圍 0）',
    'معرف مصدر الاستشهاد السابق (النطاق 0)',
    'ID прежнего источника цитаты (диапазон 0)'
  ],
  prepare: [
    'Prepare exact brief refresh',
    'Exakte Briefing-Aktualisierung vorbereiten',
    'Preparar actualización exacta',
    'Préparer l’actualisation exacte',
    '正確なブリーフ更新を準備',
    '准备确切简报刷新',
    '準備確切簡報更新',
    'إعداد تحديث الملخص المحدد',
    'Подготовить точное обновление'
  ],
  review: [
    'I reviewed both exact outputs and their ordered approvals',
    'Ich habe beide exakten Ausgaben und ihre geordneten Freigaben geprüft',
    'Revisé ambos resultados y sus aprobaciones ordenadas',
    'J’ai vérifié les deux résultats et leurs approbations ordonnées',
    '両方の正確な出力と承認順序を確認しました',
    '我已审阅两项确切输出及其有序审批',
    '我已審閱兩項確切輸出及其有序核准',
    'راجعت المخرجين المحددين والموافقات المرتبة',
    'Проверены оба точных результата и порядок согласований'
  ],
  publish: [
    'Publish reviewed brief and manifest',
    'Geprüftes Briefing und Manifest veröffentlichen',
    'Publicar informe y manifiesto revisados',
    'Publier le document et le manifeste vérifiés',
    '確認済みブリーフとマニフェストを公開',
    '发布已审阅简报及清单',
    '發佈已審閱簡報及清單',
    'نشر الملخص والبيان المراجعين',
    'Опубликовать проверенные отчёт и манифест'
  ],
  discard: [
    'Discard prepared command',
    'Vorbereiteten Befehl verwerfen',
    'Descartar comando preparado',
    'Abandonner la commande préparée',
    '準備済みコマンドを破棄',
    '丢弃已准备命令',
    '捨棄已準備指令',
    'تجاهل الأمر المعد',
    'Отменить подготовленную команду'
  ],
  inspect: [
    'Inspect original command',
    'Ursprünglichen Befehl prüfen',
    'Inspeccionar comando original',
    'Inspecter la commande d’origine',
    '元のコマンドを確認',
    '检查原始命令',
    '檢查原始指令',
    'فحص الأمر الأصلي',
    'Проверить исходную команду'
  ],
  invalid: [
    'Review invalidated. Inspect or discard the original command before preparing again.',
    'Prüfung ungültig. Ursprünglichen Befehl vor erneuter Vorbereitung prüfen oder verwerfen.',
    'Revisión invalidada. Inspecciona o descarta el comando original antes de preparar de nuevo.',
    'Vérification invalidée. Inspectez ou abandonnez la commande d’origine avant une nouvelle préparation.',
    '確認が無効になりました。再準備の前に元のコマンドを確認または破棄してください。',
    '审阅已失效。重新准备前请检查或丢弃原始命令。',
    '審閱已失效。重新準備前請檢查或捨棄原始指令。',
    'أصبحت المراجعة غير صالحة. افحص الأمر الأصلي أو تجاهله قبل الإعداد مجدداً.',
    'Проверка недействительна. Перед новой подготовкой проверьте или отмените исходную команду.'
  ],
  lease: [
    'Preparation holds an owned command lease for up to 300 seconds. Closing this panel does not cancel it. Discard waits for a terminal receipt; committed effects are never undone.',
    'Vorbereitung hält eine Befehls-Lease bis zu 300 Sekunden. Schließen bricht sie nicht ab. Verwerfen wartet auf einen Endbeleg; bestätigte Effekte werden nie rückgängig gemacht.',
    'La preparación retiene el comando hasta 300 segundos. Cerrar el panel no lo cancela. Descartar requiere un recibo terminal; no revierte efectos confirmados.',
    'La préparation réserve la commande jusqu’à 300 secondes. Fermer le panneau ne l’annule pas. L’abandon attend un reçu terminal ; les effets validés ne sont pas annulés.',
    '準備は最大300秒コマンドを保持します。パネルを閉じても取り消されません。破棄は終了受領を待ち、確定済みの効果を元に戻しません。',
    '准备会持有命令租约最多 300 秒。关闭面板不会取消。丢弃需等待终态回执；已提交的效果不会撤销。',
    '準備會持有指令租約最多 300 秒。關閉面板不會取消。捨棄需等待終態回執；已提交的效果不會撤銷。',
    'يحتفظ الإعداد بالأمر لمدة تصل إلى 300 ثانية. إغلاق اللوحة لا يلغيه. ينتظر التجاهل إيصالاً نهائياً ولا يعكس الآثار المثبتة.',
    'Подготовка удерживает команду до 300 секунд. Закрытие панели не отменяет её. Отмена ждёт окончательной квитанции и не откатывает выполненные действия.'
  ],
  disconnected: [
    'Connection unavailable. Existing command effects may still be pending.',
    'Verbindung nicht verfügbar. Befehlseffekte können noch ausstehen.',
    'Conexión no disponible. Los efectos del comando pueden seguir pendientes.',
    'Connexion indisponible. Les effets de la commande peuvent rester en attente.',
    '接続できません。既存コマンドの処理が継続している可能性があります。',
    '连接不可用。现有命令效果可能仍待处理。',
    '連線不可用。現有指令效果可能仍待處理。',
    'الاتصال غير متاح. قد تبقى آثار الأمر معلقة.',
    'Соединение недоступно. Действия команды могут ожидать выполнения.'
  ],
  otherScope: [
    'Original command belongs to another connection or session. Return to that scope to inspect or discard it; new preparation is blocked here.',
    'Der ursprüngliche Befehl gehört zu einer anderen Verbindung oder Sitzung. Dorthin zurückkehren, um ihn zu prüfen oder zu verwerfen; neue Vorbereitung ist hier gesperrt.',
    'El comando original pertenece a otra conexión o sesión. Vuelve para inspeccionarlo o descartarlo; la preparación nueva está bloqueada.',
    'La commande d’origine appartient à une autre connexion ou session. Revenez-y pour l’inspecter ou l’abandonner ; nouvelle préparation bloquée.',
    '元のコマンドは別の接続またはセッションに属します。元の場所で確認または破棄してください。新規準備はブロックされています。',
    '原始命令属于另一连接或会话。请返回原范围检查或丢弃；此处禁止新准备。',
    '原始指令屬於另一連線或工作階段。請返回原範圍檢查或捨棄；此處禁止新準備。',
    'الأمر الأصلي يتبع اتصالاً أو جلسة أخرى. عد إليها لفحصه أو تجاهله؛ الإعداد الجديد محظور هنا.',
    'Исходная команда относится к другому соединению или сеансу. Вернитесь для проверки или отмены; новая подготовка заблокирована.'
  ],
  confirm: [
    'Publish these exact outputs?',
    'Diese exakten Ausgaben veröffentlichen?',
    '¿Publicar estos resultados exactos?',
    'Publier ces résultats exacts ?',
    'これらの正確な出力を公開しますか？',
    '发布这些确切输出？',
    '發佈這些確切輸出？',
    'نشر هذه المخرجات المحددة؟',
    'Опубликовать эти точные результаты?'
  ],
  nonAtomic: [
    'The brief publishes first, its dependency manifest second. Publication is not atomic; partial effects can remain. This does not confirm delivery or independent claim verification.',
    'Das Briefing wird zuerst veröffentlicht, dann das Manifest. Veröffentlichung ist nicht atomar; Teileffekte können bleiben. Zustellung und unabhängige Prüfung sind nicht bestätigt.',
    'Primero se publica el informe, después el manifiesto. La publicación no es atómica; pueden quedar efectos parciales. No confirma entrega ni verificación independiente.',
    'Le document est publié avant son manifeste. Publication non atomique ; des effets partiels peuvent subsister. Livraison et vérification indépendante non confirmées.',
    'ブリーフ、依存マニフェストの順に公開します。公開はアトミックでなく、一部だけ確定する場合があります。配信や主張の独立検証は確認されません。',
    '先发布简报，再发布依赖清单。发布不是原子的，可能留下部分效果。不确认交付或独立论点验证。',
    '先發佈簡報，再發佈相依清單。發佈不是原子的，可能留下部分效果。不確認交付或獨立論點驗證。',
    'ينشر الملخص أولاً ثم بيان التبعيات. النشر غير ذري وقد تبقى آثار جزئية. لا يؤكد التسليم أو التحقق المستقل من الادعاءات.',
    'Сначала публикуется отчёт, затем манифест зависимостей. Публикация неатомарна; возможны частичные результаты. Доставка и независимая проверка не подтверждены.'
  ],
  validation: [
    'Validation or runtime status',
    'Validierung oder Laufzeitstatus',
    'Validación o estado',
    'Validation ou état d’exécution',
    '検証または実行状態',
    '验证或运行状态',
    '驗證或執行狀態',
    'التحقق أو حالة التنفيذ',
    'Проверка или состояние выполнения'
  ],
  details: [
    'Exact outputs and approvals',
    'Exakte Ausgaben und Freigaben',
    'Resultados y aprobaciones exactos',
    'Résultats et approbations exacts',
    '正確な出力と承認',
    '确切输出及审批',
    '確切輸出及核准',
    'المخرجات والموافقات المحددة',
    'Точные результаты и согласования'
  ],
  noCoverage: [
    'Check the current source references first. Missing or stale evidence keeps the previous brief unchanged.',
    'Zuerst aktuelle Quellen prüfen. Fehlende oder veraltete Belege lassen das vorherige Briefing unverändert.',
    'Comprueba primero las fuentes actuales. La evidencia ausente o vencida conserva el informe anterior.',
    'Vérifiez d’abord les références actuelles. Les preuves absentes ou périmées préservent le document précédent.',
    'まず現在のソースを確認してください。証拠が欠落または古い場合は以前のブリーフを保持します。',
    '请先检查当前来源。缺失或过期的证据会保留先前简报。',
    '請先檢查目前來源。缺失或過期的證據會保留先前簡報。',
    'افحص المراجع الحالية أولاً. الأدلة المفقودة أو القديمة تبقي الملخص السابق دون تغيير.',
    'Сначала проверьте текущие источники. При устаревших или отсутствующих доказательствах прежний отчёт сохраняется.'
  ]
} satisfies Record<string, readonly [string, string, string, string, string, string, string, string, string]>

export type Copy = Record<keyof typeof messages, string>

export function researchCopy(locale: string): Copy {
  const index = Math.max(0, locales.indexOf(locale as (typeof locales)[number]))

  return Object.fromEntries(Object.entries(messages).map(([key, values]) => [key, values[index]])) as Copy
}
