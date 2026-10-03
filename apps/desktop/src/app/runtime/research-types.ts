import type { ArtifactProposalResult, BriefPrepareParams } from '../../../../shared/src/gateway-contract.generated'
import type { RuntimeRequest } from '../../../../shared/src/runtime-control'
import type { ResearchSourceRequest } from '../../../../shared/src/runtime-research'

export interface Props {
  request: RuntimeRequest
  sessionId: string
  connected: boolean
}
export interface SourceDraft {
  sourceId: string
  projectId: string
  sourceType: ResearchSourceRequest['source_type']
  version: string
  digest: string
  authority: NonNullable<ResearchSourceRequest['authority']>
  start: string
  end: string
  quote: string
  rangeDigest: string
  freshUntil: string
}

export const emptySource = (): SourceDraft => ({
  sourceId: '',
  projectId: '',
  sourceType: 'project_artifact',
  version: '1',
  digest: '',
  authority: 'source_claim',
  start: '0',
  end: '',
  quote: '',
  rangeDigest: '',
  freshUntil: ''
})

export interface BriefDraft {
  mode: 'refresh' | 'initial'
  project: string
  artifact: string
  parent: string
  manifest: string
  manifestVersion: string
  manifestDigest: string
  command: string
  requestId: string
  claim: string
  replacement: string
  section: string
  sectionDigest: string
  original: string
  kind: 'fact' | 'interpretation'
  citation: string
}
export type OwnedBrief = Omit<BriefPrepareParams, 'session_id' | 'schema_version'>
export interface RetainedCommand {
  id: string
  request: RuntimeRequest
  sessionId: string
  revision: number
  terminal: boolean
  valid: boolean
  discardRequested: boolean
  prepared?: { brief: ArtifactProposalResult; manifest: ArtifactProposalResult; summary: string }
  input: OwnedBrief | string
  mode: BriefDraft['mode']
  sources: ResearchSourceRequest[]
}
export const terminalStatuses = new Set(['completed', 'cancelled', 'failed', 'blocked'])


export interface Preparing { id: string; request: RuntimeRequest; sessionId: string; dispatched: boolean; failedBeforeDispatch: boolean }
