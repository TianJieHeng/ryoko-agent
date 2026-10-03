import { type ResearchSourceRequest, type ResearchView, RuntimeInputError, validateResearchSources } from '../../../../shared/src/runtime-research'

import type { SourceDraft } from './research-types'

export function sourceRequests(drafts: SourceDraft[]): ResearchSourceRequest[] {
  return validateResearchSources(
    drafts.map(source => ({
      source_id: source.sourceId,
      project_id: source.projectId,
      source_type: source.sourceType,
      version: Number(source.version),
      sha256: source.digest,
      authority: source.authority,
      ...(source.freshUntil ? { fresh_until: Number(source.freshUntil) } : {}),
      ...(source.quote || source.end || source.rangeDigest
        ? {
            evidence_ranges: [
              { start: Number(source.start), end: Number(source.end), quote: source.quote, sha256: source.rangeDigest }
            ]
          }
        : {})
    }))
  )
}

export function assertSourceBinding(view: ResearchView, requested: ResearchSourceRequest[]): void {
  if (
    view.sources.length !== requested.length ||
    view.sources.some((source, index) => {
      const pin = requested[index]

      return (
        source.sourceId !== pin.source_id ||
        source.projectId !== pin.project_id ||
        source.version !== pin.version ||
        source.sha256 !== pin.sha256 ||
        source.ranges.some(
          range =>
            !pin.evidence_ranges?.some(
              expected =>
                range.start === expected.start && range.end === expected.end && range.sha256 === expected.sha256
            )
        )
      )
    })
  ) {
    throw new RuntimeInputError('Source evidence differs from the exact selected references')
  }
}
