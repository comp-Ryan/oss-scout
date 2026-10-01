import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import * as api from '../api'
import { Queue } from './Queue'

const rows = [
  { id: 'A', rank: 1, score: 9.5, repo: 'o/r', number: 1, title: 'Quant thing', url: 'u', state: 'shortlisted', category: 'quant_trading', kind: 'bug', difficulty: 'small', has_bounty: 0, breakdown: {} },
  { id: 'B', rank: 2, score: 8.0, repo: 'o/s', number: 2, title: 'Web thing', url: 'u', state: 'shortlisted', category: 'web_frontend', kind: 'feature', difficulty: 'medium', has_bounty: 1, breakdown: {} },
]

test('renders rows and filters by category chip', async () => {
  const spy = vi.spyOn(api, 'getQueue').mockResolvedValue(rows)
  render(<MemoryRouter><Queue /></MemoryRouter>)
  await waitFor(() => expect(screen.getByText('Quant thing')).toBeInTheDocument())
  expect(screen.getByText('Web thing')).toBeInTheDocument()
  spy.mockResolvedValue([rows[0]])
  await userEvent.click(screen.getByRole('button', { name: 'quant_trading' }))
  await waitFor(() => expect(screen.queryByText('Web thing')).not.toBeInTheDocument())
  expect(spy).toHaveBeenLastCalledWith(expect.objectContaining({ category: 'quant_trading' }))
})

test('empty queue shows an empty-state line', async () => {
  vi.spyOn(api, 'getQueue').mockResolvedValue([])
  render(<MemoryRouter><Queue /></MemoryRouter>)
  await waitFor(() => expect(screen.getByText(/nothing shortlisted yet/i)).toBeInTheDocument())
})
