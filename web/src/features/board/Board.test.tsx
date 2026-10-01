import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderApp } from '@/test/render'
import * as api from '@/lib/api'
import { Board } from './Board'

const board = { columns: [
  { name: 'Picked', cards: [{ id: 'A', repo: 'o/r', number: 1, title: 'Fix it', url: 'u', state: 'analyzed', lane: 'resume', stars: 10, language: 'Python', pr_url: null, label: 'brief ready' }] },
  { name: 'Working on', cards: [] }, { name: 'PR opened', cards: [] }, { name: 'Progressing', cards: [] }, { name: 'Accepted', cards: [] },
] }

test('shows columns and moves a card; PR opened asks for the link', async () => {
  vi.spyOn(api, 'getBoard').mockResolvedValue(board)
  const move = vi.spyOn(api, 'postMove').mockResolvedValue({ id: 'A', state: 'discussing' })
  vi.spyOn(window, 'prompt').mockReturnValue('https://github.com/o/r/pull/9')
  renderApp(<Board />)
  await waitFor(() => expect(screen.getByRole('link', { name: /Fix it/ })).toBeInTheDocument())
  expect(screen.getByText('Accepted')).toBeInTheDocument()
  await userEvent.selectOptions(screen.getByLabelText(/move Fix it/i), 'Working on')
  expect(move).toHaveBeenCalledWith('A', 'Working on', undefined)
  await userEvent.selectOptions(screen.getByLabelText(/move Fix it/i), 'PR opened')
  expect(move).toHaveBeenCalledWith('A', 'PR opened', 'https://github.com/o/r/pull/9')
})
