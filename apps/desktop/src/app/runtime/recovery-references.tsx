import { useState, useSyncExternalStore } from 'react'

import { Button } from '@/components/ui/button'

import type { OwnedRuntimeScope, RecoveryReference } from './owned-runtime-scope'
import { useRuntimeUiText } from './runtime-ui-copy'

export function RecoveryReferences({ scope, connected }: { scope: OwnedRuntimeScope; connected: boolean }) {
  const rt = useRuntimeUiText()
  const references = useSyncExternalStore(scope.subscribe, scope.getSnapshot, scope.getSnapshot)
  const [busy, setBusy] = useState(false), [output, setOutput] = useState('')
  const request = scope.request

  async function inspect(ref: RecoveryReference) {
    if (!connected || busy) {return}
    setBusy(true)

    try {
      const base = { session_id: scope.sessionId, schema_version: 1 as const }

      const result = ref.kind === 'operator' || ref.kind === 'speech'
        ? await request('runtime.operations.inspect', base)
        : ref.kind === 'specialist'
          ? await request('runtime.specialist.status', { ...base, command_id: ref.id })
          : ref.kind === 'memory'
        ? await request('runtime.memory.output.control.get', { ...base, control_id: ref.id })
        : ref.kind === 'artifact'
          ? await request('runtime.artifact.status', { ...base, command_id: ref.id })
          : await request('runtime.snapshot', base)

      if (scope.request !== request) {return}
      setOutput(JSON.stringify(result, null, 2))
    } catch { if (scope.request === request) {setOutput(rt("Original recovery status unavailable. No mutation was replayed; the retained identity remains available."))} }
    finally { if (scope.request === request) {setBusy(false)} }
  }

  if (!references.length) {return null}

  return <section className="grid gap-2 rounded border p-2"><h4 className="text-sm font-medium">{rt("Original recovery references")}</h4><p className="text-xs">{rt("Identifiers retained for this exact connection, route and session. A cache hit never restores approval or proves completion. Reopen the relevant review after fresh backend inspection.")}</p>{references.map(ref => <div className="flex flex-wrap items-center gap-2 text-xs" key={`${ref.kind}:${ref.id}`}><span className="break-all">{ref.method} · {ref.id} · {ref.unknown ? rt("outcome unknown") : rt("prepared or pending receipt")}</span><Button disabled={!connected || busy} onClick={() => void inspect(ref)} size="xs" variant="secondary">{rt("Inspect original backend status")}</Button></div>)}{output && <pre aria-live="polite" className="max-h-48 overflow-auto whitespace-pre-wrap break-words text-xs">{output}</pre>}</section>
}
