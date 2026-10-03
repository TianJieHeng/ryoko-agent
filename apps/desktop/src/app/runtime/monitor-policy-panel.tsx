import type { RuntimeRequest } from '@hermes/shared/runtime-control'
import { controlJson, controlRecord } from '@hermes/shared/runtime-research'
import { useEffect, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { Input } from '@/components/ui/input'

import { runtimeUiTemplates, useRuntimeUiFormat, useRuntimeUiText } from './runtime-ui-copy'

type View = Record<string, unknown>
interface Props { request: RuntimeRequest; sessionId: string; connected: boolean }

export function MonitorPolicyPanel(props: Props) {
  const [scope, setScope] = useState({ request: props.request, sessionId: props.sessionId, generation: 0 })

  if (scope.request !== props.request || scope.sessionId !== props.sessionId) {
    setScope({ request: props.request, sessionId: props.sessionId, generation: scope.generation + 1 })

    return null
  }

  return <OwnedMonitorPolicy key={scope.generation} {...props} />
}

function OwnedMonitorPolicy({ request, sessionId, connected }: Props) {
  const format = useRuntimeUiFormat()
  const rt = useRuntimeUiText()
  const [project, setProject] = useState(''), [schedule, setSchedule] = useState(''), [view, setView] = useState<View | null>(null)
  const [timezone, setTimezone] = useState('UTC'), [quietStart, setQuietStart] = useState(''), [quietEnd, setQuietEnd] = useState('')
  const [digest, setDigest] = useState('0'), [maximum, setMaximum] = useState('10'), [expiry, setExpiry] = useState(''), [snooze, setSnooze] = useState('')
  const [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null), [pending, setPending] = useState<string | null>(null)
  const [review, setReview] = useState<{ kind: 'policy' | 'snooze' | 'dismiss'; value: unknown; description: string } | null>(null)
  const lifetimeRef = useRef({ request, sessionId, connected, active: true, busy: false })

  if (lifetimeRef.current.request !== request || lifetimeRef.current.sessionId !== sessionId || lifetimeRef.current.connected !== connected) {
    lifetimeRef.current.active = false
    lifetimeRef.current = { request, sessionId, connected, active: true, busy: false }
  }

  const lifetime = lifetimeRef.current
  useEffect(() => { lifetime.active = true; setReview(null); setBusy(false);

 return () => { lifetime.active = false } }, [lifetime])

  const parse = (json: string) => {
    const row = controlRecord(controlJson(json, 131072, false))

    if (!Array.isArray(row.notices) || row.notices.length > 8 || !Number.isSafeInteger(row.revision)) {throw new Error(rt("Malformed monitor projection"))}

    for (const raw of row.notices) { const notice = controlRecord(raw); controlRecord(notice.payload);

 if (notice.delivery != null) {controlRecord(notice.delivery)} }

    return row
  }

  const base = { session_id: sessionId, schema_version: 1 as const, project_id: project.trim(), schedule_id: schedule.trim() }

  async function run(work: () => Promise<void>) {
    if (!connected || lifetime.busy) {return}
    lifetime.busy = true; setBusy(true); setError(null)

    try { await work() } catch { if (lifetime.active) {setError(rt("Monitor control unavailable or changed. Inspect the original command and refresh; no automatic retry was sent."))} }
    finally { lifetime.busy = false;

 if (lifetime.active) {setBusy(false)} }
  }

  async function inspect(older = false) {
    const result = await request('runtime.monitor.notifications', { ...base, cursor_json: older && typeof view?.next_cursor_json === 'string' ? view.next_cursor_json : null })

    if (lifetime.active) {setView(parse(result.record_json))}
  }

  function instant(value: string): number {
    if (!/(Z|[+-]\d{2}:\d{2})$/.test(value)) {throw new Error(rt("Use an ISO timestamp with explicit offset"))}
    const number = Date.parse(value) / 1000

    if (!Number.isFinite(number)) {throw new Error(rt("Invalid timestamp"))}

    return number
  }

  function reviewPolicy() {
    try {
      new Intl.DateTimeFormat('en', { timeZone: timezone }).format()

      const minute = (value: string) => { if (!/^\d{2}:\d{2}$/.test(value)) {throw new Error(rt("Invalid quiet hour"));} const [hour, min] = value.split(':').map(Number);

 if (hour > 23 || min > 59) {throw new Error(rt("Invalid quiet hour"));}

 return hour * 60 + min }

      const quiet_hours = quietStart || quietEnd ? { start_minute: minute(quietStart), end_minute: minute(quietEnd), fold: 'both', gap: 'next_valid' } : null
      const digest_seconds = Number(digest), max_deliveries = Number(maximum), expires_at = instant(expiry)

      if (!Number.isInteger(digest_seconds) || !(digest_seconds === 0 || digest_seconds >= 60 && digest_seconds <= 86400) || !Number.isInteger(max_deliveries) || max_deliveries < 1 || max_deliveries > 1000 || quiet_hours?.start_minute === quiet_hours?.end_minute && quiet_hours !== null) {throw new Error(rt("Invalid finite policy"))}
      const value = { kind: 'local_runtime', timezone, quiet_hours, digest_seconds, max_deliveries, expires_at }
      setError(null); setReview({ kind: 'policy', value, description: format(runtimeUiTemplates.monitorAuthorize, { maximum: max_deliveries, expiry: new Date(expires_at * 1000).toISOString(), timezone, quiet: quiet_hours ? `${quietStart}–${quietEnd} ${rt('(both folded minutes)')}` : rt('none'), seconds: digest_seconds }) })
    } catch { setError(rt("Enter a valid IANA timezone, distinct HH:MM quiet hours (or leave both blank), finite limits and an ISO expiry with offset.")) }
  }

  async function confirm() {
    if (!review || !view || pending || !Number.isSafeInteger(view.revision)) {return}
    const command_id = crypto.randomUUID(), expected_revision = Number(view.revision), captured = review
    await run(async () => {
      setPending(command_id); setReview(null)
      const common = { ...base, command_id, expected_revision }

      const result = captured.kind === 'policy'
        ? await request('runtime.monitor.policy.set', { ...common, policy_json: JSON.stringify(captured.value) })
        : captured.kind === 'snooze'
          ? await request('runtime.monitor.snooze', { ...common, until_at: captured.value as number | null })
          : await request('runtime.monitor.dismiss', { ...common, intent_id: captured.value as string })

      if (lifetime.active) { setView(parse(result.record_json)); setPending(null) }
    })
  }

  async function controlStatus() {
    if (!pending) {return}
    const result = await request('runtime.artifact.status', { session_id: sessionId, schema_version: 1, command_id: pending })

    if (!lifetime.active) {return}

    if (result.command_id !== pending) {throw new Error(rt("Mismatched receipt"))}

    if (['completed', 'cancelled', 'blocked', 'failed'].includes(result.status)) { setPending(null); await inspect() }
    else {setError(`Original control remains ${result.status}; do not resubmit with a new identity.`)}
  }

  const notices = Array.isArray(view?.notices) ? view.notices.map(value => controlRecord(value)) : []
  const disabled = !connected || busy || !!pending

  return <section className="grid gap-3"><h4 className="text-sm font-medium">{rt("Local monitor delivery policy")}</h4>
    <p className="text-xs">{rt("Only retained-artifact monitors declared for local delivery can be authorized. No external notification service is enabled.")}</p>
    <label>{rt("Monitor project ID")}<Input disabled={busy || !!pending} onChange={event => { setProject(event.target.value); setView(null); setReview(null) }} value={project} /></label>
    <label>{rt("Monitor schedule ID")}<Input disabled={busy || !!pending} onChange={event => { setSchedule(event.target.value); setView(null); setReview(null) }} value={schedule} /></label>
    <Button disabled={!connected || busy || !project || !schedule} onClick={() => void run(() => inspect())} size="xs" variant="secondary">{rt("Inspect retained notices and policy")}</Button>
    {view && <><p className="break-words text-xs">{rt("Revision") + " "}{String(view.revision)}{" " + rt("· state") + " "}{String(view.schedule_state)}{" " + rt("· pending") + " "}{String(view.pending_total)}{" " + rt("/ total") + " "}{String(view.notices_total)}{" " + rt("· remaining deliveries") + " "}{String(view.remaining_deliveries)}</p><pre className="whitespace-pre-wrap break-words text-xs">{JSON.stringify({ policy: view.policy, destination: view.destination, snoozed_until: view.snoozed_until }, null, 2)}</pre>
      <label>{rt("Notification timezone")}<Input disabled={disabled} onChange={event => { setTimezone(event.target.value); setReview(null) }} value={timezone} /></label>
      <div className="grid gap-2 md:grid-cols-2"><label>{rt("Quiet start HH:MM")}<Input disabled={disabled} onChange={event => { setQuietStart(event.target.value); setReview(null) }} value={quietStart} /></label><label>{rt("Quiet end HH:MM")}<Input disabled={disabled} onChange={event => { setQuietEnd(event.target.value); setReview(null) }} value={quietEnd} /></label></div>
      <label>{rt("Digest seconds (0 or 60–86400)")}<Input disabled={disabled} onChange={event => { setDigest(event.target.value); setReview(null) }} type="number" value={digest} /></label>
      <label>{rt("Maximum deliveries (1–1000)")}<Input disabled={disabled} onChange={event => { setMaximum(event.target.value); setReview(null) }} type="number" value={maximum} /></label>
      <label>{rt("Policy expiry ISO with offset")}<Input disabled={disabled} onChange={event => { setExpiry(event.target.value); setReview(null) }} placeholder="2026-10-04T18:00:00Z" value={expiry} /></label>
      <Button disabled={disabled} onClick={reviewPolicy} size="xs" variant="secondary">{rt("Review exact local delivery policy")}</Button>
      <label>{rt("Snooze until ISO with offset (empty clears)")}<Input disabled={disabled} onChange={event => { setSnooze(event.target.value); setReview(null) }} value={snooze} /></label>
      <Button disabled={disabled} onClick={() => { try { const value = snooze ? instant(snooze) : null; setReview({ kind: 'snooze', value, description: value ? format(runtimeUiTemplates.monitorSnooze, { until: new Date(value * 1000).toISOString() }) : rt("Clear snooze for this monitor. Existing quiet hours, expiry and budgets still apply.") }) } catch { setError(rt("Use an ISO snooze timestamp with explicit offset")) } }} size="xs" variant="secondary">{rt("Review snooze change")}</Button>
      {notices.map(notice => <article className="grid gap-1 rounded border p-2 text-xs" key={String(notice.intent_id)}><p>{String(controlRecord(notice.payload).question)}</p><p>{rt("Notice") + " "}{String(notice.intent_id)} · {String(notice.state)}{" " + rt("· hold") + " "}{String(notice.hold_reason ?? 'none')}</p><p>{rt("Delivery") + " "}{String(notice.delivery_id ?? 'not admitted')} · {notice.delivery ? String(controlRecord(notice.delivery).state) : rt("no receipt")}</p><Button disabled={disabled || notice.dismissed_at !== null} onClick={() => setReview({ kind: 'dismiss', value: String(notice.intent_id), description: format(runtimeUiTemplates.monitorDismiss, { notice: String(notice.intent_id) }) })} size="xs" variant="text">{rt("Dismiss this notice")}</Button></article>)}
      <Button disabled={!connected || busy || typeof view.next_cursor_json !== 'string'} onClick={() => void run(() => inspect(true))} size="xs" variant="ghost">{rt("Load older retained notices")}</Button>
      {!!view.history_truncated && <p className="text-xs">{rt("This page is bounded; global pending/total counts include other pages.")}</p>}
    </>}
    {pending && <div><p className="break-words text-xs">{rt("Retained control") + " "}{pending}{rt("; outcome may be unknown.")}</p><Button disabled={!connected || busy} onClick={() => void run(controlStatus)} size="xs" variant="secondary">{rt("Inspect original notification control")}</Button></div>}
    <ConfirmDialog confirmLabel={rt("Confirm exact monitor change")} description={review?.description ?? ''} onClose={() => setReview(null)} onConfirm={confirm} open={!!review && connected} title={rt("Review monitor notification change")} />
    {error && <p role="alert">{error}</p>}
  </section>
}
