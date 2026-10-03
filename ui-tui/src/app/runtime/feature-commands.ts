import type { RuntimeRequest } from '@hermes/shared/runtime-control'
import { RuntimeFeatureSession } from '@hermes/shared/runtime-feature-session'
import { runtimeFeatures } from '@hermes/shared/runtime-features'

import type { GatewayClient } from '../../gatewayClient.js'
import type { SlashRunCtx } from '../slash/types.js'

const clients = new WeakMap<GatewayClient, { sessionId: string; controller: RuntimeFeatureSession }>()

export function runRuntimeFeature(name: string, argument: string, ctx: SlashRunCtx): boolean {
  const feature = Object.hasOwn(runtimeFeatures, name) ? runtimeFeatures[name] : undefined

  if (!feature || !ctx.sid) {return false}
  const gateway = ctx.gateway.gw
  let entry = clients.get(gateway)

  if (!entry || entry.sessionId !== ctx.sid) {
    entry?.controller.close()
    const request: RuntimeRequest = (method, params) => gateway.request(method, { ...params })
    entry = { sessionId: ctx.sid, controller: new RuntimeFeatureSession(request, ctx.sid) }
    clients.set(gateway, entry)
  }

  const controller = entry.controller

  if (controller.getState().busy) {
    ctx.transcript.sys('A runtime request is pending; inspect its result before submitting again')

    return true
  }

  void controller.run(feature, argument).then(() => {
    if (ctx.stale()) {return}
    const state = controller.getState()
    ctx.transcript.sys(state.error ?? state.output)
  })

  return true
}
