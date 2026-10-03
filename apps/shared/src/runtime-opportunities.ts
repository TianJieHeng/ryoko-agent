import type { OpportunityCandidate, OpportunityDiscoverResult, OpportunityHistoryResult, OpportunityListResult } from './gateway-contract.generated.js'
import type { RuntimeRequest } from './runtime-control.js'
import { controlId } from './runtime-research.js'

export interface OpportunityState {
  busy: boolean
  candidates: OpportunityCandidate[]
  history: OpportunityHistoryResult | null
  scan: OpportunityDiscoverResult | OpportunityListResult | null
  error: 'unknown' | 'unavailable' | 'stale' | null
  pending: { requestId: string; candidateId: string | null; disposition: OpportunityCandidate['disposition'] | null } | null
}

/** A scoped projection only; discovery and every choice remain explicit backend operations. */
export class OpportunityReviewSession {
  private readonly request: RuntimeRequest
  private readonly sessionId: string
  private readonly projectIds: string[]
  private readonly newId: () => string
  private epoch = 0
  private state: OpportunityState = { busy: false, candidates: [], history: null, scan: null, error: null, pending: null }
  private listeners = new Set<() => void>()
  constructor(request: RuntimeRequest, sessionId: string, projectIds: string[], newId: () => string = () => crypto.randomUUID()) {
    this.request = request; this.sessionId = controlId(sessionId); this.newId = newId

    if (!projectIds.length || projectIds.length > 8 || new Set(projectIds).size !== projectIds.length) { throw new Error('Select one to eight distinct projects') }
    this.projectIds = projectIds.map(controlId)
  }
  getState = () => this.state
  subscribe = (listener: () => void) => { this.listeners.add(listener);

 return () => { this.listeners.delete(listener) } }
  close() { this.epoch++; this.state = { ...this.state, busy: false } }
  private patch(update: Partial<OpportunityState>) { this.state = { ...this.state, ...update }; this.listeners.forEach(listener => listener()) }
  private base() { return { session_id: this.sessionId, schema_version: 1 as const } }
  async load(discover = false) {
    if (this.state.busy || discover && this.state.pending) { return }
    const epoch = this.epoch
    const requestId = discover ? this.newId() : null
    this.patch({ busy: true, error: null, ...(requestId ? { pending: { requestId, candidateId: null, disposition: null } } : {}) })

    try {
      const result = discover
        ? await this.request('runtime.opportunity.discover', { ...this.base(), project_ids: this.projectIds, request_id: requestId!, limit: 20, scan_limit_per_source: 20 })
        : await this.request('runtime.opportunity.list', { ...this.base(), project_ids: this.projectIds, limit: 20, dispositions: ['proposed', 'saved', 'dismissed', 'accepted'] })

      if (epoch !== this.epoch) { return }

      if (result.complete !== false || result.candidates.length > 20 || result.project_ids.length !== this.projectIds.length || result.project_ids.some(id => !this.projectIds.includes(id)) || result.candidates.some(candidate => !this.projectIds.includes(candidate.project_id) || candidate.execution_authorized !== false || candidate.confidence.level !== 'deterministic_rule_match')) { throw new Error('Unexpected opportunity authority or scope') }
      this.patch({ candidates: result.candidates, scan: result, ...(discover ? { pending: null } : {}) })
    } catch { if (epoch === this.epoch) { this.patch({ error: discover ? 'unknown' : 'unavailable' }) } }
    finally { if (epoch === this.epoch) { this.patch({ busy: false }) } }
  }
  async choose(candidate: OpportunityCandidate, disposition: 'saved' | 'dismissed' | 'accepted') {
    if (this.state.busy || this.state.pending || !this.state.candidates.includes(candidate)) { return }

    if (disposition !== 'dismissed' && !candidate.evidence_current) { this.patch({ error: 'stale' });

 return }

    const epoch = this.epoch, requestId = this.newId()
    this.patch({ busy: true, error: null, pending: { requestId, candidateId: candidate.candidate_id, disposition } })

    try {
      const result = await this.request('runtime.opportunity.disposition', { ...this.base(), project_id: candidate.project_id, candidate_id: candidate.candidate_id, request_id: requestId, expected_revision: candidate.revision, expected_evidence_digest: candidate.evidence_digest, disposition })

      if (epoch !== this.epoch) { return }

      if (result.tasks_created !== false || result.execution_authorized !== false || result.candidate.candidate_id !== candidate.candidate_id || result.candidate.project_id !== candidate.project_id || result.candidate.execution_authorized !== false || result.candidate.disposition !== disposition || result.candidate.revision !== candidate.revision + 1) { throw new Error('Unexpected opportunity effect') }
      this.patch({ candidates: this.state.candidates.map(item => item === candidate ? result.candidate : item), pending: null })
    } catch { if (epoch === this.epoch) { this.patch({ error: 'unknown' }) } }
    finally { if (epoch === this.epoch) { this.patch({ busy: false }) } }
  }
  async inspect(candidate: OpportunityCandidate) {
    if (this.state.busy || !this.state.candidates.includes(candidate)) { return }
    const epoch = this.epoch; this.patch({ busy: true, error: null })

    try {
      const history = await this.request('runtime.opportunity.history', { ...this.base(), project_id: candidate.project_id, candidate_id: candidate.candidate_id, limit: 20 })

      if (epoch !== this.epoch) { return }

      if (history.complete !== false || history.history.some(item => item.candidate.project_id !== candidate.project_id || item.candidate.candidate_id !== candidate.candidate_id)) { throw new Error('Foreign history') }
      this.patch({ history })
    } catch { if (epoch === this.epoch) { this.patch({ error: 'unavailable' }) } }
    finally { if (epoch === this.epoch) { this.patch({ busy: false }) } }
  }
}
