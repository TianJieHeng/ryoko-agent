import type { MemoryStatusResult, MissionRecord, RuntimeCapabilities, RuntimeEffectRecord } from './gateway-contract.generated.js'
import type { RuntimeRequest } from './runtime-control.js'
import { summarizeCommitmentReview } from './runtime-followthrough.js'
import { controlJson, controlList, controlRecord, controlText } from './runtime-research.js'
import { summarizeSchedule } from './runtime-workflows.js'

export interface RuntimeStatusView {
  checkedAt: string
  ready: MissionRecord[]
  waiting: MissionRecord[]
  active: MissionRecord[]
  upcoming: string[]
  commitments: string[]
  effects: RuntimeEffectRecord[]
  capabilities: RuntimeCapabilities | null
  memory: MemoryStatusResult | null
  errors: string[]
  partial: boolean
}

/** Authorized server projections only; missing queues never become an all-clear. */
export async function inspectRuntimeStatus(request: RuntimeRequest, sessionId: string, projectId?: string): Promise<RuntimeStatusView> {
  const base = { session_id: sessionId, schema_version: 1 as const }

  const [missions, effects, capabilities, memory] = await Promise.allSettled([
    request('runtime.mission.list', { ...base, limit: 100 }), request('runtime.effects.list', { ...base, unresolved_only: true, limit: 100 }),
    request('runtime.capabilities', { session_id: sessionId }), request('runtime.memory.status', base)
  ])

  const view: RuntimeStatusView = { checkedAt: new Date().toISOString(), ready: [], waiting: [], active: [], upcoming: [], commitments: [], effects: effects.status === 'fulfilled' ? effects.value.effects : [], capabilities: capabilities.status === 'fulfilled' ? capabilities.value : null, memory: memory.status === 'fulfilled' ? memory.value : null, errors: [], partial: true }
  const results = [missions, effects, capabilities, memory]
  results.forEach((result, index) => { if (result.status === 'rejected') {view.errors.push(`${['Mission', 'Effect', 'Capability', 'Memory'][index]} status unavailable; refresh its configured backend`)} })

  if (missions.status === 'fulfilled') {
    const records = missions.value.missions.filter(mission => !projectId || mission.project_id === projectId)
    view.ready = records.filter(mission => mission.state === 'ready_to_review')
    view.waiting = records.filter(mission => ['waiting_for_user', 'waiting_for_source', 'partially_completed', 'paused', 'failed'].includes(mission.state))
    view.active = records.filter(mission => ['ready', 'working'].includes(mission.state))
  }

  if (projectId) {
    try {
      const result = await request('runtime.schedule.list', { ...base, project_id: projectId })
      const rows = controlList(controlRecord(controlJson(result.record_json, 131072, false)).schedules, 0, 100)
      view.upcoming = rows.filter(row => controlRecord(row).state === 'active').map(summarizeSchedule)
    } catch { view.errors.push('Upcoming schedules unavailable for this project; do not infer that none exist') }

    try {
      const result = await request('runtime.commitment.review', { ...base, project_id: projectId })
      view.commitments = [summarizeCommitmentReview(controlJson(result.record_json, 131072, false))]
    } catch { view.errors.push('Accepted commitment review unavailable for this project') }
  } else {view.errors.push('Select a project to inspect its upcoming schedules')}

  return view
}

export function runtimeStatusLines(view: RuntimeStatusView): string[] {
  const queue = (label: string, records: MissionRecord[]) => [`${label}: ${records.length} in this bounded view`, ...records.map(record => `${record.mission_id} · revision ${record.revision} · ${record.outcome}\n${record.next_step || record.state}\n${record.blockers.join('; ')}`)]

  return [`Checked ${view.checkedAt}; bounded projections, not a complete all-clear`,
    ...queue('Ready to review', view.ready), ...queue('Waiting for user/source or repair', view.waiting), ...queue('Active', view.active),
    ...view.upcoming.map(item => `Upcoming: ${item}`), ...view.commitments, ...view.effects.map(effect => `Unresolved effect ${effect.effect_id}: ${effect.state}; owner run ${effect.run_id}`),
    ...view.errors, 'Durable dismissal/opportunity-discovery and repair-plan APIs are not advertised; no task is created by this view']
}

export async function runStatusCommand(argument: string, request: RuntimeRequest, sessionId: string): Promise<string> {
  const [action = 'help', id] = argument.trim().split(/\s+/)
  const base = { session_id: sessionId, schema_version: 1 as const }

  if (action === 'queues') {return runtimeStatusLines(await inspectRuntimeStatus(request, sessionId, id)).join('\n')}

  if (action === 'effect' || action === 'reconcile') {
    const effect_id = controlText(id)
    const result = await request(action === 'effect' ? 'runtime.effect.get' : 'runtime.effect.reconcile', { ...base, effect_id })

    return [`Effect ${result.effect.effect_id}: ${result.effect.state}; generation ${result.effect.generation}`,
      `Owner ${result.effect.run_id}; operation ${result.effect.operation_type}; approval ${result.effect.approval_id ?? 'none'}`,
      ...result.evidence.map(item => `${item.from_state} → ${item.state}; receipt ${item.receipt_available ? 'available' : 'unavailable'}${item.receipt_sha256 ? ` (${item.receipt_sha256})` : ''}`),
      'Inspection/reconciliation never replays the external mutation and does not guarantee exactly-once external effects'].join('\n')
  }

  if (action === 'privacy') {
    const result = await request('runtime.memory.status', base)

    return [`Store: ${result.capabilities.backend}; health ${result.health.status}`,
      `Delete ${result.capabilities.delete ? 'declared supported' : 'unavailable'}; export ${result.capabilities.export ? 'declared supported' : 'unavailable'}`,
      'Built-in record deletion is a tombstone, not physical erasure of backups. A primary harness requires its own supported acknowledgment; hiding a row is not deletion.',
      'Bulk export, retention and deletion-manifest controls are not exposed here. No data was exported or deleted'].join('\n')
  }

  return 'Status: queues [project-id] | effect <effect-id> | reconcile <effect-id> | privacy\nRepair is local evidence inspection only; delivery retry is a separate control. Opportunity discovery and durable dismissal require backend operations'
}
