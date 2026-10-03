import { describe, expect, it, vi } from 'vitest'

import type { ArtifactProposalResult } from './gateway-contract.generated.js'
import type { RuntimeRequest } from './runtime-control.js'
import { runResearchCommand, summarizeResearch } from './runtime-research.js'

const sha = 'a'.repeat(64)

const source = { source_id: 'source-a', source_type: 'project_artifact', project_id: 'project-a', version: 2, sha256: sha,
  evidence_ranges: [{ start: 0, end: 2, quote: '20', sha256: sha }] }

const sourceView = { ...source, scope: { project_id: 'project-a', principal_id: 'private-principal' },
  availability: 'available', freshness: 'within_declared_window', errors: [], covered_bytes: 2, size: 20,
  head_version: 2, claim_verification: 'not_performed' }

const resolved = { sources: [sourceView], coverage: { requested: 1, available: 1, missing: 0, inaccessible: 0, invalid: 0, stale: 0, cited: 1 },
  complete: true, search_complete: false, validator_manifest: { status: 'passed', claim_verification: 'not_performed', external_connected_sources: 0 } }

const proposal: ArtifactProposalResult = { project_id: 'project-a', artifact_id: 'brief', version: 3, sha256: sha, size: 100, mime: 'text/markdown',
  request_id: 'refresh', parent_version: 2, expected_head_version: 2, action_digest: sha,
  approval_id: 'approval-brief', approval_digest: sha, expires_at: 2000000000 }

const manifest = { ...proposal, artifact_id: 'manifest', mime: 'application/json', approval_id: 'approval-manifest' }

const input = { project_id: 'project-a', command_id: 'refresh', request_id: 'refresh', artifact_id: 'brief', parent_version: 2,
  manifest_ref: { artifact_id: 'manifest', version: 2, sha256: sha },
  request_json: JSON.stringify({ requests: [source], updates: [{ claim_id: 'limit', replacement: 'The limit is 20.' }] }) }

