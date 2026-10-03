// @vitest-environment jsdom
import { act, StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/i18n', () => ({ useI18n: () => ({ locale: 'en', t: { common: { close: 'Close' } } }) }))
vi.mock('@/store/gateway', () => ({ $gateway: { get: () => null }, $activeGatewayRoute: {} }))
vi.mock('@/store/session', () => ({ $activeSessionId: {}, $gatewayState: {} }))
vi.mock('@/components/ui/button', () => ({ Button: (props: React.ComponentProps<'button'>) => <button {...props} /> }))
import { OwnedRuntimeInspector } from './inspector'

const roots: ReturnType<typeof createRoot>[] = []
afterEach(() => { roots.forEach(root => act(() => root.unmount())); roots.length = 0; window.document.body.innerHTML = '' })

describe('owned runtime inspector', () => {
  it('probes only on request and discards identity results after dismissal', async () => {
    const request = vi.fn(async () => { throw new Error('unavailable') })
    const gateway = { request } as unknown as React.ComponentProps<typeof OwnedRuntimeInspector>['gateway']
    const container = window.document.createElement('div'); window.document.body.append(container)
    const root = createRoot(container); roots.push(root)
    await act(async () => root.render(<OwnedRuntimeInspector connected gateway={gateway} sessionId="a" />))
    expect(request).not.toHaveBeenCalled()
    await act(async () => container.querySelector('button')!.click())
    expect(request).toHaveBeenCalledWith('runtime.memory.status', { session_id: 'a', schema_version: 1 })
    expect(container.textContent).toContain('Memory probe unavailable')
    expect(container.textContent).not.toContain('secret')
    await act(async () => root.render(<OwnedRuntimeInspector connected={false} gateway={gateway} sessionId="a" />))
    expect(container.querySelector('button')!.disabled).toBe(true)
    expect(container.textContent).toContain('Accepted work may still be running')
  })
  it('never paints a prior gateway probe after the active socket changes', async () => {
    let resolveMemory!: (value: unknown) => void

    const requestA = vi.fn(async (method: string) => {
      if (method === 'runtime.memory.status') {return new Promise(resolve => { resolveMemory = resolve })}
      throw new Error('unavailable')
    })

    const requestB = vi.fn(async () => { throw new Error('unavailable') })
    const gatewayA = { request: requestA } as unknown as React.ComponentProps<typeof OwnedRuntimeInspector>['gateway']
    const gatewayB = { request: requestB } as unknown as React.ComponentProps<typeof OwnedRuntimeInspector>['gateway']
    const container = window.document.createElement('div'); window.document.body.append(container)
    const root = createRoot(container); roots.push(root)
    await act(async () => root.render(<OwnedRuntimeInspector connected gateway={gatewayA} sessionId="a" />))
    await act(async () => container.querySelector('button')!.click())
    expect(resolveMemory).toBeDefined()
    await act(async () => root.render(<OwnedRuntimeInspector connected gateway={gatewayB} sessionId="b" />))
    await act(async () => resolveMemory({ health: { backend: 'personal_mcp', status: 'unconfigured', reason_code: 'private_a_status', supported_operations: [] }, capabilities: { backend: 'personal_mcp', recall: false, write: false, supersede: false, delete: false, export: false, session_ingest: false } }))
    expect(container.textContent).not.toContain('private_a_status')
    expect(container.textContent).not.toContain('Owned session: a')
  })

})

it('keeps the current request usable after compiled StrictMode cleanup/setup without restoring an old binding', async () => {
  const request = vi.fn(async () => { throw new Error('unavailable') })
  const gateway = { request } as unknown as React.ComponentProps<typeof OwnedRuntimeInspector>['gateway']
  const container = window.document.createElement('div'); window.document.body.append(container)
  const root = createRoot(container); roots.push(root)
  await act(async () => root.render(<StrictMode><OwnedRuntimeInspector connected gateway={gateway} sessionId="strict" /></StrictMode>))
  expect(request).not.toHaveBeenCalled()
  await act(async () => container.querySelector('button')!.click())
  expect(request).toHaveBeenCalledWith('runtime.memory.status', { session_id: 'strict', schema_version: 1 })
  expect(container.textContent).not.toContain('Runtime transport changed')
})
