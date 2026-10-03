import type { RuntimeRequest } from '@hermes/shared/runtime-control'
// @vitest-environment jsdom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { expect, it, vi } from 'vitest'

vi.mock('@/i18n', () => ({ useI18n: () => ({ locale: 'en', t: { common: { close: 'Close', cancel: 'Cancel', loading: 'Loading' }, notifications: { voice: {} } } }) }))
vi.mock('@/components/ui/button', () => ({ Button: (props: React.ComponentProps<'button'>) => <button {...props} /> }))
vi.mock('@/components/ui/input', () => ({ Input: (props: React.ComponentProps<'input'>) => <input {...props} /> }))
import { RuntimeFeaturePanel } from './feature-panel'

it('requires explicit submission, renders text safely and clears results on feature change', async () => {
  const request = vi.fn() as RuntimeRequest
  const node = window.document.createElement('div'); window.document.body.append(node)
  const root = createRoot(node)
  await act(async () => root.render(<RuntimeFeaturePanel connected request={request} sessionId="a" />))
  expect(request).not.toHaveBeenCalled()
  await act(async () => node.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
  expect(node.textContent).toContain('Resume is a bounded')
  expect(request).not.toHaveBeenCalled()
  await act(async () => (Array.from(node.querySelectorAll('button')).find(button => button.textContent === 'Scoped memory')!).click())
  expect(node.textContent).not.toContain('Resume is a bounded')
  await act(async () => root.unmount())
  node.remove()
})

it('mounts additive review controls on the existing overview and speech surfaces without automatic RPCs', async () => {
  const request = vi.fn() as RuntimeRequest
  const node = window.document.createElement('div'); window.document.body.append(node)
  const root = createRoot(node)
  await act(async () => root.render(<RuntimeFeaturePanel connected connectionGeneration={1} request={request} sessionId="owned" />))

  const clickLabel = async (label: string) => { await act(async () => (Array.from(node.querySelectorAll('button')).find(button => button.textContent === label)!).click()) }
  await clickLabel('Current work & repair')
  expect(node.textContent).toContain('Repair and privacy')
  expect(node.textContent).toContain('Project opportunities')
  await clickLabel('Specialists & channels')
  expect(node.textContent).toContain('Configured specialist')
  expect(request).not.toHaveBeenCalled()
  await act(async () => root.unmount()); node.remove()
})
