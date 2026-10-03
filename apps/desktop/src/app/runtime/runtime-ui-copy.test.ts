import { cleanup, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  runtimeUiCatalogs,
  runtimeUiFormat,
  runtimeUiKeys,
  runtimeUiLocales,
  runtimeUiTemplateKeys,
  runtimeUiText,
  useRuntimeUiFormat,
  useRuntimeUiText
} from './runtime-ui-copy'

const selection = vi.hoisted(() => ({ locale: 'en' }))
vi.mock('@/i18n', () => ({ useI18n: () => selection }))

afterEach(() => { cleanup(); selection.locale = 'en' })

function placeholders(text: string): string[] {
  return [...text.matchAll(/\{([a-zA-Z][a-zA-Z0-9]*)\}/g)].map(match => match[1]).sort()
}

describe('finite runtime chrome catalogs', () => {
  it('covers each bundled locale with exactly the same nonempty English keys', () => {
    expect(runtimeUiLocales).toEqual(['en', 'de', 'es', 'fr', 'ja', 'zh', 'zh-hant', 'ar', 'ru'])
    expect(Object.keys(runtimeUiCatalogs).sort()).toEqual([...runtimeUiLocales].sort())
    expect(runtimeUiKeys.length).toBeGreaterThan(0)
    expect(new Set(runtimeUiKeys).size).toBe(runtimeUiKeys.length)

    for (const locale of runtimeUiLocales) {
      const catalog = runtimeUiCatalogs[locale]
      expect(Object.keys(catalog).sort()).toEqual([...runtimeUiKeys].sort())

      for (const key of runtimeUiKeys) {
        expect(catalog[key].trim(), `${locale}: ${key}`).not.toBe('')
        expect(runtimeUiText(locale, key)).toBe(catalog[key])
        expect(runtimeUiText('en', key)).toBe(key)
      }

      if (locale !== 'en') {
        expect(runtimeUiText(locale, 'Request cancellation')).not.toBe('Request cancellation')
        expect(runtimeUiText(locale, 'Approve and publish this exact version')).not.toBe('Approve and publish this exact version')
        expect(runtimeUiText(locale, '. This never means a person read the notice.')).not.toBe('. This never means a person read the notice.')
      }
    }
  })

  it('keeps the catalogs immutable', () => {
    expect(Object.isFrozen(runtimeUiCatalogs)).toBe(true)
    expect(Object.isFrozen(runtimeUiKeys)).toBe(true)

    for (const locale of runtimeUiLocales) {expect(Object.isFrozen(runtimeUiCatalogs[locale])).toBe(true)}
  })

  it('falls back verbatim for an unknown locale, including inherited property names', () => {
    for (const locale of ['', 'xx', 'en-US', 'DE', '__proto__', 'constructor', 'toString']) {
      expect(runtimeUiText(locale, 'Project ID')).toBe('Project ID')
    }
  })

  it('never normalizes or partially translates unknown keys, identifiers or content', () => {
    const exactValues = [
      '', ' Project ID ', 'project id', 'Project ID: user-authored content',
      'project-42/artifact-7@3', 'runtime.artifact.publish',
      '{"label":"Project ID","status":"received"}',
      '<script>Request cancellation</script>', '__proto__', 'constructor', 'toString',
      'Unknown result\n受領済み وليس مقروءًا', 'Approved by Alice {revision}'
    ]

    for (const locale of runtimeUiLocales) {
      for (const value of exactValues) {expect(runtimeUiText(locale, value)).toBe(value)}
    }
  })

  it('preserves prepared, receipt, stale and cancellation distinctions', () => {
    expect(runtimeUiText('de', 'Prepared for review only; no version published')).toBe('Nur zur Prüfung vorbereitet; keine Version veröffentlicht')
    expect(runtimeUiText('ja', '. This never means a person read the notice.')).toBe('。これは人が通知を読んだことを意味するものではありません。')
    expect(runtimeUiText('zh', 'Connection is stale. Accepted work may still be running; controls are disabled.')).toBe('连接状态已过期。已接受工作可能仍在运行；控制已禁用。')
    expect(runtimeUiText('fr', 'Captured audio discarded. Accepted mission work was not cancelled.')).toBe('Audio enregistré supprimé. Le travail de mission accepté n’a pas été annulé.')
  })

  it('localizes the bounded-source, isolated-context and explicit-grant disclosures in every locale', () => {
    const disclosures = [
      'Inspect pending recovery references in existing sessions before opening another runtime view.',
      'Local UTF-8 or supplied-text processing only. Search uses lexical overlap and limited spelling similarity; no semantic understanding, OCR or image analysis. Results cover a bounded scan, never the entire inbox.',
      'Ryoko’s personal context harness is opaque and read-only here. There is no verified record mutation contract; built-in memory controls do not apply to it.',
      'Created paused. A separate finite draft-production grant and explicit activation are required.',
      'Fresh conversation roster. Last-started tools do not prove current activity; missing children do not prove completion.',
      'No remote device substitution. A prepared form is not a confirmed browser effect. Focused checks do not prove a full test suite, commit, merge or deployment. Closing this panel never cancels accepted work.'
    ]

    for (const locale of runtimeUiLocales) {
      for (const disclosure of disclosures) {
        expect(runtimeUiKeys).toContain(disclosure)
        const translated = runtimeUiText(locale, disclosure)
        expect(translated.trim()).not.toBe('')

        if (locale !== 'en') {expect(translated).not.toBe(disclosure)}
      }
    }
  })

  it('retains protocol markers and numeric limits inside translated explanatory chrome', () => {
    const coverage = 'Coverage: fresh_memory_context_only. These references were supplied to a provider call. They are not a causal explanation. Historical system/transcript context and arbitrary tool results are not exhaustively listed. No references does not mean no other context was used.'
    const readonly = 'Read-only. This cannot activate LAYA, change per-channel policy, reduce an approval floor or send private packets. Feedback/override APIs and production evaluation gates remain unavailable.'

    for (const locale of runtimeUiLocales) {
      expect(runtimeUiText(locale, coverage)).toContain('fresh_memory_context_only')
      expect(runtimeUiText(locale, 'Assets: lineage_only (')).toContain('lineage_only')
      expect(runtimeUiText(locale, '· store artifact_templates')).toContain('artifact_templates')
      expect(runtimeUiText(locale, 'matches · complete: false · truncated:')).toContain('false')
      expect(runtimeUiText(locale, 'Grant expiry must be future, within 30 days and no later than the schedule expiry')).toContain('30')
      expect(runtimeUiText(locale, readonly)).toContain('LAYA')
    }
  })

  it('does not treat dynamic receipts as translated chrome even when they contain known fragments', () => {
    const receipts = [
      'Run run-42 · backend personal_mcp · historical · receipt available',
      'Schedule schedule-42 v1: paused; revision 7',
      'Source: project-4/artifact-9@3 · Scope unknown',
      'artifact_templates · template-1@2 · SHA-256 abc123'
    ]

    for (const locale of runtimeUiLocales) {
      for (const receipt of receipts) {
        expect(runtimeUiText(locale, receipt)).toBe(receipt)
        expect(runtimeUiFormat(locale, receipt, { index: 7 })).toBe(receipt)
      }
    }
  })
})

