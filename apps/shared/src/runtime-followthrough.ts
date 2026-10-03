import type { CommitmentDue, RpcMethods, ScheduleRecordResult } from './gateway-contract.generated.js'
import type { RuntimeRequest } from './runtime-control.js'
import {
  controlChoice, controlDigest, controlFailure, controlId, controlInteger, controlJson,
  controlList, controlRecord, controlRef, controlText, RuntimeInputError, summarizeArtifactCommand
} from './runtime-research.js'

type Owned<M extends keyof RpcMethods> = Omit<RpcMethods[M]['params'], 'session_id' | 'schema_version'>

function record(result: ScheduleRecordResult): Record<string, unknown> { return controlRecord(controlJson(result.record_json, 131072, false)) }

function words(rest: string, count: number): string[] {
  const values = rest.trim().split(/\s+/u)

  if (values.length !== count || values.some(value => !value)) {throw new RuntimeInputError(`Expected ${count} arguments; use help for syntax`)}

  return values.map(controlId)
}

function text(value: unknown, maximum: number): string {
  if (typeof value !== 'string' || !value.trim() || value.trim() !== value || value.length > maximum || [...value].some(char => { const code = char.charCodeAt(0);

 return (code < 32 && code !== 9 && code !== 10) || code === 127 })) {throw new RuntimeInputError('Expected bounded, nonempty text without unsafe control characters')}

  return value
}

function zone(value: unknown): string {
  const result = controlText(value, 128)

  try { new Intl.DateTimeFormat('en-US', { timeZone: result }).format(0) } catch { throw new RuntimeInputError('Use an available IANA timezone') }

  return result
}

function timestamp(value: unknown): string {
  const result = controlText(value, 64)

  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:\d{2})$/u.test(result) || !Number.isFinite(Date.parse(result))) {throw new RuntimeInputError('Use an ISO timestamp with an explicit offset; relative dates are unresolved')}

  return result
}

function due(value: unknown): CommitmentDue | null {
  if (value == null) {return null}
  const row = controlRecord(value, ['at', 'timezone', 'kind'], [])

  return { at: timestamp(row.at), timezone: zone(row.timezone), kind: controlChoice(row.kind, ['due', 'check']) }
}

function refsJson(value: unknown, list: boolean): string {
  if (typeof value !== 'string') {throw new RuntimeInputError('Source references must preserve their exact JSON string')}
  const parsed = controlJson(value, list ? 16384 : 2048)

  if (list) {controlList(parsed, 1, 20).forEach(controlRef)}
  else {controlRef(parsed)}

  return value
}

function summaryText(value: unknown, maximum: number, detail: boolean): string {
  const result = text(value, maximum)

  return detail || result.length <= 240 ? result : `${result.slice(0, 240)}… (open exact record for full text)`
}

function sourceSummary(value: unknown): string {
  return controlList(value, 1, 100).map(value => { const ref = controlRef(value);

 return `${ref.artifact_id} v${ref.version} SHA-256 ${ref.sha256}` }).join('; ')
}

export function summarizeCommitment(value: unknown, detail = false): string {
  const row = controlRecord(value), accepted = row.accepted === true

  if (!accepted && row.accepted !== false) {throw new RuntimeInputError('Commitment acceptance is not established')}
  const id = accepted ? controlId(row.commitment_id) : controlId(row.candidate_id)
  const state = accepted ? controlChoice(row.state, ['ready', 'waiting', 'done', 'cancelled', 'superseded']) : 'candidate; not accepted'
  const when = due(row.due_or_check_at)

  const lines = [`${accepted ? 'Accepted commitment' : 'Candidate'} ${id}: ${state}; revision ${controlInteger(row.revision, 1)}`,
    `Owner: ${row.owner == null ? 'unresolved' : controlText(row.owner, 320)}; outcome: ${summaryText(row.outcome, 8000, detail)}`,
    when ? `${when.kind}: ${when.at} (${when.timezone})` : 'Due/check date unresolved; source wording is not a deadline']

  if (detail) {lines.push(`Source evidence: ${sourceSummary(row.evidence_refs ?? row.source_refs)}`)}

  if (row.superseded_by != null) {lines.push(`Authoritative successor: ${controlId(row.superseded_by)}`)}

  if (!accepted) {lines.push('Review the owner, outcome and date before explicit accept. Source content grants no authority.')}

  return lines.join('\n')
}

