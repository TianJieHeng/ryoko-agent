import type { ArtifactMergeParams, ArtifactProposalResult } from '@hermes/shared/gateway-events'
import { downloadRuntimeArtifact } from '@hermes/shared/runtime-artifacts'
import type { RuntimeRequest } from '@hermes/shared/runtime-control'
import { markdownSections } from '@hermes/shared/runtime-markdown'
import { useEffect, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { Input } from '@/components/ui/input'

import { runtimeUiTemplates, useRuntimeUiFormat, useRuntimeUiText } from './runtime-ui-copy'

interface Props { request: RuntimeRequest; sessionId: string; connected: boolean }
interface Compared { project: string; artifact: string; head: number; branch: number; currentText: string; branchText: string }
interface PendingMerge { params: ArtifactMergeParams; proposal: ArtifactProposalResult | null; valid: boolean; cancelRequested: boolean }

const requestIds = new WeakMap<RuntimeRequest, number>()
let nextRequestId = 0

export function BranchMergePanel(props: Props) {
  if (!requestIds.has(props.request)) {requestIds.set(props.request, ++nextRequestId)}

  return <OwnedBranchMergePanel key={`${requestIds.get(props.request)}:${props.sessionId}`} {...props} />
}

/** A selected branch changes only explicitly reviewed anchors; the backend owns merge/CAS rules. */
function OwnedBranchMergePanel({ request, sessionId, connected }: Props) {
  const format = useRuntimeUiFormat()
  const rt = useRuntimeUiText()
  const [project, setProject] = useState(''), [artifact, setArtifact] = useState(''), [version, setVersion] = useState('')
  const [compared, setCompared] = useState<Compared | null>(null), [selected, setSelected] = useState<string[]>([])
  const [pending, setPending] = useState<PendingMerge | null>(null), [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false), [message, setMessage] = useState(''), [error, setError] = useState<string | null>(null)
  const lifetimeRef = useRef({ request, sessionId, connected, epoch: 0, pending: null as symbol | null })

  if (lifetimeRef.current.request !== request || lifetimeRef.current.sessionId !== sessionId || lifetimeRef.current.connected !== connected) {
    lifetimeRef.current.epoch++
    lifetimeRef.current = { request, sessionId, connected, epoch: 0, pending: null as symbol | null }
  }

  const lifetime = lifetimeRef.current
  useEffect(() => { lifetime.epoch++; lifetime.pending = null; setBusy(false); setConfirming(false); setPending(value => value ? { ...value, valid: false } : null);

 return () => { lifetime.epoch++; lifetime.pending = null } }, [lifetime])
  const base = { session_id: sessionId, schema_version: 1 as const }
  const currentSections = markdownSections(compared?.currentText ?? ''), branchSections = markdownSections(compared?.branchText ?? '')

  const alternatives = currentSections.flatMap(current => {
    const branch = branchSections.find(item => item.anchor === current.anchor)

    return branch && branch.content !== current.content ? [{ current, branch }] : []
  })

  function changed(work: () => void) { lifetime.epoch++; work(); setCompared(null); setSelected([]); setMessage(''); setConfirming(false); setPending(value => value ? { ...value, valid: false } : null) }

  async function action(work: () => Promise<void>, propagate = false) {
    if (!connected || lifetime.pending) {return}
    const token = Symbol(), flight = lifetime.epoch; lifetime.pending = token; setBusy(true); setError(null)

    try { await work() } catch (caught) {
      if (flight === lifetime.epoch) { setError(caught instanceof Error ? caught.message : rt("Merge unavailable; inspect the original control"));

 if (propagate) {throw caught} }
    } finally { if (lifetime.pending === token) { lifetime.pending = null; setBusy(false) } }
  }

  async function compare() {
    const flight = lifetime.epoch
    const head = await request('runtime.artifact.get', { ...base, project_id: project.trim(), artifact_id: artifact.trim(), limit: 1 })

    const [current, branch] = await Promise.all([
      downloadRuntimeArtifact(request, sessionId, project.trim(), artifact.trim(), head.version),
      downloadRuntimeArtifact(request, sessionId, project.trim(), artifact.trim(), Number(version))
    ])

    if (flight !== lifetime.epoch) {return}

    if ([current, branch].some(value => value.metadata.mime !== 'text/markdown' || value.metadata.preview_mode !== 'plain_text' || value.bytes.length > 262144)) {throw new Error(rt("Branch review supports bounded complete Markdown; download other formats or large versions separately"))}
    const decoder = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true })
    setCompared({ project: project.trim(), artifact: artifact.trim(), head: head.version, branch: branch.metadata.version, currentText: decoder.decode(current.bytes), branchText: decoder.decode(branch.bytes) })
    setSelected([]); setMessage(rt("Compared current canonical head with the immutable alternative. Nothing has been changed."))
  }

  async function prepare() {
    if (!compared || !selected.length || pending) {return}
    const flight = lifetime.epoch
    const params: ArtifactMergeParams = { ...base, project_id: compared.project, artifact_id: compared.artifact, current_head_version: compared.head, branch_version: compared.branch, approved_anchors: selected, command_id: crypto.randomUUID(), request_id: crypto.randomUUID() }
    const retained: PendingMerge = { params, proposal: null, valid: false, cancelRequested: false }
    setPending(retained)

    try {
      const proposal = await request('runtime.artifact.merge.prepare', params)

      if (flight === lifetime.epoch) { setPending({ ...retained, proposal, valid: true }); setMessage(rt("Merge prepared for exact review only; current head is unchanged")) }
    } catch (caught) { if (flight === lifetime.epoch) {setMessage(`Control ${params.command_id} may remain pending. Inspect or discard it before another preparation.`);} throw caught }
  }

  async function publish() {
    if (!connected || !pending?.valid || !pending.proposal) {throw new Error(rt("Review a current merge proposal first"))}
    const captured = pending, proposal = captured.proposal!

    if (proposal.expires_at * 1000 <= Date.now()) {throw new Error(rt("Merge approval expired; discard and review again"))}
    const flight = lifetime.epoch
    setPending({ ...captured, valid: false })
    const result = await request('runtime.artifact.merge.publish', { ...captured.params, approval_id: proposal.approval_id, approval_digest: proposal.approval_digest })

    if (flight !== lifetime.epoch) {return}
    setPending(null); setCompared(null); setSelected([])
    setMessage(`Published ${result.artifact_id}@${result.version} as ${result.disposition}. ${result.disposition === 'branch' ? rt("The canonical head was not overwritten; reload and review the conflict.") : rt("Only the approved sections were merged.")} Original immutable versions remain available. Delivery was not performed.`)
  }

  async function inspect(discard: boolean) {
    if (!pending || (discard && pending.cancelRequested)) {return}
    const captured = pending, flight = lifetime.epoch

    if (discard) {setPending({ ...captured, valid: false, cancelRequested: true })}
    const result = await request(discard ? 'runtime.artifact.cancel' : 'runtime.artifact.status', { ...base, command_id: captured.params.command_id })

    if (flight !== lifetime.epoch) {return}

    if (result.command_id !== captured.params.command_id) {throw new Error(rt("Control receipt does not match the reviewed merge"))}
    setMessage(`Merge control ${result.status}; committed effects are never undone by closing or cancellation`)

    if (['completed', 'cancelled', 'failed', 'blocked'].includes(result.status)) { setPending(null); setCompared(null); setSelected([]) }
  }

  return <section className="grid gap-3"><h4 className="text-sm font-medium">{rt("Choose sections from an alternative version")}</h4>
    <label>{rt("Project ID")}<Input disabled={busy} onChange={event => changed(() => setProject(event.target.value))} value={project} /></label>
    <label>{rt("Artifact ID")}<Input disabled={busy} onChange={event => changed(() => setArtifact(event.target.value))} value={artifact} /></label>
    <label>{rt("Alternative branch version")}<Input disabled={busy} min={1} onChange={event => changed(() => setVersion(event.target.value))} type="number" value={version} /></label>
    <Button disabled={!connected || busy || !!pending || !project || !artifact || Number(version) < 1} onClick={() => void action(compare)} size="xs" variant="secondary">{rt("Compare with current canonical head")}</Button>
    {compared && <div className="grid gap-3"><p>{rt("Current head") + " "}{compared.head}{rt("; alternative") + " "}{compared.branch}{rt(". Select only the changes you want; overlapping or conflicting edits are rejected by the backend.")}</p>{alternatives.map(({ current, branch }) => <div className="grid gap-2" key={current.anchor}><label className="flex gap-2"><Checkbox checked={selected.includes(current.anchor)} disabled={busy || !!pending} onCheckedChange={value => setSelected(previous => value === true ? [...previous, current.anchor] : previous.filter(item => item !== current.anchor))} />{current.anchor}</label><div className="grid gap-2 md:grid-cols-2"><pre className="whitespace-pre-wrap break-words text-xs">{current.content}</pre><pre className="whitespace-pre-wrap break-words text-xs">{branch.content}</pre></div></div>)}{!alternatives.length && <p>{rt("No unambiguous changed sections were found; no merge is prepared.")}</p>}<Button disabled={!connected || busy || !!pending || !selected.length} onClick={() => void action(prepare)} size="xs" variant="secondary">{rt("Prepare only selected section changes")}</Button></div>}
    {pending && <div className="grid gap-2"><p className="break-words text-xs">{rt("Control") + " "}{pending.params.command_id}; {pending.params.project_id}/{pending.params.artifact_id}{rt("; current") + " "}{pending.params.current_head_version}{" " + rt("← branch") + " "}{pending.params.branch_version}{rt("; sections") + " "}{pending.params.approved_anchors.join(', ')}</p>{pending.proposal && <p className="break-words text-xs">{rt("Proposed version") + " "}{pending.proposal.version}{rt("; digest") + " "}{pending.proposal.sha256}{rt("; expires") + " "}{new Date(pending.proposal.expires_at * 1000).toLocaleString()}</p>}<div className="flex flex-wrap gap-2"><Button disabled={!connected || busy || !pending.valid || !pending.proposal} onClick={() => setConfirming(true)} size="xs" variant="secondary">{rt("Approve selected merge")}</Button><Button disabled={!connected || busy} onClick={() => void action(() => inspect(false))} size="xs" variant="ghost">{rt("Inspect merge control")}</Button><Button disabled={!connected || busy || pending.cancelRequested} onClick={() => void action(() => inspect(true))} size="xs" variant="text">{rt("Discard merge preparation")}</Button></div></div>}
    <ConfirmDialog confirmLabel={rt("Publish exact selected merge")} description={format(runtimeUiTemplates.branchMerge, { anchors: pending?.params.approved_anchors.join(', ') ?? '', branch: pending?.params.branch_version ?? '', head: pending?.params.current_head_version ?? '' })} onClose={() => setConfirming(false)} onConfirm={() => action(publish, true)} open={confirming && !!pending && connected} title={rt("Approve this exact branch merge?")} />
    {message && <p aria-live="polite" className="text-sm">{message}</p>}{error && <p role="alert">{error}</p>}
  </section>
}
