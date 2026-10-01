import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import * as api from '../api'
import { Agents } from './Agents'

test('lists runs and polls events for the selected running run until it finishes', async () => {
  vi.spyOn(api, 'getRuns').mockResolvedValue([
    { id: 5, status: 'running', model: 'm', started_at: '2026-10-01T00:00:00Z', finished_at: null, duration_s: null, input_tokens: 10, output_tokens: 2, error: null, repo: 'o/r', number: 1, title: 'T' },
  ])
  const ev = vi.spyOn(api, 'getEvents')
    .mockResolvedValueOnce({ status: 'running', events: [{ seq: 1, at: 'x', event: { event: 'step_update', step_update: { step_type: 'tool', tool_name: 'view_file' } } }] })
    .mockResolvedValue({ status: 'done', events: [{ seq: 2, at: 'x', event: { event: 'result', result: { status: 'SUCCESS' } } }] })
  render(<Agents pollMs={50} />)
  await waitFor(() => expect(screen.getByText(/o\/r#1/)).toBeInTheDocument())
  await userEvent.click(screen.getByText(/o\/r#1/))
  await waitFor(() => expect(screen.getByText(/view_file/)).toBeInTheDocument())
  await waitFor(() => expect(screen.getByText(/result/)).toBeInTheDocument(), { timeout: 3000 })
  expect(ev).toHaveBeenLastCalledWith(5, 1)
  const calls = ev.mock.calls.length
  await new Promise(r => setTimeout(r, 200))
  expect(ev.mock.calls.length).toBe(calls)  // stopped polling once the run was done
})
