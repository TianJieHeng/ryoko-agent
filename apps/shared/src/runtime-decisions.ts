import type { DecisionReceipt, RuntimeEventEnvelope } from './gateway-contract.generated.js'
import type { RuntimeRequest } from './runtime-control.js'

const fallbackReasons: Partial<Record<NonNullable<DecisionReceipt['fallback']>, string>> = {
  off: 'This decision point was off; existing routing remained in control',
  shadow_observation: 'The model was observed in shadow mode; its suggestion did not control the action',
  point_gate_required: 'No qualified point gate was available; the existing path remained in control',
  privacy_not_qualified: 'Private input was not qualified for this decision service',
  private_transport_unqualified: 'The private transport had not passed its qualification gate',
  private_destination_authorization_required: 'The specific private-data destination lacked authorization',
  transport_unconfigured: 'The decision service transport was not configured',
  node_unavailable: 'The decision service was unavailable',
  deadline_exceeded: 'The bounded decision deadline was exceeded',
  below_threshold: 'The recorded point-specific threshold was not met',
  unclear: 'The service returned an unclear result',
  receipt_unavailable: 'A durable decision receipt could not be established'
}

export function decisionExplanation(receipt: DecisionReceipt): string[] {
  return [`${receipt.point_id} · ${receipt.mode} · contract ${receipt.contract_version}`,
    `Recorded route: ${receipt.actual_route}; incumbent ${receipt.incumbent}${receipt.selected ? `; optional selected signal ${receipt.selected}` : ''}`,
    receipt.fallback ? fallbackReasons[receipt.fallback] ?? `Recorded fallback: ${receipt.fallback.replaceAll('_', ' ')}` : 'No fallback was recorded; this is an observable routing receipt, not hidden model reasoning',
    `Receipt ${receipt.receipt_id}; recorded ${new Date(receipt.recorded_at * 1000).toISOString()}`,
    'A model signal never grants permission or proves completion. Deterministic capability, approval and evidence gates remain authoritative']
}

export interface DecisionInspection { events: RuntimeEventEnvelope[]; cursor: string; hasMore: boolean; gap: boolean; lines: string[] }

export async function inspectRuntimeDecisions(request: RuntimeRequest, sessionId: string, cursor?: string): Promise<DecisionInspection> {
  const result = await request('runtime.events.since', { session_id: sessionId, schema_version: 1, cursor: cursor ?? null, limit: 200 })
  const events = result.events.filter(event => event.type.startsWith('decision.'))

  const lines = events.flatMap(event => {
    const payload = event.payload

    if (payload.decision_receipt) {return decisionExplanation(payload.decision_receipt)}

    if (payload.decision_tool_plan) {
      const plan = payload.decision_tool_plan

      return [`Tool plan (${plan.mode}): ${plan.need}; effort ${plan.effort_bucket}; authorized families ${plan.families.join(', ') || 'none'}`,
        `Verified tools ${plan.verified_tool_ids.join(', ') || 'none initially'}; authorized search/describe/call recovery remains available`,
        ...(plan.fallback ? [`Recorded fallback: ${plan.fallback}`] : [])]
    }

    if (payload.decision_planner_miss) {return [`Missing-tool recovery: ${payload.decision_planner_miss.recovered ? 'recovered' : 'not recovered'}; ${payload.decision_planner_miss.reason}; observation only ${payload.decision_planner_miss.observation_only ?? false}`]}

    if (payload.decision_outcome) {return [`Recorded independent outcome ${payload.decision_outcome.outcome}: ${payload.decision_outcome.label}; receipt ${payload.decision_outcome.receipt_id}`]}

    if (payload.decision_policy) {return [`${payload.decision_policy.point_id}: recorded ${payload.decision_policy.operation}; mode ${payload.decision_policy.mode ?? 'not supplied'}; reason ${payload.decision_policy.reason ?? 'not supplied'}`]}

    return []
  })

  if (result.status === 'snapshot_required') {lines.unshift('Replay history has a gap or expired cursor; a fresh snapshot was required. Earlier decisions cannot be reconstructed here')}

  if (!events.length) {lines.push('No decision receipts in this bounded page; this does not prove the service is active or inactive')}
  lines.push('Feedback/override, per-channel opt-in and mode-changing RPCs are not advertised. This view cannot activate LAYA, reduce required approvals, train a model or expose private state packets')

  return { events, cursor: result.last_cursor, hasMore: result.has_more, gap: result.status === 'snapshot_required', lines }
}

export async function runDecisionCommand(argument: string, request: RuntimeRequest, sessionId: string): Promise<string> {
  const [action = 'help', cursor] = argument.trim().split(/\s+/)

  if (action !== 'inspect') {return 'Decisions: inspect [cursor]\nRead-only receipt explanations. Off/shadow defaults and all deterministic floors remain unchanged'}
  const result = await inspectRuntimeDecisions(request, sessionId, cursor)

  return [...result.lines, ...(result.hasMore ? [`More retained events: inspect ${result.cursor}`] : [])].join('\n')
}
