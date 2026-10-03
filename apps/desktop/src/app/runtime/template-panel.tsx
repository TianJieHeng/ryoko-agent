import type { TemplateRecord } from '@hermes/shared/gateway-events'
import type { RuntimeRequest } from '@hermes/shared/runtime-control'
import { useEffect, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'

import { runtimeUiTemplates, useRuntimeUiFormat, useRuntimeUiText } from './runtime-ui-copy'

export function TemplatePanel({ request, sessionId, connected }: { request: RuntimeRequest; sessionId: string; connected: boolean }) {
  const format = useRuntimeUiFormat()
  const rt = useRuntimeUiText()
  const [project, setProject] = useState(''), [name, setName] = useState(''), [artifact, setArtifact] = useState(''), [version, setVersion] = useState('1')
  const [structure, setStructure] = useState(''), [exclusions, setExclusions] = useState(''), [style, setStyle] = useState('')
  const [templates, setTemplates] = useState<TemplateRecord[]>([]), [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null), [review, setReview] = useState(false)
  const generation = useRef(0)
  useEffect(() => () => { generation.current++ }, [request, sessionId])
  const lines = (value: string) => value.split('\n').map(line => line.trim()).filter(Boolean)

  async function list() {
    if (busy || !connected) {return}
    const flight = ++generation.current
    setBusy(true); setError(null)

    try { const result = await request('runtime.template.list', { session_id: sessionId, schema_version: 1, project_id: project });

 if (flight === generation.current) {setTemplates(result.templates)} }
    catch (caught) { if (flight === generation.current) {setError(caught instanceof Error ? caught.message : rt("Template list unavailable"))} }
    finally { if (flight === generation.current) {setBusy(false)} }
  }

  async function save() {
    if (!connected) {throw new Error(rt("Reconnect before saving"))}
    const flight = generation.current

    const result = await request('runtime.template.create', {
      session_id: sessionId, schema_version: 1, project_id: project.trim(), template_id: name.trim(), version: 1,
      baseline_ref: { artifact_id: artifact.trim(), version: Number(version) }, structure: lines(structure), style: { guidance: style }, assets: [],
      slots: [{ name: 'topic', purpose: 'Replace the original subject with the new requested topic', required: true }], exclusions: lines(exclusions)
    })

    if (flight === generation.current) {setTemplates(previous => [...previous.filter(item => item.template_id !== result.template.template_id), result.template])}
  }

  return <section className="grid gap-3"><h4 className="text-sm font-medium">{rt("Reusable structure from an approved example")}</h4>
    <label>{rt("Project ID")}<Input disabled={busy} onChange={event => { generation.current++; setProject(event.target.value); setTemplates([]); setReview(false) }} value={project} /></label>
    <Button disabled={!connected || busy || !project} onClick={() => void list()} size="xs" variant="secondary">{rt("List approved templates")}</Button>
    {templates.map(template => <div className="grid gap-1 text-sm" key={`${template.template_id}:${template.version}`}><strong>{template.template_id}@{template.version}</strong><p>{rt("Baseline") + " "}{template.baseline_ref.artifact_id}@{template.baseline_ref.version}</p><p>{rt("Structure:") + " "}{template.structure.join(' → ')}</p><p>{rt("Excluded:") + " "}{template.exclusions.join('; ')}</p></div>)}
    <label>{rt("New template ID")}<Input onChange={event => { setName(event.target.value); setReview(false) }} value={name} /></label>
    <label>{rt("Approved artifact ID")}<Input onChange={event => { setArtifact(event.target.value); setReview(false) }} value={artifact} /></label>
    <label>{rt("Exact version")}<Input min={1} onChange={event => { setVersion(event.target.value); setReview(false) }} type="number" value={version} /></label>
    <label>{rt("Structure (one section per line)")}<Textarea onChange={event => { setStructure(event.target.value); setReview(false) }} value={structure} /></label>
    <label>{rt("Reusable style guidance")}<Textarea onChange={event => { setStyle(event.target.value); setReview(false) }} value={style} /></label>
    <label>{rt("Names, examples and incidental content to exclude")}<Textarea onChange={event => { setExclusions(event.target.value); setReview(false) }} value={exclusions} /></label>
    <Button disabled={!connected || !project || !name || !artifact || !structure || !exclusions || Number(version) < 1} onClick={() => setReview(true)} size="xs" variant="secondary">{rt("Review and save this template")}</Button>
    <ConfirmDialog confirmLabel={rt("Save approved structure")} description={format(runtimeUiTemplates.templateCreate, { name, artifact, version, project, exclusions })} onClose={() => setReview(false)} onConfirm={save} open={review} title={rt("Save a reusable template?")} />
    {error && <p role="alert">{error}</p>}<p className="text-xs text-muted-foreground">{rt("Use a reviewed workflow to apply the template to a new topic. Inspect varied outputs before claiming the template is validated.")}</p>
  </section>
}
