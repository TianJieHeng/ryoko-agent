import type { ChannelBindParams, ChannelSubmitParams, MediaResponse, ScreenAnnotateParams, ScreenCaptureParams, ScreenSubmitParams, VoiceSubmitParams } from './gateway-contract.generated.js'
import type { RuntimeRequest } from './runtime-control.js'

export const SPECIALIST_HELP = 'Specialists: list/team | manifest | steer <JSON: subagent_id,text> | interrupt <subagent-id> | media | voice-stop | voice-discard | voice-hangup | voice-submit <JSON> | screen-capture <JSON: png_base64,scope,window_ref> | screen-inspect <frame-id> | screen-annotate <JSON: frame_id,region,label?> | screen-submit <JSON> | channel-bind <local_jsonrpc|voice|screen> | channel-submit <JSON>\nTask submissions require binding_id,input_id,expected_revision and exact text confirmation for voice/screen. Channel submissions require operation and its exact payload. No implicit recording, continuous capture, external channel mapping or team memory sharing.'

type ObjectValue = Record<string, unknown>

function object(value: unknown, label = 'input'): ObjectValue {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {throw new Error(`Invalid ${label}: expected an object`)}

  return value as ObjectValue
}

function fields(value: unknown, allowed: string[]): ObjectValue {
  const result = object(value)

  if (Object.keys(result).some(key => !allowed.includes(key))) {throw new Error('Unsupported field; session, schema, identity, history and private memory cannot be supplied')}

  return result
}

function input(value: string, allowed: string[], max = 1400000): ObjectValue {
  if (value.length > max) {throw new Error('Input exceeds the bounded media contract')}

  return fields(JSON.parse(value), allowed)
}

function text(value: unknown, label: string, max = 256): string {
  if (typeof value !== 'string' || !value.trim() || value.length > max || Array.from(value).some(char => char.charCodeAt(0) < 32 && !'\n\t\r'.includes(char) || char.charCodeAt(0) === 127)) {throw new Error(`Invalid ${label}`)}

  return value
}

function identifier(value: unknown, label: string): string {
  const result = text(value, label)

  if (/\s/.test(result)) {throw new Error(`Invalid ${label}; one exact identifier required`)}

  return result
}

function integer(value: unknown, label: string, min = 0): number {
  if (!Number.isSafeInteger(value) || (value as number) < min) {throw new Error(`Invalid ${label}`)}

  return value as number
}

function sha(value: unknown): string {
  if (typeof value !== 'string' || !/^[a-f0-9]{64}$/.test(value)) {throw new Error('Invalid screen digest')}

  return value
}

function region(value: unknown): number[] {
  if (!Array.isArray(value) || value.length !== 4) {throw new Error('region must be [x,y,width,height]')}

  return value.map((part, index) => integer(part, 'region coordinate', index < 2 ? 0 : 1))
}

function response(value: MediaResponse): ObjectValue {
  if (typeof value.response_json !== 'string' || value.response_json.length > 2 * 1024 * 1024) {throw new Error('Invalid bounded media response')}

  return object(JSON.parse(value.response_json), 'media response')
}

function strings(value: unknown): string[] {
  if (!Array.isArray(value) || value.length > 64) {throw new Error('Invalid capability list')}

  return value.map(item => text(item, 'capability'))
}

function code(error: unknown): string {
  const data = error && typeof error === 'object' ? (error as { data?: unknown }).data : undefined
  const value = data && typeof data === 'object' ? (data as { code?: unknown }).code : undefined

  if (typeof value === 'string' && /^[a-z][a-z0-9_]{0,100}$/.test(value)) {return value}
  const message = error instanceof Error ? error.message : ''

  return /^(screen_|speech_|handoff_|channel_|project_|identity_|stale_owner|media_)[a-z_]*$/.test(message) ? message : 'request_unavailable'
}

async function invoke<T>(operation: () => Promise<T>, mutation = false): Promise<T> {
  try { return await operation() }
  catch (error) {
    throw new Error(`${mutation ? 'Control outcome unconfirmed' : 'Inspection unavailable'} (${code(error)}). Refresh the owned session${mutation ? '; inspect current state before any explicit retry' : ''}. Stale frames require a fresh explicit selected-window snapshot.`)
  }
}

function noArgs(value: string): void { if (value) {throw new Error('This control takes no arguments')} }

function safeSummary(value: string | null | undefined, max = 300): string { return value ? Array.from(value).map(char => char.charCodeAt(0) < 32 || char.charCodeAt(0) === 127 ? ' ' : char).join('').slice(0, max) : 'unreported' }

