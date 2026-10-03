import type { MissionRecord, MissionVerificationReceipt } from './gateway-contract.generated.js'
import type { RuntimeRequest } from './runtime-control.js'

function text(value: string | undefined, field: string): string {
  if (!value?.trim()) {throw new Error(`Missing ${field}`)}

  return value
}

function revision(value: string | undefined): number {
  if (!value || !/^\d+$/.test(value) || !Number.isSafeInteger(Number(value)) || Number(value) < 1) {throw new Error('Use the current positive mission revision')}

  return Number(value)
}

export function missionLines(mission: MissionRecord): string[] {
  return [
    `${mission.mission_id} · revision ${mission.revision} · ${mission.state.replaceAll('_', ' ')}`,
    mission.outcome,
    `Execution: ${mission.execution_status}; acceptance: ${mission.acceptance_status}; delivery: ${mission.delivery_status}`,
    `Next: ${mission.next_step || 'inspect current evidence before choosing an action'}`,
    ...(mission.deliverables ?? []).map(item => `${item.required ? 'Required' : 'Optional'} output ${item.deliverable_id}: ${item.description || ''}; ${item.artifact_ref ? `${item.artifact_ref.artifact_id}@${item.artifact_ref.version}` : 'not produced'}`),
    ...mission.blockers.map(blocker => `Blocked: ${blocker}`),
    ...mission.missed_steer.map(steer => `Correction revision ${steer.revision} missed: ${steer.reason}; dispatched effects remain inspectable`),
    ...mission.effect_refs.map(effect => `Effect ${effect.effect_id}: ${effect.state}`),
    ...(mission.effect_refs_truncated ? ['Effect list is partial; inspect effects separately'] : []),
    ...mission.delivery_refs.map(delivery => `Delivery ${delivery.delivery_id}: ${delivery.state}`),
    `Turns ${mission.turns_used}/${mission.max_turns}; verification ${mission.verification_current ? 'current' : 'not current or not yet established'}`,
    'Ready to review is not accepted/completed. Cancellation does not undo external effects'
  ]
}

function receiptLines(receipts: MissionVerificationReceipt[]): string[] {
  return receipts.map(receipt => `${receipt.criterion_id}: ${receipt.result}; verifier ${receipt.verifier}; evidence ${receipt.evidence_ref}; mission revision ${receipt.mission_revision ?? 'unavailable'}`)
}

export async function runMissionCommand(argument: string, request: RuntimeRequest, sessionId: string): Promise<string> {
  const [action = 'help', value, ...tail] = argument.trim().split(/\s+/)
  const base = { session_id: sessionId, schema_version: 1 as const }

  const actions: Record<string, () => Promise<string>> = {
    get: async () => {
      const { mission } = await request('runtime.mission.get', base)

      return mission ? missionLines(mission).join('\n') : 'No mission intent exists for this owned conversation'
    },
    list: async () => {
      const result = await request('runtime.mission.list', base)

      return `Bounded mission list${result.limit_reached ? '; limit reached' : ''}\n${result.missions.map(m => missionLines(m).join('\n')).join('\n\n') || 'No authorized missions'}`
    },
    create: async () => {
      const result = await request('runtime.mission.create', { ...base, mission_id: text(value, 'mission ID'), contract: { outcome: text(tail.join(' '), 'outcome'), policy: 'reviewed' } })

      return [...missionLines(result.mission), 'Intent created only; no model or external action dispatched. Attach concrete outputs and verify before acceptance'].join('\n')
    },
    revise: async () => {
      const result = await request('runtime.mission.revise', { ...base, expected_revision: revision(value), contract: { outcome: text(tail.join(' '), 'revised outcome') } })

      return [...missionLines(result.mission), 'Only outcome text revised; the backend retains other requirements, versions and budget ceilings'].join('\n')
    },
    attach: async () => {
      const [deliverableId, projectId, artifactId, artifactVersion] = tail
      const expected_revision = revision(value)
      const { mission } = await request('runtime.mission.get', base)

      if (!mission || mission.revision !== expected_revision) {throw new Error('Mission changed; inspect its current revision')}
      const artifact = await request('runtime.artifact.get', { ...base, project_id: text(projectId, 'project ID'), artifact_id: text(artifactId, 'artifact ID'), version: revision(artifactVersion), limit: 1 })
      const ref = { artifact_id: artifact.artifact_id, version: artifact.version, digest: artifact.sha256 }
      const id = text(deliverableId, 'deliverable ID')
      const criterionId = `exists:${id}`

      const result = await request('runtime.mission.revise', {
        ...base, expected_revision,
        contract: {
          outcome: mission.outcome,
          deliverables: [...(mission.deliverables ?? []).filter(item => item.deliverable_id !== id), { deliverable_id: id, description: id, artifact_ref: ref, required: true }],
          acceptance: [...(mission.acceptance ?? []).filter(item => item.criterion_id !== criterionId), { criterion_id: criterionId, kind: 'existence', required: true, artifact_refs: [ref], parameters: { require_current_head: true, require_current_dependencies: true } }]
        }
      })

      return [...missionLines(result.mission), 'Artifact attached with exact version/digest; deterministic verification still required'].join('\n')
    },
    verify: async () => {
      const result = await request('runtime.mission.verify', { ...base, expected_revision: revision(value) })

      return [...missionLines(result.mission), ...receiptLines(result.receipts)].join('\n')
    },
    accept: async () => missionLines((await request('runtime.mission.accept', { ...base, expected_revision: revision(value) })).mission).join('\n'),
    receipts: async () => {
      const result = await request('runtime.mission.receipts.list', base)

      return [`Bounded verification receipts${result.limit_reached ? '; limit reached' : ''}`, ...receiptLines(result.receipts), 'Missing/unsupported evidence is not a passing test; focused checks do not imply full-suite validation'].join('\n')
    }
  }

  for (const operation of ['pause', 'resume', 'cancel'] as const) {
    actions[operation] = async () => {
      const method = `runtime.mission.${operation}` as const
      const result = await request(method, { ...base, expected_revision: revision(value), reason: tail.join(' ') })

      return missionLines(result.mission).join('\n')
    }
  }

  if (!Object.hasOwn(actions, action)) {return 'Missions: get | list | create <mission-id> <outcome> | revise <revision> <outcome> | attach <revision> <deliverable-id> <project-id> <artifact-id> <version> | verify/accept <revision> | pause/resume/cancel <revision> [reason] | receipts\nIntent/control does not itself dispatch new work. Two outputs can be attached separately, then verified against current exact bytes'}

  return actions[action]()
}

