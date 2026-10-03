import { describe, expect, it, vi } from 'vitest'

import type { ArtifactProposalResult, ArtifactPublishResult } from './gateway-contract.generated.js'
import type { RuntimeRequest } from './runtime-control.js'
import { runDomainCommand, validateDomainJob } from './runtime-domains.js'

const digest = 'a'.repeat(64)

const proposal: ArtifactProposalResult = { project_id: 'project-a', artifact_id: 'output', version: 1, sha256: digest, size: 50, mime: 'text/markdown',
  request_id: 'domain:creative:0', parent_version: null, expected_head_version: null, action_digest: digest,
  approval_id: 'approval-output', approval_digest: digest, expires_at: 2000000000 }

const manifest = { ...proposal, artifact_id: 'manifest', request_id: 'domain:creative:manifest', approval_id: 'approval-manifest', mime: 'application/json' }
const publishRow = (row: ArtifactProposalResult): ArtifactPublishResult => ({ ...row, disposition: 'canonical', head_version: row.version, validation_status: 'passed', approval_status: 'approved' })
const creative = { job_id: 'creative', project_id: 'project-a', adapter: 'creative', arguments: { brief: 'Private client brief', prompts: ['Private prompt'], assets: [], continuity: ['Private continuity'] } }
const input = { command_id: 'creative-control', job_json: JSON.stringify(creative) }

const data = { job_id: 'totals', project_id: 'project-a', adapter: 'data', arguments: {
  inputs: [{ source_id: 'orders', ref: { artifact_id: 'csv', version: 2, sha256: digest }, options: {
    encoding: 'utf-8', delimiter: ',', date_format: null, currency: null, null_values: [''], units: { amount: 'points' }, duplicate_keys: 'allow', column_types: { amount: 'decimal' } } }],
  recipe: { base: 'orders', aggregate: { group_by: ['group'], aggregations: { total: { operation: 'sum', column: 'amount' } }, nulls: 'reject' },
    exports: ['csv', 'xlsx', 'ipynb'], charts: [{ kind: 'bar', category: 'group', value: 'total' }] } } }

