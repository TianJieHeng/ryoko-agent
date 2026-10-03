import type { Dispatch, RefObject, SetStateAction } from 'react'

import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { ErrorState } from '@/components/ui/error-state'
import { Input } from '@/components/ui/input'
import { Loader } from '@/components/ui/loader'
import { SegmentedControl } from '@/components/ui/segmented-control'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'

import type { ArtifactProposalResult } from '../../../../shared/src/gateway-contract.generated'
import { artifactReference, type ResearchView } from '../../../../shared/src/runtime-research'

import type { Copy } from './research-copy'
import { type BriefDraft, emptySource, type RetainedCommand, type SourceDraft } from './research-types'

type Setter<T> = Dispatch<SetStateAction<T>>
interface ViewProps {
  c: Copy; connected: boolean; busy: boolean; sources: SourceDraft[]; previous: SourceDraft[]
  setSources: Setter<SourceDraft[]>; setPrevious: Setter<SourceDraft[]>; edit: (work: () => void) => void
  checkSources: () => void; coverage: { view: ResearchView; summary: string } | null; draft: BriefDraft
  update: <K extends keyof BriefDraft>(key: K, value: BriefDraft[K]) => void; command: RetainedCommand | null
  owned: boolean; canReview: boolean; reviewed: boolean; setReviewed: Setter<boolean>; reviewedRef: RefObject<boolean>
  confirming: boolean; setConfirming: Setter<boolean>; receipt: string; error: string
  prepare: () => void; publish: () => Promise<void>; inspect: (discard?: boolean) => void
}

function Field({
  label,
  value,
  onChange,
  multiline = false,
  type = 'text'
}: {
  label: string
  value: string
  onChange: (value: string) => void
  multiline?: boolean
  type?: 'text' | 'number'
}) {
  return (
    <label className="grid gap-1 text-xs">
      {label}
      {multiline ? (
        <Textarea onChange={event => onChange(event.target.value)} value={value} />
      ) : (
        <Input onChange={event => onChange(event.target.value)} type={type} value={value} />
      )}
    </label>
  )
}

