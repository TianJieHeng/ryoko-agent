import { useLayoutEffect, useState, useSyncExternalStore } from 'react'

import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { ErrorState } from '@/components/ui/error-state'
import { Input } from '@/components/ui/input'
import { SegmentedControl } from '@/components/ui/segmented-control'
import { Textarea } from '@/components/ui/textarea'
import { useI18n } from '@/i18n'

import type { ArtifactProposalResult } from '../../../../shared/src/gateway-contract.generated'
import { type ConnectedSourceInput, type ConnectedSourceReview, ConnectedSourceSession } from '../../../../shared/src/runtime-connected-sources'
import type { RuntimeRequest } from '../../../../shared/src/runtime-control'

import { type ConnectedSourceCopyKey, connectedSourceText } from './connected-source-copy'

export interface ConnectedSourcePanelProps {
  request: RuntimeRequest; sessionId: string; connected: boolean; connectionGeneration?: string | number
  /** Passive IDs, never approval authority. Only unresolved source commands belong here. */
  unresolvedCommandIds?: readonly string[]
}

type Copy = (key: ConnectedSourceCopyKey) => string
const date = (value: number | null | undefined) => value == null ? '—' : new Date(value * 1000).toLocaleString()

export function ConnectedSourcePanel(props: ConnectedSourcePanelProps) {
  const [scope, setScope] = useState({ request: props.request, session: props.sessionId, generation: 0 })

  if (scope.request !== props.request || scope.session !== props.sessionId) {
    setScope({ request: props.request, session: props.sessionId, generation: scope.generation + 1 })

    return null
  }

  return <OwnedConnectedSourcePanel key={scope.generation} {...props} />
}

function OwnedConnectedSourcePanel(props: ConnectedSourcePanelProps) {
  const [session] = useState(() => new ConnectedSourceSession(props.request, props.sessionId))
  const [scope, setScope] = useState({ connected: props.connected, connection: props.connectionGeneration, generation: 0 })

  if (scope.connected !== props.connected || scope.connection !== props.connectionGeneration) {
    setScope({ connected: props.connected, connection: props.connectionGeneration, generation: scope.generation + 1 })

    return null
  }

  return <ConnectedSourceView connected={props.connected} key={scope.generation} pendingJson={JSON.stringify(props.unresolvedCommandIds ?? [])} session={session} />
}

