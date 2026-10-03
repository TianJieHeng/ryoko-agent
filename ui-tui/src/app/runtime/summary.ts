import { type RuntimeControlState, runtimeWorkLabel } from '@hermes/shared/runtime-control'

export function runtimeSummary(state: RuntimeControlState): string {
  const snapshot = state.snapshot

  const lines = [
    `Runtime connection: ${state.transport}`,
    runtimeWorkLabel(snapshot),
    ...(state.error ? [state.error] : [])
  ]

  if (snapshot) {
    lines.push(`Revision ${snapshot.revision} · ${snapshot.compatibility_status}`)
    lines.push(`Pending decisions: ${snapshot.outstanding_requests.length}; artifacts: ${snapshot.artifacts.length}; unresolved effects: ${snapshot.unresolved_effects.length}`)

    if (snapshot.references_truncated) {lines.push('Reference list is partial; inspect the relevant feature for all records')}
  }

  if (state.receipt) {lines.push(`Command ${state.receipt.command_id}: ${state.receipt.status} (receipt only, not completion)`)}

  if (state.receipt?.conflict) {lines.push(`Conflict ${state.receipt.conflict.code}: ${state.receipt.conflict.message}`)}

  for (const event of state.events.slice(-10)) {lines.push(`Event ${event.type} · sequence ${event.seq} · generation ${event.generation}${event.run_id ? ` · run ${event.run_id}` : ''}`)}

  if (state.pending) {lines.push('Command outcome unknown. /runtime refresh then /runtime retry reuses the same operation identity')}

  for (const operation of state.capabilities?.operations ?? []) {
    lines.push(`${operation.operation}: ${operation.accepts_commands && operation.executes ? 'available' : 'unavailable'}${operation.reason ? `; ${operation.reason}` : ''}`)
  }

  return lines.join('\n')
}