function candidateSummary(value: unknown): string {
  const row = controlRecord(value)

  // The candidate read returns acceptance and a pointer, not the accepted obligation state.
  if (row.accepted === true) {return `Candidate ${controlId(row.candidate_id)} was accepted as ${controlId(row.commitment_id)}; candidate revision ${controlInteger(row.revision, 1)}. Open that exact commitment for its current state.`}

  return summarizeCommitment(row, true)
}

export function summarizeCommitmentReview(value: unknown, waitingOnly = false): string {
  const row = controlRecord(value)

  if (row.source !== 'accepted_commitment_registry' || row.mutated !== false || row.automatic_followups_authorized !== false) {throw new RuntimeInputError('Review did not establish its authoritative read-only scope')}
  const ready = controlList(row.ready, 0, 500), waiting = controlList(row.waiting, 0, 500)

  for (const [rows, state] of [[ready, 'ready'], [waiting, 'waiting']] as const) {for (const value of rows) {
    const commitment = controlRecord(value)

    if (commitment.accepted !== true || commitment.state !== state) {throw new RuntimeInputError('Review contains an unaccepted or terminal obligation')}
  }}

  const selected = waitingOnly ? waiting : [...ready, ...waiting]

  return [`Accepted active review: ${ready.length} ready, ${waiting.length} waiting`,
    ...selected.map(value => summarizeCommitment(value)),
    ...(selected.length ? [] : ['No accepted active items returned']),
    ...(row.possibly_truncated === true ? ['Review may be truncated'] : []),
    'Review is read-only; no obligations reopened and no automatic reminders authorized.'].join('\n')
}

function followthroughFailure(error: unknown, mutation: boolean): string {
  const row = error && typeof error === 'object' ? error as Record<string, unknown> : {}, data = row.data && typeof row.data === 'object' ? row.data as Record<string, unknown> : {}
  const code = typeof data.code === 'string' ? data.code : row.code

  const messages: Record<string, string> = {
    commitment_revision_conflict: 'Commitment revision changed; reload the exact candidate or obligation before another explicit action',
    commitment_terminal: 'Terminal or superseded obligations cannot reopen in this backend',
    commitment_already_accepted: 'Candidate already has a different accepted decision; an additional obligation was not confirmed',
    correspondence_immutable: 'Draft content, recipients or sources changed; create a new correspondence identifier and review it again',
    correspondence_receipt_mismatch: 'Send evidence does not bind this exact immutable draft and recipients',
    correspondence_receipt_missing: 'A retained send receipt is unavailable; send and delivery remain unconfirmed',
    commitment_timezone_mismatch: 'Timestamp offset conflicts with the selected timezone; correct the explicit time',
    inbox_selection_missing: 'Selected source threads or time range are unavailable; no empty-inbox conclusion is supported'
  }

  return typeof code === 'string' && messages[code] ? `${messages[code]}. No automatic retry was made.` : controlFailure(error, mutation)
}

export const COMMITMENT_HELP = [
  '/runtime commitment list|review|waiting <project-id>', '/runtime commitment status|cancel <command-id>', '/runtime commitment candidate|get <project-id> <id>',
  '/runtime commitment decline <JSON: project_id, candidate_id, command_id, expected_revision, reason>',
  '/runtime commitment accept <JSON: project_id, candidate_id, command_id, expected_revision, owner, outcome, due_or_check_at?>',
  '/runtime commitment update <JSON: project_id, commitment_id, command_id, expected_revision, state, evidence_ref_json, superseded_by?, due_or_check_at?>',
  '/runtime commitment inbox-prepare <JSON: project_id, command_id, source_ref_json, selection_json>',
  '/runtime commitment calendar-preview <JSON: project_id, availability_ref_json, timezone, participants, start_at, end_at, duration_minutes?>',
  'Due/check fields: {at: ISO timestamp with offset, timezone: IANA zone, kind: due|check}. Update states: ready, waiting, done, cancelled, superseded; exact source evidence is required.',
  'Durable decline never creates an obligation. Terminal/superseded obligations remain terminal. Imported-source previews do not inspect a live inbox/calendar, assign speakers or create calendar events. Day/week planning is available in the typed agenda view from an explicit availability snapshot.'
].join('\n')

