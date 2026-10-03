import type { BriefManifestRef, DomainApproval, DomainPrepareParams, DomainPrepareResult, DomainPublishResult } from './gateway-contract.generated.js'
import type { RuntimeRequest } from './runtime-control.js'
import {
  artifactProposal, artifactPublication, artifactReference, controlChoice, controlDigest, controlFailure,
  controlId, controlInteger, controlJson, controlList, controlRecord, controlRef, controlText,
  RuntimeInputError, summarizeArtifactCommand
} from './runtime-research.js'

export interface DataOptions {
  encoding: 'utf-8' | 'utf-8-sig' | 'latin-1'; delimiter: ',' | ';' | '\t' | '|'
  date_format: 'YYYY-MM-DD' | 'DD/MM/YYYY' | 'MM/DD/YYYY' | 'excel_serial' | null
  currency: string | null; null_values: string[]; units: Record<string, string>; duplicate_keys: 'allow' | 'reject' | 'keep_first'
  mapping?: Record<string, string | number> | null; column_types?: Record<string, 'text' | 'decimal' | 'integer' | 'currency' | 'date' | 'native'>
  keys?: string[]; decimal_separator?: '.' | ','; grouping_separator?: '.' | ',' | null; sheet?: string
}
export interface DataInput { source_id: string; ref: BriefManifestRef; options: DataOptions }
export interface DataJoin {
  right: string; left_on: string[]; right_on: string[]; how: 'inner' | 'left'
  cardinality: 'one_to_one' | 'many_to_one' | 'one_to_many' | 'many_to_many'; null_keys: 'reject' | 'never_match'; right_prefix?: string
}
export interface DataRecipe {
  base: string; joins?: DataJoin[]
  aggregate?: { group_by: string[]; aggregations: Record<string, { operation: 'sum' | 'mean' | 'min' | 'max' | 'count'; column: string }>; nulls: 'reject' | 'skip' } | null
  charts?: { kind: 'bar' | 'line'; category: string; value: string }[]; exports?: ('csv' | 'xlsx' | 'ipynb')[]
}
export interface CreativeAsset { name: string; ref: BriefManifestRef; rights: 'owned' | 'licensed' | 'permission_recorded' | 'unknown'; stage: 'supplied' }
export interface CreativeArguments { brief: string; prompts: string[]; assets?: CreativeAsset[]; continuity?: string[] }
export type SelectedDomainJob =
  { job_id: string; project_id: string; adapter: 'data'; arguments: { inputs: DataInput[]; recipe: DataRecipe } } |
  { job_id: string; project_id: string; adapter: 'creative'; arguments: CreativeArguments }
type OwnedDomainInput = Omit<DomainPrepareParams, 'session_id' | 'schema_version'>

function domainId(value: unknown): string {
  if (typeof value !== 'string' || !/^[A-Za-z0-9_.:-]{1,128}$/u.test(value)) {throw new RuntimeInputError('Domain IDs require 1–128 letters, numbers, underscores, dots, colons or hyphens')}

  return value
}

function texts(value: unknown, minimum = 0, maximum = 256): string[] {
  return controlList(value, minimum, maximum).map(item => controlText(item))
}

function mapValues(value: unknown, validate: (item: unknown) => unknown): void {
  const row = controlRecord(value)

  if (Object.keys(row).length > 256) {throw new RuntimeInputError('Column mapping exceeds the supported bound')}

  for (const [key, item] of Object.entries(row)) { controlText(key); validate(item) }
}