function ConnectedSourceView({ session, connected, pendingJson }: { session: ConnectedSourceSession; connected: boolean; pendingJson: string }) {
  const { locale } = useI18n(), c = connectedSourceText(locale)
  const state = useSyncExternalStore(session.subscribe, session.getState)
  const [confirming, setConfirming] = useState<ConnectedSourceReview | null>(null)
  useLayoutEffect(() => session.attach(connected), [session, connected])
  useLayoutEffect(() => session.setUnresolved(JSON.parse(pendingJson) as string[]), [session, pendingJson])
  const pending = JSON.parse(pendingJson) as string[]
  const disabled = !connected || state.busy
  const locked = disabled || state.attempt?.status === 'unknown' || pending.some(id => id !== state.attempt?.commandId)
  const review = state.review
  const exactConfirmation = confirming && confirming === review && state.originalApproved && (!review.result.projection || state.projectionApproved) ? confirming : null
  const field = (key: keyof ConnectedSourceInput, label: ConnectedSourceCopyKey, maximum = 256) => <label className="grid gap-1 text-xs">{c(label)}<Input disabled={locked} maxLength={maximum} onChange={event => session.edit({ [key]: event.target.value })} value={state.input[key]} /></label>
  const retained = [...new Set([...(state.attempt ? [state.attempt.commandId] : []), ...pending])]

  return <section aria-label={c('title')} className="grid gap-4">
    <h4 className="text-sm font-medium">{c('title')}</h4>
    <p className="text-xs text-muted-foreground">{c('bounds')}</p>
    {!connected && <p role="status">{c('offline')}</p>}
    <form className="grid gap-3" onSubmit={event => { event.preventDefault(); void session.prepare() }}>
      {field('project', 'project')}{field('account', 'account')}
      <SegmentedControl disabled={locked} onChange={kind => session.edit({ kind })} options={[{ id: 'gmail_thread', label: c('gmail') }, { id: 'calendar_availability', label: c('calendar') }]} value={state.input.kind} />
      {state.input.kind === 'gmail_thread' ? <>{field('mailbox', 'mailbox', 320)}{field('thread', 'thread')}</> : <>
        <label className="grid gap-1 text-xs">{c('calendars')}<Textarea disabled={locked} maxLength={5140} onChange={event => session.edit({ calendars: event.target.value })} value={state.input.calendars} /></label>
        {field('timezone', 'timezone', 128)}{field('start', 'start', 64)}{field('end', 'end', 64)}
      </>}
      <Button disabled={locked} size="xs" type="submit" variant="secondary">{state.busy ? c('busy') : c('prepare')}</Button>
    </form>
    {state.result && <section className="grid gap-2">
      <p className="text-xs" role="status">{c('coverage')}: {c(state.result.coverage)}</p>
      <p className="text-xs">{c('observed')}: {date(state.result.observed_at)} · {c('fresh')}: {date(state.result.fresh_until)}</p>
      {state.result.errors.length > 0 && <p className="break-words text-xs">{c('errors')}: {state.result.errors.join(', ')}</p>}
      <Evidence label={c('evidence')} text={state.result.record_json} />
      {state.result.original && !review && state.attempt?.status === 'prepared' && !state.busy && <p className="text-xs">{c('metadataOnly')}</p>}
      {state.result.state === 'published' && <p role="status">{c('published')}</p>}
      {state.result.state === 'partial' && <p role="status">{c('partialPublish')}</p>}
    </section>}
    {review && <section className="grid gap-3">
      <ReviewEvidence c={c} review={review} />
      <label className="flex items-center gap-2 text-xs"><Checkbox checked={state.originalApproved} disabled={locked} onCheckedChange={value => session.approve('original', value === true)} />{c('approveOriginal')}</label>
      {review.result.projection && <label className="flex items-center gap-2 text-xs"><Checkbox checked={state.projectionApproved} disabled={locked} onCheckedChange={value => session.approve('projection', value === true)} />{c('approveProjection')}</label>}
      <Button disabled={locked || !state.originalApproved || !!review.result.projection && !state.projectionApproved} onClick={() => setConfirming(review)} size="xs" variant="secondary">{c('review')}</Button>
    </section>}
    <ConfirmDialog busyLabel={c('busy')} cancelLabel={c('cancel')} confirmLabel={c('publish')} description={c('confirmation')} dismissOnConfirm doneLabel={c('done')} onClose={() => { setConfirming(null); session.dismiss(confirming) }} onConfirm={() => exactConfirmation ? session.publish(exactConfirmation) : undefined} open={!!exactConfirmation && !locked} title={c('review')}>
      {exactConfirmation && <ReviewEvidence c={c} review={exactConfirmation} />}
    </ConfirmDialog>
    {retained.length > 0 && <section className="grid gap-2">
      {(state.attempt?.status === 'unknown' || pending.length > 0) && <p role="status">{c('unknown')}</p>}
      {state.attempt && <p className="break-all text-xs">{state.attempt.projectId} · {state.attempt.requestId} · {state.attempt.preparationId ?? '—'}</p>}
      {retained.map(id => <Button disabled={disabled} key={id} onClick={() => void session.inspect(id)} size="xs" variant="textStrong">{c('inspect')}: {id}</Button>)}
      <p className="text-xs text-muted-foreground">{c('inspectionOnly')}</p>
    </section>}
    {state.inspection && <Evidence label={c('inspect')} text={JSON.stringify({ command_id: state.inspection.command_id, run_id: state.inspection.run_id, status: state.inspection.status, owner_live: state.inspection.owner_live, expires_at: state.inspection.expires_at }, null, 2)} />}
    {state.error && <div role="alert"><ErrorState title={c(state.error)} /></div>}
  </section>
}

function Evidence({ label, text }: { label: string; text: string }) {
  return <div className="grid min-w-0 gap-1"><p className="text-xs font-medium">{label}</p><pre aria-label={label} className="max-h-72 overflow-auto whitespace-pre-wrap break-words text-xs">{text}</pre></div>
}

function Proposal({ c, label, value, text }: { c: Copy; label: string; value: ArtifactProposalResult; text: string }) {
  return <div className="grid min-w-0 gap-2">
    <p className="break-all text-xs">{label}: {value.artifact_id}@{value.version} · {value.size} B · {value.mime}</p>
    <p className="break-all text-xs">SHA-256: {value.sha256} · {value.approval_id} · {value.approval_digest}</p>
    <p className="text-xs">{c('expires')}: {date(value.expires_at)}</p>
    <Evidence label={label} text={text} />
  </div>
}

function ReviewEvidence({ c, review }: { c: Copy; review: ConnectedSourceReview }) {
  return <div className="grid min-w-0 gap-3">
    <p className="break-all text-xs">{review.params.project_id} · {review.params.command_id} · {review.result.preparation_id}</p>
    {review.result.original && review.originalText !== undefined && <Proposal c={c} label={c('original')} text={review.originalText} value={review.result.original} />}
    {review.result.projection && review.projectionText !== undefined && <Proposal c={c} label={c('projection')} text={review.projectionText} value={review.result.projection} />}
  </div>
}
