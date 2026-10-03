import type { RuntimeRequest } from './runtime-control.js'

export interface RuntimeFeature {
  label: string
  help: string
  run: (argument: string, request: RuntimeRequest, sessionId: string) => Promise<string>
}
export interface RuntimeFeatureState {
  busy: boolean
  output: string
  error: string | null
}

/** Ephemeral inspector state. Never auto-retries a possibly accepted mutation. */
export class RuntimeFeatureSession {
  private generation = 0
  private listeners = new Set<() => void>()
  private state: RuntimeFeatureState = { busy: false, output: '', error: null }
  readonly request: RuntimeRequest
  readonly sessionId: string
  constructor(request: RuntimeRequest, sessionId: string) {
    this.request = request
    this.sessionId = sessionId
  }
  getState = (): RuntimeFeatureState => this.state
  subscribe = (listener: () => void) => { this.listeners.add(listener);

 return () => { this.listeners.delete(listener) } }
  private set(patch: Partial<RuntimeFeatureState>) { this.state = { ...this.state, ...patch }; this.listeners.forEach(listener => listener()) }
  close(): void { this.generation++; this.set({ busy: false, output: '', error: null }) }
  async run(feature: RuntimeFeature, argument: string): Promise<void> {
    if (this.state.busy) {return}
    const generation = this.generation
    this.set({ busy: true, error: null })

    try {
      const output = await feature.run(argument, this.request, this.sessionId)

      if (typeof output !== 'string') {throw new Error('Invalid runtime display response')}

      if (generation === this.generation) {this.set({ busy: false, output })}
    } catch (error) {
      if (generation === this.generation) {this.set({ busy: false, error: `${error instanceof Error ? error.message : 'Runtime request failed'}. No automatic retry was sent. Inspect current state before retrying a change.` })}
    }
  }
}
