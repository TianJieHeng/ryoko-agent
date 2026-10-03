import type { RuntimeRequest } from './runtime-control.js'
import { controlId, controlRecord } from './runtime-research.js'
import { voiceResponse } from './runtime-voice.js'

export interface VoiceAdmission {
  requestId: string
  accountId: string
  rootId: string
  deadline: number
  budget: Record<string, unknown>
}
export interface VoiceOperation { request_id: string; budget_account_id: string }

/** Capability declarations select the protocol; absence preserves older non-budgeted backends. */
export function strictVoiceBudget(capabilities: Record<string, unknown> | null): boolean {
  if (!capabilities?.budget) { return false }
  const budget = controlRecord(capabilities.budget)

  if (budget.mode === 'not_configured') { return false }

  if (budget.mode !== 'existing_run_tree' || budget.admission_rpc !== 'runtime.voice.admit') {
    throw new Error('Unsupported speech budget protocol')
  }

  return true
}

export async function admitRuntimeVoice(request: RuntimeRequest, sessionId: string, requestId: string, now = Date.now()): Promise<VoiceAdmission> {
  const response = voiceResponse(await request('runtime.voice.admit', {
    session_id: controlId(sessionId), schema_version: 1, request_id: controlId(requestId),
  }))

  if (response.status !== 'admitted' || response.capture_started !== false || response.model_dispatched !== false || response.mission_accepted !== false || response.reservation_created !== false) {
    throw new Error('Speech admission did not return a finite inactive account')
  }

  if (typeof response.deadline !== 'number' || !Number.isFinite(response.deadline) || response.deadline * 1000 <= now) {
    throw new Error('Speech admission expired')
  }

  return { requestId, accountId: controlId(response.budget_account_id), rootId: controlId(response.root_id), deadline: response.deadline, budget: controlRecord(response.budget) }
}

/** Generate once per explicit operation, before dispatch; never mint a replacement on timeout. */
export function voiceOperation(admission: VoiceAdmission, requestId: string, now = Date.now()): VoiceOperation {
  if (admission.deadline * 1000 <= now) { throw new Error('Speech admission expired') }

  return { request_id: controlId(requestId), budget_account_id: controlId(admission.accountId) }
}