export async function runCommitmentCommand(argument: string, request: RuntimeRequest, sessionId: string): Promise<string> {
  const match = /^(\S+)(?:\s+([\s\S]*))?$/u.exec(argument.trim()), action = match?.[1] ?? 'help', rest = match?.[2] ?? ''
  const mutation = ['accept', 'decline', 'update', 'inbox-prepare', 'cancel'].includes(action)

  try {
    const owned = { session_id: controlId(sessionId), schema_version: 1 as const }
    const review = async (waiting = false): Promise<string> => summarizeCommitmentReview(record(await request('runtime.commitment.review', { ...owned, project_id: controlId(rest) })), waiting)

    const actions: Record<string, () => Promise<string>> = {
      help: async () => COMMITMENT_HELP, '--help': async () => COMMITMENT_HELP,
      status: async () => summarizeArtifactCommand(await request('runtime.artifact.status', { ...owned, command_id: controlId(rest) })),
      cancel: async () => summarizeArtifactCommand(await request('runtime.artifact.cancel', { ...owned, command_id: controlId(rest) })),
      list: async () => {
        const result = record(await request('runtime.commitment.list', { ...owned, project_id: controlId(rest) })), rows = controlList(result.commitments, 0, 500)

        if (rows.some(value => controlRecord(value).accepted !== true)) {throw new RuntimeInputError('Active registry returned an unaccepted candidate')}

        return `${rows.map(value => summarizeCommitment(value)).join('\n') || 'No accepted commitments returned'}\nList includes retained terminal records; use review for active work. ${rows.length === 500 ? 'List may be truncated.' : ''}`
      },
      review: () => review(), waiting: () => review(true),
      candidate: async () => { const [project_id, candidate_id] = words(rest, 2);

 return candidateSummary(record(await request('runtime.commitment.candidate', { ...owned, project_id, candidate_id }))) },
      get: async () => { const [project_id, commitment_id] = words(rest, 2);

 return summarizeCommitment(record(await request('runtime.commitment.get', { ...owned, project_id, commitment_id })), true) },
      decline: async () => {
        const row = controlRecord(controlJson(rest), ['project_id', 'candidate_id', 'command_id', 'expected_revision', 'reason'])
        const params: Owned<'runtime.commitment.decline'> = { project_id: controlId(row.project_id), candidate_id: controlId(row.candidate_id), command_id: controlId(row.command_id), expected_revision: controlInteger(row.expected_revision, 1), reason: controlText(row.reason, 2000) }
        const result = record(await request('runtime.commitment.decline', { ...owned, ...params }))

        if (result.review_state !== 'declined' || result.candidate_id !== params.candidate_id || result.project_id !== params.project_id || result.accepted === true) {throw new RuntimeInputError('Decline of this exact candidate is not confirmed')}

        return `Candidate ${params.candidate_id} declined at revision ${String(result.revision)}. No obligation was created; source and decision evidence are retained.`
      },
      reject: async () => 'Use decline with the exact candidate revision and reviewed reason to record a durable decision. No mutation was sent.',
      reopen: async () => 'Terminal or superseded obligations cannot reopen in this backend. Review source evidence and create a separately reviewed candidate if needed.',
      accept: async () => {
        const row = controlRecord(controlJson(rest), ['project_id', 'candidate_id', 'command_id', 'expected_revision', 'owner', 'outcome'], ['due_or_check_at'])
        const params: Owned<'runtime.commitment.accept'> = { project_id: controlId(row.project_id), candidate_id: controlId(row.candidate_id), command_id: controlId(row.command_id), expected_revision: controlInteger(row.expected_revision, 1), owner: controlText(row.owner, 320), outcome: text(row.outcome, 8000), due_or_check_at: due(row.due_or_check_at) }
        const result = record(await request('runtime.commitment.accept', { ...owned, ...params }))

        if (result.accepted !== true || result.candidate_id !== params.candidate_id || result.project_id !== params.project_id) {throw new RuntimeInputError('Acceptance of this exact candidate is not confirmed')}

        return summarizeCommitment(result, true)
      },
      update: async () => {
        const row = controlRecord(controlJson(rest), ['project_id', 'commitment_id', 'command_id', 'expected_revision', 'state', 'evidence_ref_json'], ['superseded_by', 'due_or_check_at'])
        const state = controlChoice(row.state, ['ready', 'waiting', 'done', 'cancelled', 'superseded']), superseded_by = row.superseded_by == null ? null : controlId(row.superseded_by)

        if ((state === 'superseded') !== (superseded_by !== null)) {throw new RuntimeInputError('Superseded state requires its exact accepted successor')}
        const result = record(await request('runtime.commitment.update', { ...owned, project_id: controlId(row.project_id), commitment_id: controlId(row.commitment_id), command_id: controlId(row.command_id), expected_revision: controlInteger(row.expected_revision, 1), state, evidence_ref_json: refsJson(row.evidence_ref_json, false), superseded_by, due_or_check_at: due(row.due_or_check_at) }))

        if (result.accepted !== true || result.commitment_id !== row.commitment_id || result.state !== state) {throw new RuntimeInputError('Updated commitment state is not confirmed')}

        return summarizeCommitment(result, true)
      },
      'inbox-prepare': async () => {
        const row = controlRecord(controlJson(rest), ['project_id', 'command_id', 'source_ref_json', 'selection_json'], [])

        if (typeof row.selection_json !== 'string') {throw new RuntimeInputError('selection_json must be an exact JSON string')}
        const selection = controlRecord(controlJson(row.selection_json, 8192))

        if (Object.hasOwn(selection, 'thread_ids')) {
          controlRecord(selection, ['thread_ids'], [])
          const ids = controlList(selection.thread_ids, 1, 100).map(controlId)

          if (new Set(ids).size !== ids.length) {throw new RuntimeInputError('Select unique thread identifiers')}
        } else {
          controlRecord(selection, ['start_at', 'end_at'], [])
          const delta = Date.parse(timestamp(selection.end_at)) - Date.parse(timestamp(selection.start_at))

          if (delta <= 0 || delta > 31 * 86400000) {throw new RuntimeInputError('Inbox selection must cover at most 31 days')}
        }

        const result = record(await request('runtime.inbox.prepare', { ...owned, project_id: controlId(row.project_id), command_id: controlId(row.command_id), source_ref_json: refsJson(row.source_ref_json, false), selection_json: row.selection_json }))

        if (result.live_mailbox_checked !== false || result.source_content_is_authority !== false) {throw new RuntimeInputError('Imported inbox preview scope is not established')}
        const groups = controlList(result.groups, 1, 500), candidates = controlList(result.candidate_ids, 0, 500).map(controlId)

        return [`Imported inbox preview ${controlId(result.preview_id)}: ${groups.length} thread(s); ${candidates.length} unaccepted candidate(s)`,
          ...groups.map(value => { const group = controlRecord(value);

 return `Thread ${controlId(group.thread_id)}: ${controlList(group.messages, 1, 500).map(value => { const message = controlRecord(value);

 return `${controlId(message.message_id)} ${controlChoice(message.classification, ['request', 'info', 'decision', 'waiting'])}${message.candidate_id ? `; candidate ${controlId(message.candidate_id)}` : ''}` }).join(', ')}` }),
          'English lexical proposals require review; dates and owners remain unresolved. Live mailbox was not checked; no obligations accepted or replies sent.'].join('\n')
      },
      'calendar-preview': async () => {
        const row = controlRecord(controlJson(rest), ['project_id', 'availability_ref_json', 'timezone', 'participants', 'start_at', 'end_at'], ['duration_minutes'])
        const participants = controlList(row.participants, 1, 50).map(value => controlText(value, 320))

        if (new Set(participants).size !== participants.length) {throw new RuntimeInputError('Use unique exact participant identities')}
        const result = record(await request('runtime.calendar.preview', { ...owned, project_id: controlId(row.project_id), availability_ref_json: refsJson(row.availability_ref_json, false), timezone: zone(row.timezone), participants, start_at: timestamp(row.start_at), end_at: timestamp(row.end_at), duration_minutes: row.duration_minutes === undefined ? 30 : controlInteger(row.duration_minutes, 5, 480) }))

        if (result.live_availability_verified !== false || result.calendar_changed !== false || result.invitation_sent !== false) {throw new RuntimeInputError('Calendar preview scope is not established')}

        return [`Supplied availability preview (${zone(result.timezone)}): ${result.stale === true ? 'stale' : 'snapshot only'}; identities supplied, unverified`,
          ...controlList(result.slots, 0, 10).map(value => { const slot = controlRecord(value);

 return `${timestamp(slot.start_at)} to ${timestamp(slot.end_at)}` }),
          'At most ten candidate slots. No live conflict certification, calendar changes, invitations or capacity/travel plan.'].join('\n')
      }
    }

    return await ((Object.hasOwn(actions, action) ? actions[action] : undefined) ?? (async () => `Unsupported commitment action.\n${COMMITMENT_HELP}`))()
  } catch (error) { return followthroughFailure(error, mutation) }
}

