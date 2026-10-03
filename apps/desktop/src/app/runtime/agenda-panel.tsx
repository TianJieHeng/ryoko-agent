import type { RuntimeRequest } from '@hermes/shared/runtime-control'
import { controlJson, controlRecord } from '@hermes/shared/runtime-research'
import { useEffect, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'

import { runtimeUiTemplates, useRuntimeUiFormat, useRuntimeUiText } from './runtime-ui-copy'

interface Props { request: RuntimeRequest; sessionId: string; connected: boolean }
interface WindowRow { start_at: string; end_at: string }
interface WorkRow { item_id: string; duration_minutes: number }

export function AgendaPanel(props: Props) {
  const [scope, setScope] = useState({ request: props.request, sessionId: props.sessionId, generation: 0 })

  if (scope.request !== props.request || scope.sessionId !== props.sessionId) {
    setScope({ request: props.request, sessionId: props.sessionId, generation: scope.generation + 1 })

    return null
  }

  return <OwnedAgenda key={scope.generation} {...props} />
}

function OwnedAgenda({ request, sessionId, connected }: Props) {
  const format = useRuntimeUiFormat()
  const rt = useRuntimeUiText()
  const [project, setProject] = useState(''), [artifact, setArtifact] = useState(''), [version, setVersion] = useState('1'), [digest, setDigest] = useState('')
  const [timezone, setTimezone] = useState('UTC'), [participants, setParticipants] = useState(''), [buffer, setBuffer] = useState('10'), [capacity, setCapacity] = useState('360')
  const [windows, setWindows] = useState<WindowRow[]>([{ start_at: '', end_at: '' }]), [work, setWork] = useState<WorkRow[]>([{ item_id: '', duration_minutes: 30 }])
  const [view, setView] = useState<Record<string, unknown> | null>(null), [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null)
  const lifetimeRef = useRef({ request, sessionId, connected, active: true, busy: false })

  if (lifetimeRef.current.request !== request || lifetimeRef.current.sessionId !== sessionId || lifetimeRef.current.connected !== connected) {
    lifetimeRef.current.active = false
    lifetimeRef.current = { request, sessionId, connected, active: true, busy: false }
  }

  const lifetime = lifetimeRef.current
  useEffect(() => { lifetime.active = true; setView(null); setBusy(false);

 return () => { lifetime.active = false } }, [lifetime])
  const disabled = !connected || busy

  function changed(action: () => void) { action(); setView(null); setError(null) }

  async function preview() {
    if (disabled || lifetime.busy) {return}
    lifetime.busy = true; setBusy(true); setError(null); setView(null)

    try {
      new Intl.DateTimeFormat('en', { timeZone: timezone }).format()

      if (!project.trim() || !artifact.trim() || !Number.isSafeInteger(Number(version)) || Number(version) < 1 || !/^[a-f0-9]{64}$/.test(digest)) {throw new Error(rt("Select an exact retained availability artifact version and SHA256"))}

      for (const row of windows) {if (![row.start_at, row.end_at].every(value => /(Z|[+-]\d{2}:\d{2})$/.test(value) && Number.isFinite(Date.parse(value)))) {throw new Error(rt("Each work window needs ISO start/end with explicit timezone offset"))}}

      if (!participants.trim() || !Number.isSafeInteger(Number(buffer)) || Number(buffer) < 0 || Number(buffer) > 120 || !Number.isSafeInteger(Number(capacity)) || Number(capacity) < 1 || Number(capacity) > 720 || work.some(row => !row.item_id.trim() || !Number.isSafeInteger(row.duration_minutes) || row.duration_minutes < 1 || row.duration_minutes > 720)) {throw new Error(rt("Use explicit participants, unique work IDs, bounded duration estimates and capacity"))}
      const result = await request('runtime.agenda.plan', { session_id: sessionId, schema_version: 1, project_id: project.trim(), availability_ref_json: JSON.stringify({ artifact_id: artifact.trim(), version: Number(version), sha256: digest }), timezone, participants: participants.split('\n').map(value => value.trim()).filter(Boolean), windows, work, buffer_minutes: Number(buffer), daily_capacity_minutes: Number(capacity) })
      const next = controlRecord(controlJson(result.record_json, 131072, false))

      if (next.calendar_changed !== false || next.invitation_sent !== false || next.obligations_created !== false || next.live_availability_verified !== false) {throw new Error(rt("Unexpected planning authority; no calendar action is confirmed"))}

      if (!Array.isArray(next.days) || !Array.isArray(next.overflow)) {throw new Error(rt("Malformed agenda projection"))}

      for (const raw of next.days) { const day = controlRecord(raw);

 if (!Array.isArray(day.fixed) || !Array.isArray(day.flexible)) {throw new Error(rt("Malformed agenda day"));} day.fixed.forEach(value => controlRecord(value)); day.flexible.forEach(value => controlRecord(value)) }

      next.overflow.forEach(value => controlRecord(value))

      if (lifetime.active) {setView(next)}
    } catch (caught) { if (lifetime.active) {setError(caught instanceof Error ? caught.message : rt("Agenda unavailable; no calendar changes were made"))} }
    finally { lifetime.busy = false;

 if (lifetime.active) {setBusy(false)} }
  }

  const field = (label: string, value: string, change: (value: string) => void, type = 'text') => <label>{label}<Input disabled={disabled} onChange={event => changed(() => change(event.target.value))} type={type} value={value} /></label>
  const days = Array.isArray(view?.days) ? view.days.map(value => controlRecord(value)) : [], overflow = Array.isArray(view?.overflow) ? view.overflow.map(value => controlRecord(value)) : []

  return <section className="grid gap-3"><h4 className="text-sm font-medium">{rt("Day/week plan from reviewed availability")}</h4><p className="text-xs">{rt("This local plan uses an explicit retained availability snapshot and your ordered work estimates. It never sends an invitation, writes a calendar or creates obligations.")}</p>
    {field(rt("Agenda project ID"), project, setProject)}{field(rt("Availability artifact ID"), artifact, setArtifact)}{field(rt("Availability version"), version, setVersion, 'number')}{field(rt("Availability SHA-256"), digest, setDigest)}{field(rt("Agenda timezone"), timezone, setTimezone)}
    <label>{rt("Exact participant identities (one per line)")}<Textarea disabled={disabled} onChange={event => changed(() => setParticipants(event.target.value))} value={participants} /></label>
    <div className="grid gap-2 md:grid-cols-2">{field(rt("Buffer minutes"), buffer, setBuffer, 'number')}{field(rt("Daily flexible capacity minutes"), capacity, setCapacity, 'number')}</div>
    <h5 className="text-sm">{rt("One to seven daily windows")}</h5>{windows.map((row, index) => <div className="grid gap-2 md:grid-cols-2" key={index}>{field(format(runtimeUiTemplates.agendaDayStart, { index: index + 1 }), row.start_at, value => setWindows(previous => previous.map((item, at) => at === index ? { ...item, start_at: value } : item)))}{field(format(runtimeUiTemplates.agendaDayEnd, { index: index + 1 }), row.end_at, value => setWindows(previous => previous.map((item, at) => at === index ? { ...item, end_at: value } : item)))}</div>)}
    <div className="flex gap-2"><Button disabled={disabled || windows.length === 7} onClick={() => changed(() => setWindows(previous => [...previous, { start_at: '', end_at: '' }]))} size="xs" variant="secondary">{rt("Add daily window")}</Button><Button disabled={disabled || windows.length === 1} onClick={() => changed(() => setWindows(previous => previous.slice(0, -1)))} size="xs" variant="text">{rt("Remove last day")}</Button></div>
    <h5 className="text-sm">{rt("Work in explicit priority order")}</h5>{work.map((row, index) => <div className="grid gap-2 md:grid-cols-2" key={index}>{field(format(runtimeUiTemplates.agendaWorkId, { index: index + 1 }), row.item_id, value => setWork(previous => previous.map((item, at) => at === index ? { ...item, item_id: value } : item)))}{field(format(runtimeUiTemplates.agendaWorkDuration, { index: index + 1 }), String(row.duration_minutes), value => setWork(previous => previous.map((item, at) => at === index ? { ...item, duration_minutes: Number(value) } : item)), 'number')}</div>)}
    <div className="flex gap-2"><Button disabled={disabled || work.length >= 100} onClick={() => changed(() => setWork(previous => [...previous, { item_id: '', duration_minutes: 30 }]))} size="xs" variant="secondary">{rt("Add work estimate")}</Button><Button disabled={disabled || !work.length} onClick={() => changed(() => setWork(previous => previous.slice(0, -1)))} size="xs" variant="text">{rt("Remove last estimate")}</Button></div>
    <Button disabled={disabled} onClick={() => void preview()} size="xs" variant="secondary">{rt("Preview fixed, flexible and overflow work")}</Button>
    {view && <div aria-live="polite" className="grid gap-3"><p className="text-xs">{String(view.view)}{" " + rt("plan · zone") + " "}{String(view.timezone)}{" " + rt("· captured") + " "}{String(view.captured_at)} · {view.stale ? rt("STALE availability snapshot") : rt("snapshot only, not live availability")}{" " + rt("· planned") + " "}{String(view.planning_at)}</p>{days.map(day => <article className="grid gap-1 rounded border p-2 text-xs" key={String(day.date)}><h5>{String(day.date)} · {String(day.planned_minutes)} / {String(day.capacity_minutes)}{" " + rt("flexible minutes")}</h5><p>{rt("Fixed commitments")}</p>{(Array.isArray(day.fixed) ? day.fixed : []).map((raw, index) => { const row = controlRecord(raw);

 return <p key={index}>{String(row.start_at)} → {String(row.end_at)}</p> })}<p>{rt("Flexible work")}</p>{(Array.isArray(day.flexible) ? day.flexible : []).map((raw, index) => { const row = controlRecord(raw);

 return <p key={index}>{String(row.item_id)} · {String(row.duration_minutes)}{" " + rt("minutes ·") + " "}{String(row.start_at)} → {String(row.end_at)}</p> })}</article>)}<h5 className="text-sm">{rt("Overflow (")}{overflow.length})</h5>{overflow.map(row => <p className="text-xs" key={String(row.item_id)}>{String(row.item_id)} · {String(row.duration_minutes)}{" " + rt("minutes ·") + " "}{String(row.reason)}</p>)}<p className="text-xs">{rt("No live availability, calendar write, invitation or newly accepted obligation is implied.")}</p></div>}
    {error && <p role="alert">{error}</p>}
  </section>
}
