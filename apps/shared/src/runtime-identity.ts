import type { MemoryStatusResult, RuntimeCapabilities } from './gateway-contract.generated.js'
import type { RuntimeRequest } from './runtime-control.js'

export interface RuntimeIdentityView {
  sessionId: string
  verifiedAt: string
  capabilities: RuntimeCapabilities | null
  memory: MemoryStatusResult | null
  errors: string[]
}

/** Probe real owned runtime operations. Stored configuration is never a green badge. */
export async function inspectRuntimeIdentity(request: RuntimeRequest, sessionId: string): Promise<RuntimeIdentityView> {
  const results = await Promise.allSettled([
    request('runtime.capabilities', { session_id: sessionId }),
    request('runtime.memory.status', { session_id: sessionId, schema_version: 1 })
  ])

  return {
    sessionId, verifiedAt: new Date().toISOString(),
    capabilities: results[0].status === 'fulfilled' ? results[0].value : null,
    memory: results[1].status === 'fulfilled' ? results[1].value : null,
    errors: results.flatMap((result, index) => result.status === 'rejected'
      ? [`${index === 0 ? 'Runtime identity/capability' : 'Memory'} probe unavailable; verify the active profile identity and service configuration`] : [])
  }
}

export function runtimeIdentityLines(view: RuntimeIdentityView): string[] {
  const lines = [`Owned session: ${view.sessionId}`, `Last checked: ${view.verifiedAt}`, ...view.errors]
  const capabilities = view.capabilities

  if (capabilities) {
    lines.push(`Identity binding: ${capabilities.strict_identity_required ? 'required and checked by runtime' : 'legacy; verify before using durable controls'}`)

    if (capabilities.provider) {
      const provider = capabilities.provider
      lines.push(`Backend adapter: ${provider.adapter}; mode: ${provider.api_mode}; execution owner: ${provider.execution_owner}`)
      lines.push(`Model support: ${provider.model_capabilities}; remote cancellation: ${provider.cancellation}`)
    }

    if (capabilities.tool_view) {
      lines.push(`Frozen tool policy: ${capabilities.tool_view.session_policy_version}`)
      lines.push(`Authorized tools: ${capabilities.tool_view.authorized_tool_ids.length}; selected tools: ${capabilities.tool_view.selected_tool_ids.length}`)
    }

    if (capabilities.admission) {lines.push(`Queue scope: ${capabilities.admission.scope}; active limit: ${capabilities.admission.max_active}; queued limit: ${capabilities.admission.max_queued}`)}
  }

  if (view.memory) {
    const { health, capabilities: memory } = view.memory
    lines.push(memory.backend === 'personal_mcp'
      ? 'Memory: primary personal MCP harness; never shared with specialists'
      : 'Memory: isolated built-in agent memory; shared project artifacts are separate')
    lines.push(`Memory health: ${health.status}${health.reason_code ? ` (${health.reason_code})` : ''}`)
    lines.push(`Acknowledged operations: ${health.supported_operations.join(', ') || 'none'}`)
    lines.push(`Recall ${memory.recall ? 'supported' : 'unavailable'}; correction ${memory.supersede ? 'supported' : 'unavailable'}; deletion ${memory.delete ? 'supported' : 'unavailable'}`)

    if (health.status !== 'ready') {lines.push('Repair the configured backend and retry this probe; no fallback to another agent’s memory')}
  }

  lines.push('Settings changes remain backend-owned; prompt-affecting changes may require the next session. This view does not reveal credentials or grant access.')

  return lines
}
