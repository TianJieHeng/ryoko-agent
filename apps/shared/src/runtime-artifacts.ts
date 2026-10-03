import type { ArtifactEditParams, ArtifactPrepareParams, ArtifactProposalResult, ArtifactReadResult } from './gateway-contract.generated.js'
import type { RuntimeRequest } from './runtime-control.js'

export interface DownloadedArtifact {
  metadata: Omit<ArtifactReadResult, 'data_base64'>
  bytes: Uint8Array
}

/** Only complete, version/digest-checked bytes may be offered as a download. */
export async function downloadRuntimeArtifact(request: RuntimeRequest, sessionId: string, projectId: string, artifactId: string, version: number, signal?: AbortSignal): Promise<DownloadedArtifact> {
  if (!Number.isSafeInteger(version) || version < 1) {throw new Error('Choose an exact positive artifact version')}
  let offset = 0
  let first: ArtifactReadResult | null = null
  const chunks: Uint8Array[] = []

  for (;;) {
    signal?.throwIfAborted()
    const part = await request('runtime.artifact.get', { session_id: sessionId, schema_version: 1, project_id: projectId, artifact_id: artifactId, version, offset, limit: 65536 })
    signal?.throwIfAborted()

    if (part.project_id !== projectId || part.artifact_id !== artifactId || part.version !== version || part.offset !== offset) {throw new Error('Artifact response does not match the requested immutable version')}

    if (!Number.isSafeInteger(part.size) || part.size < 0 || part.size > 32 * 1024 * 1024) {throw new Error('Artifact size is invalid or exceeds this 32 MiB download window')}

    if (first && (part.sha256 !== first.sha256 || part.size !== first.size || part.mime !== first.mime)) {throw new Error('Artifact changed during download; no partial file will be exposed')}
    first ??= part
    const bytes = Uint8Array.from(atob(part.data_base64), char => char.charCodeAt(0))

    if (part.next_offset !== offset + bytes.length || part.next_offset > part.size || (!part.eof && bytes.length === 0)) {throw new Error('Artifact download has a gap or invalid cursor')}
    chunks.push(bytes)
    offset = part.next_offset

    if (part.eof) {
      if (offset !== part.size) {throw new Error('Artifact download is truncated')}

      break
    }
  }

  const bytes = new Uint8Array(offset)
  let at = 0
  chunks.forEach(chunk => { bytes.set(chunk, at); at += chunk.length })
  const digest = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))).map(value => value.toString(16).padStart(2, '0')).join('')

  if (digest !== first!.sha256) {throw new Error('Artifact digest mismatch; download discarded')}
  const { data_base64: _data, ...metadata } = first!

  return { bytes, metadata: { ...metadata, next_offset: offset, eof: true } }
}

interface PreparedArtifact {
  kind: 'markdown' | 'edit'
  params: ArtifactPrepareParams | ArtifactEditParams
  proposal: ArtifactProposalResult
}
const prepared = new WeakMap<RuntimeRequest, Map<string, PreparedArtifact>>()

function scope(request: RuntimeRequest): Map<string, PreparedArtifact> {
  let value = prepared.get(request)

  if (!value) { value = new Map(); prepared.set(request, value) }

  return value
}

function key(sessionId: string, commandId: string) { return `${sessionId.length}:${sessionId}:${commandId}` }

function text(value: unknown, field: string): string {
  if (typeof value !== 'string' || !value.trim()) {throw new Error(`${field} must be nonempty text`)}

  return value
}

function integer(value: unknown, field: string): number {
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < 1) {throw new Error(`${field} must be a positive integer`)}

  return value
}

function strings(value: unknown, field: string): string[] {
  if (value === undefined) {return []}

  if (!Array.isArray(value) || value.some(item => typeof item !== 'string')) {throw new Error(`${field} must contain only text`)}

  return value as string[]
}

function input(argument: string, allowed: string[]): Record<string, unknown> {
  if (argument.length > 100000) {throw new Error('Preparation input is too large')}
  const result: unknown = JSON.parse(argument)

  if (!result || typeof result !== 'object' || Array.isArray(result)) {throw new Error('Preparation needs a JSON object')}

  for (const field of Object.keys(result)) {if (!allowed.includes(field)) {throw new Error(`Unsupported preparation field: ${field}`)}}

  return result as Record<string, unknown>
}

