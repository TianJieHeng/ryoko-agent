import { describe, expect, it, vi } from 'vitest'

import type { RuntimeRequest } from './runtime-control.js'
import { runCaptureCommand, runMemoryCommand, runProjectCommand } from './runtime-projects.js'

describe('project and memory controls', () => {
  it('refuses stale project attachment and never clears unrelated canonical artifacts', async () => {
    const request = vi.fn(async () => ({ project: { project_id: 'p', revision: 8, canonical_artifact_refs: [{ artifact_id: 'old', version: 2 }] } })) as RuntimeRequest
    await expect(runProjectCommand('attach p 7 new 1', request, 'owned')).rejects.toThrow('Project changed')
    expect(request).toHaveBeenCalledTimes(1)
    await runProjectCommand('attach p 8 new 1', request, 'owned')
    expect(request).toHaveBeenLastCalledWith('runtime.project.update', {
      session_id: 'owned', schema_version: 1, project_id: 'p', expected_revision: 8,
      changes: { canonical_artifact_refs: [{ artifact_id: 'old', version: 2 }, { artifact_id: 'new', version: 1 }] }
    })
  })
  it('shows correction conflict without success and preserves explicit memory scope', async () => {
    const request = vi.fn(async () => ({ outcome: { success: false, code: 'version_conflict', expected_version: 2, current_version: 3 } })) as RuntimeRequest
    const result = await runMemoryCommand('correct record 2 project:p Use concise prose', request, 'owned')
    expect(result).toContain('not applied')
    expect(request).toHaveBeenCalledWith('runtime.memory.record.write', expect.objectContaining({ expected_version: 2, scope: 'project:p', session_id: 'owned' }))
    expect(result).not.toContain('Acknowledged')
  })
  it('retains original capture references on extraction failure and files by exact revision', async () => {
    const capture = { capture_id: 'c', revision: 4, original_ref: { artifact_id: 'a', version: 2 }, annotation: 'original note', filed_project_id: null, extractions: [{ status: 'failed', failure_code: 'unsupported' }] }
    const request = vi.fn(async () => ({ capture })) as RuntimeRequest
    const result = await runCaptureCommand('get c', request, 'owned')
    expect(result).toContain('Original a@2')
    expect(result).toContain('failed (unsupported)')
    await runCaptureCommand('file c - 4', request, 'owned')
    expect(request).toHaveBeenLastCalledWith('runtime.capture.file', { session_id: 'owned', schema_version: 1, capture_id: 'c', filed_project_id: null, expected_revision: 4 })
  })
})
