import type { RuntimeRequest } from '@hermes/shared/runtime-control'
import { RuntimeFeatureSession } from '@hermes/shared/runtime-feature-session'
import { runtimeFeatures } from '@hermes/shared/runtime-features'
import { useEffect, useMemo, useState, useSyncExternalStore } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { useI18n } from '@/i18n'

import type { OpportunityCandidate } from '../../../../shared/src/gateway-contract.generated'

import { AgendaPanel } from './agenda-panel'
import { ArtifactDownload } from './artifact-download'
import { ArtifactWorkbench } from './artifact-workbench'
import { BranchMergePanel } from './branch-merge-panel'
import { CaptureReviewPanel } from './capture-review-panel'
import { ConnectedSourcePanel } from './connected-source-panel'
import { DecisionPanel } from './decision-panel'
import { DeliveryPanel } from './delivery-panel'
import { DomainPanel } from './domain-panel'
import { ExecutionPanel } from './execution-panel'
import { FollowthroughPanel } from './followthrough-panel'
import { MissionPanel } from './mission-panel'
import { MonitorCreatePanel } from './monitor-create-panel'
import { MonitorPolicyPanel } from './monitor-policy-panel'
import { NamedSpecialistPanel } from './named-specialist-panel'
import { OperatorRepairPanel } from './operator-repair-panel'
import { OpportunityPanel } from './opportunity-panel'
import { OutputInfluencePanel } from './output-influence-panel'
import type { RecoveryReference } from './owned-runtime-scope'
import { ResearchPanel } from './research-panel'
import { useRuntimeUiText } from './runtime-ui-copy'
import { ScheduledDraftPanel } from './scheduled-draft-panel'
import { SpecialistPanel } from './specialist-panel'
import { StatusPanel } from './status-panel'
import { TemplateApplicationPanel } from './template-application-panel'
import { TemplatePanel } from './template-panel'
import { VoicePanel } from './voice-panel'
import { WorkflowPanel } from './workflow-panel'

interface RuntimeFeaturePanelProps {
  request: RuntimeRequest
  sessionId: string
  connected: boolean
  connectionGeneration?: number
  recoveryReferences?: readonly RecoveryReference[]
}

