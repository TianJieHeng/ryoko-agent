import type { MemoryMutationResult, RuntimeProjectRecord } from './gateway-contract.generated.js'
import type { RuntimeRequest } from './runtime-control.js'

function required(value: string | undefined, name: string): string {
  if (!value?.trim()) {throw new Error(`Missing ${name}; use the feature help for its command syntax`)}

  return value
}

function revision(value: string | undefined): number {
  if (!value || !/^\d+$/.test(value) || !Number.isSafeInteger(Number(value))) {throw new Error('Use an exact nonnegative revision/version')}

  return Number(value)
}

function projectSummary(project: RuntimeProjectRecord): string {
  return `${project.name} (${project.project_id}) · revision ${project.revision}\n${project.purpose || 'Purpose not set'}\nCanonical artifacts: ${project.canonical_artifact_refs.map(ref => `${ref.artifact_id}@${ref.version}`).join(', ') || 'none'}\nProject selection grants no new access`
}

export async function runProjectCommand(argument: string, request: RuntimeRequest, sessionId: string): Promise<string> {
  const [action = 'help', id, ...rest] = argument.trim().split(/\s+/)
  const base = { session_id: sessionId, schema_version: 1 as const }

  const actions: Record<string, () => Promise<string>> = {
    list: async () => {
      const result = await request('runtime.project.list', base)

      return result.projects.length ? result.projects.map(projectSummary).join('\n\n') : 'No authorized projects found'
    },
    create: async () => projectSummary((await request('runtime.project.create', { ...base, name: required([id, ...rest].filter(Boolean).join(' '), 'project name') })).project),
    get: async () => projectSummary((await request('runtime.project.get', { ...base, project_id: required(id, 'project ID') })).project),
    select: async () => {
      const project = await request('runtime.project.get', { ...base, project_id: required(id, 'project ID') })
      const result = await request('runtime.memory.scope.set', { ...base, project_id: project.project.project_id })

      return `Project scope acknowledged: ${result.project_id ?? 'none'}\n${projectSummary(project.project)}\nPrior cached conversation history is unchanged`
    },
    attach: async () => {
      const [expected, artifact, version] = rest
      const current = await request('runtime.project.get', { ...base, project_id: required(id, 'project ID') })
      const expected_revision = revision(expected)

      if (current.project.revision !== expected_revision) {throw new Error('Project changed; inspect the latest revision before attaching')}
      const artifact_id = required(artifact, 'artifact ID')
      const artifactVersion = revision(version)

      if (artifactVersion < 1) {throw new Error('Artifact version must be positive')}
      const refs = current.project.canonical_artifact_refs.filter(ref => ref.artifact_id !== artifact_id)

      return projectSummary((await request('runtime.project.update', {
        ...base, project_id: id, expected_revision,
        changes: { canonical_artifact_refs: [...refs, { artifact_id, version: artifactVersion }] }
      })).project)
    },
    resume: async () => {
      const result = await request('runtime.resume.get', { ...base, project_id: required(id, 'project ID') })

      return [
        `Resume ${result.project.project_id} · revision ${result.project.revision}${result.project_revision_stable ? '' : ' (changed while assembling; refresh before acting)'}`,
        result.project.purpose,
        ...result.artifacts.map(ref => `Artifact ${ref.artifact_id}@${ref.version} · ${ref.derived_validity}${ref.filed_version !== ref.version ? `; filed version ${ref.filed_version}` : ''}`),
        ...result.missions.map(mission => `Mission ${mission.run_id}: ${mission.status}`),
        ...result.evidence.map(evidence => `Evidence ${evidence.anchor_id}: ${evidence.authority}, ${evidence.effective_validity}, ${evidence.freshness}`),
        ...result.blockers.map(blocker => `Blocked ${blocker.reference}: ${blocker.code}`),
        `Bounded view${Object.values(result.truncated).some(Boolean) ? '; some references omitted' : ''}. No new work started`,
        result.blockers.length ? 'Next: resolve a listed blocker or inspect the current artifact' : 'Next: inspect a current artifact or choose work to continue'
      ].filter(Boolean).join('\n')
    }
  }

  if (!Object.hasOwn(actions, action)) {return 'Projects: list | create <name> | get <project-id> | select <project-id> | resume <project-id> | attach <project-id> <expected-revision> <artifact-id> <version>\nResume is a bounded authoritative package; it does not start new work'}

  return actions[action]()
}

function memoryMutation(result: MemoryMutationResult): string {
  const outcome = result.outcome

  if (!outcome.success) {return `Correction not applied: version conflict; expected ${outcome.expected_version}, current ${outcome.current_version}. Inspect before retrying`}

  return `Acknowledged ${outcome.record.record_id}@${outcome.acknowledged_version}; scope ${outcome.record.scope}; ${outcome.record.deletion_state === 'deleted' ? 'tombstoned, not physical erasure of backups' : 'saved'}\n${outcome.record.source_ref}`
}

