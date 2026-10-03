import type { Dispatch, RefObject, SetStateAction } from 'react'

import type { RuntimeRequest } from '../../../../shared/src/runtime-control'
import { artifactProposal, controlDigest, controlFailure, controlId, controlInteger, controlJson, controlRecord, prepareBrief, prepareInitialBrief, type ResearchSourceRequest, researchView, type ResearchView, RuntimeInputError, summarizeBrief } from '../../../../shared/src/runtime-research'

import { assertSourceBinding, sourceRequests } from './research-input'
import type { BriefDraft, OwnedBrief, Preparing, RetainedCommand, SourceDraft } from './research-types'
type Setter<T> = Dispatch<SetStateAction<T>>
export interface ResearchRun { <T>(work: () => Promise<T>, apply: (result: T) => void, mutation?: boolean, inputBound?: boolean, propagate?: boolean): Promise<void> }
interface RunContext {
  connected: boolean; pending: RefObject<symbol | null>; epoch: RefObject<number>; revision: RefObject<number>
  preparing: RefObject<Preparing | null>; reviewedRef: RefObject<boolean>; setBusy: Setter<boolean>; setError: Setter<string>
  setReviewed: Setter<boolean>; setCommand: Setter<RetainedCommand | null>
}
interface PrepareContext {
  coverage: { view: ResearchView; summary: string } | null; command: RetainedCommand | null; pending: RefObject<symbol | null>
  connected: boolean; sources: SourceDraft[]; draft: BriefDraft; retired: RefObject<Set<string>>; previous: SourceDraft[]
  request: RuntimeRequest; sessionId: string; revision: RefObject<number>; run: ResearchRun; setCommand: Setter<RetainedCommand | null>
  setReceipt: Setter<string>; setReviewed: Setter<boolean>; reviewedRef: RefObject<boolean>; preparing: RefObject<Preparing | null>
  trackedRequest: RuntimeRequest; setError: Setter<string>
}

export async function runResearchRequest<T>(
  context: RunContext,
    work: () => Promise<T>,
    apply: (result: T) => void,
    mutation = false,
    inputBound = true,
    propagate = false
  ) {
    const { connected, pending, epoch, revision, preparing, reviewedRef, setBusy, setError, setReviewed, setCommand } = context

    if (!connected || pending.current) {
      return
    }

    const token = Symbol(),
      flight = epoch.current,
      inputVersion = revision.current

    const current = () =>
      flight === epoch.current && pending.current === token && (!inputBound || inputVersion === revision.current)

    pending.current = token
    setBusy(true)
    setError('')

    try {
      const result = await work()

      if (current()) {
        apply(result)
      }
    } catch (caught) {
      const noDispatch = mutation && preparing.current?.failedBeforeDispatch === true
      const message = controlFailure(caught, mutation && !noDispatch)

      if (current()) {
        setError(message)

        if (mutation) {
          reviewedRef.current = false
          setReviewed(false)
          setCommand(value => noDispatch && value?.id === preparing.current?.id ? null : value ? { ...value, valid: false } : value)
        }
      }

      if (propagate && current()) {
        throw new Error(message)
      }
    } finally {
      if (pending.current === token) {
        pending.current = null

        if (flight === epoch.current) {
          setBusy(false)
        }
      }
    }
  }


export function prepareResearch(context: PrepareContext) {
    const { coverage, command, pending, connected, sources, draft, retired, previous, request, sessionId, revision, run, setCommand, setReceipt, setReviewed, reviewedRef, preparing, trackedRequest, setError } = context

    if (
      !coverage?.view.complete ||
      coverage.view.stale ||
      (command && !command.terminal) ||
      pending.current ||
      !connected
    ) {
      return
    }

    let input: OwnedBrief | string, selected: ResearchSourceRequest[]

    try {
      selected = sourceRequests(sources)

      const common = {
        project_id: controlId(draft.project),
        artifact_id: controlId(draft.artifact),
        parent_version: controlInteger(Number(draft.parent), 1),
        command_id: controlId(draft.command),
        request_id: controlId(draft.requestId)
      }

      if (retired.current.has(common.command_id)) {
        throw new RuntimeInputError('Use a new command ID after a terminal receipt')
      }

      const updates = [{ claim_id: controlId(draft.claim), replacement: draft.replacement }]

      if (draft.mode === 'initial') {
        input = JSON.stringify({
          ...common,
          previous_requests: sourceRequests(previous),
          requests: selected,
          updates,
          claims: [
            {
              claim_id: draft.claim,
              section: controlId(draft.section),
              text: draft.original,
              kind: draft.kind,
              section_sha256: controlDigest(draft.sectionDigest),
              citations: [{ source_id: controlId(draft.citation), range_index: 0 }]
            }
          ]
        })
      } else {
        input = {
          ...common,
          manifest_ref: {
            artifact_id: controlId(draft.manifest),
            version: controlInteger(Number(draft.manifestVersion), 1),
            sha256: controlDigest(draft.manifestDigest)
          },
          request_json: JSON.stringify({ requests: selected, updates })
        }
      }
    } catch (caught) {
      setError(controlFailure(caught, false))

      return
    }

    const retained: RetainedCommand = {
      id: draft.command,
      request,
      sessionId,
      revision: revision.current,
      terminal: false,
      valid: false,
      discardRequested: false,
      input,
      mode: draft.mode,
      sources: selected
    }

    void run(
      async () => {
        setCommand(retained)
        setReceipt('')
        setReviewed(false)
        reviewedRef.current = false

        const tracker = { id: retained.id, request, sessionId, dispatched: false, failedBeforeDispatch: false }
        preparing.current = tracker

        try {
          return typeof input === 'string'
            ? await prepareInitialBrief(input, trackedRequest, sessionId)
            : await prepareBrief(input, trackedRequest, sessionId)
        } catch (caught) {
          tracker.failedBeforeDispatch = !tracker.dispatched
          throw caught
        }
      },
      response => {
        const summary = summarizeBrief(response),
          row = controlRecord(controlJson(response.response_json, 3 * 1024 * 1024, false))

        if (row.state !== 'awaiting_approval') {
          throw new RuntimeInputError('Preparation did not return an approval proposal')
        }

        const view = researchView({ response_json: JSON.stringify(row.source_manifest) })
        assertSourceBinding(view, selected)

        if (!view.complete || view.stale) {
          throw new RuntimeInputError('Current source evidence is missing or stale; retain the previous brief')
        }

        setCommand({
          ...retained,
          valid: true,
          prepared: { brief: artifactProposal(row.brief), manifest: artifactProposal(row.manifest), summary }
        })
      },
      true
    )
  }
