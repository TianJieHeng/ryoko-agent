import { useEffect, useMemo, useRef, useState } from 'react'

import { useI18n } from '@/i18n'

import type { ResearchResponse } from '../../../../shared/src/gateway-contract.generated'
import type { RuntimeRequest } from '../../../../shared/src/runtime-control'
import { controlFailure, controlJson, controlRecord, publishBrief, publishInitialBrief, type ResearchSourceRequest, researchView, type ResearchView, resolveResearch, RuntimeInputError, summarizeArtifactCommand, summarizeBrief, summarizeResearch } from '../../../../shared/src/runtime-research'

import { prepareResearch, runResearchRequest } from './research-actions'
import { researchCopy } from './research-copy'
import { assertSourceBinding, sourceRequests } from './research-input'
import { type BriefDraft, emptySource, type Props, type RetainedCommand, type SourceDraft, terminalStatuses } from './research-types'
import { ResearchPanelView } from './research-view'

export function ResearchPanel({ request, sessionId, connected }: Props) {
  const { locale } = useI18n(),
    c = researchCopy(locale)

  const [sources, setSources] = useState<SourceDraft[]>([emptySource()]),
    [previous, setPrevious] = useState<SourceDraft[]>([emptySource()])

  const [draft, setDraft] = useState<BriefDraft>({
    mode: 'refresh',
    project: '',
    artifact: '',
    parent: '1',
    manifest: '',
    manifestVersion: '1',
    manifestDigest: '',
    command: '',
    requestId: '',
    claim: '',
    replacement: '',
    section: '',
    sectionDigest: '',
    original: '',
    kind: 'fact',
    citation: ''
  })

  const [coverage, setCoverage] = useState<{ view: ResearchView; summary: string } | null>(null)

  const [command, setCommand] = useState<RetainedCommand | null>(null),
    [receipt, setReceipt] = useState(''),
    [error, setError] = useState('')

  const [busy, setBusy] = useState(false),
    [reviewed, setReviewed] = useState(false),
    [confirming, setConfirming] = useState(false)

  const epoch = useRef(0),
    revision = useRef(0),
    pending = useRef<symbol | null>(null),
    reviewedRef = useRef(false)

  const retired = useRef(new Set<string>())
  const preparing = useRef<{ id: string; request: RuntimeRequest; sessionId: string; dispatched: boolean; failedBeforeDispatch: boolean } | null>(null)

  // Initial baselines use a WeakMap keyed by this exact function. Never create a
  // per-click wrapper: the same transport must reach both prepare and publish.
  const trackedRequest = useMemo<RuntimeRequest>(() => (method, params) => {
    if (method === 'runtime.brief.prepare' && 'command_id' in params && preparing.current?.id === params.command_id) {
      preparing.current.dispatched = true
    }

    return request(method, params)
  }, [request])

  // These refs own request lifetimes and synchronous approval gates, not mirrored store state.
  // Connection/session lifecycle invalidates approval, never the retained command identity.
  // eslint-disable-next-line no-restricted-syntax
  useEffect(() => {
    epoch.current++
    pending.current = null
    reviewedRef.current = false
    setBusy(false)
    setCoverage(null)
    setReviewed(false)
    setConfirming(false)
    setError('')
    setReceipt('')
    setCommand(value => {
      const stopped = preparing.current

      if (value && stopped?.failedBeforeDispatch && stopped.id === value.id && stopped.request === request && stopped.sessionId === sessionId) {return null}

      return value ? { ...value, valid: false } : value
    })

    return () => {
      // Invalidate the latest request epoch; this ref is not a DOM node.
      // eslint-disable-next-line react-hooks/exhaustive-deps
      epoch.current++
      pending.current = null
      reviewedRef.current = false
    }
  }, [request, sessionId, connected])
  const owned = command?.request === request && command?.sessionId === sessionId

  const expiry = command?.prepared
    ? Math.min(command.prepared.brief.expires_at, command.prepared.manifest.expires_at) * 1000
    : null

  // The expiry timer revokes the imperative approval gate alongside its rendered state.
  // eslint-disable-next-line no-restricted-syntax
  useEffect(() => {
    if (expiry === null || !command?.valid) {
      return
    }

    const timer = window.setTimeout(
      () => {
        reviewedRef.current = false
        setReviewed(false)
        setConfirming(false)
        setCommand(value => (value ? { ...value, valid: false } : value))
      },
      Math.min(2 ** 31 - 1, Math.max(0, expiry - Date.now()))
    )

    return () => window.clearTimeout(timer)
  }, [expiry, command?.valid])

  function edit(work: () => void) {
    revision.current++
    reviewedRef.current = false
    work()
    setReviewed(false)
    setConfirming(false)
    setCoverage(null)
    setError('')
    setReceipt('')
    setCommand(value => (value ? { ...value, valid: false } : value))
  }

  const update = <K extends keyof BriefDraft>(key: K, value: BriefDraft[K]) =>
    edit(() => setDraft(current => ({ ...current, [key]: value })))

  function run<T>(work: () => Promise<T>, apply: (result: T) => void, mutation = false, inputBound = true, propagate = false) {
    return runResearchRequest({ connected, pending, epoch, revision, preparing, reviewedRef, setBusy, setError, setReviewed, setCommand }, work, apply, mutation, inputBound, propagate)
  }

  function checkSources() {
    if (!connected || pending.current) {
      return
    }

    setCoverage(null)
    reviewedRef.current = false
    setReviewed(false)
    setConfirming(false)
    setCommand(value => (value ? { ...value, valid: false } : value))
    let selected: ResearchSourceRequest[]

    try {
      selected = sourceRequests(sources)
    } catch (caught) {
      setError(controlFailure(caught, false))

      return
    }

    void run(async () => {
      const response = await resolveResearch(selected, request, sessionId),
        view = researchView(response)

      assertSourceBinding(view, selected)

      return { view, summary: summarizeResearch(response) }
    }, setCoverage)
  }

  const prepare = () => prepareResearch({ coverage, command, pending, connected, sources, draft, retired, previous, request, sessionId, revision, run, setCommand, setReceipt, setReviewed, reviewedRef, preparing, trackedRequest, setError })

  const canReview = Boolean(
    connected &&
    owned &&
    command?.valid &&
    command.prepared &&
    command.revision === revision.current &&
    expiry &&
    expiry > Date.now() &&
    !busy
  )

  async function publish() {
    if (!canReview || !reviewedRef.current || !command?.prepared || pending.current) {
      throw new Error(c.invalid)
    }

    const prepared = command.prepared,
      captured = command

    const approvals = {
      brief_approval_id: prepared.brief.approval_id,
      brief_approval_digest: prepared.brief.approval_digest,
      manifest_approval_id: prepared.manifest.approval_id,
      manifest_approval_digest: prepared.manifest.approval_digest
    }

    await run(
      async () => {
        reviewedRef.current = false
        setReviewed(false)
        setCommand(value => (value ? { ...value, valid: false } : value))

        return typeof captured.input === 'string'
          ? publishInitialBrief(JSON.stringify({ command_id: captured.id, ...approvals }), trackedRequest, sessionId)
          : publishBrief({ ...captured.input, ...approvals }, trackedRequest, sessionId)
      },
      response => {
        setReceipt(summarizeBrief(response))

        const published =
          controlRecord(controlJson(response.response_json, 3 * 1024 * 1024, false)).state === 'published'

        if (published) {
          retired.current.add(captured.id)
        }

        setCommand(value => (value ? { ...value, terminal: published, valid: false } : value))
      },
      true,
      false,
      true
    )
  }

  function inspect(discard = false) {
    if (
      !command ||
      !owned ||
      !connected ||
      pending.current ||
      (discard && (command.terminal || command.discardRequested))
    ) {
      return
    }

    const captured = command

    if (discard) {
      reviewedRef.current = false
      setReviewed(false)
      setConfirming(false)
      setCommand(value => (value ? { ...value, valid: false, discardRequested: true } : value))
    }

    void run(
      () =>
        request(discard ? 'runtime.artifact.cancel' : 'runtime.artifact.status', {
          session_id: sessionId,
          schema_version: 1,
          command_id: captured.id
        }),
      status => {
        if (status.command_id !== captured.id) {
          throw new RuntimeInputError('Command receipt belongs to a different command')
        }

        const terminal = terminalStatuses.has(status.status)

        if (terminal) {
          retired.current.add(captured.id)
          reviewedRef.current = false
          setReviewed(false)
          setConfirming(false)
        }

        setReceipt(
          summarizeArtifactCommand(status) +
            (status.result && 'response_json' in status.result
              ? `\n${summarizeBrief(status.result as ResearchResponse)}`
              : '')
        )
        setCommand(value => (value ? { ...value, terminal, valid: !terminal && !discard && value.valid } : value))
      },
      discard,
      false
    )
  }

  return <ResearchPanelView {...{ c, connected, busy, sources, previous, setSources, setPrevious, edit, checkSources, coverage, draft, update, command, owned, canReview, reviewed, setReviewed, reviewedRef, confirming, setConfirming, receipt, error, prepare, publish, inspect }} />
}
