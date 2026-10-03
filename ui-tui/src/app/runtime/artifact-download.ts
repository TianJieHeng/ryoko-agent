import { writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'

import { downloadRuntimeArtifact } from '@hermes/shared/runtime-artifacts'
import type { RuntimeRequest } from '@hermes/shared/runtime-control'

import type { SlashRunCtx } from '../slash/types.js'

/** Explicit local-client destination, complete bytes only, and never overwrite. */
export function runArtifactDownload(argument: string, ctx: SlashRunCtx): boolean {
  if (!argument.startsWith('download ')) {return false}
  const [_, project, artifact, version, ...path] = argument.split(/\s+/)
  const destination = path.join(' ')

  if (!ctx.sid || !project || !artifact || !version || !destination) {
    ctx.transcript.sys('Use /runtime artifact download <project-id> <artifact-id> <version> <new-local-file-path>')

    return true
  }

  const request: RuntimeRequest = (method, params) => ctx.gateway.gw.request(method, { ...params })
  void (async () => {
    const output = await downloadRuntimeArtifact(request, ctx.sid!, project, artifact, Number(version))

    if (ctx.stale()) {return}
    const target = resolve(destination)
    await writeFile(target, output.bytes, { flag: 'wx', mode: 0o600 })

    if (!ctx.stale()) {ctx.transcript.sys(`Downloaded complete ${output.metadata.artifact_id}@${output.metadata.version}: ${output.bytes.length} bytes; SHA-256 verified\nLocal client file: ${target}\nExisting files are never overwritten; remote artifact remains unchanged`)}
  })().catch(ctx.guardedErr)

  return true
}
