import { describe, expect, it } from 'vitest'

import type { OutputContextResult, OutputControlResult, OutputReference } from './gateway-contract.generated.js'
import { buildOutputControl, describeOutputControl, outputControlBlock, verifyOutputContext, verifyOutputControl } from './runtime-output-influences.js'
const ref: OutputReference = { record_id: 'preference', version: 7, source_ref: 'user:stated', namespace_id: 'owned', deletion_state: 'present', scope: 'project:p' }
const context: OutputContextResult = { run_id: 'run', backend: 'builtin', namespace_id: 'owned', project_id: 'p', references: [ref], context_packet_sha256: 'a'.repeat(64), immutable_prefix_sha256: 'b'.repeat(64), context_sha256: 'c'.repeat(64), coverage: 'fresh_memory_context_only', controls: [], degraded: false, state: 'supplied_to_provider_call', latest: true, causal_explanation: false, historical_context_enumerated: false }
const input = () => buildOutputControl('session', context, ref, 'correct', 'project', 'Use metric units', 'control')
const receipt = (): OutputControlResult => ({ control_id: 'control', run_id: 'run', action: 'correct', scope: 'project', project_id: 'p', status: 'memory_acknowledged', acknowledged_version: 8, applied_run_id: null, current_output_changed: false, current_run_application: 'late_not_applied', deletion_semantics: 'none' })
describe('output references and scoped controls', () => {
  it('requires exact selected run, digest and namespace', () => {
    expect(() => verifyOutputContext(context, { run_id: 'run', context_sha256: context.context_sha256 })).not.toThrow()
    expect(() => verifyOutputContext(context, { run_id: 'other', context_sha256: context.context_sha256 })).toThrow('refresh')
    expect(() => verifyOutputContext({ ...context, references: [{ ...ref, namespace_id: 'foreign' }] }, { run_id: 'run', context_sha256: context.context_sha256 })).toThrow('namespace')
  })
  it('binds exact record/version/namespace/run/context/scope without promotion', () => {
    expect(input()).toEqual({ session_id: 'session', schema_version: 1, control_id: 'control', run_id: 'run', context_sha256: context.context_sha256, record_id: 'preference', expected_version: 7, namespace_id: 'owned', action: 'correct', scope: 'project', project_id: 'p', content: 'Use metric units' })
    const response = buildOutputControl('session', context, ref, 'correct', 'response', 'Only next response', 'once')
    expect(response.project_id).toBeNull()
    expect(() => buildOutputControl('session', context, ref, 'correct', 'general', 'Wrong promotion', 'bad')).toThrow('another scope')
  })
  it('enforces durable scope, current record and latest output', () => {
    expect(outputControlBlock({ ...context, latest: false }, ref, 'ignore', 'response')).toContain('latest')
    expect(outputControlBlock(context, { ...ref, deletion_state: 'deleted' }, 'correct', 'project')).toContain('no longer present')
    expect(outputControlBlock(context, { ...ref, version: 8 }, 'ignore', 'response')).toContain('exact reference')
    const general = { ...ref, scope: 'individual' }, ctx = { ...context, references: [general] }
    expect(outputControlBlock(ctx, general, 'correct', 'project')).toContain('existing scope')
    expect(outputControlBlock(ctx, general, 'ignore', 'project')).toBeNull()
  })
  it('keeps personal harness opaque and mutation unsupported', () => {
    expect(outputControlBlock({ ...context, backend: 'personal_mcp' }, ref, 'ignore', 'response')).toContain('read-only')
    expect(() => verifyOutputContext({ ...context, backend: 'personal_mcp' }, { run_id: 'run', context_sha256: context.context_sha256 })).toThrow('no verified')
  })
  it('rejects response removal and oversized correction bytes', () => {
    expect(() => buildOutputControl('session', context, ref, 'remove', 'response', '', 'control')).toThrow('tombstone')
    expect(() => buildOutputControl('session', context, ref, 'correct', 'response', '界'.repeat(1500), 'control')).toThrow('4096')
  })
  it('does not equate queued, acknowledged or supplied context', () => {
    expect(describeOutputControl({ ...receipt(), status: 'queued_next_turn', acknowledged_version: null })).toContain('not yet supplied')
    expect(describeOutputControl(receipt())).toContain('model application is not yet confirmed')
    expect(() => verifyOutputControl({ ...receipt(), acknowledged_version: null }, input())).toThrow('newer store version')
    expect(() => verifyOutputControl({ ...receipt(), status: 'context_supplied', applied_run_id: 'run' }, input())).toThrow('later run')
  })
  it.each(['mutation_pending', 'version_conflict', 'stale_not_applied', 'expired'] as const)('keeps %s distinct', status => {
    const value = { ...receipt(), status, acknowledged_version: null }
    expect(describeOutputControl(value)).toContain(`${status}:`)
    expect(describeOutputControl(value)).toContain('Current output remains unchanged')
  })
  it('requires matching identity and tombstone acknowledgment', () => {
    expect(() => verifyOutputControl({ ...receipt(), control_id: 'foreign' }, input())).toThrow('exact requested')
    const params = buildOutputControl('session', context, ref, 'remove', 'project', '', 'control')
    expect(() => verifyOutputControl({ ...receipt(), action: 'remove' }, params)).toThrow('tombstone')
    const result = { ...receipt(), action: 'remove' as const, deletion_semantics: 'tombstone_not_physical_erasure' as const }
    expect(() => verifyOutputControl(result, params)).not.toThrow()
    expect(describeOutputControl(result)).toContain('not physically erased')
  })
})
