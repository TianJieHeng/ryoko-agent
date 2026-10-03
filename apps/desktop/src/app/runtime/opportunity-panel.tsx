import { useEffect, useRef, useState, useSyncExternalStore } from 'react'

import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { Textarea } from '@/components/ui/textarea'

import type { OpportunityCandidate } from '../../../../shared/src/gateway-contract.generated'
import type { RuntimeRequest } from '../../../../shared/src/runtime-control'
import { OpportunityReviewSession } from '../../../../shared/src/runtime-opportunities'
import { controlId } from '../../../../shared/src/runtime-research'

import { useOpportunityCopy } from './opportunity-copy'

type Choice = 'saved' | 'dismissed' | 'accepted'
interface Props {
  request: RuntimeRequest; sessionId: string; connected: boolean; connectionGeneration?: number
  onOpenReview?: (candidate: OpportunityCandidate) => void
  unresolvedRequestIds?: readonly string[]
}
interface Binding { request: RuntimeRequest; sessionId: string; generation?: number; connected: boolean; selection: string; controller: OpportunityReviewSession }

export function OpportunityPanel(props: Props) {
  const copy = useOpportunityCopy()
  const [projects, setProjects] = useState(''), [error, setError] = useState(false)
  const [binding, setBinding] = useState<Binding | null>(null)
  const current = useRef(binding)
  current.current = binding
  const valid = binding && binding.request === props.request && binding.sessionId === props.sessionId && binding.generation === props.connectionGeneration && binding.connected === props.connected && binding.selection === projects

  if (binding && !valid) { binding.controller.close(); current.current = null }
  useEffect(() => () => { current.current?.controller.close() }, [])

  function select() {
    try {
      const ids = projects.split('\n').map(value => controlId(value.trim()))
      const controller = new OpportunityReviewSession(props.request, props.sessionId, ids)
      binding?.controller.close()
      setBinding({ request: props.request, sessionId: props.sessionId, generation: props.connectionGeneration, connected: props.connected, selection: projects, controller }); setError(false)
    } catch { setError(true) }
  }

  return <section className="grid gap-3"><h4 className="text-sm font-medium">{copy('title')}</h4><p className="text-xs">{copy('limits')}</p>
    <label>{copy('scope')}<Textarea onChange={event => setProjects(event.target.value)} value={projects} /></label>
    <Button disabled={!props.connected} onClick={select} size="xs" variant="secondary">{copy('open')}</Button>
    {error && <p role="alert">{copy('invalid')}</p>}
    {valid && <OpportunityBody {...props} controller={binding.controller} />}
  </section>
}

function OpportunityBody({ controller, connected, onOpenReview, unresolvedRequestIds = [] }: Props & { controller: OpportunityReviewSession }) {
  const copy = useOpportunityCopy()
  const state = useSyncExternalStore(controller.subscribe, controller.getState, controller.getState)
  const [review, setReview] = useState<{ candidate: OpportunityCandidate; choice: Choice; controller: OpportunityReviewSession } | null>(null)
  const blocked = !connected || state.busy || !!state.pending || unresolvedRequestIds.length > 0
  const exactReview = review?.controller === controller && state.candidates.includes(review.candidate) ? review : null
  useEffect(() => () => controller.close(), [controller])

  return <div className="grid gap-3">
    <div className="flex flex-wrap gap-2"><Button disabled={!connected || state.busy} onClick={() => void controller.load()} size="xs" variant="secondary">{copy('retained')}</Button><Button disabled={blocked} onClick={() => void controller.load(true)} size="xs" variant="secondary">{copy('discover')}</Button></div>
    {state.scan && <details><summary>{copy('limits')}</summary><pre className="max-h-48 overflow-auto whitespace-pre-wrap break-words text-xs">{JSON.stringify({ complete: state.scan.complete, limit: state.scan.limit, result_limit_reached: state.scan.result_limit_reached, ...('scanned' in state.scan ? { scanned: state.scan.scanned, suppressed_count: state.scan.suppressed_count } : {}) }, null, 2)}</pre></details>}
    {state.scan && !state.candidates.length && <p>{copy('empty')}</p>}
    {state.candidates.map(candidate => <article className="grid gap-2 rounded border p-3 text-xs" key={candidate.candidate_id}>
      <h5 className="font-medium">{candidate.title}</h5><p className="break-all">{candidate.project_id} · {candidate.candidate_id} · {candidate.disposition} · {candidate.revision}</p>
      <p>{copy('benefit')}: {candidate.benefit}</p><p>{copy('effort')}: {candidate.effort}</p><p>{candidate.confidence.explanation}</p>
      {!candidate.evidence_current && <p role="status">{copy('stale')} · {candidate.changed_source_reason}</p>}
      <details><summary>{copy('evidence')}</summary><pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words">{JSON.stringify({ references: candidate.evidence_refs, digest: candidate.evidence_digest, action: candidate.suggested_action }, null, 2)}</pre></details>
      <div className="flex flex-wrap gap-2">{(['saved', 'dismissed', 'accepted'] as const).map(choice => <Button disabled={blocked || choice !== 'dismissed' && !candidate.evidence_current} key={choice} onClick={() => setReview({ candidate, choice, controller })} size="xs" variant="secondary">{copy(choice === 'saved' ? 'save' : choice === 'dismissed' ? 'dismiss' : 'accept')}</Button>)}<Button disabled={!connected || state.busy} onClick={() => void controller.inspect(candidate)} size="xs" variant="text">{copy('history')}</Button>
      {candidate.disposition === 'accepted' && candidate.evidence_current && onOpenReview && <Button disabled={!connected || state.busy} onClick={() => onOpenReview(candidate)} size="xs" variant="secondary">{copy('next')}</Button>}</div>
    </article>)}
    {state.history && <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words text-xs">{JSON.stringify(state.history, null, 2)}</pre>}
    {state.pending && <p className="break-all text-xs" role="status">{copy(state.busy ? 'pending' : 'unknown')} · {state.pending.requestId} · {state.pending.candidateId}</p>}
    {!!unresolvedRequestIds.length && <p className="break-all text-xs" role="status">{copy('unknown')} · {unresolvedRequestIds.join(', ')}</p>}
    {state.error && <p role="alert">{copy(state.error)}</p>}
    <ConfirmDialog confirmLabel={copy('confirm')} description={`${copy('noEffect')}\n${exactReview?.choice ?? ''} · ${exactReview?.candidate.title ?? ''}\n${exactReview?.candidate.evidence_digest ?? ''}`} onClose={() => setReview(null)} onConfirm={async () => { if (!exactReview || blocked) { return } const exact = exactReview; setReview(null); await controller.choose(exact.candidate, exact.choice) }} open={!!exactReview && !blocked} title={copy('review')} />
  </div>
}
