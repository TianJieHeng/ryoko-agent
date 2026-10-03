import type { RuntimeRequest } from '@hermes/shared/runtime-control'
import { useEffect, useMemo, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'

import type { ChannelBindParams, SubagentListResult } from '../../../../shared/src/gateway-contract.generated'
import { bindRuntimeChannel, runSpecialistCommand } from '../../../../shared/src/runtime-specialists'

import { useRuntimeUiText } from './runtime-ui-copy'

interface SpecialistPanelProps {
  request: RuntimeRequest
  sessionId: string
  connected: boolean
}

export function SpecialistPanel({ request, sessionId, connected }: SpecialistPanelProps) {
  const rt = useRuntimeUiText()
  const identity = useMemo(() => ({ request, sessionId }), [request, sessionId])

  const scope = useMemo(
    () => ({ identity, connected, active: true, busy: false, generation: 0 }),
    [identity, connected]
  )

  const [view, setView] = useState<{
    scope: object
    output: string
    error: string
    busy: boolean
    roster: SubagentListResult | null
  }>({ scope, output: '', error: '', busy: false, roster: null })

  const [form, setForm] = useState({
    scope: identity,
    child: '',
    message: '',
    channel: 'local_jsonrpc' as ChannelBindParams['channel'],
    binding: '',
    inputId: '',
    revision: '',
    task: '',
    attempted: false
  })

  const current = view.scope === scope ? view : { output: '', error: '', busy: false, roster: null }

  const fields =
    form.scope === identity
      ? form
      : {
          scope: identity,
          child: '',
          message: '',
          channel: 'local_jsonrpc' as ChannelBindParams['channel'],
          binding: '',
          inputId: '',
          revision: '',
          task: '',
          attempted: false
        }

  const disabled = !connected || current.busy
  const selected = current.roster?.subagents?.find(child => child.subagent_id === fields.child)

  const terminal =
    selected && ['completed', 'failed', 'error', 'timeout', 'interrupted'].includes(selected.status ?? '')

  useEffect(() => {
    scope.active = true

    return () => {
      scope.active = false
      scope.generation++
    }
  }, [scope])

  const run = async (operation: () => Promise<{ output: string; roster?: SubagentListResult }>) => {
    if (!connected || scope.busy || !scope.active) {
      return
    }

    scope.busy = true
    const generation = scope.generation
    setView({ scope, ...current, busy: true, error: '' })

    try {
      const result = await operation()

      if (scope.active && scope.generation === generation) {
        setView({ scope, output: result.output, roster: result.roster ?? current.roster, busy: false, error: '' })
      }
    } catch (error) {
      if (scope.active && scope.generation === generation) {
        setView({
          scope,
          ...current,
          busy: false,
          error: error instanceof Error ? error.message : rt("Request unavailable; refresh the owned session before acting")
        })
      }
    } finally {
      scope.busy = false
    }
  }

  const command = (argument: string) =>
    run(async () => ({ output: await runSpecialistCommand(argument, request, sessionId) }))

  const childId = fields.child

  return (
    <section aria-label={rt("Specialists and media")} className="grid gap-3">
      <p className="text-sm">{rt("Inspect session-owned children. A visible child may still lack current transport authority for control.")}</p>
      {!connected && (
        <p className="text-sm text-muted-foreground" role="status">{rt("Disconnected. Children and accepted mission work may continue; reconnect and refresh before acting.")}</p>
      )}
      <div className="flex flex-wrap gap-2">
        <Button
          disabled={disabled}
          onClick={() =>
            void run(async () => {
              let roster: SubagentListResult

              try {
                roster = await request('subagent.list', { session_id: sessionId })
              } catch {
                throw new Error(
                  rt("Child roster unavailable. Refresh the owned session and connection; no alternate identity or private memory used.")
                )
              }

              return {
                roster,
                output:
                  'Fresh conversation roster. Last-started tools do not prove current activity; missing children do not prove completion.'
              }
            })
          }
          size="xs"
          variant="secondary"
        >{rt("Refresh child roster")}</Button>
        <Button disabled={disabled} onClick={() => void command('manifest')} size="xs" variant="ghost">{rt("Specialist availability")}</Button>
      </div>
      <ul aria-label={rt("Session children")} className="grid gap-2">
        {current.roster?.subagents?.map(child => (
          <li className="grid gap-1" key={child.subagent_id}>
            <Button
              aria-pressed={childId === child.subagent_id}
              disabled={disabled}
              onClick={() => setForm({ ...fields, child: child.subagent_id })}
              size="xs"
              variant="secondary"
            >
              {child.subagent_id}: {child.status ?? 'unknown'}
            </Button>
            <p className="break-words text-xs text-muted-foreground">{rt("Assignment:") + " "}{child.goal || 'unreported'}{rt("; parent") + " "}{child.parent_id || 'unreported'}{rt("; delegation")}{' '}
              {child.delegation_id || 'unreported'}
            </p>
          </li>
        ))}
        {current.roster && !current.roster.subagents?.length && (
          <li className="text-sm">{rt("No live child rows returned")}</li>
        )}
        {current.roster?.delegations?.map(child => (
          <li className="text-sm" key={`${child.delegation_id}:${child.task_index}`}>{rt("Delegation") + " "}{child.delegation_id}: {child.status}{rt("; task") + " "}{child.task_index ?? 'unreported'}
          </li>
        ))}
      </ul>
      <form
        aria-label={rt("Steer selected child")}
        className="grid gap-2"
        onSubmit={event => {
          event.preventDefault()

          if (!connected || scope.busy || !scope.active) {
            return
          }

          if (selected && !terminal && selected.accepting_steer !== false) {
            void command(`steer ${JSON.stringify({ subagent_id: childId, text: fields.message })}`)
          }
        }}
      >
        <label className="text-sm">{rt("Instructions for selected child")}<Input
            disabled={disabled || !selected || !!terminal}
            onChange={event => setForm({ ...fields, message: event.target.value })}
            value={fields.message}
          />
        </label>
        <div className="flex flex-wrap gap-2">
          <Button
            disabled={
              disabled || !selected || !!terminal || selected.accepting_steer === false || !fields.message.trim()
            }
            size="xs"
            type="submit"
            variant="secondary"
          >{rt("Queue steer")}</Button>
          <Button
            disabled={disabled || !selected || !!terminal}
            onClick={() => void command(`interrupt ${childId}`)}
            size="xs"
            type="button"
            variant="destructive"
          >{rt("Interrupt selected child")}</Button>
        </div>
      </form>
      <p className="text-xs text-muted-foreground">{rt("Queued steering is not delivered steering. Named specialist manifests and team budget/dependency views are unavailable. Specialist built-in memories remain isolated; personal memory is never offered as team context.")}</p>
      <div aria-label={rt("Media controls")} className="flex flex-wrap gap-2" role="group">
        <Button disabled={disabled} onClick={() => void command('media')} size="xs" variant="secondary">{rt("Inspect media capabilities")}</Button>
        <Button disabled={disabled} onClick={() => void command('voice-stop')} size="xs" variant="ghost">{rt("Stop speech only")}</Button>
        <Button disabled={disabled} onClick={() => void command('voice-discard')} size="xs" variant="ghost">{rt("Discard captured audio")}</Button>
      </div>
      <p className="text-xs text-muted-foreground">{rt("Call hangup belongs to the existing call UI. These controls do not cancel accepted work or start recording. Selected-window capture is explicit; continuous capture and OS actions are unavailable here.")}</p>
      <div aria-label={rt("Local handoff channel")} className="flex flex-wrap gap-2" role="group">
        {(['local_jsonrpc', 'voice', 'screen'] as const).map(channel => (
          <Button
            aria-pressed={fields.channel === channel}
            disabled={disabled}
            key={channel}
            onClick={() => setForm({ ...fields, channel, binding: '' })}
            size="xs"
            variant="ghost"
          >
            {channel}
          </Button>
        ))}
        <Button
          disabled={disabled}
          onClick={() =>
            void run(async () => {
              const generation = scope.generation
              const binding = await bindRuntimeChannel(request, sessionId, fields.channel)

              if (scope.active && scope.generation === generation) {
                setForm({ ...fields, binding: binding.bindingId })
              }

              return { output: binding.summary }
            })
          }
          size="xs"
          variant="secondary"
        >{rt("Bind existing mission")}</Button>
      </div>
      <form
        aria-label={rt("Submit same-mission input")}
        className="grid gap-2"
        onSubmit={event => {
          event.preventDefault()

          if (!connected || scope.busy || !scope.active) {
            return
          }

          if (fields.attempted) {
            return
          }

          const inputId = fields.inputId || crypto.randomUUID()
          setForm({ ...fields, inputId, attempted: true })
          void command(
            `channel-submit ${JSON.stringify({ binding_id: fields.binding, input_id: inputId, expected_revision: Number(fields.revision), operation: 'submit', payload: { text: fields.task } })}`
          )
        }}
      >
        <label className="text-sm">{rt("Verified binding ID")}<Input
            disabled={disabled || fields.attempted}
            onChange={event => setForm({ ...fields, binding: event.target.value })}
            value={fields.binding}
          />
        </label>
        <label className="text-sm">{rt("Expected runtime revision")}<Input
            disabled={disabled || fields.attempted}
            min="0"
            onChange={event => setForm({ ...fields, revision: event.target.value })}
            step="1"
            type="number"
            value={fields.revision}
          />
        </label>
        <label className="text-sm">{rt("Confirmed task text")}<Input
            disabled={disabled || fields.attempted}
            onChange={event => setForm({ ...fields, task: event.target.value })}
            value={fields.task}
          />
        </label>
        <Button
          disabled={disabled || fields.attempted || !fields.binding || fields.revision === '' || !fields.task.trim()}
          size="xs"
          type="submit"
          variant="secondary"
        >{rt("Submit to existing mission")}</Button>
        {fields.attempted && (
          <p className="break-words text-xs text-muted-foreground">{rt("Logical input") + " "}{fields.inputId}{" " + rt("was attempted. Inspect the durable mission receipt before creating another input; this panel never retries automatically.")}</p>
        )}
      </form>
      <p className="text-xs text-muted-foreground">{rt("Binding verifies the existing local mission and replays no history. It does not connect an external channel or move work to another device. Closing this panel does not cancel work.")}</p>
      <div aria-busy={current.busy} aria-live="polite" className="whitespace-pre-wrap break-words text-sm">
        {current.error ? <p role="alert">{current.error}</p> : current.output}
      </div>
    </section>
  )
}
