import { useLayoutEffect, useState, useSyncExternalStore } from 'react'

import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorState } from '@/components/ui/error-state'
import { Input } from '@/components/ui/input'
import { Loader } from '@/components/ui/loader'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { useI18n } from '@/i18n'

import type { SpecialistDescriptor, SpecialistReference, SpecialistStatus } from '../../../../shared/src/gateway-contract.generated'
import type { RuntimeRequest } from '../../../../shared/src/runtime-control'
import { type NamedSpecialistReview, NamedSpecialistSession, type SpecialistReferenceDraft } from '../../../../shared/src/runtime-named-specialists'

import { namedSpecialistCopy, type NamedSpecialistCopyKey, namedSpecialistLocales, type NamedSpecialistText } from './named-specialist-copy'

export interface NamedSpecialistPanelProps {
  request: RuntimeRequest
  sessionId: string
  connected: boolean
  connectionGeneration?: string | number
  /** Recovery identifiers from the owning session cache, never reusable approval authority. */
  unresolvedCommandIds?: readonly string[]
}

export function NamedSpecialistPanel(props: NamedSpecialistPanelProps) {
  const [scope, setScope] = useState({ request: props.request, sessionId: props.sessionId, generation: 0 })

  if (scope.request !== props.request || scope.sessionId !== props.sessionId) {
    setScope({ request: props.request, sessionId: props.sessionId, generation: scope.generation + 1 })

    return null
  }

  return <OwnedSpecialistPanel key={scope.generation} {...props} />
}

function OwnedSpecialistPanel(props: NamedSpecialistPanelProps) {
  const [session] = useState(() => new NamedSpecialistSession(props.request, props.sessionId))
  const [scope, setScope] = useState({ connected: props.connected, connection: props.connectionGeneration, generation: 0 })

  if (scope.connected !== props.connected || scope.connection !== props.connectionGeneration) {
    setScope({ connected: props.connected, connection: props.connectionGeneration, generation: scope.generation + 1 })

    return null
  }

  return <SpecialistPanelView connected={props.connected} key={scope.generation} pendingJson={JSON.stringify(props.unresolvedCommandIds ?? [])} session={session} />
}