export async function runMemoryCommand(argument: string, request: RuntimeRequest, sessionId: string): Promise<string> {
  const [action = 'help', id, ...rest] = argument.trim().split(/\s+/)
  const base = { session_id: sessionId, schema_version: 1 as const }

  const actions: Record<string, () => Promise<string>> = {
    list: async () => {
      const result = await request('runtime.memory.records.list', { ...base, ...(id ? { project_id: id } : {}) })
      const lines = result.records.map(record => `${record.record_id}@${record.version} · ${record.kind} · ${record.scope} · ${record.validity}\n${record.content ?? '[deleted]'}\nSource: ${record.source_ref} · updated ${new Date(record.updated_at * 1000).toISOString()}`)

      return `Memory snapshot revision ${result.revision}; ${result.has_more ? 'partial list; narrow scope or use paginated inspection' : 'end of this snapshot'}\n${lines.join('\n\n') || 'No matching memory records'}`
    },
    get: async () => {
      const { record } = await request('runtime.memory.record.get', { ...base, record_id: required(id, 'record ID') })

      return `${record.record_id}@${record.version} · ${record.kind} · ${record.scope} · ${record.validity}\n${record.content ?? '[deleted]'}\nSource: ${record.source_ref}; created ${new Date(record.created_at * 1000).toISOString()}; supersedes ${record.supersedes_version ?? 'none'}`
    },
    remember: async () => memoryMutation(await request('runtime.memory.record.write', {
      ...base, record_id: crypto.randomUUID(), expected_version: 0, kind: 'preference',
      scope: required(id, 'individual or project:<id> scope'), content: required(rest.join(' '), 'preference text')
    })),
    correct: async () => {
      const [version, scope, ...content] = rest

      return memoryMutation(await request('runtime.memory.record.write', {
        ...base, record_id: required(id, 'record ID'), expected_version: revision(version),
        scope: required(scope, 'scope'), content: required(content.join(' '), 'correction'), kind: 'preference'
      }))
    },
    forget: async () => memoryMutation(await request('runtime.memory.record.delete', {
      ...base, record_id: required(id, 'record ID'), expected_version: revision(rest[0])
    }))
  }

  if (!Object.hasOwn(actions, action)) {return 'Memory: list [project-id] | get <record-id> | remember <individual|project:id> <preference> | correct <record-id> <expected-version> <scope> <preference> | forget <record-id> <expected-version>\nOnly explicit remember/correct changes durable memory. Unconfigured primary harness never falls back to built-in memory'}

  return actions[action]()
}

export async function runCaptureCommand(argument: string, request: RuntimeRequest, sessionId: string): Promise<string> {
  const [action = 'help', id, ...rest] = argument.trim().split(/\s+/)
  const base = { session_id: sessionId, schema_version: 1 as const }

  const actions: Record<string, () => Promise<string>> = {
    list: async () => {
      const result = await request('runtime.capture.list', { ...base, project_id: required(id, 'project ID') })

      return `Bounded capture list${result.limit_reached ? '; limit reached' : ''}\n${result.captures.map(c => `${c.capture_id} · revision ${c.revision} · ${c.filed_project_id || 'unfiled'}\nOriginal ${c.original_ref.artifact_id}@${c.original_ref.version}\n${c.annotation}`).join('\n\n') || 'No captures found'}`
    },
    get: async () => {
      const { capture } = await request('runtime.capture.get', { ...base, capture_id: required(id, 'capture ID') })

      return `Capture ${capture.capture_id} · revision ${capture.revision}\nOriginal ${capture.original_ref.artifact_id}@${capture.original_ref.version}\n${capture.annotation}\nExtraction: ${capture.extractions.map(e => `${e.status}${e.failure_code ? ` (${e.failure_code})` : ''}`).join(', ') || 'not performed'}\nOriginal remains authoritative; capture does not launch research`
    },
    add: async () => {
      const [artifact, version, ...annotation] = rest
      const { capture } = await request('runtime.capture.create', { ...base, project_id: required(id, 'project ID'), capture_id: crypto.randomUUID(), original_ref: { artifact_id: required(artifact, 'artifact ID'), version: revision(version) }, annotation: annotation.join(' ') })

      return `Captured ${capture.capture_id}; original ${capture.original_ref.artifact_id}@${capture.original_ref.version}; filing awaits review`
    },
    file: async () => {
      const { capture } = await request('runtime.capture.file', { ...base, capture_id: required(id, 'capture ID'), filed_project_id: rest[0] === '-' ? null : required(rest[0], 'destination project or -'), expected_revision: revision(rest[1]) })

      return `Filing acknowledged at revision ${capture.revision}: ${capture.filed_project_id ?? 'unfiled'}`
    },
    duplicates: async () => {
      const result = await request('runtime.capture.duplicates', { ...base, project_id: required(id, 'project ID') })

      return `Duplicate candidates only; no consolidation performed${result.truncated ? '; partial scan' : ''}\n${result.groups.map(group => group.capture_ids.join(', ')).join('\n') || 'No duplicates in this bounded scan'}`
    }
  }

  if (!Object.hasOwn(actions, action)) {return 'Capture: list <project-id> | get <capture-id> | add <project-id> <artifact-id> <version> <annotation> | file <capture-id> <project-id|-> <expected-revision> | duplicates <project-id>\nText indexing/mobile intake require backend adapters; originals remain accessible'}

  return actions[action]()
}
