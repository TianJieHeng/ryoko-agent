import type { OutputContextResult, OutputContextSummary, OutputControlParams, OutputControlResult, OutputReference } from './gateway-contract.generated.js'
import { controlDigest, controlId, controlInteger, RuntimeInputError } from './runtime-research.js'

export function verifyOutputContext(value: OutputContextResult, expected: OutputContextSummary): void {
  controlId(value.run_id); controlDigest(value.context_sha256); controlDigest(value.context_packet_sha256); controlDigest(value.immutable_prefix_sha256)

  if (value.run_id !== expected.run_id || value.context_sha256 !== expected.context_sha256) { throw new RuntimeInputError('Output receipt changed; refresh the run list and inspect again') }

  if (value.coverage !== 'fresh_memory_context_only' || value.causal_explanation !== false || value.historical_context_enumerated !== false || value.state !== 'supplied_to_provider_call') { throw new RuntimeInputError('Unsupported output context coverage') }

  if (value.backend === 'personal_mcp' && value.references.length) { throw new RuntimeInputError('The personal harness has no verified record inspection contract') }

  for (const ref of value.references) {
    controlId(ref.record_id); controlId(ref.namespace_id); controlInteger(ref.version, 1)

    if (ref.namespace_id !== value.namespace_id) { throw new RuntimeInputError('Output reference namespace differs from its receipt') }
  }
}

export function outputControlBlock(context: OutputContextResult, ref: OutputReference, action: OutputControlParams['action'], scope: OutputControlParams['scope']): string | null {
  if (context.backend !== 'builtin') { return 'The personal context harness is opaque and read-only; its record mutation contract is not verified' }

  if (context.degraded || !context.latest) { return 'Inspect the latest available output before changing its context' }

  if (ref.deletion_state !== 'present') { return 'This supplied record is no longer present; historical receipts are unchanged' }

  if (ref.namespace_id !== context.namespace_id || !context.references.some(item => item.record_id === ref.record_id && item.version === ref.version && item.namespace_id === ref.namespace_id && item.scope === ref.scope)) { return 'Choose an exact reference from this output receipt' }

  if (action === 'remove' && scope === 'response') { return 'Use ignore for a response-only change; removal is a durable tombstone' }

  if (scope === 'project' && !context.project_id) { return 'This output has no project scope' }

  if (scope !== 'response' && ref.scope.startsWith('project:') && (scope !== 'project' || ref.scope !== `project:${context.project_id}`)) { return 'A project reference cannot be applied to another scope' }

  if (scope !== 'response' && action !== 'ignore' && ref.scope !== (scope === 'general' ? 'individual' : `project:${context.project_id}`)) { return 'Durable changes must match the record’s existing scope; a one-off edit is not promoted' }

  return null
}

export function buildOutputControl(sessionId: string, context: OutputContextResult, ref: OutputReference, action: OutputControlParams['action'], scope: OutputControlParams['scope'], content: string, controlIdValue: string): OutputControlParams {
  const block = outputControlBlock(context, ref, action, scope)

  if (block) { throw new RuntimeInputError(block) }
  controlId(sessionId); controlId(controlIdValue); controlDigest(context.context_sha256)

  if (action === 'correct' && (!content.trim() || new TextEncoder().encode(content).length > 4096)) { throw new RuntimeInputError('Replacement text must contain 1–4096 UTF-8 bytes') }

  return { session_id: sessionId, schema_version: 1, run_id: context.run_id, context_sha256: context.context_sha256, control_id: controlIdValue,
    record_id: ref.record_id, expected_version: ref.version, namespace_id: ref.namespace_id, action, scope,
    project_id: scope === 'project' ? context.project_id : null, content: action === 'correct' ? content : null }
}

const statuses: Record<OutputControlResult['status'], string> = {
  queued_next_turn: 'Queued for a future turn boundary; not yet supplied to the model',
  mutation_pending: 'Durable write outcome is pending or uncertain; do not replay this mutation',
  memory_acknowledged: 'Durable store acknowledged the exact new version; model application is not yet confirmed',
  version_conflict: 'Record version conflict; this change was not acknowledged. Refresh references before making a new decision',
  context_supplied: 'The context update was supplied to a later provider call; this is not a causal guarantee',
  stale_not_applied: 'Context or memory became stale; the requested directive was not applied',
  expired: 'Response-only override expired; it is not a durable preference'
}

export function verifyOutputControlReceipt(result: OutputControlResult): void {
  controlId(result.control_id); controlId(result.run_id)

  if (!Object.hasOwn(statuses, result.status) || result.current_output_changed !== false || !['not_applied', 'late_not_applied'].includes(result.current_run_application)) { throw new RuntimeInputError('Unsupported output control receipt') }

  if (result.status === 'memory_acknowledged' && (result.scope === 'response' || result.action === 'ignore' || result.acknowledged_version === null)) { throw new RuntimeInputError('Durable acknowledgment is missing a newer store version') }

  if (result.acknowledged_version !== null) { controlInteger(result.acknowledged_version, 1) }

  if (result.status === 'context_supplied' && (!result.applied_run_id || result.applied_run_id === result.run_id)) { throw new RuntimeInputError('Context supply is missing its later run identity') }

  if (result.action === 'remove' && ['memory_acknowledged', 'context_supplied'].includes(result.status) && result.deletion_semantics !== 'tombstone_not_physical_erasure') { throw new RuntimeInputError('Removal acknowledgment is missing its tombstone semantics') }
}

export function verifyOutputControl(result: OutputControlResult, input: OutputControlParams): void {
  verifyOutputControlReceipt(result)

  if (result.control_id !== input.control_id || result.run_id !== input.run_id || result.action !== input.action || result.scope !== input.scope || result.project_id !== (input.project_id ?? null) || result.current_output_changed !== false || !Object.hasOwn(statuses, result.status)) { throw new RuntimeInputError('Control receipt does not match the exact requested change') }

  if (!['not_applied', 'late_not_applied'].includes(result.current_run_application)) { throw new RuntimeInputError('Unsupported current-output application claim') }

  if (result.status === 'memory_acknowledged' && (result.acknowledged_version === null || result.acknowledged_version <= input.expected_version)) { throw new RuntimeInputError('Durable acknowledgment is missing a newer store version') }

  if (result.status === 'context_supplied' && (!result.applied_run_id || result.applied_run_id === input.run_id)) { throw new RuntimeInputError('Context supply is missing its later run identity') }

  if (input.action === 'remove' && result.status === 'memory_acknowledged' && result.deletion_semantics !== 'tombstone_not_physical_erasure') { throw new RuntimeInputError('Removal acknowledgment is missing its tombstone semantics') }
}

export function describeOutputControl(result: OutputControlResult): string {
  verifyOutputControlReceipt(result)

  return `${result.status}: ${statuses[result.status]}. Current output remains unchanged (${result.current_run_application}).${result.acknowledged_version !== null ? ` Acknowledged version: ${result.acknowledged_version}.` : ''}${result.applied_run_id ? ` Supplied to run: ${result.applied_run_id}.` : ''}${result.deletion_semantics === 'tombstone_not_physical_erasure' ? ' One record was tombstoned; backups, transcript history and previously supplied context are not physically erased.' : ''}`
}
