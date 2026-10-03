import { expect, it } from 'vitest'

import { markdownSections } from './runtime-markdown.js'

it('ignores fenced headings and rejects ambiguous anchors while preserving exact nested section bytes', () => {
  const text = '# Main\nα\n## Child\nβ\n```md\n# Fake\n```\n# Next\nx\n## Duplicate\na\n## Duplicate\nb\n'
  const sections = markdownSections(text)
  expect(sections.find(item => item.anchor === 'Main')?.content).toBe('# Main\nα\n## Child\nβ\n```md\n# Fake\n```\n')
  expect(sections.map(item => item.anchor)).not.toContain('Fake')
  expect(sections.map(item => item.anchor)).not.toContain('Duplicate')
})