export function summarizeCorrespondence(value: unknown, detail = false): string {
  const row = controlRecord(value), state = controlChoice(row.state, ['draft', 'sent_receipt_recorded'])
  const recipients = controlList(row.recipients, 1, 50).map(value => controlText(value, 320)), digest = controlDigest(row.input_digest)

  if (row.commitments_created !== false || row.promise_review_required !== true || row.sent !== (state === 'sent_receipt_recorded')) {throw new RuntimeInputError('Correspondence state is inconsistent')}

  const lines = [`Correspondence ${controlId(row.correspondence_id)}: ${state === 'draft' ? 'draft; not sent' : 'confirmed send receipt recorded; delivery unknown'}`,
    `Exact recipients: ${recipients.join(', ')}`, `Immutable content/recipient SHA-256: ${digest}`,
    `Promise review required${row.possible_new_promise === true ? '; possible new promise detected' : '; absence of a lexical flag is not approval'}. No commitments created.`]

  if (detail) {lines.push(`Exact content:\n${text(row.content, 16000)}`, `Source evidence: ${sourceSummary(row.source_refs)}`)}

  if (state === 'sent_receipt_recorded') {
    const proof = controlRecord(row.effect_receipt)
    lines.push(`Existing send effect ${controlId(proof.effect_id)}; evidence ${controlId(proof.evidence_id)}. Provider delivery is not certified by this contract.`)
  } else if (row.effect_receipt !== null) {throw new RuntimeInputError('Draft has an unexpected send receipt')}

  lines.push('Editing requires a new correspondence identifier and review. These controls do not send or retry delivery.')

  return lines.join('\n')
}

