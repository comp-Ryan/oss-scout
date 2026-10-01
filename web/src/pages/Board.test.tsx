import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import * as api from '../api'
import { Board } from './Board'

const board = { columns: [
  { name: 'Picked', cards: [{ id: 'A', repo: 'o/r', number: 1, title: 'Fix it', url: 'u', state: 'analyzed', lane: 'resume', stars: 10, language: 'Python', pr_url: null, label: 'brief ready' }] },
  { name: 'Working on', cards: [] }, { name: 'PR opened', cards: [] }, { name: 'Progressing', cards: [] }, { name: 'Accepted', cards: [] },
] }

test('shows columns and moves a card with the move control', async () => {
  const get = vi.spyOn(api, 'getBoard').mockResolvedValue(board)
  const move = vi.spyOn(api, 'postMove').mockResolvedValue({ id: 'A', state: 'discussing' })
  render(<MemoryRouter><Board /></MemoryRouter>)
  await waitFor(() => expect(screen.getByRole('link', { name: /Fix it/ })).toBeInTheDocument())
  expect(screen.getByText('Accepted')).toBeInTheDocument()
  get.mockResolvedValue({ columns: [board.columns[0], { name: 'Working on', cards: board.columns[0].cards }, ...board.columns.slice(2)] })
  await userEvent.selectOptions(screen.getByLabelText(/move Fix it/i), 'Working on')
  expect(move).toHaveBeenCalledWith('A', 'Working on', undefined)
})

test('moving to PR opened asks for the link when there is none', async () => {
  vi.spyOn(api, 'getBoard').mockResolvedValue(board)
  const move = vi.spyOn(api, 'postMove').mockResolvedValue({ id: 'A', state: 'pr_open' })
  vi.spyOn(window, 'prompt').mockReturnValue('https://github.com/o/r/pull/9')
  render(<MemoryRouter><Board /></MemoryRouter>)
  await waitFor(() => expect(screen.getByRole('link', { name: /Fix it/ })).toBeInTheDocument())
  await userEvent.selectOptions(screen.getByLabelText(/move Fix it/i), 'PR opened')
  expect(move).toHaveBeenCalledWith('A', 'PR opened', 'https://github.com/o/r/pull/9')
})