function dataOptions(value: unknown): void {
  const row = controlRecord(value, ['encoding', 'delimiter', 'date_format', 'currency', 'null_values', 'units', 'duplicate_keys'],
    ['mapping', 'column_types', 'keys', 'decimal_separator', 'grouping_separator', 'sheet'])

  controlChoice(row.encoding, ['utf-8', 'utf-8-sig', 'latin-1']); controlChoice(row.delimiter, [',', ';', '\t', '|'])

  if (row.date_format !== null) {controlChoice(row.date_format, ['YYYY-MM-DD', 'DD/MM/YYYY', 'MM/DD/YYYY', 'excel_serial'])}

  if (row.currency !== null && (typeof row.currency !== 'string' || !/^[A-Z]{3}$/u.test(row.currency))) {throw new RuntimeInputError('Currency must be an explicit uppercase ISO code or null')}
  controlList(row.null_values, 0, 32).forEach(item => { if (typeof item !== 'string' || item.length > 128) {throw new RuntimeInputError('Null markers must be bounded strings')} })
  mapValues(row.units, item => controlText(item, 64)); controlChoice(row.duplicate_keys, ['allow', 'reject', 'keep_first'])

  if (row.keys !== undefined) {texts(row.keys)}

  if (row.mapping != null) {mapValues(row.mapping, item => typeof item === 'number' ? controlInteger(item, 0, 255) : controlText(item))}

  if (row.column_types !== undefined) {mapValues(row.column_types, item => controlChoice(item, ['text', 'decimal', 'integer', 'currency', 'date', 'native']))}
  const decimal = row.decimal_separator ?? '.'
  controlChoice(decimal, ['.', ','])

  if (row.grouping_separator != null) {
    controlChoice(row.grouping_separator, ['.', ','])

    if (row.grouping_separator === decimal) {throw new RuntimeInputError('Decimal and grouping separators must differ')}
  }

  if (row.sheet !== undefined) {controlText(row.sheet)}
}

function dataRecipe(value: unknown, sourceIds: string[]): void {
  const row = controlRecord(value, ['base'], ['joins', 'aggregate', 'charts', 'exports'])

  if (!sourceIds.includes(controlText(row.base))) {throw new RuntimeInputError('Recipe base must select an input source_id')}

  if (row.joins !== undefined) {controlList(row.joins, 0, 7).forEach(item => {
    const join = controlRecord(item, ['right', 'left_on', 'right_on', 'how', 'cardinality', 'null_keys'], ['right_prefix'])

    if (!sourceIds.includes(controlText(join.right))) {throw new RuntimeInputError('Join must select an input source_id')}
    const left = texts(join.left_on, 1), right = texts(join.right_on, 1)

    if (left.length !== right.length || new Set(left).size !== left.length || new Set(right).size !== right.length) {throw new RuntimeInputError('Join keys must be equally sized and unique')}
    controlChoice(join.how, ['inner', 'left']); controlChoice(join.cardinality, ['one_to_one', 'many_to_one', 'one_to_many', 'many_to_many']); controlChoice(join.null_keys, ['reject', 'never_match'])

    if (join.right_prefix !== undefined) {controlText(join.right_prefix)}
  })}

  if (row.aggregate != null) {
    const aggregate = controlRecord(row.aggregate, ['group_by', 'aggregations', 'nulls'], [])
    const groups = texts(aggregate.group_by)

    if (new Set(groups).size !== groups.length) {throw new RuntimeInputError('Grouping columns must be unique')}
    const operations = controlRecord(aggregate.aggregations)

    if (!Object.keys(operations).length) {throw new RuntimeInputError('At least one aggregation is required')}
    mapValues(operations, item => {
      const spec = controlRecord(item, ['operation', 'column'], [])
      controlChoice(spec.operation, ['sum', 'mean', 'min', 'max', 'count']); controlText(spec.column)
    })
    controlChoice(aggregate.nulls, ['reject', 'skip'])
  }

  if (row.charts !== undefined) {controlList(row.charts, 0, 4).forEach(item => {
    const chart = controlRecord(item, ['kind', 'category', 'value'], [])
    controlChoice(chart.kind, ['bar', 'line']); controlText(chart.category); controlText(chart.value)
  })}

  if (row.exports !== undefined) {
    const exports = controlList(row.exports, 1, 3).map(item => controlChoice(item, ['csv', 'xlsx', 'ipynb']))

    if (new Set(exports).size !== exports.length) {throw new RuntimeInputError('Export formats must be unique')}
  }
}

