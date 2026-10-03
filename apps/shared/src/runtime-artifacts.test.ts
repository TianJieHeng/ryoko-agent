import { describe, expect, it, vi } from 'vitest'

import { downloadRuntimeArtifact, runArtifactCommand } from './runtime-artifacts.js'
import type { RuntimeRequest } from './runtime-control.js'

const source = '# Title\nComplete document\n'
const content = new TextEncoder().encode(source)
const sha256 = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', content))).map(value => value.toString(16).padStart(2, '0')).join('')

describe('complete artifact controls', () => {
  it('assembles all immutable chunks and rejects truncation or digest substitution', async () => {
    const request = vi.fn(async (_method: string, params: { offset?: number }) => {
      const offset = params.offset ?? 0
      const end = Math.min(content.length, offset + 8)

      return { project_id: 'p', artifact_id: 'a', version: 2, sha256, size: content.length, mime: 'text/markdown', offset, data_base64: btoa(new TextDecoder().decode(content.subarray(offset, end))), next_offset: end, eof: end === content.length, preview_mode: 'plain_text' }
    }) as RuntimeRequest

    const result = await downloadRuntimeArtifact(request, 'owned', 'p', 'a', 2)
    expect(result.bytes).toEqual(new Uint8Array(content))
    expect(result.metadata.eof).toBe(true)
    expect(request).toHaveBeenCalledTimes(Math.ceil(content.length / 8))
    const broken = vi.fn(async () => ({ project_id: 'p', artifact_id: 'a', version: 2, sha256, size: content.length, mime: 'text/markdown', offset: 0, data_base64: 'eA==', next_offset: 1, eof: true, preview_mode: 'plain_text' })) as RuntimeRequest
    await expect(downloadRuntimeArtifact(broken, 'owned', 'p', 'a', 2)).rejects.toThrow('truncated')
  })
  it('never publishes on prepare and requires exact reviewed approval/content before promotion', async () => {
    const proposal = { request_id: 'req', project_id: 'p', artifact_id: 'a', version: 1, sha256, size: content.length, mime: 'text/markdown', parent_version: null, expected_head_version: null, approval_id: 'ap', approval_digest: 'd'.repeat(64), action_digest: 'e'.repeat(64), expires_at: Date.now() / 1000 + 120 }
    const request = vi.fn(async (method: string) => method === 'runtime.artifact.prepare' ? proposal : { ...proposal, disposition: 'canonical', validation_status: 'passed' }) as RuntimeRequest
    const json = JSON.stringify({ project_id: 'p', command_id: 'cmd', request_id: 'req', content: source })
    const prepared = await runArtifactCommand(`prepare ${json}`, request, 'owned')
    expect(prepared).toContain('NOT published')
    expect(request).toHaveBeenCalledTimes(1)
    await expect(runArtifactCommand('publish cmd wrong wrong', request, 'owned')).rejects.toThrow('does not match')
    await runArtifactCommand(`publish cmd ap ${'d'.repeat(64)}`, request, 'owned')
    expect(request).toHaveBeenLastCalledWith('runtime.artifact.publish', expect.objectContaining({ session_id: 'owned', content: source, command_id: 'cmd', approval_id: 'ap' }))
    await expect(runArtifactCommand(`publish cmd ap ${'d'.repeat(64)}`, request, 'other')).rejects.toThrow('not in this view')
  })
})
