import type { RpcMethods } from '@hermes/shared/gateway-events'
import type { RuntimeRequest } from '@hermes/shared/runtime-control'

export interface RecoveryReference { id: string; kind: 'artifact' | 'memory' | 'input' | 'operator' | 'speech' | 'opportunity' | 'specialist'; method: string; unknown: boolean }
export interface RuntimeGatewayTransport { request<T>(method: string, params?: Record<string, unknown>): Promise<T>; connectionState?: string; onState?: (handler: (state: string) => void) => () => void }
const caches = new WeakMap<RuntimeGatewayTransport, Map<string, OwnedRuntimeScope>>()
const artifactTerminal = new Set(['completed', 'cancelled', 'failed', 'blocked'])
const memoryTerminal = new Set(['memory_acknowledged', 'context_supplied', 'stale_not_applied', 'expired', 'version_conflict'])

/** Bounded ephemeral recovery identifiers only. No persisted policy, result or approval authority. */
export class OwnedRuntimeScope {
  private connected = false
  private epoch = 0
  private speechCaptures = new Map<string, string>()
  private refs = new Map<string, RecoveryReference>()
  private snapshot: RecoveryReference[] = []
  private listeners = new Set<() => void>()
  request: RuntimeRequest
  private unsubscribe?: () => void
  constructor(private gateway: RuntimeGatewayTransport, readonly sessionId: string) {
    this.request = this.makeRequest()
    this.unsubscribe = gateway.onState?.(state => { this.setConnected(state === 'open'); this.emit() })
  }
  dispose() { this.unsubscribe?.(); this.unsubscribe = undefined; this.setConnected(false) }
  getGeneration = () => this.epoch
  get generation() { return this.epoch }
  subscribe = (listener: () => void) => { this.listeners.add(listener);

 return () => { this.listeners.delete(listener) } }
  getSnapshot = () => this.snapshot
  private emit() { this.snapshot = [...this.refs.values()]; this.listeners.forEach(listener => listener()) }
  setConnected(value: boolean) {
    if (value === this.connected) {return}
    this.connected = value; this.epoch++; this.request = this.makeRequest()
    // No subscriber update during render; new request identity invalidates all prepared UI authority.
  }
  private makeRequest(): RuntimeRequest {
    const generation = this.epoch

    return async (method, params) => {
      if (!this.connected || generation !== this.epoch || this.gateway.connectionState !== undefined && this.gateway.connectionState !== 'open') {throw new Error('Runtime transport changed; refresh the exact owned session before acting')}

      if (!('session_id' in params) || params.session_id !== this.sessionId) {throw new Error('Runtime request does not match its exact owned session')}
      const kind = method.startsWith('runtime.operations.') && 'authorization_digest' in params ? 'operator' : method.startsWith('runtime.voice.') && ('request_id' in params || 'capture_id' in params && this.speechCaptures.has(String(params.capture_id))) ? 'speech' : method.startsWith('runtime.opportunity.') && 'request_id' in params ? 'opportunity' : method.startsWith('runtime.specialist.') && 'command_id' in params ? 'specialist' : method === 'runtime.command' ? 'input' : 'control_id' in params ? 'memory' : 'input_id' in params ? 'input' : 'command_id' in params ? 'artifact' : null
      const id = kind === 'speech' && 'capture_id' in params ? this.speechCaptures.get(String(params.capture_id))! : kind === 'operator' && 'authorization_digest' in params ? String(params.authorization_digest) : (kind === 'speech' || kind === 'opportunity') && 'request_id' in params ? String(params.request_id) : 'control_id' in params ? String(params.control_id) : 'input_id' in params ? String(params.input_id) : 'command_id' in params ? String(params.command_id) : null
      const lookup = method.endsWith('.status') || method.endsWith('.get') || method === 'runtime.sources.preview'
      const key = kind && id ? `${kind}:${id}` : null

      if (key && !lookup && !this.refs.has(key)) {
        if (this.refs.size >= 100) {throw new Error('Inspect retained recovery controls before starting more operations in this session')}
        this.refs.set(key, { id: id!, kind: kind!, method, unknown: true }); this.emit()
      }

      if (kind === 'speech' && key && this.refs.has(key)) { this.refs.set(key, { ...this.refs.get(key)!, unknown: true }); this.emit() }

      try {
        const result = await this.gateway.request<RpcMethods[typeof method]['result']>(method, { ...params })

        let media: Record<string, unknown> | null = null

        if (method.startsWith('runtime.voice.') && result && typeof result === 'object' && 'response_json' in result) {
          try { media = JSON.parse(String(result.response_json)) as Record<string, unknown> } catch { /* Consumer rejects malformed payload. */ }
        }

        if (kind === 'speech') {
          const expected = method === 'runtime.voice.admit' ? media?.status === 'admitted' || media?.status === 'not_required'
            : method === 'runtime.voice.speak' ? media?.state === 'ready'
              : method === 'runtime.voice.capture.start' ? media?.state === 'recording' && typeof media.capture_id === 'string'
                : method === 'runtime.voice.capture.feed' ? media?.state === ('final' in params && params.final === true ? 'transcribed' : 'recording') : false

          if (!expected) { throw new Error('Speech response did not acknowledge this operation; inspect original identity') }
        }

        if (method === 'runtime.voice.capture.start' && key && media?.state === 'recording' && typeof media.capture_id === 'string') { this.speechCaptures.set(media.capture_id, id!) }

        if (method === 'runtime.voice.capture.cancel' && media?.state === 'discarded') {
          for (const [captureId, requestId] of this.speechCaptures) { if (this.refs.get(`speech:${requestId}`)?.unknown === false) { this.refs.delete(`speech:${requestId}`); this.speechCaptures.delete(captureId) } }
          this.emit()
        }

        if (key) {
          const status = result && typeof result === 'object' && 'status' in result ? String(result.status) : ''
          const prepared = method.endsWith('.prepare')
          const retain = kind === 'speech' && (method === 'runtime.voice.capture.start' || method === 'runtime.voice.capture.feed' && (!('final' in params) || params.final !== true)) || prepared || (kind === 'memory' ? !memoryTerminal.has(status) : kind === 'specialist' ? !artifactTerminal.has(status) : (method.endsWith('.cancel') || lookup) && !artifactTerminal.has(status))

          if (retain) {
            const old = this.refs.get(key)

            if (old) {this.refs.set(key, { ...old, unknown: false })}
          } else {this.refs.delete(key);

 if (kind === 'speech' && 'capture_id' in params) { this.speechCaptures.delete(String(params.capture_id)) }}

          this.emit()
        }

        return result
      } catch (error) {
        if (key && !lookup) { const old = this.refs.get(key);

 if (old) {this.refs.set(key, { ...old, unknown: true });} this.emit() }

        throw error
      }
    }
  }
}

export function ownedRuntimeScope(gateway: RuntimeGatewayTransport, route: string, sessionId: string): OwnedRuntimeScope {
  let scopes = caches.get(gateway)

  if (!scopes) { scopes = new Map(); caches.set(gateway, scopes) }
  const key = JSON.stringify([route, sessionId])
  const existing = scopes.get(key)

  if (existing) { scopes.delete(key); scopes.set(key, existing);

 return existing }

  if (scopes.size >= 32) {
    const idle = [...scopes].find(([, scope]) => scope.getSnapshot().length === 0)

    if (!idle) {throw new Error('Too many owned sessions have unresolved recovery references; inspect them before opening another runtime inspector')}
    idle[1].dispose(); scopes.delete(idle[0])
  }

  const created = new OwnedRuntimeScope(gateway, sessionId); scopes.set(key, created);

 return created
}