function creativeText(value: unknown, maximum: number): void {
  if (typeof value !== 'string' || !value.length || value.length > maximum || value.includes('\0')) {throw new RuntimeInputError('Creative text exceeds the supported bound')}
}

function creativeArguments(value: unknown): void {
  const row = controlRecord(value, ['brief', 'prompts'], ['assets', 'continuity'])
  creativeText(row.brief, 16000)
  controlList(row.prompts, 1, 32).forEach(item => creativeText(item, 16000))

  if (row.continuity !== undefined) {controlList(row.continuity).forEach(item => creativeText(item, 2000))}

  if (row.assets !== undefined) {
    const names = controlList(row.assets).map(item => {
      const asset = controlRecord(item, ['name', 'ref', 'rights', 'stage'], [])
      const name = controlText(asset.name, 120)

      if (!/^[A-Za-z0-9][A-Za-z0-9_.-]*$/u.test(name)) {throw new RuntimeInputError('Assets require safe basenames')}
      controlRef(asset.ref); controlChoice(asset.rights, ['owned', 'licensed', 'permission_recorded', 'unknown']); controlChoice(asset.stage, ['supplied'])

      return name
    })

    if (new Set(names).size !== names.length) {throw new RuntimeInputError('Asset names must be unique')}
  }
}

export function validateDomainJob(value: unknown): SelectedDomainJob {
  const row = controlRecord(value, ['job_id', 'project_id', 'adapter', 'arguments'], [])
  domainId(row.job_id); domainId(row.project_id)
  const adapter = controlChoice(row.adapter, ['data', 'creative'])

  const validators = {
    creative: creativeArguments,
    data: (value: unknown): void => {
      const args = controlRecord(value, ['inputs', 'recipe'], [])

      const ids = controlList(args.inputs, 1, 8).map(item => {
        const input = controlRecord(item, ['source_id', 'ref', 'options'], [])
        controlRef(input.ref); dataOptions(input.options)

        return controlText(input.source_id)
      })

      if (new Set(ids).size !== ids.length) {throw new RuntimeInputError('Dataset source IDs must be unique')}
      dataRecipe(args.recipe, ids)
    }
  }

  validators[adapter](row.arguments)

  // Full finite adapter validation above; preserve input field order and exact source pins.
  return row as unknown as SelectedDomainJob
}

function domainInput(value: unknown, publish: boolean): { input: OwnedDomainInput; job: SelectedDomainJob; approvals: DomainApproval[] } {
  const row = controlRecord(value, ['command_id', 'job_json', ...(publish ? ['approvals'] : [])], [])

  if (typeof row.job_json !== 'string') {throw new RuntimeInputError('job_json must be the exact prepared JSON string')}
  const job = validateDomainJob(controlJson(row.job_json, 65536))

  const approvals = publish ? controlList(row.approvals, 2, 17).map(item => {
    const approval = controlRecord(item, ['approval_id', 'approval_digest'], [])

    return { approval_id: controlId(approval.approval_id), approval_digest: controlDigest(approval.approval_digest) }
  }) : []

  if (new Set(approvals.map(approval => approval.approval_id)).size !== approvals.length) {throw new RuntimeInputError('Every output requires a distinct exact approval')}

  return { input: { command_id: controlId(row.command_id), job_json: row.job_json }, job, approvals }
}

export async function prepareDomain(input: OwnedDomainInput, request: RuntimeRequest, sessionId: string): Promise<DomainPrepareResult> {
  const validated = domainInput(input, false)

  return request('runtime.domain.prepare', { ...validated.input, session_id: controlId(sessionId), schema_version: 1 })
}

export async function publishDomain(input: OwnedDomainInput & { approvals: DomainApproval[] }, request: RuntimeRequest, sessionId: string): Promise<DomainPublishResult> {
  const validated = domainInput(input, true)

  return request('runtime.domain.publish', { ...validated.input, approvals: validated.approvals, session_id: controlId(sessionId), schema_version: 1 })
}

