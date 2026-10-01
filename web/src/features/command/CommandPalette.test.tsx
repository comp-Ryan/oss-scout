import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderApp } from '@/test/render'
import * as api from '@/lib/api'
import { row } from '@/lib/fixtures'
import { CommandPalette } from './CommandPalette'

test('Ctrl+K opens the palette; a page command navigates; an issue command selects it', async () => {
  vi.spyOn(api, 'getQueue').mockResolvedValue([row({ title: 'Dependency minimums raising' })])
  renderApp(<CommandPalette />)
  await userEvent.keyboard('{Control>}k{/Control}')
  const input = await screen.findByPlaceholderText(/jump to/i)
  await userEvent.type(input, 'board')
  await userEvent.click(await screen.findByText(/go to board/i))
  await waitFor(() => expect(screen.getByTestId('location').textContent).toContain('/board'))
  await userEvent.keyboard('{Control>}k{/Control}')
  await userEvent.type(await screen.findByPlaceholderText(/jump to/i), 'minimums')
  await userEvent.click(await screen.findByText(/Dependency minimums/))
  await waitFor(() => expect(screen.getByTestId('location').textContent).toContain('issue=A'))
})
