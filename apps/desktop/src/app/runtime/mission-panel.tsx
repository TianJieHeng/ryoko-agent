import type { MissionRecord, MissionVerificationReceipt } from '@hermes/shared/gateway-events'
import type { RuntimeRequest } from '@hermes/shared/runtime-control'
import { useEffect, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'

import { runtimeUiTemplates, useRuntimeUiFormat, useRuntimeUiText } from './runtime-ui-copy'

interface Props { request: RuntimeRequest; sessionId: string; connected: boolean; onMission?: (mission: MissionRecord | null) => void }

export function MissionPanel({ request, sessionId, connected, onMission }: Props) {
  const format = useRuntimeUiFormat()
  const rt = useRuntimeUiText()
  const [mission, setMission] = useState<MissionRecord | null>(null)
  const [receipts, setReceipts] = useState<MissionVerificationReceipt[]>([])
  const [outcome, setOutcome] = useState(''), [missionId, setMissionId] = useState(''), [project, setProject] = useState('')
  const [deliverable, setDeliverable] = useState(''), [artifact, setArtifact] = useState(''), [version, setVersion] = useState('1'), [headings, setHeadings] = useState('')
  const [busy, setBusy] = useState(false), [needsRefresh, setNeedsRefresh] = useState(true), [error, setError] = useState<string | null>(null)
  const [confirmation, setConfirmation] = useState<'accept' | 'cancel' | null>(null)
  const lifetimeRef = useRef({ request, sessionId, connected, epoch: 0, pending: false })

  if (lifetimeRef.current.request !== request || lifetimeRef.current.sessionId !== sessionId || lifetimeRef.current.connected !== connected) {
    lifetimeRef.current.epoch++
    lifetimeRef.current = { request, sessionId, connected, epoch: 0, pending: false }
  }

  const lifetime = lifetimeRef.current
  useEffect(() => {
    lifetime.epoch++; lifetime.pending = false; setBusy(false); setNeedsRefresh(true); setConfirmation(null); setMission(null); setReceipts([])

    return () => { lifetime.epoch++; lifetime.pending = false }
  }, [lifetime])
  const base = { session_id: sessionId, schema_version: 1 as const }

  function publishMission(value: MissionRecord | null) { setMission(value); onMission?.(value) }

  async function run(work: () => Promise<void>, mutation = false) {
    if (lifetime.pending || !connected) {return}
    const flight = lifetime.epoch
    lifetime.pending = true; setBusy(true); setError(null)

    try { await work() } catch (caught) {
      if (flight === lifetime.epoch) {
        setError(`${caught instanceof Error ? caught.message : rt("Mission request unavailable")}${mutation ? rt(". Outcome may be unknown; refresh before another change. No automatic retry was sent.") : ''}`)

        if (mutation) {setNeedsRefresh(true)}
      }
    } finally { if (flight === lifetime.epoch) { lifetime.pending = false; setBusy(false) } }
  }

  async function load() {
    const flight = lifetime.epoch
    const result = await request('runtime.mission.get', base)
    const verification = await request('runtime.mission.receipts.list', base)

    if (flight !== lifetime.epoch) {return}
    publishMission(result.mission); setReceipts(verification.receipts); setNeedsRefresh(false)

    if (result.mission) { setOutcome(result.mission.outcome); setProject(result.mission.project_id ?? '') }
  }

  async function create() {
    const flight = lifetime.epoch
    const result = await request('runtime.mission.create', { ...base, mission_id: missionId.trim(), contract: { outcome, ...(project.trim() ? { project_id: project.trim() } : {}), policy: 'reviewed' } })

    if (flight === lifetime.epoch) { publishMission(result.mission); setNeedsRefresh(false); setReceipts([]) }
  }

  async function revise() {
    if (!mission) {return}
    const flight = lifetime.epoch
    const result = await request('runtime.mission.revise', { ...base, expected_revision: mission.revision, contract: { outcome } })

    if (flight === lifetime.epoch) { publishMission(result.mission); setReceipts([]) }
  }

  async function attach() {
    if (!mission) {return}
    const flight = lifetime.epoch
    const bytes = await request('runtime.artifact.get', { ...base, project_id: project.trim(), artifact_id: artifact.trim(), version: Number(version), limit: 1 })

    if (flight !== lifetime.epoch) {return}
    const ref = { artifact_id: bytes.artifact_id, version: bytes.version, digest: bytes.sha256 }
    const id = deliverable.trim(), criterion = `output:${id}`, required = headings.split('\n').map(value => value.trim()).filter(Boolean)

    const result = await request('runtime.mission.revise', { ...base, expected_revision: mission.revision, contract: {
      outcome: mission.outcome,
      deliverables: [...(mission.deliverables ?? []).filter(item => item.deliverable_id !== id), { deliverable_id: id, description: id, artifact_ref: ref, required: true }],
      acceptance: [...(mission.acceptance ?? []).filter(item => item.criterion_id !== criterion), required.length ? { criterion_id: criterion, kind: 'markdown_sections', required: true, artifact_refs: [ref], parameters: { required_sections: required, nonempty: true, require_current_head: true, require_current_dependencies: true } } : { criterion_id: criterion, kind: 'existence', required: true, artifact_refs: [ref], parameters: { require_current_head: true, require_current_dependencies: true } }]
    } })

    if (flight === lifetime.epoch) { publishMission(result.mission); setReceipts([]) }
  }

  async function verify() {
    if (!mission) {return}
    const flight = lifetime.epoch
    const result = await request('runtime.mission.verify', { ...base, expected_revision: mission.revision })

    if (flight === lifetime.epoch) { publishMission(result.mission); setReceipts(result.receipts) }
  }

  async function control(operation: 'pause' | 'resume' | 'cancel' | 'accept') {
    if (!mission || !connected || needsRefresh) {throw new Error(rt("Refresh the current mission before acting"))}
    const flight = lifetime.epoch
    const result = await request(`runtime.mission.${operation}`, { ...base, expected_revision: mission.revision })

    if (flight === lifetime.epoch) {publishMission(result.mission)}
  }

  const disabled = !connected || busy || needsRefresh

  return <section className="grid gap-3"><h4 className="text-sm font-medium">{rt("Outcome and completion review")}</h4>
    <Button disabled={!connected || busy} onClick={() => void run(load)} size="xs" variant="secondary">{rt("Refresh current mission and evidence")}</Button>
    {!connected && <p role="status">{rt("Connection is stale. Accepted work may still be running; controls are disabled.")}</p>}
    {mission ? <><div aria-live="polite" className="grid gap-1 text-sm"><strong>{mission.mission_id}{" " + rt("· revision") + " "}{mission.revision} · {mission.state.replaceAll('_', ' ')}</strong><p>{rt("Execution:") + " "}{mission.execution_status}{rt("; acceptance:") + " "}{mission.acceptance_status}{rt("; delivery:") + " "}{mission.delivery_status}</p><p>{rt("Next:") + " "}{mission.next_step}</p>{mission.blockers.map(blocker => <p key={blocker}>{rt("Blocked:") + " "}{blocker}</p>)}{mission.missed_steer.map(item => <p key={item.revision}>{rt("Revision") + " "}{item.revision}{" " + rt("correction missed:") + " "}{item.reason}</p>)}</div>
      <label>{rt("Revised outcome")}<Textarea disabled={busy} onChange={event => setOutcome(event.target.value)} value={outcome} /></label><Button disabled={disabled || outcome === mission.outcome || !outcome.trim()} onClick={() => void run(revise, true)} size="xs" variant="secondary">{rt("Submit revision against this version")}</Button>
      <div className="grid gap-2">{(mission.deliverables ?? []).map(item => <p className="text-sm" key={item.deliverable_id}>{item.deliverable_id}: {item.artifact_ref ? `${item.artifact_ref.artifact_id}@${item.artifact_ref.version}` : rt("not produced")}{item.required ? rt(" (required)") : ''}</p>)}</div>
      <details><summary>{rt("Attach a reviewed output and its completion standard")}</summary><div className="grid gap-2"><label>{rt("Deliverable name")}<Input onChange={event => setDeliverable(event.target.value)} value={deliverable} /></label><label>{rt("Project ID")}<Input onChange={event => setProject(event.target.value)} value={project} /></label><label>{rt("Artifact ID")}<Input onChange={event => setArtifact(event.target.value)} value={artifact} /></label><label>{rt("Exact artifact version")}<Input min={1} onChange={event => setVersion(event.target.value)} type="number" value={version} /></label><label>{rt("Required nonempty Markdown headings (one per line; optional)")}<Textarea onChange={event => setHeadings(event.target.value)} value={headings} /></label><Button disabled={disabled || !deliverable || !project || !artifact || Number(version) < 1} onClick={() => void run(attach, true)} size="xs" variant="secondary">{rt("Attach exact version; preserve other outputs")}</Button></div></details>
      <div className="flex flex-wrap gap-2"><Button disabled={disabled} onClick={() => void run(verify)} size="xs" variant="secondary">{rt("Verify current outputs")}</Button><Button disabled={disabled || !mission.verification_current || mission.state !== 'ready_to_review'} onClick={() => setConfirmation('accept')} size="xs" variant="secondary">{rt("Accept reviewed result")}</Button><Button disabled={disabled} onClick={() => void run(() => control('pause'), true)} size="xs" variant="ghost">{rt("Pause")}</Button><Button disabled={disabled || mission.state !== 'paused'} onClick={() => void run(() => control('resume'), true)} size="xs" variant="ghost">{rt("Resume")}</Button><Button disabled={disabled || ['completed', 'cancelled'].includes(mission.state)} onClick={() => setConfirmation('cancel')} size="xs" variant="text">{rt("Request cancellation")}</Button></div>
      <ul className="grid gap-1 text-xs">{receipts.map(receipt => <li key={receipt.receipt_id}>{receipt.criterion_id}: {receipt.result} · {receipt.verifier} · {receipt.evidence_ref}</li>)}</ul>
      <details><summary>{rt("Effects and delivery remain separate from completion")}</summary>{mission.effect_refs.map(item => <p key={item.effect_id}>{item.effect_id}: {item.state}</p>)}{mission.delivery_refs.map(item => <p key={item.delivery_id}>{item.delivery_id}: {item.state}</p>)}</details>
    </> : <><label>{rt("Mission ID")}<Input disabled={busy} onChange={event => setMissionId(event.target.value)} value={missionId} /></label><label>{rt("Outcome")}<Textarea disabled={busy} onChange={event => setOutcome(event.target.value)} value={outcome} /></label><label>{rt("Project ID (optional)")}<Input disabled={busy} onChange={event => setProject(event.target.value)} value={project} /></label><Button disabled={!connected || busy || !missionId || !outcome.trim()} onClick={() => void run(create, true)} size="xs" variant="secondary">{rt("Create reviewed mission brief")}</Button><p className="text-xs text-muted-foreground">{rt("Creates intent only. No model or external effect is dispatched; continue work through the existing conversation.")}</p></>}
    <ConfirmDialog confirmLabel={confirmation === 'cancel' ? rt("Request cancellation") : rt("Accept this result")} description={confirmation === 'cancel' ? rt("Stop further mission work where supported. Already dispatched effects may remain unknown and are not undone.") : format(runtimeUiTemplates.missionAccept, { mission: mission?.mission_id ?? '', revision: mission?.revision ?? '' })} onClose={() => setConfirmation(null)} onConfirm={async () => { if (confirmation) { try { await control(confirmation) } catch (caught) { setNeedsRefresh(true); throw caught } } }} open={confirmation !== null} title={confirmation === 'cancel' ? rt("Cancel this mission?") : rt("Accept the reviewed outputs?")} />
    {error && <p role="alert">{error}</p>}
  </section>
}