function domainScope(job: SelectedDomainJob): string {
  if (job.adapter === 'creative') {return `Creative prompt-only package; generated assets: 0; awaiting production. ${job.arguments.assets?.length ?? 0} supplied reference assets; rights remain declarations requiring review.`}

  return `Data recipe: ${job.arguments.inputs.length} pinned datasets; ${job.arguments.recipe.joins?.length ?? 0} joins; exports ${(job.arguments.recipe.exports ?? ['xlsx', 'ipynb']).join(', ')}. Formula caches are preserved, not recalculated. Row lineage and assumptions are retained in the manifest.`
}

export function summarizeDomainPrepared(result: DomainPrepareResult, job: SelectedDomainJob): string {
  if (result.publication_atomic !== false) {throw new RuntimeInputError('Unexpected domain publication contract')}
  const proposals = controlList(result.proposals, 2, 17).map(artifactProposal)

  if (proposals.some(proposal => proposal.project_id !== job.project_id) || proposals.at(-1)?.mime !== 'application/json') {throw new RuntimeInputError('Prepared bundle has a different project or missing manifest')}

  if (proposals.some((proposal, index) => proposal.request_id !== `domain:${job.job_id}:${index === proposals.length - 1 ? 'manifest' : index}`)) { throw new RuntimeInputError('Prepared output order differs from the exact selected job') }

  return [`Prepared ${job.adapter} package; awaiting approval, nothing published`, domainScope(job),
    ...proposals.map((proposal, index) => `${index === proposals.length - 1 ? 'Manifest (last)' : `Output ${index + 1}`}: ${artifactReference(proposal)}`),
    `Exact ordered approvals: ${JSON.stringify(proposals.map(({ approval_id, approval_digest }) => ({ approval_id, approval_digest })))}`,
    'Publish with the identical command_id and job_json. Every output has its own approval; publication is not atomic.'].join('\n')
}

export function summarizeDomainPublished(result: DomainPublishResult, job?: SelectedDomainJob): string {
  if (result.state !== 'published' || result.publication_atomic !== false || result.external_production !== 'not_performed') {throw new RuntimeInputError('Domain publication is not confirmed')}
  const outputs = controlList(result.outputs, 1, 16).map(artifactPublication), manifest = artifactPublication(result.manifest)

  if ([...outputs, manifest].some(output => output.project_id !== result.project_id) || (job && result.project_id !== job.project_id)) {throw new RuntimeInputError('Published bundle project differs')}

  return [`Published ${outputs.length} output(s); manifest committed last: ${artifactReference(manifest)}`,
    ...outputs.map(output => `Output: ${artifactReference(output)}; ${output.disposition}, validation ${output.validation_status}`),
    ...(job ? [domainScope(job)] : []), 'Publication was not atomic. External production was not performed. Delivery is not confirmed.'].join('\n')
}