describe('selected deterministic domain controls', () => {
  it('prepares a prompt-only package with zero generation calls and preserves ordered exact publication approvals', async () => {
    const request = vi.fn(async () => ({ proposals: [proposal, manifest], publication_atomic: false }))
    const preview = await runDomainCommand(`prepare ${JSON.stringify(input)}`, request as RuntimeRequest, 'owned-session')
    expect(request).toHaveBeenCalledExactlyOnceWith('runtime.domain.prepare', { ...input, session_id: 'owned-session', schema_version: 1 })
    expect(preview).toContain('prompt-only')
    expect(preview).toContain('generated assets: 0')
    expect(preview).toContain('awaiting approval')
    expect(preview).not.toContain('Private')
    const approvals = [proposal, manifest].map(row => ({ approval_id: row.approval_id, approval_digest: row.approval_digest }))
    const published = vi.fn(async () => ({ project_id: 'project-a', outputs: [publishRow(proposal)], manifest: publishRow(manifest), state: 'published', publication_atomic: false, external_production: 'not_performed' }))
    const result = await runDomainCommand(`publish ${JSON.stringify({ ...input, approvals })}`, published as RuntimeRequest, 'owned-session')
    expect(published).toHaveBeenCalledExactlyOnceWith('runtime.domain.publish', { ...input, approvals, session_id: 'owned-session', schema_version: 1 })
    expect(result).toContain('Published')
    expect(result).toContain('awaiting production')
    expect(result).toContain('Delivery is not confirmed')
  })

  it('accepts pinned data recipes and rejects unsupported exports, ambiguous assumptions, identity injection and media-generation stages before RPC', async () => {
    expect(validateDomainJob(data)).toEqual(data)
    const request = vi.fn()

    const invalid = [
      { ...data, arguments: { ...data.arguments, recipe: { ...data.arguments.recipe, exports: ['pdf'] } } },
      { ...creative, arguments: { ...creative.arguments, identity: 'other' } },
      { ...creative, adapter: 'host_shell' },
      { ...creative, arguments: { ...creative.arguments, assets: [{ name: 'image.png', ref: { artifact_id: 'image', version: 1, sha256: digest }, rights: 'owned', stage: 'generated' }] } }
    ]

    for (const job of invalid) {
      const result = await runDomainCommand(`prepare ${JSON.stringify({ command_id: 'test', job_json: JSON.stringify(job) })}`, request as RuntimeRequest, 'owned-session')
      expect(result).toContain('Invalid')
    }

    expect(request).not.toHaveBeenCalled()
    expect(await runDomainCommand('--help', request as RuntimeRequest, 'owned-session')).toContain('lineage')
  })

  it('does not retry an uncertain non-atomic publication or echo private errors, and preserves pending command state', async () => {
    const request = vi.fn(async () => { throw new Error('private data, API key and source path') })
    const approvals = [proposal, manifest].map(row => ({ approval_id: row.approval_id, approval_digest: digest }))
    const failure = await runDomainCommand(`publish ${JSON.stringify({ ...input, approvals })}`, request as RuntimeRequest, 'owned-session')
    expect(request).toHaveBeenCalledTimes(1)
    expect(failure).toContain('not confirmed')
    expect(failure).not.toContain('private data')
    const pending = vi.fn(async () => ({ command_id: 'creative-control', run_id: 'run', status: 'claimed', owner_live: true, expires_at: null, result: null }))
    const status = await runDomainCommand('inspect creative-control', pending as RuntimeRequest, 'owned-session')
    expect(status).toContain('active; publication is not confirmed')
    expect(pending).toHaveBeenCalledTimes(1)
  })

  it('opens a digest-verified authorized data manifest and renders source-row lineage without cells, refusing corrupted bytes', async () => {
    const manifestRecord = { schema_version: 1, project_id: 'project-a', adapter: 'data', domain_metadata: {
      profile: { rows: 1, columns: [{ name: 'total', null_count: 0, formula_count: 0, unit: 'points' }] },
      recipe: data.arguments.recipe, formula_status: 'preserved_not_recalculated',
      lineage: [[{ source_id: 'orders', row: 2, sheet: '', sha256: digest }, { source_id: 'orders', row: 3, sheet: '', sha256: digest }]],
      private_cells: ['sensitive value that must not be rendered'] } }

    const bytes = new TextEncoder().encode(JSON.stringify(manifestRecord))
    const hash = [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))].map(byte => byte.toString(16).padStart(2, '0')).join('')
    const ref = { artifact_id: 'manifest', version: 1, sha256: hash }

    const page = { project_id: 'project-a', ...ref, size: bytes.length, mime: 'application/json', offset: 0,
      data_base64: btoa(String.fromCharCode(...bytes)), next_offset: bytes.length, eof: true, preview_mode: 'plain_text' }

    const request = vi.fn(async () => page)
    const command = `lineage ${JSON.stringify({ project_id: 'project-a', manifest_ref: ref, row: 0 })}`
    const text = await runDomainCommand(command, request as RuntimeRequest, 'owned-session')
    expect(request).toHaveBeenCalledExactlyOnceWith('runtime.artifact.get', { session_id: 'owned-session', schema_version: 1, project_id: 'project-a', artifact_id: 'manifest', version: 1, offset: 0, limit: 65536 })
    expect(text).toContain('2 source row(s)')
    expect(text).toContain('source row 2')
    expect(text).toContain('source row 3')
    expect(text).toContain('preserved_not_recalculated')
    expect(text).not.toContain('sensitive value')
    const corrupted = new Uint8Array(bytes)
    corrupted[0] = 91
    request.mockResolvedValueOnce({ ...page, data_base64: btoa(String.fromCharCode(...corrupted)) })
    expect(await runDomainCommand(command, request as RuntimeRequest, 'owned-session')).toContain('Complete manifest digest differs')
    expect(request).toHaveBeenCalledTimes(2)
  })
})