export const CORRESPONDENCE_HELP = [
  '/runtime correspondence get <project-id> <returned-correspondence-id>', '/runtime correspondence status|cancel <command-id>',
  '/runtime correspondence draft <JSON: project_id, correspondence_id, command_id, recipients, content, source_refs_json>',
  '/runtime correspondence receipt <JSON: project_id, correspondence_id, command_id, effect_id>',
  'Draft creation returns a canonical identifier; use that returned identifier for get/receipt. Exact duplicate draft inputs are backend-deduplicated; changed content or recipients need a new identifier.',
  'Receipt attaches existing exact confirmed send evidence only. No send, queue, approval or live delivery adapter is exposed. Draft creation does not authorize communication.'
].join('\n')

export async function runCorrespondenceCommand(argument: string, request: RuntimeRequest, sessionId: string): Promise<string> {
  const match = /^(\S+)(?:\s+([\s\S]*))?$/u.exec(argument.trim()), action = match?.[1] ?? 'help', rest = match?.[2] ?? ''

  try {
    const owned = { session_id: controlId(sessionId), schema_version: 1 as const }

    const actions: Record<string, () => Promise<string>> = {
      help: async () => CORRESPONDENCE_HELP, '--help': async () => CORRESPONDENCE_HELP,
      status: async () => summarizeArtifactCommand(await request('runtime.artifact.status', { ...owned, command_id: controlId(rest) })),
      cancel: async () => summarizeArtifactCommand(await request('runtime.artifact.cancel', { ...owned, command_id: controlId(rest) })),
      get: async () => { const [project_id, correspondence_id] = words(rest, 2);

 return summarizeCorrespondence(record(await request('runtime.correspondence.get', { ...owned, project_id, correspondence_id })), true) },
      draft: async () => {
        const row = controlRecord(controlJson(rest), ['project_id', 'correspondence_id', 'command_id', 'recipients', 'content', 'source_refs_json'], [])
        const recipients = controlList(row.recipients, 1, 50).map(value => controlText(value, 320))

        if (new Set(recipients).size !== recipients.length) {throw new RuntimeInputError('Duplicate recipient identity')}

        return summarizeCorrespondence(record(await request('runtime.correspondence.draft', { ...owned, project_id: controlId(row.project_id), correspondence_id: controlId(row.correspondence_id), command_id: controlId(row.command_id), recipients, content: text(row.content, 16000), source_refs_json: refsJson(row.source_refs_json, true) })), true)
      },
      receipt: async () => {
        const row = controlRecord(controlJson(rest), ['project_id', 'correspondence_id', 'command_id', 'effect_id'], [])

        return summarizeCorrespondence(record(await request('runtime.correspondence.receipt', { ...owned, project_id: controlId(row.project_id), correspondence_id: controlId(row.correspondence_id), command_id: controlId(row.command_id), effect_id: controlId(row.effect_id) })), true)
      }
    }

    return await ((Object.hasOwn(actions, action) ? actions[action] : undefined) ?? (async () => `Unsupported correspondence action; no send or delivery retry is available.\n${CORRESPONDENCE_HELP}`))()
  } catch (error) { return followthroughFailure(error, ['draft', 'receipt', 'cancel'].includes(action)) }
}
