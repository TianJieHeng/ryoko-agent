// @vitest-environment jsdom
import { act, fireEvent, render, waitFor } from '@testing-library/react'
import { expect, it, vi } from 'vitest'

vi.mock('@nanostores/react', () => ({ useStore: () => 'open' }))
vi.mock('@/store/session', () => ({ $gatewayState: {} }))
vi.mock('@/i18n', () => ({ useI18n: () => ({ locale: 'en' }) }))
vi.mock('@/components/ui/button', () => ({ Button: (props: React.ComponentProps<'button'>) => <button {...props} /> }))
vi.mock('@/components/ui/dialog', () => ({ Dialog: ({ open, children }: { open: boolean; children: React.ReactNode }) => open ? <div>{children}</div> : null, DialogContent: ({ children }: { children: React.ReactNode }) => <div>{children}</div>, DialogHeader: ({ children }: { children: React.ReactNode }) => <div>{children}</div>, DialogTitle: ({ children }: { children: React.ReactNode }) => <h2>{children}</h2>, DialogDescription: ({ children }: { children: React.ReactNode }) => <p>{children}</p> }))
vi.mock('./mission-panel', () => ({ MissionPanel: ({ sessionId }: { sessionId: string }) => <div>Mission scope {sessionId}</div> }))
vi.mock('./delivery-panel', () => ({ DeliveryPanel: () => null }))
import { ConversationMissionControl } from './conversation-mission'

it('opens only on user action and closes the previous scope when the conversation changes', async () => {
  const gateway = { request: vi.fn(async () => ({ mission: null })), on: vi.fn(() => () => {}) } as unknown as React.ComponentProps<typeof ConversationMissionControl>['gateway']
  const view = render(<ConversationMissionControl gateway={gateway} sessionId="a" />)
  expect(view.queryByText('Mission scope a')).toBeNull()
  fireEvent.click(view.getByRole('button', { name: 'Mission' }))
  expect(view.getByText('Mission scope a')).toBeTruthy()
  view.rerender(<ConversationMissionControl gateway={gateway} sessionId="b" />)
  await waitFor(() => expect(view.queryByText('Mission scope a')).toBeNull())
  expect(view.queryByText('Mission scope b')).toBeNull()
  view.unmount()
})

it('ignores an old conversation summary that arrives after switching sessions', async () => {
  let resolveA!: (value: unknown) => void

  const request = vi.fn(async (_method: string, params: { session_id: string }) => params.session_id === 'a'
    ? new Promise(resolve => { resolveA = resolve })
    : { mission: { state: 'working', outcome: 'Current B work', next_step: 'B next step' } })

  const gateway = { request, on: vi.fn(() => () => {}) } as unknown as React.ComponentProps<typeof ConversationMissionControl>['gateway']
  const view = render(<ConversationMissionControl gateway={gateway} sessionId="a" />)
  await waitFor(() => expect(resolveA).toBeDefined())
  view.rerender(<ConversationMissionControl gateway={gateway} sessionId="b" />)
  await waitFor(() => expect(view.getByText('working: Current B work')).toBeTruthy())
  await act(async () => resolveA({ mission: { state: 'completed', outcome: 'Old A private work', next_step: 'Old A next step' } }))
  expect(view.queryByText(/Old A private/)).toBeNull()
  expect(view.getByText('B next step')).toBeTruthy()
  view.unmount()
})
