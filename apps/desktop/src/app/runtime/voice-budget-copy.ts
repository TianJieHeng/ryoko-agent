import { useI18n } from '@/i18n'

import { runtimeUiLocales } from './runtime-ui-copy'
type Words = readonly [string, string, string, string, string, string, string, string, string]
export const voiceBudgetCopy = {
  admit: ['Admit finite speech budget', 'Begrenztes Sprachbudget zulassen', 'Admitir presupuesto de voz limitado', 'Admettre un budget vocal limité', '有限音声予算を受け入れる', '准入有限语音预算', '准入有限語音預算', 'إقرار ميزانية صوت محدودة', 'Допустить ограниченный бюджет речи'],
  explain: ['Strict speech needs an explicit finite account before recording or synthesis. Admission starts no microphone, model or mission and does not reset an existing budget.', 'Strenge Sprachkontrolle benötigt vor Aufnahme oder Synthese ein explizites begrenztes Konto. Zulassung startet weder Mikrofon, Modell noch Auftrag und setzt kein Budget zurück.', 'La voz estricta requiere una cuenta limitada explícita antes de grabar o sintetizar. Admitir no inicia micrófono, modelo o misión ni reinicia presupuestos.', 'La voix stricte exige un compte limité explicite avant enregistrement ou synthèse. L’admission ne démarre ni micro, ni modèle, ni mission et ne réinitialise aucun budget.', '厳格な音声処理には録音・合成前に明示的な有限予算が必要です。予算受付はマイク・モデル・ミッションを開始せず、既存予算をリセットしません。', '严格语音处理需在录音或合成前明确准入有限账户。准入不会启动麦克风、模型或任务，也不会重置现有预算。', '嚴格語音處理需在錄音或合成前明確准入有限帳戶。准入不會啟動麥克風、模型或任務，也不會重設現有預算。', 'يتطلب الصوت الصارم حساباً محدوداً صريحاً قبل التسجيل أو التركيب. الإقرار لا يشغّل الميكروفون أو النموذج أو المهمة ولا يعيد ضبط الميزانية.', 'Для строгого режима нужен явный ограниченный счёт до записи или синтеза. Допуск не включает микрофон, модель или задачу и не сбрасывает бюджет.'],
  unknown: ['Speech outcome unknown. Original request/account IDs are retained. Inspect the budget before further speech; audio is never automatically replayed.', 'Sprachergebnis unbekannt. Ursprüngliche Anfrage-/Konto-IDs bleiben erhalten. Budget vor weiterer Sprache prüfen; Audio wird nie automatisch wiederholt.', 'Resultado de voz desconocido. Se conservan los IDs originales. Revise el presupuesto antes de continuar; nunca se reproduce audio automáticamente.', 'Résultat vocal inconnu. Les ID d’origine sont conservés. Vérifiez le budget avant de continuer ; aucun audio n’est rejoué automatiquement.', '音声結果不明。元のリクエスト・アカウントIDを保持します。次の音声処理前に予算を確認してください。音声は自動再生されません。', '语音结果未知。保留原始请求及账户ID。继续语音前请检查预算；不会自动重放音频。', '語音結果未知。保留原始請求及帳戶ID。繼續語音前請檢查預算；不會自動重播音訊。', 'نتيجة الصوت غير معروفة. حُفظت معرفات الطلب والحساب الأصلية. افحص الميزانية قبل المزيد؛ لا إعادة صوت تلقائية.', 'Исход речи неизвестен. Исходные ID запроса и счёта сохранены. Проверьте бюджет перед продолжением; звук автоматически не повторяется.'],
  receipt: ['Speech admission / usage receipt', 'Sprachzulassung / Nutzungsbeleg', 'Recibo de admisión / uso de voz', 'Reçu d’admission / usage vocal', '音声予算受付・使用記録', '语音准入及用量回执', '語音准入及用量回執', 'إيصال إقرار الصوت واستخدامه', 'Квитанция допуска и использования речи'],
} satisfies Record<string, Words>

export function useVoiceBudgetCopy() {
  const { locale } = useI18n(), index = runtimeUiLocales.indexOf(locale as typeof runtimeUiLocales[number])

  return (key: keyof typeof voiceBudgetCopy) => voiceBudgetCopy[key][index < 0 ? 0 : index]
}