export function RuntimeFeaturePanel({ request, sessionId, connected, connectionGeneration, recoveryReferences = [] }: RuntimeFeaturePanelProps) {
  const rt = useRuntimeUiText()
  const { t } = useI18n()
  const session = useMemo(() => new RuntimeFeatureSession(request, sessionId), [request, sessionId])
  const state = useSyncExternalStore(session.subscribe, session.getState, session.getState)
  const [selected, setSelected] = useState('project')
  const [argument, setArgument] = useState('help')
  const [targetBinding, setReviewTarget] = useState<{ candidate: OpportunityCandidate; request: RuntimeRequest; sessionId: string; generation?: number } | null>(null)
  const reviewTarget = targetBinding?.request === request && targetBinding.sessionId === sessionId && targetBinding.generation === connectionGeneration ? targetBinding.candidate : null
  const [visited, setVisited] = useState<string[]>([])
  const feature = runtimeFeatures[selected]
  useEffect(() => () => session.close(), [session])

  return (
    <div className="grid gap-3">
      {reviewTarget && <aside className="rounded border p-2 text-xs"><p>{reviewTarget.title}</p><p>{reviewTarget.suggested_action.description}</p><pre className="whitespace-pre-wrap break-words">{JSON.stringify({ project_id: reviewTarget.project_id, target_id: reviewTarget.suggested_action.target_id, target_version: reviewTarget.suggested_action.target_version, evidence_digest: reviewTarget.evidence_digest }, null, 2)}</pre></aside>}
      <div aria-label={rt("Runtime feature")} className="flex flex-wrap gap-1" role="group">
        {Object.entries(runtimeFeatures).map(([id, item]) => (
          <Button aria-pressed={selected === id} disabled={state.busy} key={id} onClick={() => { session.close(); setSelected(id); setVisited(previous => previous.includes(id) ? previous : [...previous, id]); setArgument('help') }} size="xs" variant={selected === id ? 'secondary' : 'ghost'}>{item.label}</Button>
        ))}
      </div>
      {visited.includes('artifact') && <div hidden={selected !== 'artifact'}><ArtifactWorkbench connected={connected} request={request} sessionId={sessionId} /><ArtifactDownload connected={connected} request={request} sessionId={sessionId} /><BranchMergePanel connected={connected} request={request} sessionId={sessionId} /></div>}
      {visited.includes('template') && <div hidden={selected !== 'template'}><TemplatePanel connected={connected} request={request} sessionId={sessionId} /><TemplateApplicationPanel connected={connected} request={request} sessionId={sessionId} /></div>}
      {visited.includes('mission') && <div hidden={selected !== 'mission'}><MissionPanel connected={connected} request={request} sessionId={sessionId} /></div>}
      {visited.includes('delivery') && <div hidden={selected !== 'delivery'}><DeliveryPanel connected={connected} request={request} sessionId={sessionId} /></div>}
      {visited.includes('research') && <div hidden={selected !== 'research'}><ResearchPanel connected={connected} request={request} sessionId={sessionId} /></div>}
      {visited.some(id => id === 'research' || id === 'commitment' || id === 'correspondence') && <div hidden={!['research', 'commitment', 'correspondence'].includes(selected)}><ConnectedSourcePanel connected={connected} connectionGeneration={connectionGeneration} request={request} sessionId={sessionId} unresolvedCommandIds={recoveryReferences.filter(ref => ref.kind === 'artifact' && ref.unknown && ref.method.startsWith('runtime.sources.')).map(ref => ref.id)} /></div>}
      {visited.some(id => id === 'workflow' || id === 'schedule') && <div hidden={!['workflow', 'schedule'].includes(selected)}><WorkflowPanel connected={connected} request={request} sessionId={sessionId} /></div>}
      {visited.includes('domain') && <div hidden={selected !== 'domain'}><DomainPanel connected={connected} request={request} sessionId={sessionId} /></div>}
      {visited.some(id => id === 'commitment' || id === 'correspondence') && <div hidden={!['commitment', 'correspondence', 'execution', 'specialist', 'overview', 'decision', 'memory', 'capture'].includes(selected)}><FollowthroughPanel connected={connected} request={request} sessionId={sessionId} /><AgendaPanel connected={connected} request={request} sessionId={sessionId} /></div>}
      {visited.includes('execution') && <div hidden={selected !== 'execution'}><ExecutionPanel connected={connected} request={request} sessionId={sessionId} /></div>}
      {visited.includes('specialist') && <div hidden={selected !== 'specialist'}><SpecialistPanel connected={connected} request={request} sessionId={sessionId} /><NamedSpecialistPanel connected={connected} connectionGeneration={connectionGeneration} request={request} sessionId={sessionId} unresolvedCommandIds={recoveryReferences.filter(ref => ref.kind === 'specialist' && ref.unknown).map(ref => ref.id)} /><VoicePanel connected={connected && selected === 'specialist'} request={request} sessionId={sessionId} unresolvedSpeechIds={recoveryReferences.filter(ref => ref.kind === 'speech' && ref.unknown).map(ref => ref.id)} /></div>}
      {visited.includes('overview') && <div hidden={selected !== 'overview'}><StatusPanel connected={connected} request={request} sessionId={sessionId} /><OperatorRepairPanel connected={connected} connectionGeneration={connectionGeneration} request={request} sessionId={sessionId} unresolvedOperationIds={recoveryReferences.filter(ref => ref.kind === 'operator' && ref.unknown).map(ref => ref.id)} /><OpportunityPanel connected={connected} connectionGeneration={connectionGeneration} onOpenReview={candidate => { const next = candidate.suggested_action.kind === 'review_artifact' ? 'artifact' : candidate.suggested_action.kind === 'review_commitment' ? 'commitment' : 'workflow'; setReviewTarget({ candidate, request, sessionId, generation: connectionGeneration }); setSelected(next); setVisited(previous => previous.includes(next) ? previous : [...previous, next]); setArgument('help'); }} request={request} sessionId={sessionId} unresolvedRequestIds={recoveryReferences.filter(ref => ref.kind === 'opportunity' && ref.unknown).map(ref => ref.id)} /></div>}
      {visited.includes('decision') && <div hidden={selected !== 'decision'}><DecisionPanel connected={connected} request={request} sessionId={sessionId} /></div>}
      {visited.includes('schedule') && <div hidden={selected !== 'schedule'}><MonitorCreatePanel connected={connected} request={request} sessionId={sessionId} /><MonitorPolicyPanel connected={connected} request={request} sessionId={sessionId} /></div>}
      {visited.includes('memory') && <div hidden={selected !== 'memory'}><OutputInfluencePanel connected={connected} request={request} sessionId={sessionId} /></div>}
      {visited.includes('capture') && <div hidden={selected !== 'capture'}><CaptureReviewPanel connected={connected} request={request} sessionId={sessionId} /></div>}
      {visited.some(id => id === 'workflow' || id === 'schedule') && <div hidden={!['workflow', 'schedule'].includes(selected)}><ScheduledDraftPanel connected={connected} request={request} sessionId={sessionId} /></div>}
      <details open={!['artifact', 'template', 'mission', 'delivery', 'research', 'workflow', 'schedule', 'domain', 'commitment', 'correspondence', 'execution', 'specialist', 'overview', 'decision', 'memory', 'capture'].includes(selected)}><summary>{rt("Advanced command inspector")}</summary>
      <form className="grid gap-2" onSubmit={event => { event.preventDefault();

 if (connected) {void session.run(feature, argument)} }}>
        <label className="text-sm" htmlFor="runtime-feature-command">{feature.label}</label>
        <p className="break-words text-xs text-muted-foreground" id="runtime-feature-help">{feature.help}</p>
        <Input aria-describedby="runtime-feature-help" disabled={state.busy || !connected} id="runtime-feature-command" onChange={event => setArgument(event.target.value)} value={argument} />
        <div className="flex gap-2"><Button disabled={state.busy || !connected} size="xs" type="submit" variant="secondary">{rt("Run selected command")}</Button>
          <Button disabled={state.busy} onClick={() => { session.close(); setArgument('help') }} size="xs" type="button" variant="text">{t.common.close}</Button></div>
      </form></details>
      <p className="text-xs text-muted-foreground">{rt("Changes are submitted only by this command. Closing this inspector does not cancel accepted work. Unknown results are never automatically retried.")}</p>
      <div aria-busy={state.busy} aria-live="polite" className="whitespace-pre-wrap break-words text-sm">
        {state.error ? <p role="alert">{state.error}</p> : state.output}
      </div>
    </div>
  )
}
