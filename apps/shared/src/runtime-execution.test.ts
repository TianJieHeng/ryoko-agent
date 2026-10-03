import { describe, expect, it, vi } from 'vitest'

import type { RuntimeRequest } from './runtime-control.js'
import { prepareRuntimeExecution, runExecutionCommand } from './runtime-execution.js'

const sha = 'a'.repeat(64), nextSha = 'b'.repeat(64), manifestSha = 'c'.repeat(64)
const executor = { executor_id: 'local-one', generation: 4, capability_digest: sha, location: 'local' }
const service = { service_id: 'local.text.normalize', capability: 'utf8_normalization', version: 1, processing_location: 'local_profile_store', destination: 'local_profile_store', authentication: 'live_identity_and_project_grant', data_policy: 'same_profile_no_network_no_publication', max_input_bytes: 65536, max_output_bytes: 65536 }
const manifest = { schema_version: 1, pipeline_id: 'pipeline-a', project_id: 'project-a', executor, source: { artifact_id: 'artifact-a', version: 2, sha256: sha, size: 24, mime: 'text/markdown' }, stages: [service, { ...service, service_id: 'local.document.structure', capability: 'document_structure' }], transfer: { input_bytes: 24, max_intermediate_bytes: 65536, max_output_bytes: 65536, from: 'local_project_artifact', to: 'local_profile_store', remote_bytes: 0 }, publication: 'private_staged_output_requires_separate_artifact_approval' }
const stage = { pipeline_id: 'pipeline-a', stage: 0, service_id: service.service_id, service_version: 1, input_sha256: sha, input_size: 24, output_sha256: nextSha, output_size: 20, output_mime: 'text/plain', destination: 'local_profile_store', acknowledgment_level: 'local_sqlite_commit', executor, transfer_id: 'd'.repeat(64) }
const pending = { pipeline_id: 'pipeline-a', state: 'pending', receipts: [], manifest_sha256: manifestSha, next_stage: 0, publication: manifest.publication }
const prepareInput = { project_id: 'project-a', request_id: 'input-a', artifact_id: 'artifact-a', version: 2 }
const json = (value: unknown) => ({ response_json: JSON.stringify(value) })
const prepared = () => json({ manifest, manifest_sha256: manifestSha })
const mock = (fn: (method: string, params: unknown) => unknown) => vi.fn(async (method, params) => fn(method, params)) as unknown as RuntimeRequest