function SourceFields({
  value,
  onChange,
  copy
}: {
  value: SourceDraft
  onChange: (draft: SourceDraft) => void
  copy: Copy
}) {
  const change = (key: keyof SourceDraft, text: string) => onChange({ ...value, [key]: text })

  return (
    <div className="grid gap-2">
      <SegmentedControl
        onChange={sourceType => onChange({ ...value, sourceType })}
        options={[
          { id: 'project_artifact', label: copy.artifactSource },
          { id: 'capture_original', label: copy.captureSource }
        ]}
        value={value.sourceType}
      />
      <div className="grid gap-2 sm:grid-cols-2">
        <Field label={copy.source} onChange={value => change('sourceId', value)} value={value.sourceId} />
        <Field label={copy.project} onChange={value => change('projectId', value)} value={value.projectId} />
        <Field label={copy.version} onChange={value => change('version', value)} type="number" value={value.version} />
        <Field label="SHA-256" onChange={value => change('digest', value)} value={value.digest} />
      </div>
      <label className="grid gap-1 text-xs">
        {copy.authority}
        <Select
          onValueChange={authority => onChange({ ...value, authority: authority as SourceDraft['authority'] })}
          value={value.authority}
        >
          <SelectTrigger aria-label={copy.authority}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {(['source_claim', 'authoritative_spec', 'casual_note', 'user_statement'] as const).map(authority => (
              <SelectItem key={authority} value={authority}>
                {authority}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </label>
      <Field
        label={copy.freshness}
        onChange={value => change('freshUntil', value)}
        type="number"
        value={value.freshUntil}
      />
      <details>
        <summary className="text-xs">{copy.evidence}</summary>
        <div className="grid gap-2">
          <Field label={copy.start} onChange={value => change('start', value)} type="number" value={value.start} />
          <Field label={copy.end} onChange={value => change('end', value)} type="number" value={value.end} />
          <Field label={copy.rangeDigest} onChange={value => change('rangeDigest', value)} value={value.rangeDigest} />
          <Field label={copy.quote} multiline onChange={value => change('quote', value)} value={value.quote} />
        </div>
      </details>
    </div>
  )
}

function PreparedOutputs({ outputs }: { outputs: ArtifactProposalResult[] }) {
  return <ol className="grid gap-2 break-words text-xs">{outputs.map((output, index) => <li key={output.approval_id}>
    {index + 1}. {output.project_id} / {artifactReference(output)}<br />
    {output.approval_id}: {output.approval_digest}<br />
    {`parent_version: ${output.parent_version}; expected_head_version: ${output.expected_head_version}; expires_at: ${output.expires_at}`}
  </li>)}</ol>
}

export function ResearchPanelView({ c, connected, busy, sources, previous, setSources, setPrevious, edit, checkSources, coverage, draft, update, command, owned, canReview, reviewed, setReviewed, reviewedRef, confirming, setConfirming, receipt, error, prepare, publish, inspect }: ViewProps) {
  function sourceEditor(values: SourceDraft[], setter: (value: SourceDraft[]) => void, heading: string) {
    return (
      <fieldset className="grid gap-3" disabled={busy}>
        <legend className="text-sm font-medium">{heading}</legend>
        {values.map((source, index) => (
          <fieldset className="grid gap-2" key={index}>
            <legend className="text-xs">
              {heading} {index + 1}
            </legend>
            <SourceFields
              copy={c}
              onChange={value => edit(() => setter(values.map((current, at) => (at === index ? value : current))))}
              value={source}
            />
            <Button
              disabled={values.length === 1}
              onClick={() => edit(() => setter(values.filter((_, at) => at !== index)))}
              size="xs"
              type="button"
              variant="text"
            >
              {c.remove}
            </Button>
          </fieldset>
        ))}
        <Button
          disabled={values.length >= 32}
          onClick={() => edit(() => setter([...values, emptySource()]))}
          size="xs"
          type="button"
          variant="secondary"
        >
          {c.add}
        </Button>
      </fieldset>
    )
  }

  return (
    <section aria-label={c.title} className="grid gap-4">
      <h4 className="text-sm font-medium">{c.title}</h4>
      <p className="text-xs text-muted-foreground">{c.scope}</p>
      {!connected && <p role="status">{c.disconnected}</p>}
      {sourceEditor(sources, setSources, c.current)}
      <Button disabled={!connected || busy} onClick={checkSources} size="xs" variant="secondary">
        {c.resolve}
      </Button>
      {coverage ? (
        <section aria-label={c.coverage}>
          <h5 className="text-sm font-medium">{c.coverage}</h5>
          <p className="whitespace-pre-wrap break-words text-xs">{coverage.summary}</p>
        </section>
      ) : (
        <p className="text-xs text-muted-foreground">{c.noCoverage}</p>
      )}
      <fieldset className="grid gap-3" disabled={busy}>
        <SegmentedControl
          onChange={value => update('mode', value)}
          options={[
            { id: 'refresh', label: c.refresh },
            { id: 'initial', label: c.initial }
          ]}
          value={draft.mode}
        />
        <div className="grid gap-2 sm:grid-cols-2">
          <Field label={c.project} onChange={value => update('project', value)} value={draft.project} />
          <Field label={c.brief} onChange={value => update('artifact', value)} value={draft.artifact} />
          <Field label={c.parent} onChange={value => update('parent', value)} type="number" value={draft.parent} />
          <Field label={c.command} onChange={value => update('command', value)} value={draft.command} />
          <Field label={c.request} onChange={value => update('requestId', value)} value={draft.requestId} />
        </div>
        {draft.mode === 'refresh' ? (
          <div className="grid gap-2">
            <Field label={c.manifest} onChange={value => update('manifest', value)} value={draft.manifest} />
            <Field
              label={c.manifestVersion}
              onChange={value => update('manifestVersion', value)}
              type="number"
              value={draft.manifestVersion}
            />
            <Field
              label={c.manifestDigest}
              onChange={value => update('manifestDigest', value)}
              value={draft.manifestDigest}
            />
          </div>
        ) : (
          <>
            {sourceEditor(previous, setPrevious, c.previous)}
            <Field label={c.section} onChange={value => update('section', value)} value={draft.section} />
            <Field
              label={c.sectionDigest}
              onChange={value => update('sectionDigest', value)}
              value={draft.sectionDigest}
            />
            <Field label={c.original} onChange={value => update('original', value)} value={draft.original} />
            <label className="grid gap-1 text-xs">
              {c.kind}
              <Select onValueChange={value => update('kind', value as BriefDraft['kind'])} value={draft.kind}>
                <SelectTrigger aria-label={c.kind}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {(['fact', 'interpretation'] as const).map(kind => (
                    <SelectItem key={kind} value={kind}>
                      {kind}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </label>
            <Field label={c.citation} onChange={value => update('citation', value)} value={draft.citation} />
          </>
        )}
        <Field label={c.claim} onChange={value => update('claim', value)} value={draft.claim} />
        <Field
          label={c.replacement}
          multiline
          onChange={value => update('replacement', value)}
          value={draft.replacement}
        />
      </fieldset>
      <p className="text-xs text-muted-foreground">{c.lease}</p>
      <Button
        disabled={
          !connected ||
          busy ||
          !coverage?.view.complete ||
          Boolean(coverage.view.stale) ||
          Boolean(command && !command.terminal) ||
          !draft.command ||
          !draft.claim ||
          !draft.replacement
        }
        onClick={prepare}
        size="xs"
        variant="secondary"
      >
        {c.prepare}
      </Button>
      {command && (
        <section className="grid gap-2">
          <p className="break-words text-xs">{command.id}</p>
          {!owned ? (
            <p role="status">{c.otherScope}</p>
          ) : (
            <>
              {command.prepared && (
                <details open>
                  <summary className="text-sm font-medium">{c.details}</summary>
                  {command.valid ? <p className="whitespace-pre-wrap break-words text-xs">{command.prepared.summary}</p> : <PreparedOutputs outputs={[command.prepared.brief, command.prepared.manifest]} />}
                </details>
              )}
              {!command.valid && !command.terminal && <p role="status">{c.invalid}</p>}
              {command.prepared && !command.terminal && (
                <>
                  <label className="flex items-start gap-2 text-xs">
                    <Checkbox
                      checked={reviewed}
                      disabled={!canReview}
                      onCheckedChange={value => {
                        reviewedRef.current = value === true && canReview
                        setReviewed(reviewedRef.current)
                      }}
                    />
                    {c.review}
                  </label>
                  <Button
                    disabled={!canReview || !reviewed}
                    onClick={() => setConfirming(true)}
                    size="xs"
                    variant="secondary"
                  >
                    {c.publish}
                  </Button>
                </>
              )}
              <div className="flex flex-wrap gap-2">
                <Button disabled={!connected || busy} onClick={() => inspect()} size="xs" variant="secondary">
                  {c.inspect}
                </Button>
                <Button
                  disabled={!connected || busy || command.terminal || command.discardRequested}
                  onClick={() => inspect(true)}
                  size="xs"
                  variant="text"
                >
                  {c.discard}
                </Button>
              </div>
              {receipt && (
                <p aria-live="polite" className="whitespace-pre-wrap break-words text-xs">
                  {receipt}
                </p>
              )}
            </>
          )}
        </section>
      )}
      {busy && <Loader label={c.validation} />}
      {error && (
        <div role="alert">
          <ErrorState description={error} title={c.validation} />
        </div>
      )}
      <ConfirmDialog
        confirmLabel={c.publish}
        description={c.nonAtomic}
        onClose={() => setConfirming(false)}
        onConfirm={publish}
        open={confirming && owned && connected}
        title={c.confirm}
      >
        {command?.prepared && (
<PreparedOutputs outputs={[command.prepared.brief, command.prepared.manifest]} />
        )}
      </ConfirmDialog>
    </section>
  )
}
