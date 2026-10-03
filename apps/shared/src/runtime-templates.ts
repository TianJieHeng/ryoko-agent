import type { TemplateCreateParams, TemplateRecord } from './gateway-contract.generated.js'
import type { RuntimeRequest } from './runtime-control.js'

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {throw new Error('Template input must be an object')}

  return value as Record<string, unknown>
}

function text(value: unknown, field: string): string {
  if (typeof value !== 'string' || !value.trim()) {throw new Error(`Missing ${field}`)}

  return value
}

function strings(value: unknown, field: string): string[] {
  if (!Array.isArray(value) || value.some(item => typeof item !== 'string')) {throw new Error(`${field} must be a text array`)}

  return value as string[]
}

function reference(value: unknown): { artifact_id: string; version: number } {
  const ref = record(value)

  if (Object.keys(ref).some(key => !['artifact_id', 'version'].includes(key)) || !Number.isSafeInteger(ref.version) || Number(ref.version) < 1) {throw new Error('Use an exact artifact ID/version reference')}

  return { artifact_id: text(ref.artifact_id, 'artifact_id'), version: Number(ref.version) }
}

function summary(template: TemplateRecord): string {
  return [`Template ${template.template_id}@${template.version}; project ${template.project_id}`,
    `Approved example ${template.baseline_ref.artifact_id}@${template.baseline_ref.version}`,
    `Structure: ${template.structure.join(' / ')}`,
    `Slots: ${template.slots.map(slot => `${slot.name}${slot.required ? ' (required)' : ''}: ${slot.purpose}`).join('; ')}`,
    `Excluded incidental content: ${template.exclusions.join('; ') || 'none specified; review before reuse'}`,
    'Saving a template generates no output. Use a reviewed workflow with new inputs; inspect the new-topic result for incidental content'].join('\n')
}

export async function runTemplateCommand(argument: string, request: RuntimeRequest, sessionId: string): Promise<string> {
  const split = argument.trim().search(/\s/)
  const action = split < 0 ? argument.trim() : argument.trim().slice(0, split)
  const rest = split < 0 ? '' : argument.trim().slice(split + 1).trim()
  const base = { session_id: sessionId, schema_version: 1 as const }

  const actions: Record<string, () => Promise<string>> = {
    list: async () => {
      const result = await request('runtime.template.list', { ...base, project_id: text(rest, 'project ID') })

      return `Bounded template list${result.limit_reached ? '; limit reached' : ''}\n${result.templates.map(summary).join('\n\n') || 'No templates found'}`
    },
    get: async () => {
      const [id, version] = rest.split(/\s+/)

      if (version !== undefined && (!Number.isSafeInteger(Number(version)) || Number(version) < 1)) {throw new Error('Version must be positive')}

      return summary((await request('runtime.template.get', { ...base, template_id: text(id, 'template ID'), ...(version ? { version: Number(version) } : {}) })).template)
    },
    create: async () => {
      const data = record(JSON.parse(rest))
      const allowed = ['project_id', 'template_id', 'version', 'parent_version', 'baseline_ref', 'structure', 'style', 'assets', 'slots', 'exclusions']

      if (Object.keys(data).some(field => !allowed.includes(field))) {throw new Error('Unsupported template field; identity/session ownership cannot be supplied')}
      const style = record(data.style ?? {})

      if (Object.values(style).some(value => typeof value !== 'string')) {throw new Error('Style values must be text')}

      if (!Array.isArray(data.slots)) {throw new Error('Define template slots explicitly')}

      const slots = data.slots.map(item => {
        const slot = record(item)

        if (Object.keys(slot).some(field => !['name', 'purpose', 'required'].includes(field)) || typeof slot.required !== 'boolean') {throw new Error('Slot needs name, purpose and required boolean')}

        return { name: text(slot.name, 'slot name'), purpose: text(slot.purpose, 'slot purpose'), required: slot.required }
      })

      if (data.assets !== undefined && !Array.isArray(data.assets)) {throw new Error('Assets must be exact artifact references')}
      const version = data.version ?? 1

      if (!Number.isSafeInteger(version) || Number(version) < 1) {throw new Error('Version must be positive')}

      if (data.parent_version !== undefined && (!Number.isSafeInteger(data.parent_version) || Number(data.parent_version) < 1)) {throw new Error('Parent version must be positive')}

      const params: TemplateCreateParams = {
        ...base, project_id: text(data.project_id, 'project_id'), template_id: text(data.template_id, 'template_id'), version: Number(version),
        ...(data.parent_version ? { parent_version: Number(data.parent_version) } : {}), baseline_ref: reference(data.baseline_ref),
        structure: strings(data.structure, 'structure'), style: style as Record<string, string>, assets: (data.assets as unknown[] ?? []).map(reference), slots, exclusions: strings(data.exclusions, 'exclusions')
      }

      return summary((await request('runtime.template.create', params)).template)
    }
  }

  if (!Object.hasOwn(actions, action)) {return 'Templates: list <project-id> | get <template-id> [version] | create <JSON: project_id,template_id,baseline_ref,structure,style,assets,slots,exclusions>\nOnly explicitly approved examples belong in reusable templates. Creation does not train a model or grant source access'}

  return actions[action]()
}
