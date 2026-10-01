import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderApp } from '@/test/render'
import * as api from '@/lib/api'
import { row } from '@/lib/fixtures'
import { IssueList } from './IssueList'

const rows = [row(), row({ id: 'B', rank: 2, score: 8, number: 2, title: 'Web thing', category: 'web_frontend', preview: '' })]

test('renders rows, j/k move the selection, Enter opens the issue, a analyzes, d dismisses', async () => {
  vi.spyOn(api, 'getQueue').mockResolvedValue(rows)
  const analyze = vi.spyOn(api, 'postAnalyze').mockResolvedValue({ run_id: 7, issue: 'B' })
  const dismiss = vi.spyOn(api, 'postDismiss').mockResolvedValue({ id: 'B', state: 'dismissed' })
  renderApp(<IssueList />)
  await waitFor(() => expect(screen.getByText(/Quant thing/)).toBeInTheDocument())
  expect(screen.getByText(/The first lines/)).toBeInTheDocument()
  await userEvent.keyboard('j')
  expect(screen.getByRole('option', { name: /Web thing/ })).toHaveAttribute('aria-selected', 'true')
  await userEvent.keyboard('{Enter}')
  expect(screen.getByTestId('location').textContent).toContain('issue=B')
  await userEvent.keyboard('a')
  await waitFor(() => expect(analyze).toHaveBeenCalledWith('B'))
  await userEvent.keyboard('d')
  await waitFor(() => expect(dismiss).toHaveBeenCalledWith('B', ''))
  await userEvent.keyboard('k')
  expect(screen.getByRole('option', { name: /Quant thing/ })).toHaveAttribute('aria-selected', 'true')
})

test('empty list invites action', async () => {
  vi.spyOn(api, 'getQueue').mockResolvedValue([])
  renderApp(<IssueList />)
  await waitFor(() => expect(screen.getByText(/nothing here yet/i)).toBeInTheDocument())
})
