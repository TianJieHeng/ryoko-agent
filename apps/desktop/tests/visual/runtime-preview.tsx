/** Test-only Vite entry. No real gateway, credentials, provider, recording or external effects. */
import { createRoot } from 'react-dom/client'
import { useMemo, useRef, useState } from 'react'
import type { RuntimeRequest } from '@hermes/shared/runtime-control'
import { I18nProvider } from '../../src/i18n'
import { RuntimeFeaturePanel } from '../../src/app/runtime/feature-panel'
import '../../src/styles.css'

function Preview() {
  const [session, setSession] = useState('fixture-a'), [connected, setConnected] = useState(true)
  const [fail, setFail] = useState(false), [calls, setCalls] = useState<string[]>([])
  const failRef = useRef(false); failRef.current = fail
  const request = useMemo(() => (async (method: string, params: Record<string, unknown>) => {
    setCalls(previous => [...previous, method].slice(-8))
    await new Promise(resolve => setTimeout(resolve, 450))
    if (failRef.current) throw new Error('Synthetic connection failure; no real service was contacted')
    if (method === 'runtime.project.list') return { projects: [], limit_reached: false }
    if (method === 'runtime.artifact.prepare') {
      const bytes = new TextEncoder().encode(String(params.content))
      const sha256 = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))).map(byte => byte.toString(16).padStart(2, '0')).join('')
      return { request_id: params.request_id, project_id: params.project_id, artifact_id: 'fixture-artifact', version: 1, parent_version: null, expected_head_version: null, mime: 'text/markdown', size: bytes.length, sha256, approval_id: 'fixture-approval', approval_digest: 'b'.repeat(64), expires_at: Date.now() / 1000 + 300 }
    }
    if (method === 'runtime.artifact.publish') return { artifact_id: 'fixture-artifact', version: 1, disposition: 'canonical', validation_status: 'passed' }
    if (method === 'runtime.artifact.cancel' || method === 'runtime.artifact.status') return { command_id: params.command_id, status: 'cancelled', result: { effects_undone: false } }
    if (method === 'runtime.mission.list') return { missions: [{ mission_id: 'fixture-mission', revision: 3, state: 'waiting_for_source', project_id: 'fixture-project', outcome: 'Read a long retained report and prepare a reviewable, version-pinned brief '.repeat(7), next_step: 'Reconnect the configured source; no empty-source conclusion is supported', blockers: ['Source is unavailable. Retained immutable artifact remains readable.'] }], limit_reached: false }
    if (method === 'runtime.effects.list') return { effects: [], limit_reached: false }
    if (method === 'runtime.capabilities') return { schema_versions: [1], operations: [], strict_identity_required: true, durable_replay: true, max_events: 200, cursor_policy: 'snapshot_required_on_expired_or_unknown_cursor' }
    if (method === 'runtime.memory.status') throw new Error('Synthetic primary harness unavailable')
    if (method === 'runtime.events.since') return { status: 'snapshot_required', snapshot: null, events: [], last_cursor: 'fixture:1', has_more: false }
    throw new Error(`Synthetic fixture does not implement ${method}; no real backend was contacted`)
  }) as RuntimeRequest, [])
  return <I18nProvider configClient={null} initialLocale="en"><main className="mx-auto grid max-w-4xl gap-4 p-4 text-foreground"><header><h1 className="text-lg font-semibold">Synthetic runtime UI fixture</h1><p className="text-sm">Layout and interaction only. No real gateway, publication, delivery, recording or authority proof.</p></header><div className="flex flex-wrap gap-3 text-sm"><label><input checked={connected} onChange={event => setConnected(event.target.checked)} type="checkbox" /> Connected fixture</label><label><input checked={fail} onChange={event => setFail(event.target.checked)} type="checkbox" /> Return asynchronous error</label><button className="rounded border px-2" onClick={() => setSession(value => value === 'fixture-a' ? 'fixture-b' : 'fixture-a')}>Switch fixture session</button><span>{session}</span></div><RuntimeFeaturePanel connected={connected} key={session} request={request} sessionId={session} /><details><summary>Fixture request trace</summary><pre className="whitespace-pre-wrap break-words text-xs">{calls.join('\n')}</pre></details></main></I18nProvider>
}
createRoot(document.getElementById('root')!).render(<Preview />)