function frameLines(value: ObjectValue, expected?: string): string {
  const id = identifier(value.frame_id, 'frame ID')

  if (expected && id !== expected) {throw new Error('Frame does not match the requested snapshot')}

  if (value.scope !== 'selected_window') {throw new Error('Unsupported observation scope')}

  return [`Frame ${id}; selected window ${text(value.window_ref, 'window reference')}; ${integer(value.width, 'width', 1)}×${integer(value.height, 'height', 1)}`,
    `Digest ${sha(value.sha256)}; server freshness check passed at inspection`,
    'Client acquisition time is unverified; the backend exposes no capture timestamp. Re-observe after any layout change. No OCR, OS actions or continuous capture.'].join('\n')
}

function submission(value: ObjectValue): Pick<VoiceSubmitParams, 'binding_id' | 'input_id' | 'expected_revision' | 'text' | 'confirmed_text'> {
  const result = { binding_id: identifier(value.binding_id, 'binding_id'), input_id: identifier(value.input_id, 'input_id'), expected_revision: integer(value.expected_revision, 'expected_revision'), text: text(value.text, 'text', 65536), confirmed_text: text(value.confirmed_text, 'confirmed_text', 65536) }

  if (result.text !== result.confirmed_text) {throw new Error('Confirm the exact complete transcript/selected text, including decisive names and numbers, before task submission')}

  return result
}

function commandLines(value: ObjectValue): string {
  if (value.schema_version !== 1 || !['accepted', 'rejected', 'duplicate'].includes(String(value.status))) {throw new Error('Invalid durable command receipt; outcome unconfirmed')}
  const conflict = value.conflict == null ? null : object(value.conflict)

  return [`Command ${identifier(value.command_id, 'command ID')}: ${value.status}; durable revision ${integer(value.durable_revision, 'durable revision')}`,
    `Run ${value.run_id == null ? 'not assigned' : identifier(value.run_id, 'run ID')}${conflict ? `; reason ${text(conflict.code, 'conflict code')}` : ''}`,
    'Admission is not execution or delivery. The same logical input_id deduplicates across owned local surfaces; inspect mission/effect status before resubmitting.'].join('\n')
}

export interface RuntimeChannelBinding { bindingId: string, missionId: string, projectId: string, channel: ChannelBindParams['channel'], summary: string }

export async function bindRuntimeChannel(request: RuntimeRequest, sessionId: string, channel: ChannelBindParams['channel']): Promise<RuntimeChannelBinding> {
  const base = { session_id: sessionId, schema_version: 1 as const }
  const result = response(await invoke(() => request('runtime.channel.bind', { ...base, channel }), true))

  if (result.channel !== channel || result.verification !== 'owned_live_transport_and_stored_identity' || result.history_replayed !== false) {throw new Error('Invalid owned-channel binding receipt')}

  const summary = `Binding ${identifier(result.binding_id, 'binding ID')}; channel ${channel}; existing mission ${identifier(result.mission_id, 'mission ID')}; project ${identifier(result.project_id, 'project ID')}\nVerified by owned live transport and stored identity. History was not replayed; no new mission, external channel connection or remote-device transfer is implied.`

  return { bindingId: identifier(result.binding_id, 'binding ID'), missionId: identifier(result.mission_id, 'mission ID'), projectId: identifier(result.project_id, 'project ID'), channel, summary }
}