describe('FE09 bounded execution controls', () => {
  it('discloses actual unavailable service health and location without inventing a remote executor', async () => {
    const request = mock(() => json({ services: [{ ...service, health: 'unavailable' }] }))
    const result = await runExecutionCommand('capabilities', request, 'owned')
    expect(result).toContain('unavailable; utf8_normalization')
    expect(result).toContain('Processing local_profile_store')
    expect(result).toContain('remote execution are unavailable')
    expect(request).toHaveBeenCalledWith('runtime.services.capabilities', { session_id: 'owned', schema_version: 1 })
  })

  it('prepares an exact source and requires reviewed digest/session before executing', async () => {
    const request = mock(method => method === 'runtime.services.prepare' ? prepared() : json(pending))
    const proposal = await prepareRuntimeExecution(request, 'owned', prepareInput)
    expect(proposal.summary).toContain('no stages executed')
    expect(proposal.summary).toContain('24 input bytes')
    expect(proposal.summary).toContain(sha)
    expect(proposal.summary).toContain('local-one; generation 4')
    expect(request).toHaveBeenCalledTimes(1)
    await expect(runExecutionCommand(`execute pipeline-a ${manifestSha}`, request, 'foreign')).rejects.toThrow('current session')
    await expect(runExecutionCommand(`execute pipeline-a ${sha}`, request, 'owned')).rejects.toThrow('exact manifest')
    await runExecutionCommand(`execute pipeline-a ${manifestSha}`, request, 'owned')
    expect(request).toHaveBeenLastCalledWith('runtime.services.execute', { session_id: 'owned', schema_version: 1, pipeline_id: 'pipeline-a', manifest_sha256: manifestSha })
  })

  it.each(['session_id', 'schema_version', 'identity', 'executor', 'profile'])('rejects caller authority field %s without any RPC', async field => {
    const request = mock(() => prepared())
    await expect(runExecutionCommand(`prepare ${JSON.stringify({ ...prepareInput, [field]: 'foreign' })}`, request, 'owned')).rejects.toThrow('Unsupported preparation field')
    expect(request).not.toHaveBeenCalled()
  })

  it('retains committed transfer receipts and shows a truthful partial service outage', async () => {
    const request = mock(() => json({ ...pending, state: 'partial', next_stage: 1, receipts: [stage], blocked_reason: 'service_unavailable' }))
    const result = await runExecutionCommand('status pipeline-a', request, 'owned')
    expect(result).toContain('partial; blocked: service_unavailable')
    expect(result).toContain('local_sqlite_commit')
    expect(result).toContain('Input 24 bytes')
    expect(result).toContain(stage.transfer_id)
    expect(result).toContain('No service substitution or automatic retry')
  })

  it.each(['executor_disconnected', 'project_grant_revoked', 'stale_owner'])('never retries an uncertain %s mutation and keeps exact safe reason', async reason => {
    const request = mock(method => {
      if (method === 'runtime.services.prepare') { return prepared() }
      throw Object.assign(new Error('sensitive details must not print'), { data: { code: reason } })
    })

    await prepareRuntimeExecution(request, 'owned', prepareInput)
    await expect(runExecutionCommand(`execute pipeline-a ${manifestSha}`, request, 'owned')).rejects.toThrow(`outcome unconfirmed (${reason})`)
    await expect(runExecutionCommand(`execute pipeline-a ${manifestSha}`, request, 'owned')).rejects.toThrow('already attempted')
    expect(request).toHaveBeenCalledTimes(2)
  })

  it('rejects substituted source and broken receipt-chain responses', async () => {
    const request = mock(() => json({ manifest: { ...manifest, source: { ...manifest.source, version: 99 } }, manifest_sha256: manifestSha }))
    await expect(prepareRuntimeExecution(request, 'owned', prepareInput)).rejects.toThrow('does not match')
    const broken = mock(() => json({ ...pending, state: 'completed', next_stage: null, receipts: [stage, { ...stage, stage: 1, input_sha256: sha }] }))
    await expect(runExecutionCommand('status pipeline-a', broken, 'owned')).rejects.toThrow('receipt chain')
  })

  it('does not display completed service work without matching stage receipts', async () => {
    const request = mock(() => json({ ...pending, state: 'completed', next_stage: null }))
    await expect(runExecutionCommand('status pipeline-a', request, 'owned')).rejects.toThrow('matching stage receipts')
  })

  it('verifies complete staged bytes without dumping source content or claiming publication', async () => {
    const content = JSON.stringify({ schema_version: 1, text: 'private text', headings: [] })
    const bytes = new TextEncoder().encode(content)
    const checksum = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))).map(value => value.toString(16).padStart(2, '0')).join('')
    const result = { pipeline_id: 'pipeline-a', receipt: { ...stage, stage: 1, service_id: 'local.document.structure', output_mime: 'application/json', output_sha256: checksum, output_size: bytes.length }, content_base64: btoa(content) }
    const request = mock(() => json(result))
    const output = await runExecutionCommand('output pipeline-a', request, 'owned')
    expect(output).toContain('bytes verified')
    expect(output).not.toContain('private text')
    expect(output).toContain('Publication and delivery have not been performed')
    const broken = mock(() => json({ ...result, content_base64: btoa('tampered') }))
    await expect(runExecutionCommand('output pipeline-a', broken, 'owned')).rejects.toThrow('digest/size mismatch')
  })

  it('shows focused verification receipts and outcome_unknown effects without fake completion', async () => {
    const effect = { effect_id: 'effect-a', state: 'outcome_unknown', operation_id: 'op-a', operation_type: 'unsupported', run_id: 'run-a', generation: 1, policy_version: 'policy-a', action_digest: sha, replay_permitted: false }
    const request = mock(method => method === 'runtime.mission.receipts.list' ? { receipts: [{ receipt_id: 'receipt-a', result: 'pass', verifier: 'focused_schema', criterion_id: 'criterion-a', mission_revision: 3, evidence_ref: 'evidence-a', observed_at: 1700000000, artifact_refs: [{ artifact_id: 'artifact-a', version: 2 }], details: { checks: ['schema_only'] } }], limit: 30, limit_reached: false, complete: false } : { effect, evidence: [{ sequence: 1, generation: 1, from_state: 'dispatched', state: 'outcome_unknown', receipt_available: false, receipt_sha256: null }] })
    expect(await runExecutionCommand('receipts', request, 'owned')).toContain('focused pass is not a full-suite pass')
    const result = await runExecutionCommand('effect effect-a', request, 'owned')
    expect(result).toContain('outcome_unknown')
    expect(result).toContain('No confirmed final outcome')
    expect(result).toContain('Operation op-a')
  })
})