function proposalLines(item: PreparedArtifact): string {
  const { proposal, params } = item

  return [`Prepared ${proposal.artifact_id}@${proposal.version}; NOT published`,
    `Project ${proposal.project_id}; base ${proposal.parent_version ?? 'new'}; current-head precondition ${proposal.expected_head_version ?? 'branch'}`,
    `${proposal.mime}; ${proposal.size} bytes; digest ${proposal.sha256}`,
    `Approval ${proposal.approval_id}; digest ${proposal.approval_digest}; expires ${new Date(proposal.expires_at * 1000).toISOString()}`,
    'Review exact content below. Publication is separate from sharing or delivery.',
    'content' in params ? params.content : params.edits.map(edit => `${edit.anchor} (expected ${edit.expected_sha256})\n${edit.replacement}`).join('\n\n'),
    `To approve this exact preparation: publish ${params.command_id} ${proposal.approval_id} ${proposal.approval_digest}`].join('\n')
}

export async function runArtifactCommand(argument: string, request: RuntimeRequest, sessionId: string): Promise<string> {
  const split = argument.trim().search(/\s/)
  const action = split < 0 ? argument.trim() : argument.trim().slice(0, split)
  const rest = split < 0 ? '' : argument.trim().slice(split + 1).trim()
  const base = { session_id: sessionId, schema_version: 1 as const }

  const actions: Record<string, () => Promise<string>> = {
    prepare: async () => {
      const data = input(rest, ['project_id', 'command_id', 'request_id', 'content', 'artifact_id', 'parent_version', 'expected_head_version', 'locked_sections', 'source_refs'])

      const params: ArtifactPrepareParams = {
        ...base, project_id: text(data.project_id, 'project_id'), command_id: text(data.command_id, 'command_id'), request_id: text(data.request_id, 'request_id'), content: text(data.content, 'content'),
        ...(data.artifact_id ? { artifact_id: text(data.artifact_id, 'artifact_id') } : {}),
        ...(data.parent_version !== undefined ? { parent_version: integer(data.parent_version, 'parent_version') } : {}),
        ...(data.expected_head_version !== undefined ? { expected_head_version: integer(data.expected_head_version, 'expected_head_version') } : {}),
        locked_sections: strings(data.locked_sections, 'locked_sections'), source_refs: strings(data.source_refs, 'source_refs')
      }

      const proposal = await request('runtime.artifact.prepare', params)
      const value: PreparedArtifact = { kind: 'markdown', params, proposal }
      scope(request).set(key(sessionId, params.command_id), value)

      return proposalLines(value)
    },
    edit: async () => {
      const data = input(rest, ['project_id', 'command_id', 'request_id', 'artifact_id', 'parent_version', 'expected_head_version', 'edits'])

      if (!Array.isArray(data.edits) || data.edits.length === 0) {throw new Error('edits must contain exact section edits')}

      const edits = data.edits.map(edit => {
        if (!edit || typeof edit !== 'object' || Array.isArray(edit)) {throw new Error('Invalid section edit')}
        const value = edit as Record<string, unknown>

        if (Object.keys(value).some(field => !['anchor', 'expected_sha256', 'replacement'].includes(field))) {throw new Error('Unsupported section edit field')}
        const expected_sha256 = text(value.expected_sha256, 'expected_sha256')

        if (!/^[0-9a-f]{64}$/.test(expected_sha256)) {throw new Error('Section digest must be exact SHA-256')}

        return { anchor: text(value.anchor, 'anchor'), expected_sha256, replacement: text(value.replacement, 'replacement') }
      })

      const params: ArtifactEditParams = { ...base, project_id: text(data.project_id, 'project_id'), command_id: text(data.command_id, 'command_id'), request_id: text(data.request_id, 'request_id'), artifact_id: text(data.artifact_id, 'artifact_id'), parent_version: integer(data.parent_version, 'parent_version'), ...(data.expected_head_version !== undefined ? { expected_head_version: integer(data.expected_head_version, 'expected_head_version') } : {}), edits }
      const proposal = await request('runtime.artifact.edit.prepare', params)
      const value: PreparedArtifact = { kind: 'edit', params, proposal }
      scope(request).set(key(sessionId, params.command_id), value)

      return proposalLines(value)
    },
    publish: async () => {
      const [command, approval, digest] = rest.split(/\s+/)
      const item = scope(request).get(key(sessionId, command))

      if (!item) {throw new Error('Exact preparation is not in this view; inspect status and prepare again rather than recreating an uncertain publication')}

      if (approval !== item.proposal.approval_id || digest !== item.proposal.approval_digest) {throw new Error('Approval does not match the reviewed preparation')}

      if (item.proposal.expires_at * 1000 <= Date.now()) {throw new Error('Preparation expired; review a fresh proposal')}
      const approved = { approval_id: approval, approval_digest: digest }

      const result = item.kind === 'markdown'
        ? await request('runtime.artifact.publish', { ...item.params as ArtifactPrepareParams, ...approved })
        : await request('runtime.artifact.edit.publish', { ...item.params as ArtifactEditParams, ...approved })

      scope(request).delete(key(sessionId, command))

      return `Published ${result.artifact_id}@${result.version} as ${result.disposition}; validation ${result.validation_status}\n${result.size} bytes; digest ${result.sha256}\nDelivery/sharing not performed. Previous immutable versions remain available`
    },
    inspect: async () => {
      const [project, artifact, version] = rest.split(/\s+/)
      const result = await request('runtime.artifact.get', { ...base, project_id: text(project, 'project'), artifact_id: text(artifact, 'artifact'), version: integer(Number(version), 'version'), limit: 1 })

      return `${result.artifact_id}@${result.version} · ${result.mime} · ${result.size} bytes\nDigest ${result.sha256}; preview ${result.preview_mode}\nMetadata read only; no complete download or visual validation claimed`
    },
    compare: async () => {
      const [project, artifact, left, right] = rest.split(/\s+/)
      const [a, b] = await Promise.all([downloadRuntimeArtifact(request, sessionId, text(project, 'project'), text(artifact, 'artifact'), Number(left)), downloadRuntimeArtifact(request, sessionId, project, artifact, Number(right))])

      if (a.metadata.preview_mode !== 'plain_text' || b.metadata.preview_mode !== 'plain_text') {return `Versions ${left}/${right}: ${a.metadata.sha256 === b.metadata.sha256 ? 'identical bytes' : 'different bytes'}; active preview unavailable. Download for format-specific review`}

      return `Baseline version ${left} (unchanged)\n${new TextDecoder().decode(a.bytes)}\n\nAlternative version ${right}\n${new TextDecoder().decode(b.bytes)}\nComparison does not select or overwrite either version`
    },
    status: async () => {
      const result = await request('runtime.artifact.status', { ...base, command_id: text(rest, 'command ID') })

      return `Artifact command ${result.command_id}: ${result.status}; owner ${result.owner_live ? 'live' : 'not live'}\nCompletion is separate from download/delivery; inspect resulting artifact version before reuse`
    },
    cancel: async () => {
      const result = await request('runtime.artifact.cancel', { ...base, command_id: text(rest, 'command ID') })

      if (['cancelled', 'completed', 'failed', 'blocked'].includes(result.status)) {scope(request).delete(key(sessionId, rest))}

      return `Artifact command ${result.command_id}: ${result.status}; cancellation never undoes already committed effects`
    }
  }

  if (!Object.hasOwn(actions, action)) {return 'Artifacts: prepare <JSON: project_id,command_id,request_id,content,...> | edit <JSON: exact base/version/section digests> | publish <command-id> <approval-id> <approval-digest> | inspect <project-id> <artifact-id> <version> | compare <project-id> <artifact-id> <left-version> <right-version> | status/cancel <command-id>\nMarkdown first. Prepare/edit never publish. Format rendering and delivery require separate receipts'}

  return actions[action]()
}