/** Fetch only the selected authorized immutable manifest and verify every chunk plus its whole-file digest. */
export async function readDomainManifest(projectId: string, ref: BriefManifestRef, request: RuntimeRequest, sessionId: string): Promise<Record<string, unknown>> {
  const pin = controlRef(ref), project_id = controlId(projectId), session_id = controlId(sessionId)
  let offset = 0, size: number | null = null
  const chunks: Uint8Array[] = []

  while (true) {
    const page = await request('runtime.artifact.get', { session_id, schema_version: 1, project_id, artifact_id: pin.artifact_id, version: pin.version, offset, limit: 65536 })
    controlInteger(page.size, 1, 4 * 1024 * 1024); controlInteger(page.next_offset, offset + 1, 4 * 1024 * 1024)

    if (page.project_id !== project_id || page.artifact_id !== pin.artifact_id || page.version !== pin.version || page.sha256 !== pin.sha256 || page.mime !== 'application/json' || page.offset !== offset || page.size > 4 * 1024 * 1024 || page.size < 1 || (size !== null && page.size !== size)) {throw new RuntimeInputError('Manifest bytes do not match the selected immutable reference')}
    size = page.size
    let chunk: Uint8Array

    try { chunk = Uint8Array.from(atob(page.data_base64), char => char.charCodeAt(0)) } catch { throw new RuntimeInputError('Manifest chunk encoding is invalid') }

    if (!chunk.length || chunk.length > 65536 || page.next_offset !== offset + chunk.length || page.next_offset > size || page.eof !== (page.next_offset === size)) {throw new RuntimeInputError('Manifest is truncated or has invalid chunk boundaries')}
    chunks.push(chunk); offset = page.next_offset

    if (page.eof) {break}
  }

  const bytes = new Uint8Array(offset)
  let position = 0

  for (const chunk of chunks) { bytes.set(chunk, position); position += chunk.length }
  const digest = [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))].map(byte => byte.toString(16).padStart(2, '0')).join('')

  if (digest !== pin.sha256) {throw new RuntimeInputError('Complete manifest digest differs; no content was rendered')}
  let decoded: string

  try { decoded = new TextDecoder('utf-8', { fatal: true }).decode(bytes) } catch { throw new RuntimeInputError('Manifest is not valid UTF-8') }
  const manifest = controlRecord(controlJson(decoded, 4 * 1024 * 1024, false), ['schema_version', 'adapter', 'project_id', 'domain_metadata'])

  if (manifest.schema_version !== 1 || manifest.project_id !== project_id) {throw new RuntimeInputError('Unsupported manifest schema or scope')}

  return manifest
}

export function summarizeDomainManifest(manifest: Record<string, unknown>, selectedRow?: number): string {
  const adapter = controlChoice(manifest.adapter, ['data', 'creative']), metadata = controlRecord(manifest.domain_metadata)

  if (adapter === 'creative') {
    const validator = controlRecord(metadata.validator_manifest)

    if (metadata.stage !== 'prompt_only' || metadata.production_status !== 'awaiting_production' || validator.generated_assets !== 0) {throw new RuntimeInputError('Creative production state is unsupported or inconsistent')}

    return `Creative manifest: prompt-only; awaiting production; generated assets: 0. Supplied assets: ${controlList(metadata.assets).length}. Rights are declarations only; reference media validation does not verify ownership.`
  }

  const profile = controlRecord(metadata.profile), recipe = controlRecord(metadata.recipe)

  const lines = [`Data manifest: ${controlInteger(profile.rows)} result rows; formula status ${controlChoice(metadata.formula_status, ['preserved_not_recalculated'])}`,
    ...controlList(profile.columns, 1, 256).map(item => { const column = controlRecord(item);

 return `${controlText(column.name)}: ${controlInteger(column.null_count)} nulls, ${controlInteger(column.formula_count)} formulas; unit ${column.unit == null ? 'unspecified' : controlText(column.unit, 64)}` }),
    `Saved recipe: base ${controlText(recipe.base)}; ${recipe.joins === undefined ? 0 : controlList(recipe.joins, 0, 7).length} joins; aggregation ${recipe.aggregate ? 'present' : 'none'}; ${recipe.charts === undefined ? 0 : controlList(recipe.charts, 0, 4).length} chart(s)`]

  if (selectedRow !== undefined) {
    const lineage = controlList(metadata.lineage, 0, 100000)
    const index = controlInteger(selectedRow, 0, lineage.length - 1)
    const references = controlList(lineage[index], 1, 100000)
    lines.push(`Result row ${index} derives from ${references.length} source row(s)`)

    // Show a bounded, explicitly truncated projection without dumping source cells.
    for (const item of references.slice(0, 50)) {
      const source = controlRecord(item)
      lines.push(`${controlId(source.source_id)}: source row ${controlInteger(source.row, 1)}${source.sheet ? `, sheet ${controlText(source.sheet)}` : ''}; SHA-256 ${controlDigest(source.sha256)}`)
    }

    if (references.length > 50) {lines.push('Showing first 50 references; the complete lineage remains in the manifest')}
  }

  return lines.join('\n')
}

