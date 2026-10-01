import { screen, waitFor } from '@testing-library/react'
import { renderApp } from '@/test/render'
import * as api from '@/lib/api'
import { status } from '@/lib/fixtures'
import { StatusBar } from './StatusBar'

test('renders the status sentence and nav', async () => {
  vi.spyOn(api, 'getStatus').mockResolvedValue(status)
  renderApp(<StatusBar />)
  await waitFor(() => expect(screen.getByText(/23 picks/)).toBeInTheDocument())
  expect(screen.getByText(/last run 09:47/)).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Board' })).toBeInTheDocument()
})
