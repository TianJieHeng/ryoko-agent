import type { RuntimeRequest } from '@hermes/shared/runtime-control'
import { runScheduleCommand } from '@hermes/shared/runtime-workflows'
import { useEffect, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'

import { runtimeUiTemplates, useRuntimeUiFormat, useRuntimeUiText } from './runtime-ui-copy'

interface Props { request: RuntimeRequest; sessionId: string; connected: boolean }

export function MonitorCreatePanel(props: Props) {
  const [scope, setScope] = useState({ request: props.request, sessionId: props.sessionId, generation: 0 })

  if (scope.request !== props.request || scope.sessionId !== props.sessionId) {
    setScope({ request: props.request, sessionId: props.sessionId, generation: scope.generation + 1 })

    return null
  }

  return <OwnedMonitorCreate key={scope.generation} {...props} />
}

function OwnedMonitorCreate({ request, sessionId, connected }: Props) {
  const format = useRuntimeUiFormat()
  const rt = useRuntimeUiText()
  const [project, setProject] = useState(''), [schedule, setSchedule] = useState(''), [question, setQuestion] = useState(''), [sources, setSources] = useState('')
  const [timezone, setTimezone] = useState('UTC'), [anchor, setAnchor] = useState(''), [expiry, setExpiry] = useState(''), [seconds, setSeconds] = useState('3600'), [checks, setChecks] = useState('24'), [bytes, setBytes] = useState('262144')
  const [localDelivery, setLocalDelivery] = useState(false), [review, setReview] = useState<Record<string, unknown> | null>(null), [pending, setPending] = useState<string | null>(null)
  const [busy, setBusy] = useState(false), [output, setOutput] = useState(''), [error, setError] = useState<string | null>(null)
  const lifetimeRef = useRef({ request, sessionId, connected, active: true, busy: false })

  if (lifetimeRef.current.request !== request || lifetimeRef.current.sessionId !== sessionId || lifetimeRef.current.connected !== connected) {
    lifetimeRef.current.active = false
    lifetimeRef.current = { request, sessionId, connected, active: true, busy: false }
  }

  const lifetime = lifetimeRef.current
  useEffect(() => { lifetime.active = true; setReview(null); setBusy(false);

 return () => { lifetime.active = false } }, [lifetime])
  const disabled = !connected || busy || !!pending

  const change = (work: () => void) => { work(); setReview(null); setError(null) }
  const field = (label: string, value: string, update: (value: string) => void, type = 'text') => <label>{label}<Input disabled={disabled} onChange={event => change(() => update(event.target.value))} type={type} value={value} /></label>

  function preview() {
    try {
      new Intl.DateTimeFormat('en', { timeZone: timezone }).format()

      const instant = (value: string) => { if (!/(Z|[+-]\d{2}:\d{2})$/.test(value) || !Number.isFinite(Date.parse(value))) {throw new Error(rt("Offset required"));}

 return Date.parse(value) / 1000 }

      const start = instant(anchor), end = instant(expiry), interval = Number(seconds), max_checks = Number(checks), max_bytes = Number(bytes), source_set = sources.split('\n').map(value => value.trim()).filter(Boolean)

      if (!project.trim() || !schedule.trim() || !question.trim() || source_set.length < 1 || source_set.length > 8 || new Set(source_set).size !== source_set.length || !Number.isSafeInteger(interval) || interval < 60 || interval > 31622400 || !Number.isSafeInteger(max_checks) || max_checks < 1 || max_checks > 10000 || !Number.isSafeInteger(max_bytes) || max_bytes < 1 || max_bytes > 2097152 || end <= start) {throw new Error(rt("Invalid finite monitor"))}
      setReview({ schedule_id: schedule.trim(), version: 1, project_id: project.trim(), timezone, trigger: { kind: 'interval', anchor: start, seconds: interval }, policy: { missed_run: 'skip', grace_seconds: 0, overlap: 'block' }, budget: { max_checks, max_bytes, deadline_seconds: 10 }, expires_at: end, kind: 'monitor', specification: { question, source_set, predicate: { kind: 'normalized_text' }, notify_policy: localDelivery ? 'local_runtime' : 'record_only', condition_action: null } })
      setError(null)
    } catch { setError(rt("Specify one to eight exact retained artifact IDs, unique monitor ID, IANA timezone, offset-aware start/expiry, interval ≥60 seconds and finite check/byte budgets.")) }
  }

  async function create() {
    if (!review || disabled || lifetime.busy) {return}
    lifetime.busy = true; setBusy(true); setError(null)
    const command_id = crypto.randomUUID(), definition_json = JSON.stringify(review)
    setPending(command_id); setReview(null)

    try {
      const result = await runScheduleCommand(`create ${JSON.stringify({ command_id, definition_json })}`, request, sessionId)

      if (lifetime.active) {setOutput(result)}
    } catch { if (lifetime.active) {setError(rt("Creation result unavailable. Inspect the retained command before any retry."))} }
    finally { lifetime.busy = false;

 if (lifetime.active) {setBusy(false)} }
  }

  async function inspect() {
    if (!pending || !connected || lifetime.busy) {return}
    lifetime.busy = true; setBusy(true)

    try {
      const result = await request('runtime.artifact.status', { session_id: sessionId, schema_version: 1, command_id: pending })

      if (!lifetime.active) {return}

      if (result.command_id !== pending) {throw new Error(rt("Mismatched command"))}
      setOutput(`Monitor creation control ${result.status}. Read the schedule before resume. No notification is authorized merely by creating a paused definition.`)

      if (['completed', 'cancelled', 'blocked', 'failed'].includes(result.status)) {setPending(null)}
    } catch { if (lifetime.active) {setError(rt("Original control still unavailable; no duplicate creation was sent."))} }
    finally { lifetime.busy = false;

 if (lifetime.active) {setBusy(false)} }
  }

  return <section className="grid gap-3"><h4 className="text-sm font-medium">{rt("Create a finite retained-source monitor")}</h4><p className="text-xs">{rt("Question plus exact sources, normalized-text change criterion, bounded cadence and expiry. The backend schedule is the only timer. Creation stays paused until explicitly resumed.")}</p>
    {field(rt("New monitor project ID"), project, setProject)}{field(rt("New monitor ID"), schedule, setSchedule)}{field(rt("Question to monitor"), question, setQuestion)}
    <label>{rt("Retained source artifact IDs (one per line)")}<Textarea disabled={disabled} onChange={event => change(() => setSources(event.target.value))} value={sources} /></label>
    {field(rt("Monitor timezone"), timezone, setTimezone)}{field(rt("First check ISO with offset"), anchor, setAnchor)}{field(rt("Monitor expiry ISO with offset"), expiry, setExpiry)}
    <div className="grid gap-2 md:grid-cols-3">{field(rt("Interval seconds"), seconds, setSeconds, 'number')}{field(rt("Maximum checks"), checks, setChecks, 'number')}{field(rt("Maximum bytes per check"), bytes, setBytes, 'number')}</div>
    <label className="flex gap-2 text-sm"><input checked={localDelivery} disabled={disabled} onChange={event => change(() => setLocalDelivery(event.target.checked))} type="checkbox" />{rt("Allow later local-notification policy review (this does not grant delivery)")}</label>
    <Button disabled={disabled} onClick={preview} size="xs" variant="secondary">{rt("Review paused monitor definition")}</Button>
    <ConfirmDialog confirmLabel={rt("Create exact paused monitor")} description={format(runtimeUiTemplates.monitorCreate, { project, monitor: schedule, question, sources: sources.split('\n').join(', '), seconds, start: anchor, expiry, checks, bytes, notificationPolicy: rt(localDelivery ? rt("A separate finite notification policy must be explicitly authorized.") : rt("Changes are recorded only.")) })} onClose={() => setReview(null)} onConfirm={create} open={!!review && connected} title={rt("Create this bounded paused monitor?")} />
    {pending && <div><p className="break-words text-xs">{rt("Retained creation control") + " "}{pending}</p><Button disabled={!connected || busy} onClick={() => void inspect()} size="xs" variant="secondary">{rt("Inspect original monitor creation")}</Button></div>}
    {output && <p aria-live="polite" className="whitespace-pre-wrap break-words text-xs">{output}</p>}{error && <p role="alert">{error}</p>}
  </section>
}
