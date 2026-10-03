import type { RuntimeRequest } from '@hermes/shared/runtime-control'
import { useEffect, useMemo, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'

import { prepareRuntimeExecution, runExecutionCommand } from '../../../../shared/src/runtime-execution'
import type { RuntimeExecutionPreparation } from '../../../../shared/src/runtime-execution'

import { useRuntimeUiText } from './runtime-ui-copy'

interface ExecutionPanelProps {
  request: RuntimeRequest
  sessionId: string
  connected: boolean
}

/** Ephemeral review state only; execution and recovery remain backend-owned. */
export function ExecutionPanel({ request, sessionId, connected }: ExecutionPanelProps) {
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
    prepared: RuntimeExecutionPreparation | null
  }>({ scope, output: '', error: '', busy: false, prepared: null })

  const [source, setSource] = useState({
    scope: identity,
    project: '',
    artifact: '',
    version: '1',
    pipeline: '',
    requestId: ''
  })

  const [review, setReview] = useState<object | null>(null)
  const current = view.scope === scope ? view : { output: '', error: '', busy: false, prepared: null }

  const fields =
    source.scope === identity
      ? source
      : { scope: identity, project: '', artifact: '', version: '1', pipeline: '', requestId: '' }

  const disabled = !connected || current.busy
  useEffect(() => {
    scope.active = true

    return () => {
      scope.active = false
      scope.generation++
    }
  }, [scope])

  const run = async (operation: () => Promise<{ output: string; prepared?: RuntimeExecutionPreparation | null }>) => {
    if (!connected || scope.busy || !scope.active) {
      return
    }

    scope.busy = true
    const generation = scope.generation
    setView({ scope, ...current, busy: true, error: '' })

    try {
      const result = await operation()

      if (scope.active && scope.generation === generation) {
        setView({
          scope,
          output: result.output,
          prepared: result.prepared === undefined ? current.prepared : result.prepared,
          busy: false,
          error: ''
        })
      }
    } catch (error) {
      if (scope.active && scope.generation === generation) {
        setView({
          scope,
          ...current,
          busy: false,
          error: error instanceof Error ? error.message : rt("Request unavailable; inspect current state before retrying")
        })
      }
    } finally {
      scope.busy = false
    }
  }

  const command = (argument: string) =>
    run(async () => ({ output: await runExecutionCommand(argument, request, sessionId) }))

  const change = (field: 'project' | 'artifact' | 'version' | 'pipeline', value: string) => {
    setSource({ ...fields, [field]: value, requestId: field === 'pipeline' ? fields.requestId : '' })
    setReview(null)
    setView({ scope, ...current, prepared: null })
  }

  return (
    <section aria-label={rt("Execution services")} className="grid gap-3">
      <p className="text-sm">{rt("Review exact local placement and transfer receipts before running bounded document services.")}</p>
      {!connected && (
        <p className="text-sm text-muted-foreground" role="status">{rt("Disconnected. Accepted work may still be running; reconnect and inspect its pipeline status.")}</p>
      )}
      <div className="flex flex-wrap gap-2">
        <Button disabled={disabled} onClick={() => void command('capabilities')} size="xs" variant="secondary">{rt("Inspect service capabilities")}</Button>
        <Button disabled={disabled} onClick={() => void command('receipts')} size="xs" variant="ghost">{rt("Validation receipts")}</Button>
        <Button disabled={disabled} onClick={() => void command('effects')} size="xs" variant="ghost">{rt("Effect outcomes")}</Button>
      </div>
      <form
        aria-label={rt("Prepare local pipeline")}
        className="grid gap-2"
        onSubmit={event => {
          event.preventDefault()

          if (!connected || scope.busy || !scope.active) {
            return
          }

          setReview(null)
          void run(async () => {
            const generation = scope.generation
            const requestId = fields.requestId || crypto.randomUUID()
            setSource({ ...fields, requestId })

            const prepared = await prepareRuntimeExecution(request, sessionId, {
              project_id: fields.project,
              artifact_id: fields.artifact,
              version: Number(fields.version),
              request_id: requestId
            })

            if (scope.active && scope.generation === generation) {
              setSource({ ...fields, requestId, pipeline: prepared.manifest.pipeline_id })
            }

            return { output: prepared.summary, prepared }
          })
        }}
      >
        <label className="text-sm">{rt("Project ID")}<Input
            disabled={disabled}
            onChange={event => change('project', event.target.value)}
            required
            value={fields.project}
          />
        </label>
        <label className="text-sm">{rt("Artifact ID")}<Input
            disabled={disabled}
            onChange={event => change('artifact', event.target.value)}
            required
            value={fields.artifact}
          />
        </label>
        <label className="text-sm">{rt("Exact artifact version")}<Input
            disabled={disabled}
            min="1"
            onChange={event => change('version', event.target.value)}
            required
            step="1"
            type="number"
            value={fields.version}
          />
        </label>
        <Button disabled={disabled || !fields.project || !fields.artifact} size="xs" type="submit" variant="secondary">{rt("Prepare route for review")}</Button>
      </form>
      {current.prepared && (
        <div className="grid gap-2">
          <p className="break-words text-xs text-muted-foreground">{rt("Prepared source") + " "}{current.prepared.manifest.source.artifact_id}@{current.prepared.manifest.source.version}{rt("; executor") + " "}{current.prepared.manifest.executor.executor_id}{rt("; location")}{' '}
            {current.prepared.manifest.executor.location}; {current.prepared.manifest.transfer.input_bytes}{" " + rt("bytes; manifest") + " "}{current.prepared.digest}
          </p>
          <Button
            aria-pressed={review === current.prepared}
            disabled={disabled}
            onClick={() => setReview(current.prepared)}
            size="xs"
            variant="secondary"
          >{rt("I reviewed this exact source and route")}</Button>
          <Button
            disabled={disabled || review !== current.prepared}
            onClick={() => {
              const prepared = current.prepared!
              setReview(null)
              void run(async () => ({
                output: await runExecutionCommand(
                  `execute ${prepared.manifest.pipeline_id} ${prepared.digest}`,
                  request,
                  sessionId
                ),
                prepared: null
              }))
            }}
            size="xs"
          >{rt("Execute reviewed local stages")}</Button>
        </div>
      )}
      <label className="text-sm">{rt("Pipeline ID")}<Input disabled={disabled} onChange={event => change('pipeline', event.target.value)} value={fields.pipeline} />
      </label>
      <div className="flex flex-wrap gap-2">
        <Button
          disabled={disabled || !fields.pipeline}
          onClick={() => void command(`status ${fields.pipeline}`)}
          size="xs"
          variant="secondary"
        >{rt("Inspect pipeline status")}</Button>
        <Button
          disabled={disabled || !fields.pipeline}
          onClick={() => void command(`output ${fields.pipeline}`)}
          size="xs"
          variant="ghost"
        >{rt("Verify staged output")}</Button>
      </div>
      <p className="text-xs text-muted-foreground">{rt("No remote device substitution. A prepared form is not a confirmed browser effect. Focused checks do not prove a full test suite, commit, merge or deployment. Closing this panel never cancels accepted work.")}</p>
      <div aria-busy={current.busy} aria-live="polite" className="whitespace-pre-wrap break-words text-sm">
        {current.error ? <p role="alert">{current.error}</p> : current.output}
      </div>
    </section>
  )
}
