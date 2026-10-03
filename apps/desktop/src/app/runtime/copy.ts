/** Small feature-local chrome; backend status/reasons retain their original wording. */
const copy = {
  en: ['Runtime & memory', 'Refresh checks', 'Retry same command', 'Open a conversation to inspect its bound runtime'],
  de: ['Laufzeit & Gedächtnis', 'Prüfungen aktualisieren', 'Denselben Befehl wiederholen', 'Eine Unterhaltung öffnen, um ihre Laufzeit zu prüfen'],
  es: ['Entorno y memoria', 'Actualizar comprobaciones', 'Reintentar el mismo comando', 'Abre una conversación para revisar su entorno'],
  fr: ['Exécution et mémoire', 'Actualiser les vérifications', 'Réessayer la même commande', 'Ouvrez une conversation pour inspecter son environnement'],
  ja: ['ランタイムとメモリ', '状態を再確認', '同じコマンドを再試行', '会話を開いてランタイムを確認してください'],
  zh: ['运行时与记忆', '重新检查', '重试同一命令', '打开对话以检查其绑定的运行时'],
  'zh-hant': ['執行環境與記憶', '重新檢查', '重試同一指令', '開啟對話以檢查其綁定的執行環境'],
  ar: ['بيئة التشغيل والذاكرة', 'تحديث الفحوصات', 'إعادة محاولة الأمر نفسه', 'افتح محادثة لفحص بيئة التشغيل المرتبطة بها'],
  ru: ['Среда выполнения и память', 'Обновить проверки', 'Повторить ту же команду', 'Откройте разговор для проверки его среды выполнения']
} as const

export function runtimeCopy(locale: string) {
  const [title, refresh, retry, noSession] = copy[locale as keyof typeof copy] ?? copy.en

  return { title, refresh, retry, noSession }
}