export const DOMAIN_HELP = [
  '/runtime domain list', '/runtime domain inspect <command-id>',
  '/runtime domain prepare <JSON: command_id, job_json>', '/runtime domain publish <same JSON plus ordered approvals [{approval_id,approval_digest}]>',
  '/runtime domain lineage <JSON: project_id, manifest_ref {artifact_id,version,sha256}, row (zero-based result row)>',
  'job_json contains job_id, project_id, adapter and arguments. Selected adapters: data and creative.',
  'Data: exact inputs [{source_id,ref,options}], recipe {base,joins?,aggregate?,charts?,exports?}. Explicit encoding/delimiter/date_format/currency/null_values/units/duplicate_keys are required.',
  'Data supports CSV/XLSX inputs, inner/left joins, sum/mean/min/max/count, bar/line charts, CSV/XLSX/IPYNB exports. No formula recalculation.',
  'Creative: brief, prompts, optional supplied assets [{name,ref,rights,stage:"supplied"}] and continuity. Prompt-only; zero image/audio/video generation.',
  'Teaching, tutoring, demonstration, coding, meeting and decision adapters are not exposed by these selected controls. No implicit remote fallback.'
].join('\n')

export async function runDomainCommand(argument: string, request: RuntimeRequest, sessionId: string): Promise<string> {
  const match = /^(\S+)(?:\s+([\s\S]*))?$/u.exec(argument.trim()), action = match?.[1] ?? '--help', rest = match?.[2] ?? ''

  try {
    const session_id = controlId(sessionId)

    const actions: Record<string, () => Promise<string>> = {
      '--help': async () => DOMAIN_HELP, help: async () => DOMAIN_HELP,
      list: async () => 'Selected deterministic packages: data (CSV/XLSX recipes, exports and lineage); creative (prompt-only and supplied references, generated assets: 0). Other domains remain deferred in these controls.',
      prepare: async () => { const { input, job } = domainInput(controlJson(rest), false);

 return summarizeDomainPrepared(await prepareDomain(input, request, session_id), job) },
      publish: async () => { const { input, job, approvals } = domainInput(controlJson(rest), true);

 return summarizeDomainPublished(await publishDomain({ ...input, approvals }, request, session_id), job) },
      inspect: async () => {
        const status = await request('runtime.artifact.status', { session_id, schema_version: 1, command_id: controlId(rest) })
        const result = status.result

        if (!result || !('outputs' in result)) {return summarizeArtifactCommand(status)}
        const published = summarizeDomainPublished(result)

        try { return `${summarizeArtifactCommand(status)}\n${published}\n${summarizeDomainManifest(await readDomainManifest(result.project_id, { artifact_id: result.manifest.artifact_id, version: result.manifest.version, sha256: result.manifest.sha256 }, request, session_id))}` }
        catch (error) { return `${summarizeArtifactCommand(status)}\n${published}\nManifest inspection unavailable: ${controlFailure(error, false)}` }
      },
      lineage: async () => {
        const row = controlRecord(controlJson(rest), ['project_id', 'manifest_ref', 'row'], [])
        const manifest = await readDomainManifest(controlId(row.project_id), controlRef(row.manifest_ref), request, session_id)

        if (manifest.adapter !== 'data') {throw new RuntimeInputError('Row lineage is available only for a data package')}

        return summarizeDomainManifest(manifest, controlInteger(row.row))
      }
    }

    return await ((Object.hasOwn(actions, action) ? actions[action] : undefined) ?? (async () => `Unsupported domain action.\n${DOMAIN_HELP}`))()
  } catch (error) { return controlFailure(error, ['prepare', 'publish'].includes(action)) }
}