export async function runApprovalCommand(argument: string, request: RuntimeRequest, sessionId: string): Promise<string> {
  const [action = 'help', id, digest] = argument.trim().split(/\s+/)
  const base = { session_id: sessionId, schema_version: 1 as const }

  if (action === 'list') {
    const result = await request('runtime.approvals.list', base)

    return [`Bounded approval list${result.truncated ? '; partial' : ''}`, ...result.approvals.map(a => `${a.approval_id}: ${a.status}${a.expired ? ', EXPIRED' : ''}; digest ${a.approval_digest}; expires ${new Date(a.expires_at * 1000).toISOString()}${a.invalidation_reason ? `; ${a.invalidation_reason}` : ''}`), 'This metadata-only inspector cannot approve an unseen action. Return to its exact prepared-content review; denial is supported here'].join('\n')
  }

  if (action === 'deny') {
    if (!digest || !/^[a-f0-9]{64}$/.test(digest)) {throw new Error('Supply the exact approval digest')}
    const result = await request('runtime.approval.resolve', { ...base, approval_id: text(id, 'approval ID'), approval_digest: digest, choice: 'deny' })

    return `Approval ${result.approval.approval_id}: ${result.approval.status}; no dispatch performed`
  }

  return 'Approvals: list | deny <approval-id> <exact-digest>\nApproval requires the originating exact-content review, not only a digest or confidence score'
}

export async function runDeliveryCommand(argument: string, request: RuntimeRequest, sessionId: string): Promise<string> {
  const [action, id] = argument.trim().split(/\s+/)

  if (!['status', 'retry'].includes(action)) {return 'Delivery: status <delivery-id> | retry <delivery-id>\nRetry sends only the existing result notification, never reruns the mission; receipt is not human-read confirmation'}
  const result = await request(action === 'retry' ? 'runtime.delivery.retry' : 'runtime.delivery.status', { session_id: sessionId, schema_version: 1, delivery_id: text(id, 'delivery ID') })

  return [`Delivery ${result.delivery_id}: ${result.state}; acknowledgment ${result.acknowledgment_level}`,
    `Artifact ${result.artifact_id}@${result.version}; text ${result.components.text}; artifact ${result.components.artifact}`,
    `Attempt ${result.attempt_count}/${result.max_attempts}; result ${result.result_available ? 'available' : 'unavailable'}`,
    result.last_error ? `Last error: ${result.last_error}` : '',
    'Execution status is unchanged. Unknown delivery must be reconciled or downloaded, not rerun'].filter(Boolean).join('\n')
}