describe('explicit authored templates', () => {
  it('retains identical placeholder names and occurrences in every locale', () => {
    expect(new Set(runtimeUiTemplateKeys).size).toBe(runtimeUiTemplateKeys.length)

    for (const template of runtimeUiTemplateKeys) {
      expect(runtimeUiKeys).toContain(template)
      expect(placeholders(template).length).toBeGreaterThan(0)

      for (const locale of runtimeUiLocales) {
        expect(placeholders(runtimeUiText(locale, template)), `${locale}: ${template}`).toEqual(placeholders(template))
      }
    }
  })

  it('substitutes only explicit values into known templates, allowing translated word order', () => {
    expect(runtimeUiText('ja', 'Day {index} start ISO with offset')).toBe('{index} 日目の開始（オフセット付き ISO）')
    expect(runtimeUiFormat('ja', 'Day {index} start ISO with offset', { index: 2 })).toBe('2 日目の開始（オフセット付き ISO）')
    expect(runtimeUiFormat('xx', 'Work {index} ID', { index: 7 })).toBe('Work 7 ID')
    expect(runtimeUiFormat('de', 'Work {index} ID', { index: 0 })).toBe('Arbeits-ID 0')
  })

  it('never recursively interpolates, translates or treats a value as replacement syntax', () => {
    const template = 'Exact text: {text}. Submit once to this owned mission at revision {revision}. Check names, numbers and intended action; admission is not completion.'
    const text = 'Project ID {$&} $1 $` {revision} <b>原文</b>\nuser/content@4'
    const result = runtimeUiFormat('de', template, { text, revision: 42 })
    expect(result).toContain(`Exakter Text: ${text}.`)
    expect(result).toContain('Revision 42')
    expect(result).toContain('{revision}')
    expect(runtimeUiFormat('en', template, { text: '', revision: 0 })).toContain('Exact text: . Submit once to this owned mission at revision 0.')
  })

  it('leaves missing placeholders visible and ignores inherited or extra values', () => {
    expect(runtimeUiFormat('en', 'Work {index} ID', {})).toBe('Work {index} ID')
    expect(runtimeUiFormat('en', 'Work {index} ID', Object.create({ index: 7 }) as Record<string, number>)).toBe('Work {index} ID')
    expect(runtimeUiFormat('en', 'Work {index} ID', { index: 3, unused: 'ignored' })).toBe('Work 3 ID')
  })

  it('does not interpolate unknown prose or ordinary static keys', () => {
    const content = 'User content {index}: Request cancellation'
    expect(runtimeUiFormat('de', content, { index: 3 })).toBe(content)
    expect(runtimeUiFormat('de', 'Project ID', { index: 3 })).toBe(runtimeUiText('de', 'Project ID'))
  })
})

describe('existing locale authority', () => {
  it('uses useI18n selection and updates text when that selection changes', () => {
    const hook = renderHook(() => useRuntimeUiText())
    const initial = hook.result.current
    expect(initial('Project ID')).toBe('Project ID')
    hook.rerender()
    expect(hook.result.current).toBe(initial)
    selection.locale = 'ja'
    hook.rerender()
    expect(hook.result.current('Project ID')).toBe('プロジェクト ID')
    selection.locale = 'unsupported'
    hook.rerender()
    expect(hook.result.current('Project ID')).toBe('Project ID')
  })

  it('uses the same selection for explicit templates without changing argument values', () => {
    selection.locale = 'zh-hant'
    const hook = renderHook(() => useRuntimeUiFormat())
    expect(hook.result.current('Work {index} duration minutes', { index: 'A-01' })).toBe('工作 A-01 時長（分鐘）')
    selection.locale = 'ru'
    hook.rerender()
    expect(hook.result.current('Work {index} duration minutes', { index: 'A-01' })).toBe('Длительность работы A-01 в минутах')
  })
})