export async function runSpecialistCommand(argument: string, request: RuntimeRequest, sessionId: string): Promise<string> {
  const trimmed = argument.trim(), split = trimmed.search(/\s/)
  const action = split < 0 ? trimmed : trimmed.slice(0, split), rest = split < 0 ? '' : trimmed.slice(split + 1).trim()
  const base = { session_id: sessionId, schema_version: 1 as const }

  const roster = async () => {
    noArgs(rest)
    const result = await invoke(() => request('subagent.list', { session_id: sessionId }))

    return ['Owned conversation child snapshot (live/recent, not complete history):', ...(result.subagents ?? []).map(item =>
      `${safeSummary(item.subagent_id)}: ${item.status ?? 'unknown'}; parent ${safeSummary(item.parent_id)}; delegation ${safeSummary(item.delegation_id)}; tools started ${item.tool_count ?? 'unreported'}; accepting steer ${item.accepting_steer ?? 'unknown'}\n  Assignment: ${safeSummary(item.goal)}`),
    ...(result.delegations ?? []).map(item => `Delegation ${safeSummary(item.delegation_id)}: ${safeSummary(item.status)}; task ${item.task_index ?? 'unreported'}; assignment ${safeSummary(item.goal)}`),
    !(result.subagents?.length || result.delegations?.length) ? 'No child rows returned; missing rows do not prove completion.' : 'Visible lineage does not grant control. Steer/interruption still require exact live session, transport and generation authority.',
    'Named specialist manifests, team dependencies/budgets and synthesized results are not exposed by this RPC. This roster does not claim team completeness or measured value.',
    'Specialists retain their isolated built-in memory. The primary personal memory harness is never read, copied or offered as team context.'].join('\n')
  }

  const handlers: Record<string, () => Promise<string>> = {
    list: roster,
    team: roster,
    manifest: async () => {
      noArgs(rest)

      return 'Named SpecialistManifest inspection/selection is unavailable in the current gateway contract. Responsibility, methods version, permitted tools, output contract and isolated memory scope cannot be inferred from a child name. Use existing configured specialist delegation; list shows only supported child metadata.'
    },
    steer: async () => {
      const value = input(rest, ['subagent_id', 'text'], 70000)
      const child = identifier(value.subagent_id, 'subagent_id'), message = text(value.text, 'text', 65536)
      const result = await invoke(() => request('subagent.steer', { session_id: sessionId, subagent_id: child, text: message }), true)

      if (result.subagent_id !== child) {throw new Error('Child control receipt mismatch; outcome unconfirmed')}

      return result.status === 'queued' ? `Child ${child}: steer queued, not delivered. A final-boundary race can miss the steer; inspect the child snapshot/result before assuming it took effect.` : `Child ${child}: steer rejected. Refresh the exact owning session; a visible child may lack current control authority.`
    },
    interrupt: async () => {
      const child = identifier(rest, 'subagent_id')
      const result = await invoke(() => request('subagent.interrupt', { session_id: sessionId, subagent_id: child }), true)

      if (result.subagent_id !== child) {throw new Error('Child control receipt mismatch; outcome unconfirmed')}

      return result.found ? `Child ${child}: interrupt signal accepted. Refresh list for terminal state; committed effects are not undone.` : `Child ${child}: no interrupt signal acknowledged. It may be finished or outside exact live-session authority; refresh list without assuming cancellation.`
    },
    media: async () => {
      noArgs(rest)
      const value = response(await invoke(() => request('runtime.media.capabilities', base)))
      const voice = object(value.voice), screen = object(value.screen)

      const adapter = (data: unknown, kind: string): string => {
        if (data === null) {return `${kind}: unconfigured`}
        const item = object(data)

        return `${kind}: ${text(item.adapter_id, 'adapter ID')}@${integer(item.version, 'adapter version', 1)}; processing ${text(item.processing_location, 'processing location')}; streaming ${item.streaming === true ? 'supported' : 'unavailable'}`
      }

      return [`Voice push-to-talk: ${voice.push_to_talk === true ? 'supported by declared adapter' : 'unavailable'}; ${adapter(voice.stt, 'STT')}; ${adapter(voice.tts, 'TTS')}`,
        `Unsupported: ${strings(voice.unsupported).join(', ') || 'none declared'}; capture ${text(voice.capture, 'voice capture')}; remote processing ${voice.remote_processing === true ? 'declared' : 'disabled'}`,
        `Screen: ${text(screen.capture, 'screen capture')}; inspection ${text(screen.inspection, 'screen inspection')}`,
        `Freshness ${text(screen.freshness, 'freshness')}; maximum server receipt age ${integer(screen.max_age_seconds, 'frame age')} seconds; client acquisition time unverified`,
        `OCR ${screen.ocr === true ? 'supported' : 'unavailable'}; OS actions ${screen.os_actions === true ? 'supported' : 'unavailable'}; workflow ${text(screen.act_workflow, 'screen workflow')}`,
        `Channels: ${strings(value.channels).join(', ')}; external adapters ${text(value.external_channel_adapters, 'external adapters')}`,
        'This inspection never starts capture or speech. Stop speech, discard captured audio and cancel accepted work are distinct controls. Microphone streaming requires the existing media UI.'].join('\n')
    },
    'voice-stop': async () => {
      noArgs(rest)
      const value = response(await invoke(() => request('runtime.voice.stop', base), true))

      if (value.state !== 'stopped' || value.mission_cancelled !== false || value.streaming_stopped !== false) {throw new Error('Unexpected speech-stop receipt; inspect current state')}

      return 'Speech stopped. UI streaming and accepted mission work continue. This is not a call hangup or mission cancellation.'
    },
    'voice-discard': async () => {
      noArgs(rest)
      const value = response(await invoke(() => request('runtime.voice.capture.cancel', base), true))

      if (value.state !== 'discarded' || value.mission_cancelled !== false) {throw new Error('Unexpected capture-discard receipt; inspect current state')}

      return 'Captured audio discarded for this transport. Accepted mission work remains active; speech stop and call hangup are separate.'
    },
    'voice-hangup': async () => {
      noArgs(rest)

      return 'Call hangup is unavailable in the bounded media gateway. Use the current call UI to disconnect; voice-stop only stops speech and voice-discard only discards captured audio. Neither cancels accepted work.'
    },
    'voice-submit': async () => {
      const value = input(rest, ['binding_id', 'input_id', 'expected_revision', 'text', 'confirmed_text'], 150000)
      const params: VoiceSubmitParams = { ...base, ...submission(value) }

      return commandLines(response(await invoke(() => request('runtime.voice.submit', params), true)))
    },
    'screen-capture': async () => {
      const value = input(rest, ['png_base64', 'scope', 'window_ref'])

      if (value.scope !== 'selected_window') {throw new Error('Only an explicitly selected-window PNG is supported')}
      const png = text(value.png_base64, 'png_base64', 1398104)

      if (!/^[A-Za-z0-9+/]*={0,2}$/.test(png) || png.length % 4 !== 0 || !png.startsWith('iVBORw0KGgo')) {throw new Error('An exact bounded PNG base64 payload is required')}
      const params: ScreenCaptureParams = { ...base, scope: 'selected_window', window_ref: text(value.window_ref, 'window_ref'), png_base64: png }

      return frameLines(response(await invoke(() => request('runtime.screen.capture', params), true)))
    },
    'screen-inspect': async () => {
      const frame = identifier(rest, 'frame_id')

      return frameLines(response(await invoke(() => request('runtime.screen.inspect', { ...base, frame_id: frame }))), frame)
    },
    'screen-annotate': async () => {
      const value = input(rest, ['frame_id', 'region', 'label'], 4096)

      if (value.label !== undefined && (typeof value.label !== 'string' || value.label.length > 512)) {throw new Error('label must be at most 512 characters')}
      const params: ScreenAnnotateParams = { ...base, frame_id: identifier(value.frame_id, 'frame_id'), region: region(value.region), label: value.label as string | undefined }
      const result = response(await invoke(() => request('runtime.screen.annotate', params), true))

      if (result.frame_id !== params.frame_id || result.os_action_supported !== false || JSON.stringify(region(result.region)) !== JSON.stringify(params.region)) {throw new Error('Annotation receipt mismatch')}

      return `Frame ${params.frame_id}; digest ${sha(result.frame_sha256)}; region ${params.region.join(', ')}\nGuide: review selected text and confirm decisive names/numbers. Workflow ${text(result.workflow, 'workflow')}; no OS action performed. Re-observe after layout changes.`
    },
    'screen-submit': async () => {
      const value = input(rest, ['binding_id', 'input_id', 'expected_revision', 'text', 'confirmed_text', 'frame_id', 'region'], 150000)
      const params: ScreenSubmitParams = { ...base, ...submission(value), frame_id: identifier(value.frame_id, 'frame_id'), region: region(value.region) }

      return commandLines(response(await invoke(() => request('runtime.screen.submit', params), true)))
    },
    'channel-bind': async () => {
      if (!['local_jsonrpc', 'voice', 'screen'].includes(rest)) {throw new Error('Only local_jsonrpc, voice and screen have verified local adapters; no external handoff available')}
      const channel = rest as ChannelBindParams['channel']

      return (await bindRuntimeChannel(request, sessionId, channel)).summary
    },
    'channel-submit': async () => {
      const value = input(rest, ['binding_id', 'input_id', 'expected_revision', 'operation', 'payload'], 75000)

      if (!['submit', 'steer', 'cancel'].includes(String(value.operation))) {throw new Error('operation must be submit, steer or cancel')}
      const operation = value.operation as ChannelSubmitParams['operation']
      const payload = fields(value.payload, operation === 'cancel' ? ['reason'] : ['text'])

      const params: ChannelSubmitParams = { ...base, binding_id: identifier(value.binding_id, 'binding_id'), input_id: identifier(value.input_id, 'input_id'), expected_revision: integer(value.expected_revision, 'expected_revision'), operation,
        payload: operation === 'cancel' ? (payload.reason === undefined ? {} : { reason: text(payload.reason, 'reason', 4096) }) : { text: text(payload.text, 'text', 65536) } }

      const result = commandLines(response(await invoke(() => request('runtime.channel.submit', params), true)))

      return operation === 'cancel' ? `${result}\nThis explicitly requests accepted-work cancellation; it does not hang up a call, stop speech, undo confirmed effects or prove provider cancellation.` : result
    }
  }

  return Object.hasOwn(handlers, action) ? handlers[action]() : SPECIALIST_HELP
}