function SpecialistPanelView({ connected, pendingJson, session }: { connected: boolean; pendingJson: string; session: NamedSpecialistSession }) {
  const { locale } = useI18n()
  const index = Math.max(0, namedSpecialistLocales.indexOf(locale as typeof namedSpecialistLocales[number]))
  const c: NamedSpecialistText = key => namedSpecialistCopy[key][index]
  const state = useSyncExternalStore(session.subscribe, session.getState)
  const [confirming, setConfirming] = useState<NamedSpecialistReview | null>(null)
  useLayoutEffect(() => session.attach(connected), [session, connected])
  useLayoutEffect(() => session.setUnresolved(JSON.parse(pendingJson) as string[]), [session, pendingJson])
  const disabled = !connected || state.busy
  const locked = disabled || session.blocked()
  const selected = state.catalog?.specialists.find(item => item.agent_id === state.draft.specialistId)
  const review = state.review
  const exactConfirmation = confirming === review ? confirming : null
  const errors: Record<NonNullable<typeof state.error>, NamedSpecialistCopyKey> = { invalid: 'invalid', unavailable: 'unavailableError', changed: 'changed', unknown: 'unknown' }
  const originalId = state.attempt?.review.params.command_id
  const recoveries = state.unresolved.filter(id => id !== originalId)

  return <section aria-label={c('title')} className="grid gap-4">
    <h4 className="text-sm font-medium">{c('title')}</h4>
    <p className="text-xs text-(--ui-text-secondary)">{c('scope')}</p>
    {!connected && <p role="status">{c('offline')}</p>}
    <label className="grid gap-1 text-xs">{c('project')}<Input onChange={event => session.edit({ projectId: event.target.value })} value={state.draft.projectId} /></label>
    <Button disabled={disabled || !state.draft.projectId} onClick={() => void session.catalog()} size="xs" variant="secondary">{c('load')}</Button>
    {state.busy && <Loader />}
    {state.catalog && !state.catalog.specialists.length && <EmptyState title={c('empty')} />}
    {!!state.catalog?.unavailable.length && <div className="grid gap-1 text-xs"><p>{c('unavailable')}</p>{state.catalog.unavailable.map(item => <p className="break-words" key={item.agent_id}>{item.agent_id}: {item.code}</p>)}</div>}
    {!!state.catalog?.specialists.length && <Select disabled={disabled} onValueChange={specialistId => session.edit({ specialistId })} value={state.draft.specialistId}>
      <SelectTrigger aria-label={c('choose')}><SelectValue placeholder={c('choose')} /></SelectTrigger>
      <SelectContent>{state.catalog.specialists.map(item => <SelectItem key={item.agent_id} value={item.agent_id}>{item.agent_id}</SelectItem>)}</SelectContent>
    </Select>}
    {selected && <>
      <Descriptor c={c} value={selected} />
      <label className="grid gap-1 text-xs">{c('objective')}<Textarea maxLength={16384} onChange={event => session.edit({ objective: event.target.value })} value={state.draft.objective} /></label>
      <ReferenceInputs c={c} label={c('artifacts')} onChange={artifacts => session.edit({ artifacts })} value={state.draft.artifacts} />
      <ReferenceInputs c={c} label={c('evidence')} onChange={evidence => session.edit({ evidence })} value={state.draft.evidence} />
      <label className="grid gap-1 text-xs">{c('constraints')}<Textarea onChange={event => session.edit({ constraints: event.target.value })} value={state.draft.constraints} /></label>
      <Button disabled={locked || !state.draft.objective.trim()} onClick={() => void session.preview()} size="xs" variant="secondary">{c('preview')}</Button>
    </>}
    {review && <div className="grid gap-3">
      <ReviewedHandoff c={c} review={review} />
      <Button disabled={locked} onClick={() => setConfirming(review)} size="xs" variant="secondary">{c('review')}</Button>
    </div>}
    <ConfirmDialog busyLabel={c('busy')} cancelLabel={c('cancel')} confirmLabel={c('confirm')} description={c('confirmation')} dismissOnConfirm onClose={() => setConfirming(null)} onConfirm={() => exactConfirmation ? session.handoff(exactConfirmation) : undefined} open={!!exactConfirmation && !locked} title={c('review')}>
      {exactConfirmation && <div className="max-h-96 overflow-auto"><Descriptor c={c} value={exactConfirmation.preview.specialist} /><ReviewedHandoff c={c} review={exactConfirmation} /></div>}
    </ConfirmDialog>
    {state.attempt && <div className="grid gap-2 text-xs" role="status">
      <Value label={c('command')} value={state.attempt.review.params.command_id} />
      <Value label={c('idempotency')} value={state.attempt.review.params.idempotency_key} />
      <Value label={c('project')} value={state.attempt.review.params.selection.project_id} />
      <p>{state.attempt.unknown ? c('unknown') : c('admission')}</p>
      {state.attempt.receipt && <>
        <Value label={c('receipt')} value={c(state.attempt.receipt.status)} />
        <Value label={c('runtimeRevision')} value={state.attempt.receipt.durable_revision} />
        <Value label={c('run')} value={state.attempt.receipt.run_id ?? c('none')} />
        {state.attempt.receipt.conflict && <p>{state.attempt.receipt.conflict.code}</p>}
      </>}
      <p>{c('retained')}</p>
      <Button disabled={disabled} onClick={() => void session.inspect(originalId)} size="xs" variant="secondary">{c('inspect')}</Button>
    </div>}
    {!!recoveries.length && <div className="grid gap-2 text-xs" role="status"><p>{c('unknown')}</p>{recoveries.map(id => <div className="grid gap-1" key={id}><Value label={c('command')} value={id} /><Button disabled={disabled} onClick={() => void session.inspect(id)} size="xs" variant="secondary">{c('inspect')}: {id}</Button></div>)}</div>}
    <label className="grid gap-1 text-xs">{c('recovery')}<Input onChange={event => session.recoveryId(event.target.value)} value={state.recoveryId} /></label>
    <Button disabled={disabled || !state.recoveryId} onClick={() => void session.inspect(state.recoveryId)} size="xs" variant="secondary">{c('status')}</Button>
    {state.status && <CommandStatus c={c} value={state.status} />}
    {state.error && <div role="alert"><ErrorState title={c(errors[state.error])} /></div>}
  </section>
}

function Value({ label, value }: { label: string; value: string | number }) {
  return <p className="break-words text-xs"><span className="font-medium">{label}: </span>{value}</p>
}

function References({ c, label, value }: { c: NamedSpecialistText; label: string; value: SpecialistReference[] }) {
  return <div className="grid gap-1 text-xs"><p className="font-medium">{label}</p>{value.length ? value.map((item, index) => <p className="break-all" key={`${item.id}:${index}`}>{item.id}@{item.version} · {item.sha256}</p>) : <p>{c('none')}</p>}</div>
}

function Descriptor({ c, value }: { c: NamedSpecialistText; value: SpecialistDescriptor }) {
  return <div className="grid gap-2">
    <Value label={c('responsibility')} value={value.responsibility} />
    <Value label={c('manifest')} value={value.manifest_sha256} />
    <References c={c} label={c('methods')} value={[value.methods_ref]} />
    <div className="grid gap-1"><p className="text-xs font-medium">{c('limits')}</p><Value label={c('depth')} value={value.limits.max_depth} /><Value label={c('total')} value={value.limits.max_total_children} /><Value label={c('concurrent')} value={value.limits.max_concurrent_children} /></div>
    <Value label={c('memory')} value={value.builtin_memory_namespace} />
    <Value label={c('tools')} value={value.grants.allowed_tools.join(', ') || c('none')} />
    <Value label={c('projects')} value={value.grants.project_grants.join(', ') || c('none')} />
    <div className="grid gap-1 text-xs"><p className="font-medium">{c('mcp')}</p>{Object.keys(value.grants.mcp_grants).length ? Object.entries(value.grants.mcp_grants).map(([server, grants]) => <p className="break-words" key={server}>{server}: {grants.join(', ') || c('none')}</p>) : <p>{c('none')}</p>}</div>
    <div className="grid gap-1 text-xs"><p className="font-medium">{c('schema')}</p><pre aria-label={c('schema')} className="max-h-64 overflow-auto whitespace-pre-wrap break-words">{value.output_contract_json}</pre></div>
  </div>
}