describe('research control projections', () => {
  it('reports exact source versions, missing sources and stale conflicts without source content or private identity', async () => {
    const conflict = { ...resolved, sources: [{ ...sourceView, freshness: 'stale', head_version: 3, errors: ['source_not_head'] },
      { ...sourceView, source_id: 'missing', availability: 'missing', freshness: 'unknown', errors: ['artifact_not_found'], size: null, covered_bytes: 0 }],
      coverage: { ...resolved.coverage, requested: 2, missing: 1, stale: 1 }, complete: false }

    const request = vi.fn(async () => ({ response_json: JSON.stringify(conflict) }))
    const text = await runResearchCommand(`resolve ${JSON.stringify([source])}`, request as RuntimeRequest, 'owned-session')
    expect(request).toHaveBeenCalledExactlyOnceWith('runtime.research.resolve', { session_id: 'owned-session', schema_version: 1, request_json: JSON.stringify([source]) })
    expect(text).toContain('1/2')
    expect(text).toContain('missing')
    expect(text).toContain('stale')
    expect(text).toContain('head v3')
    expect(text).toContain('Claims are not verified')
    expect(text).not.toContain('private-principal')
    expect(text).not.toContain('quote')
    expect(() => summarizeResearch({ response_json: '{"sources":[]}' })).toThrow()
  })

  it('preserves the exact prepare/publish binding and partial manifest result without retry or claiming delivery', async () => {
    const prepared = { project_id: 'project-a', state: 'awaiting_approval', brief: proposal, manifest, publication_atomic: false,
      factual_changes: ['limit'], interpretation_changes: [], source_manifest: resolved }

    const request = vi.fn(async () => ({ response_json: JSON.stringify(prepared) }))
    const preview = await runResearchCommand(`prepare ${JSON.stringify(input)}`, request as RuntimeRequest, 'owned-session')
    expect(preview).toContain('Prepared')
    expect(preview).toContain('approval-manifest')
    expect(preview).toContain('Factual changes: limit')

    const publishInput = { ...input, brief_approval_id: proposal.approval_id, brief_approval_digest: sha,
      manifest_approval_id: manifest.approval_id, manifest_approval_digest: sha }

    request.mockResolvedValueOnce({ response_json: JSON.stringify({ state: 'partial', project_id: 'project-a', publication_atomic: false,
      brief: { ...proposal, disposition: 'canonical', head_version: 3, validation_status: 'passed', approval_status: 'approved' },
      manifest: null, manifest_status: 'not_confirmed_committed' }) })
    const partial = await runResearchCommand(`publish ${JSON.stringify(publishInput)}`, request as RuntimeRequest, 'owned-session')
    expect(request.mock.calls).toHaveLength(2)
    expect(request.mock.calls[1]).toEqual(['runtime.brief.publish', { ...publishInput, session_id: 'owned-session', schema_version: 1 }])
    expect(partial).toContain('Partial publication')
    expect(partial).toContain('manifest commit is unconfirmed')
    expect(partial).not.toContain('Delivered')
  })

  it('blocks scope injection and malformed inputs before RPC and keeps unconfirmed/conflicting outcomes explicit', async () => {
    const request = vi.fn(async () => { throw Object.assign(new Error('private raw source content'), { data: { code: 'brief_section_changed' } }) })
    const call = (text: string) => runResearchCommand(text, request as RuntimeRequest, 'owned-session')

    for (const patch of [{ session_id: 'foreign' }, { schema_version: 1 }, { identity: {} }]) {
      expect(await call(`prepare ${JSON.stringify({ ...input, ...patch })}`)).toContain('Invalid')
    }

    expect(await call(`resolve ${JSON.stringify([{ ...source, evidence_ranges: [{ start: 4, end: 2, quote: 'x', sha256: sha }] }])}`)).toContain('Invalid')
    expect(await call('prepare {"command_id":"one","command_id":"two"}')).toContain('Duplicate JSON fields')
    expect(request).not.toHaveBeenCalled()
    const failure = await call(`prepare ${JSON.stringify(input)}`)
    expect(request).toHaveBeenCalledTimes(1)
    expect(failure).toContain('changed')
    expect(failure).toContain('not confirmed')
    expect(failure).not.toContain('private raw source content')
    expect(await call('--help')).toContain('inspect <command-id>')
  })

  it('derives initial baseline identity from an owned read, reuses exact timestamp-bound bytes and isolates session approvals', async () => {
    const prepared = { project_id: 'project-a', state: 'awaiting_approval', brief: { ...proposal, request_id: 'initial' }, manifest, publication_atomic: false,
      factual_changes: ['limit'], interpretation_changes: [], source_manifest: resolved }

    const request = vi.fn(async (method: string, _params: unknown) => ({ response_json: JSON.stringify(method === 'runtime.research.resolve' ? resolved : prepared) }))

    const initial = { project_id: 'project-a', command_id: 'initial', request_id: 'initial', artifact_id: 'brief', parent_version: 2,
      previous_requests: [source], requests: [source], updates: [{ claim_id: 'limit', replacement: 'The limit is 20.' }],
      claims: [{ claim_id: 'limit', section: 'Facts', text: 'The limit is 10.', kind: 'fact', section_sha256: sha, citations: [{ source_id: source.source_id, range_index: 0 }] }] }

    const preview = await runResearchCommand(`prepare-initial ${JSON.stringify(initial)}`, request as RuntimeRequest, 'owned-session')
    expect(preview).toContain('Prepared brief')
    expect(preview).not.toContain('private-principal')
    const binding = request.mock.calls[1][1] as { request_json: string }
    expect(JSON.parse(binding.request_json).previous_sources[0].scope.principal_id).toBe('private-principal')

    const approvals = { command_id: 'initial', brief_approval_id: proposal.approval_id, brief_approval_digest: sha,
      manifest_approval_id: manifest.approval_id, manifest_approval_digest: sha }

    expect(await runResearchCommand(`publish-initial ${JSON.stringify(approvals)}`, request as RuntimeRequest, 'other-session')).toContain('No prepared initial baseline')
    expect(request).toHaveBeenCalledTimes(2)
    request.mockResolvedValueOnce({ response_json: JSON.stringify({ state: 'partial', publication_atomic: false,
      brief: { ...proposal, disposition: 'canonical', head_version: 3, validation_status: 'passed', approval_status: 'approved' }, manifest: null }) })
    const result = await runResearchCommand(`publish-initial ${JSON.stringify(approvals)}`, request as RuntimeRequest, 'owned-session')
    expect(result).toContain('Partial publication')
    expect((request.mock.calls[2][1] as { request_json: string }).request_json).toBe(binding.request_json)
    expect(request.mock.calls.map(call => call[0])).toEqual(['runtime.research.resolve', 'runtime.brief.prepare', 'runtime.brief.publish'])
  })
})
