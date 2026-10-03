import type { ArtifactEditParams, ArtifactPrepareParams, ArtifactProposalResult } from '@hermes/shared/gateway-events'
import { type DownloadedArtifact, downloadRuntimeArtifact } from '@hermes/shared/runtime-artifacts'
import type { RuntimeRequest } from '@hermes/shared/runtime-control'
import { markdownDigest, markdownSections } from '@hermes/shared/runtime-markdown'
import { useEffect, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'

import { useRuntimeUiText } from './runtime-ui-copy'

interface Props { request: RuntimeRequest; sessionId: string; connected: boolean }
interface Prepared { proposal: ArtifactProposalResult; params: ArtifactPrepareParams | ArtifactEditParams; kind: 'new' | 'edit'; preview: string }

export function ArtifactWorkbench({ request, sessionId, connected }: Props) {
  const rt = useRuntimeUiText()
  const [project, setProject] = useState('')
  const [artifact, setArtifact] = useState('')
  const [version, setVersion] = useState('1')
  const [baseline, setBaseline] = useState<DownloadedArtifact | null>(null)
  const [baselineText, setBaselineText] = useState('')
  const [anchor, setAnchor] = useState('')
  const [content, setContent] = useState('')
  const [alternative, setAlternative] = useState('')
  const [comparison, setComparison] = useState<string | null>(null)
  const [prepared, setPrepared] = useState<Prepared | null>(null)
  const [confirming, setConfirming] = useState(false)
  const [stalePreparation, setStalePreparation] = useState(false)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState<string | null>(null)
  const generation = useRef(0)
  useEffect(() => () => { generation.current++ }, [request, sessionId])
  const sections = markdownSections(baselineText)
  const selected = sections.find(section => section.anchor === anchor)
  const base = { session_id: sessionId, schema_version: 1 as const }

  function invalidate() { if (prepared) {setStalePreparation(true);} setConfirming(false); setMessage('') }

  function resetSource() { generation.current++; setBaseline(null); setBaselineText(''); setAnchor(''); setComparison(null); invalidate() }

  async function action(work: () => Promise<void>) {
    if (busy || !connected) {return}
    const flight = generation.current
    setBusy(true); setError(null)

    try { await work() } catch (caught) { if (flight === generation.current) {setError(caught instanceof Error ? caught.message : rt("Artifact request failed. Inspect status before retrying"))} }
    finally { if (flight === generation.current) {setBusy(false)} }
  }

  async function load() {
    const flight = generation.current
    const value = await downloadRuntimeArtifact(request, sessionId, project.trim(), artifact.trim(), Number(version))

    if (flight !== generation.current) {return}

    if (value.metadata.mime !== 'text/markdown' || value.metadata.preview_mode !== 'plain_text') {throw new Error(rt("This version supports download only; section editing requires validated Markdown"))}
    setBaseline(value); setBaselineText(new TextDecoder('utf-8', { fatal: true }).decode(value.bytes)); setAnchor(''); setContent(''); invalidate()
  }

  async function prepare() {
    if (prepared) {throw new Error(rt("Discard the previous proposal before preparing changed content"))}
    const flight = generation.current
    const command_id = crypto.randomUUID(), request_id = crypto.randomUUID()

    if (artifact && !baseline) {throw new Error(rt("Load the exact base version before editing an existing artifact"))}

    if (baseline && !selected) {throw new Error(rt("Choose one unambiguous heading; unrelated sections are preserved"))}

    const params = baseline && selected ? {
      ...base, project_id: project.trim(), command_id, request_id, artifact_id: baseline.metadata.artifact_id,
      parent_version: baseline.metadata.version, expected_head_version: baseline.metadata.version,
      edits: [{ anchor: selected.anchor, expected_sha256: await markdownDigest(selected.content), replacement: content }]
    } satisfies ArtifactEditParams : { ...base, project_id: project.trim(), command_id, request_id, content } satisfies ArtifactPrepareParams

    const proposal = 'edits' in params ? await request('runtime.artifact.edit.prepare', params) : await request('runtime.artifact.prepare', params)

    if (flight !== generation.current) {return}
    setPrepared({ proposal, params, kind: 'edits' in params ? 'edit' : 'new', preview: content })
    setStalePreparation(false)
    setMessage(rt("Prepared for review only; no version published"))
  }

  async function publish() {
    const item = prepared

    if (!item || !connected || stalePreparation) {throw new Error(rt("Review a current proposal first"))}

    if (item.proposal.expires_at * 1000 <= Date.now()) {throw new Error(rt("Proposal expired; prepare and review again"))}
    const flight = generation.current
    const approved = { approval_id: item.proposal.approval_id, approval_digest: item.proposal.approval_digest }

    const result = item.kind === 'edit'
      ? await request('runtime.artifact.edit.publish', { ...item.params as ArtifactEditParams, ...approved })
      : await request('runtime.artifact.publish', { ...item.params as ArtifactPrepareParams, ...approved })

    if (flight !== generation.current) {return}
    setMessage(`Published ${result.artifact_id}@${result.version} as ${result.disposition}; validation ${result.validation_status}. Delivery/sharing not performed. Previous versions remain available.`)
    setPrepared(null)
  }

  async function discard() {
    if (!prepared) {return}
    const flight = generation.current
    const result = await request('runtime.artifact.cancel', { ...base, command_id: prepared.params.command_id })

    if (flight !== generation.current) {return}

    if (!['cancelled', 'completed', 'failed', 'blocked'].includes(result.status)) { setMessage(`Cancellation is still ${result.status}; retain this proposal until acknowledged`);

 return }

    setPrepared(null); setStalePreparation(false); setConfirming(false)
    setMessage(`Proposal control ${result.status}; already committed effects are not undone`)
  }

  async function compare() {
    if (!baseline) {throw new Error(rt("Load the baseline first"))}
    const flight = generation.current
    const value = await downloadRuntimeArtifact(request, sessionId, project.trim(), artifact.trim(), Number(alternative))

    if (flight !== generation.current) {return}

    if (value.metadata.preview_mode !== 'plain_text') {throw new Error(rt("Alternative supports download only"))}
    setComparison(new TextDecoder().decode(value.bytes))
  }

  return <section className="grid gap-3">
    <h4 className="text-sm font-medium">{rt("Create, review or revise a Markdown artifact")}</h4>
    <div className="grid gap-2">
      <label>{rt("Project ID")}<Input disabled={busy} onChange={event => { resetSource(); setProject(event.target.value) }} value={project} /></label>
      <label>{rt("Existing artifact ID (leave blank for a new artifact)")}<Input disabled={busy} onChange={event => { resetSource(); setArtifact(event.target.value) }} value={artifact} /></label>
      {artifact && <><label>{rt("Exact base version")}<Input disabled={busy} min={1} onChange={event => { resetSource(); setVersion(event.target.value) }} type="number" value={version} /></label><Button disabled={busy || !connected || !project} onClick={() => void action(load)} size="xs" variant="secondary">{rt("Load verified version")}</Button></>}
    </div>
    {baseline && <><p className="text-xs">{baseline.metadata.artifact_id}@{baseline.metadata.version} · {baseline.bytes.length}{" " + rt("complete bytes · SHA-256 verified")}</p>
      <div aria-label={rt("Section to revise")} className="flex flex-wrap gap-1">{sections.map(section => <Button aria-pressed={anchor === section.anchor} disabled={busy} key={section.anchor} onClick={() => { setAnchor(section.anchor); setContent(section.content); invalidate() }} size="xs" variant="ghost">{section.anchor}</Button>)}</div>
      <p className="text-xs text-muted-foreground">{rt("Choose one heading. Duplicate headings are unavailable; the backend checks exact section digests and inherited locks.")}</p></>}
    <label>{baseline ? rt("Replacement for the selected section (include its heading)") : rt("Complete Markdown content")}<Textarea disabled={busy} onChange={event => { setContent(event.target.value); invalidate() }} value={content} /></label>
    {selected && <details><summary>{rt("Compare unchanged baseline section with this revision")}</summary><div className="grid gap-3 md:grid-cols-2"><pre className="whitespace-pre-wrap break-words text-xs">{selected.content}</pre><pre className="whitespace-pre-wrap break-words text-xs">{content}</pre></div></details>}
    <Button disabled={busy || !connected || !!prepared || !project || !content || (!!baseline && !selected)} onClick={() => void action(prepare)} size="xs" variant="secondary">{rt("Prepare exact revision for review")}</Button>
    {prepared && <div className="grid gap-2">{stalePreparation && <p role="status">{rt("Inputs changed. Discard this old proposal before preparing a new one.")}</p>}<p className="break-words text-xs">{rt("Control command") + " "}{prepared.params.command_id}{rt(". Discard before leaving to release the pending control; closing this view does not cancel it.")}</p><p>{rt("Review") + " "}{prepared.proposal.artifact_id}@{prepared.proposal.version}; {prepared.proposal.size}{" " + rt("bytes; approval expires") + " "}{new Date(prepared.proposal.expires_at * 1000).toLocaleString()}</p><p className="break-all text-xs">{rt("Digest:") + " "}{prepared.proposal.sha256}</p><pre className="whitespace-pre-wrap break-words text-xs">{prepared.preview}</pre><div className="flex gap-2"><Button disabled={!connected || busy || stalePreparation} onClick={() => setConfirming(true)} size="xs" variant="secondary">{rt("Approve and publish this exact version")}</Button><Button disabled={!connected || busy} onClick={() => void action(discard)} size="xs" variant="text">{rt("Discard review")}</Button></div></div>}
    <ConfirmDialog confirmLabel={rt("Publish reviewed version")} description={rt("Publish only the exact reviewed bytes to this project. Existing versions remain; this does not share or deliver the file.")} onClose={() => setConfirming(false)} onConfirm={publish} open={confirming && !!prepared} title={rt("Approve this artifact revision?")} />
    {baseline && <div className="grid gap-2"><label>{rt("Alternative version to compare")}<Input disabled={busy} min={1} onChange={event => { setAlternative(event.target.value); setComparison(null) }} type="number" value={alternative} /></label><Button disabled={busy || !connected || !alternative} onClick={() => void action(compare)} size="xs" variant="secondary">{rt("Compare without changing the baseline")}</Button>{comparison !== null && <details open><summary>{rt("Baseline and alternative; neither is overwritten")}</summary><div className="grid gap-3 md:grid-cols-2"><pre className="whitespace-pre-wrap break-words text-xs">{baselineText}</pre><pre className="whitespace-pre-wrap break-words text-xs">{comparison}</pre></div><Button onClick={() => setComparison(null)} size="xs" variant="text">{rt("Close comparison")}</Button></details>}</div>}
    {message && <p aria-live="polite">{message}</p>}{error && <p role="alert">{error}</p>}
  </section>
}
