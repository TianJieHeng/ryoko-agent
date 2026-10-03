import { createHash } from 'node:crypto'
import { mkdtemp, readFile, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

import { expect, it, vi } from 'vitest'

import type { SlashRunCtx } from '../slash/types.js'

import { runArtifactDownload } from './artifact-download.js'

it('saves complete verified bytes and refuses to overwrite an existing local client file', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'ryoko-artifact-test-'))
  const target = join(directory, 'document.md')
  const content = '# Verified\nComplete file\n'
  const bytes = Buffer.from(content)
  const request = vi.fn(async () => ({ project_id: 'p', artifact_id: 'a', version: 1, sha256: createHash('sha256').update(bytes).digest('hex'), size: bytes.length, mime: 'text/markdown', offset: 0, data_base64: bytes.toString('base64'), next_offset: bytes.length, eof: true, preview_mode: 'plain_text' }))
  const sys = vi.fn(), error = vi.fn()
  const context = { sid: 'owned', gateway: { gw: { request } }, transcript: { sys }, stale: () => false, guardedErr: error } as unknown as SlashRunCtx

  try {
    expect(runArtifactDownload(`download p a 1 ${target}`, context)).toBe(true)
    await vi.waitFor(() => expect(sys).toHaveBeenCalledOnce())
    expect(await readFile(target, 'utf8')).toBe(content)
    runArtifactDownload(`download p a 1 ${target}`, context)
    await vi.waitFor(() => expect(error).toHaveBeenCalledOnce())
    expect(await readFile(target, 'utf8')).toBe(content)
  } finally { await rm(directory, { recursive: true, force: true }) }
})
