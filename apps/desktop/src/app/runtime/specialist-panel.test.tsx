import type { RuntimeRequest } from '@hermes/shared/runtime-control'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/components/ui/button', () => ({
  Button: ({
    variant: _variant,
    size: _size,
    ...props
  }: React.ComponentProps<'button'> & { variant?: string; size?: string }) => <button {...props} />
}))
vi.mock('@/components/ui/input', () => ({ Input: (props: React.ComponentProps<'input'>) => <input {...props} /> }))
import { SpecialistPanel } from './specialist-panel'

const json = (value: unknown) => ({ response_json: JSON.stringify(value) })
afterEach(cleanup)

describe('typed specialist/media panel', () => {
  it('shows owned child failure states, requires a selection, and labels steer as queued', async () => {
    const fn = vi.fn(async (method: string) =>
      method === 'subagent.list'
        ? {
            subagents: [{ subagent_id: 'child-a', status: 'running', goal: 'Review artifact', accepting_steer: true }],
            delegations: [{ delegation_id: 'failed-a', status: 'failed' }]
          }
        : { subagent_id: 'child-a', status: 'queued', text: 'private echo' }
    ) as RuntimeRequest

    render(<SpecialistPanel connected request={fn} sessionId="owned" />)
    expect(fn).not.toHaveBeenCalled()
    expect((screen.getByRole('button', { name: 'Queue steer' }) as HTMLButtonElement).disabled).toBe(true)
    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Refresh child roster' })))
    expect(screen.getByText(/Delegation failed-a: failed/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'child-a: running' }))
    fireEvent.change(screen.getByLabelText('Instructions for selected child'), {
      target: { value: 'Use the exact artifact version' }
    })
    await act(async () => fireEvent.submit(screen.getByRole('form', { name: 'Steer selected child' })))
    expect(fn).toHaveBeenLastCalledWith('subagent.steer', {
      session_id: 'owned',
      subagent_id: 'child-a',
      text: 'Use the exact artifact version'
    })
    expect(screen.getByText(/steer queued, not delivered/)).toBeTruthy()
    expect(screen.queryByText('private echo')).toBeNull()
  })

  it('stops speech or discards audio without starting recording or cancelling a mission', async () => {
    const fn = vi.fn(async (method: string) =>
      json(
        method === 'runtime.voice.stop'
          ? { state: 'stopped', mission_cancelled: false, streaming_stopped: false }
          : { state: 'discarded', mission_cancelled: false }
      )
    ) as RuntimeRequest

    render(<SpecialistPanel connected request={fn} sessionId="owned" />)
    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Stop speech only' })))
    expect(screen.getByText(/Speech stopped. UI streaming and accepted mission work continue/)).toBeTruthy()
    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Discard captured audio' })))
    expect(fn).toHaveBeenNthCalledWith(1, 'runtime.voice.stop', { session_id: 'owned', schema_version: 1 })
    expect(fn).toHaveBeenNthCalledWith(2, 'runtime.voice.capture.cancel', { session_id: 'owned', schema_version: 1 })
    expect(fn).toHaveBeenCalledTimes(2)
  })

  it('ignores late roster results after a session change, disconnect or unmount', async () => {
    let finish!: (value: unknown) => void

    const fn = vi.fn(
      () =>
        new Promise(resolve => {
          finish = resolve
        })
    ) as RuntimeRequest

    const view = render(<SpecialistPanel connected request={fn} sessionId="a" />)
    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Refresh child roster' })))
    view.rerender(<SpecialistPanel connected={false} request={fn} sessionId="b" />)
    await act(async () => finish({ subagents: [{ subagent_id: 'old-child', status: 'running' }] }))
    expect(screen.queryByRole('button', { name: 'old-child: running' })).toBeNull()
    expect((screen.getByRole('button', { name: 'Stop speech only' }) as HTMLButtonElement).disabled).toBe(true)
    view.unmount()
    expect(fn).toHaveBeenCalledTimes(1)
  })

  it('fills verified binding from the backend and admits one logical same-mission input without automatic retry', async () => {
    const fn = vi.fn(async (method: string) =>
      json(
        method === 'runtime.channel.bind'
          ? {
              binding_id: 'binding-a',
              mission_id: 'mission-a',
              project_id: 'project-a',
              channel: 'local_jsonrpc',
              verification: 'owned_live_transport_and_stored_identity',
              history_replayed: false
            }
          : {
              schema_version: 1,
              command_id: 'input-a',
              status: 'duplicate',
              durable_revision: 4,
              run_id: 'run-a',
              conflict: null
            }
      )
    ) as RuntimeRequest

    const view = render(<SpecialistPanel connected request={fn} sessionId="owned" />)
    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Bind existing mission' })))
    expect((screen.getByLabelText('Verified binding ID') as HTMLInputElement).value).toBe('binding-a')
    fireEvent.change(screen.getByLabelText('Expected runtime revision'), { target: { value: '3' } })
    fireEvent.change(screen.getByLabelText('Confirmed task text'), { target: { value: 'Review the current artifact' } })
    await act(async () => fireEvent.submit(screen.getByRole('form', { name: 'Submit same-mission input' })))
    expect(screen.getByText(/duplicate; durable revision 4/)).toBeTruthy()
    expect(fn).toHaveBeenLastCalledWith(
      'runtime.channel.submit',
      expect.objectContaining({
        session_id: 'owned',
        binding_id: 'binding-a',
        expected_revision: 3,
        operation: 'submit',
        payload: { text: 'Review the current artifact' }
      })
    )
    view.rerender(<SpecialistPanel connected={false} request={fn} sessionId="owned" />)
    view.rerender(<SpecialistPanel connected request={fn} sessionId="owned" />)
    expect((screen.getByRole('button', { name: 'Submit to existing mission' }) as HTMLButtonElement).disabled).toBe(
      true
    )
    expect(fn).toHaveBeenCalledTimes(2)
  })
})
