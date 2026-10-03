import { RuntimeControl, type RuntimeRequest } from '@hermes/shared/runtime-control'
import { inspectRuntimeIdentity, runtimeIdentityLines } from '@hermes/shared/runtime-identity'

import type { GatewayClient } from '../../../gatewayClient.js'
import { runArtifactDownload } from '../../runtime/artifact-download.js'
import { runRuntimeFeature } from '../../runtime/feature-commands.js'
import { runtimeSummary } from '../../runtime/summary.js'
import type { SlashCommand } from '../types.js'

const controllers = new WeakMap<GatewayClient, RuntimeControl>()

function controller(gateway: GatewayClient, sid: string): RuntimeControl {
  let control = controllers.get(gateway)

  if (!control) {
    const request: RuntimeRequest = (method, params) => gateway.request(method, { ...params })
    control = new RuntimeControl(request)
    const ownedControl = control
    gateway.on('exit', () => ownedControl.disconnected())
    gateway.on('event', (event: { type: string }) => {
      if (event.type === 'gateway.reconnecting') {ownedControl.disconnected()}
    })
    controllers.set(gateway, control)
  }

  if (control.getState().sessionId !== sid) {control.select('owned-transport', sid)}

  return control
}

export const runtimeCommands: SlashCommand[] = [{
  name: 'runtime',
  help: 'inspect durable work, connection health and receipt-backed controls',
  usage: '/runtime [status|identity|project|memory|capture|artifact|template|mission|approval|delivery|research|workflow|schedule|domain|commitment|correspondence|execution|specialist|overview|decision|refresh|events|cancel <reason>|steer <text>|retry]',
  run: (arg, ctx) => {
    if (!ctx.sid) {
      ctx.transcript.sys('Open an existing conversation before inspecting its runtime')

      return
    }

    if (arg.trim() === 'identity') {
      const request: RuntimeRequest = (method, params) => ctx.gateway.gw.request(method, { ...params })
      void inspectRuntimeIdentity(request, ctx.sid).then(view => {
        if (!ctx.stale()) {ctx.transcript.sys(runtimeIdentityLines(view).join('\n'))}
      }).catch(ctx.guardedErr)

      return
    }

    const control = controller(ctx.gateway.gw, ctx.sid)
    const parts = arg.trim().match(/^(\S+)(?:\s+([\s\S]*))?$/)
    const action = parts?.[1] ?? 'status'
    const text = parts?.[2] ?? ''

    if (action === 'artifact' && runArtifactDownload(text, ctx)) {return}

    if (runRuntimeFeature(action, text, ctx)) {return}

    const actions: Record<string, () => Promise<void>> = {
      status: () => control.refresh(), refresh: () => control.refresh(),
      events: () => control.replay(), retry: () => control.retry(),
      cancel: () => control.command('cancel', { reason: text }),
      steer: () => text ? control.command('steer', { text }) : Promise.reject(new Error('Use /runtime steer <correction>'))
    }

    const run = Object.hasOwn(actions, action) ? actions[action] : undefined

    if (!run) {
      ctx.transcript.sys(runtimeCommands[0].usage!)

      return
    }

    void (async () => {
      if (['cancel', 'steer'].includes(action) && !control.getState().capabilities) {await control.refresh()}

      if (ctx.stale()) {return}
      await run()

      if (!ctx.stale()) {ctx.transcript.sys(runtimeSummary(control.getState()))}
    })().catch(ctx.guardedErr)
  }
}]
