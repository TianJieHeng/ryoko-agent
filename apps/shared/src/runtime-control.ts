import type {
  CommandReceipt, MissionSnapshot, RpcMethods, RuntimeCapabilities,
  RuntimeCommandParams, RuntimeEventEnvelope
} from './gateway-contract.generated.js'

/** Presentation state only. The gateway owns identity, work, grants and effects. */
export interface RuntimeControlState {
  scope: string
  sessionId: string | null
  transport: 'offline' | 'loading' | 'ready' | 'stale' | 'unsupported'
  capabilities: RuntimeCapabilities | null
  snapshot: MissionSnapshot | null
  events: RuntimeEventEnvelope[]
  pending: RuntimeCommandParams | null
  receipt: CommandReceipt | null
  error: string | null
}

export interface RuntimeRequest {
  <M extends keyof RpcMethods>(method: M, params: RpcMethods[M]['params']): Promise<RpcMethods[M]['result']>
}

export function runtimeWorkLabel(snapshot: MissionSnapshot | null): string {
  if (!snapshot) {return 'Work status unavailable'}

  const labels: Record<MissionSnapshot['state']['status'], string> = {
    idle: 'Ready', accepted: 'Accepted; execution pending', claimed: 'Working',
    completed: 'Execution completed; check artifacts and delivery', failed: 'Failed',
    blocked: 'Blocked', cancelled: 'Cancelled; inspect unresolved effects'
  }

  return labels[snapshot.state.status]
}

export function runtimeOperationBlock(state: RuntimeControlState, operation: RuntimeCommandParams['operation']): string | null {
  if (state.transport !== 'ready') {return 'Refresh the current connection before acting'}

  if (state.pending) {return 'Resolve the pending command before submitting another'}
  const capability = state.capabilities?.operations.find(item => item.operation === operation)

  if (!capability?.accepts_commands || !capability.executes) {return capability?.reason || 'This operation is unavailable on the connected runtime'}

  return null
}

/** One instance per rendered context; selection invalidates every outstanding read. */
export class RuntimeControl {
  private generation = 0
  private readGeneration = 0
  private listeners = new Set<() => void>()
  private state: RuntimeControlState = {
    scope: '', sessionId: null, transport: 'offline', capabilities: null,
    snapshot: null, events: [], pending: null, receipt: null, error: null
  }

  private request: RuntimeRequest
  private newId: () => string
  constructor(request: RuntimeRequest, newId: () => string = () => crypto.randomUUID()) {
    this.request = request
    this.newId = newId
  }

  getState = (): RuntimeControlState => this.state
  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener)

    return () => { this.listeners.delete(listener) }
  }

  private set(patch: Partial<RuntimeControlState>): void {
    this.state = { ...this.state, ...patch }
    this.listeners.forEach(listener => listener())
  }

  select(scope: string, sessionId: string | null): void {
    this.generation++
    this.readGeneration++
    this.set({ scope, sessionId, transport: 'offline', capabilities: null, snapshot: null,
      events: [], pending: null, receipt: null, error: null })
  }

  disconnected(): void {
    this.generation++
    this.set({ transport: this.state.snapshot ? 'stale' : 'offline', error: 'Connection lost. Accepted work may still be running.' })
  }

  async refresh(): Promise<void> {
    const session_id = this.state.sessionId

    if (!session_id) {return}
    const generation = this.generation
    const read = ++this.readGeneration
    const current = () => generation === this.generation && read === this.readGeneration
    this.set({ transport: this.state.snapshot ? 'stale' : 'loading', error: null })

    try {
      const capabilities = await this.request('runtime.capabilities', { session_id })

      if (!current()) {return}

      if (!capabilities.schema_versions.includes(1)) {
        this.set({ capabilities, transport: 'unsupported', error: 'Runtime contract version 1 is unavailable; update the runtime or use its existing controls.' })

        return
      }

      const snapshot = await this.request('runtime.snapshot', { session_id, schema_version: 1 })

      if (!current()) {return}
      // Snapshot session_id is the durable lineage ID, not the live transport ID.
      this.set({ capabilities, snapshot, transport: 'ready', error: null })
    } catch (error) {
      if (current()) {this.set({ transport: this.state.snapshot ? 'stale' : 'unsupported', error: runtimeError(error) })}
    }
  }

  async replay(): Promise<void> {
    const { sessionId, snapshot } = this.state

    if (!sessionId || !snapshot) {return this.refresh()}
    const generation = this.generation
    const read = ++this.readGeneration

    try {
      const page = await this.request('runtime.events.since', {
        session_id: sessionId, schema_version: 1, cursor: snapshot.last_cursor, limit: 100
      })

      if (generation !== this.generation || read !== this.readGeneration) {return}
      const byId = new Map(this.state.events.map(event => [event.event_id, event]))
      page.events.forEach(event => byId.set(event.event_id, event))
      this.set({ events: [...byId.values()].sort((a, b) => a.seq - b.seq).slice(-200) })
      // Never synthesize completion from an event or skip a cursor gap. A fresh
      // authoritative snapshot covers all changes, including truncated replay.
      await this.refresh()
    } catch (error) {
      if (generation === this.generation && read === this.readGeneration) {this.set({ transport: 'stale', error: runtimeError(error) })}
    }
  }

  async command(operation: RuntimeCommandParams['operation'], payload: RuntimeCommandParams['payload']): Promise<void> {
    const blocked = runtimeOperationBlock(this.state, operation)

    if (blocked) {throw new Error(blocked)}
    const command_id = this.newId()

    const pending: RuntimeCommandParams = {
      session_id: this.state.sessionId!, schema_version: 1,
      command_id, idempotency_key: command_id,
      expected_revision: this.state.snapshot?.revision ?? null, operation, payload
    }

    this.set({ pending, receipt: null, error: null })
    await this.sendPending()
  }

  /** Explicit retry sends the identical command, never a new operation ID. */
  async retry(): Promise<void> {
    if (!this.state.pending || this.state.transport !== 'ready') {return}
    await this.sendPending()
  }

  private sending = new Set<RuntimeCommandParams>()
  private async sendPending(): Promise<void> {
    if (!this.state.pending || this.sending.has(this.state.pending)) {return}
    const pending = this.state.pending
    const generation = this.generation
    this.sending.add(pending)

    try {
      const receipt = await this.request('runtime.command', pending)

      if (generation !== this.generation) {return}
      this.set({ receipt, pending: null, error: receipt.conflict?.message ?? null })
      await this.refresh()
    } catch (error) {
      if (generation === this.generation) {this.set({ error: `${runtimeError(error)} Command outcome is unconfirmed. Refresh, then retry the same command if needed.` })}
    } finally {
      this.sending.delete(pending)
    }
  }
}

export function runtimeError(error: unknown): string {
  // Gateway errors already redact private inputs. Do not stringify arbitrary objects.
  return error instanceof Error ? error.message : 'Runtime request failed; refresh or inspect the connection'
}