function ReferenceInputs({ c, label, onChange, value }: { c: NamedSpecialistText; label: string; onChange: (value: SpecialistReferenceDraft[]) => void; value: SpecialistReferenceDraft[] }) {
  function edit(index: number, patch: Partial<SpecialistReferenceDraft>) { onChange(value.map((item, i) => i === index ? { ...item, ...patch } : item)) }

  return <fieldset className="grid gap-2"><legend className="text-xs font-medium">{label}</legend>
    {value.map((item, index) => <div className="grid gap-2" key={index}>
      <label className="grid gap-1 text-xs">{c('id')}<Input maxLength={256} onChange={event => edit(index, { id: event.target.value })} value={item.id} /></label>
      <label className="grid gap-1 text-xs">{c('version')}<Input inputMode="numeric" onChange={event => edit(index, { version: event.target.value })} value={item.version} /></label>
      <label className="grid gap-1 text-xs">{c('digest')}<Input maxLength={64} onChange={event => edit(index, { sha256: event.target.value })} value={item.sha256} /></label>
      <Button onClick={() => onChange(value.filter((_, i) => i !== index))} size="xs" variant="text">{c('remove')}</Button>
    </div>)}
    <Button disabled={value.length >= 32} onClick={() => onChange([...value, { id: '', version: '', sha256: '' }])} size="xs" variant="secondary">{c('add')}</Button>
  </fieldset>
}

function ReviewedHandoff({ c, review }: { c: NamedSpecialistText; review: NamedSpecialistReview }) {
  const p = review.params, s = p.selection

  return <div className="grid gap-2">
    <Value label={c('choose')} value={s.specialist_id} /><Value label={c('project')} value={s.project_id} />
    <Value label={c('objective')} value={s.objective} />
    <References c={c} label={c('artifacts')} value={s.artifacts ?? []} /><References c={c} label={c('evidence')} value={s.evidence ?? []} />
    <Value label={c('constraints')} value={s.constraints?.join('\n') || c('none')} />
    <Value label={c('manifest')} value={s.manifest_sha256} /><Value label={c('config')} value={s.config_digest} /><Value label={c('policy')} value={s.parent_policy_digest} />
    <Value label={c('mission')} value={s.mission_id ?? c('none')} /><Value label={c('missionRevision')} value={s.mission_revision ?? c('none')} />
    <Value label={c('runtimeRevision')} value={review.preview.runtime_revision} /><Value label={c('expiry')} value={new Date(s.expires_at * 1000).toISOString()} />
    <Value label={c('command')} value={p.command_id} /><Value label={c('idempotency')} value={p.idempotency_key} /><Value label={c('digest')} value={p.preview_sha256} />
    <details><summary className="text-xs">{c('exact')}</summary><pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words text-xs">{JSON.stringify(p, null, 2)}</pre></details>
  </div>
}

function CommandStatus({ c, value }: { c: NamedSpecialistText; value: SpecialistStatus }) {
  const completion = value.completion

  return <div className="grid gap-2 text-xs" role="status">
    <Value label={c('command')} value={value.command_id} /><Value label={c('run')} value={value.run_id} />
    <Value label={c('choose')} value={value.specialist_id} /><Value label={c('project')} value={value.project_id} /><Value label={c('manifest')} value={value.manifest_sha256} />
    <Value label={c('status')} value={`${c(value.status)} · ${c(value.outcome === 'unknown' ? 'unknownState' : value.outcome)}`} />
    <p>{c('retained')}</p>
    {completion && <>
      <p className="font-medium">{c('reviewRequired')}</p>
      <Value label={c('child')} value={completion.child_id ?? c('none')} />
      <Value label={c('status')} value={c(completion.state === 'unknown' ? 'unknownState' : completion.state)} />
      <Value label={c('schemaValid')} value={c(completion.schema_valid === null ? 'unknownState' : completion.schema_valid ? 'yes' : 'no')} />
      <Value label={c('truncated')} value={c(completion.summary_truncated ? 'yes' : 'no')} />
      <Value label={c('digest')} value={completion.handoff_sha256 ?? c('none')} />
      <pre aria-label={c('summary')} className="max-h-96 overflow-auto whitespace-pre-wrap break-words">{completion.summary}</pre>
    </>}
    <details><summary>{c('exact')}</summary><pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words">{JSON.stringify(value, null, 2)}</pre></details>
  </div>
}
