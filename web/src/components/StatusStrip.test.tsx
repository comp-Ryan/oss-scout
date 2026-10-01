import { render, screen, waitFor } from '@testing-library/react'
import * as api from '../api'
import { StatusStrip } from './StatusStrip'

test('renders one plain sentence from the status', async () => {
  vi.spyOn(api, 'getStatus').mockResolvedValue({ issues: { shortlisted: 20, analyzed: 3, classified: 1000 }, lanes: { resume: 20, discover: 3 },
    last_run: '2026-10-01 09:47:00', next_run: 'Thu 2026-10-01 10:46:07 PDT', runs_today: 4, tokens_in_today: 500000, tokens_out_today: 60000, labels: {}, board_order: [] })
  render(<StatusStrip />)
  await waitFor(() => expect(screen.getByText(/23 top picks/)).toBeInTheDocument())
  expect(screen.getByText(/last run 09:47/)).toBeInTheDocument()
  expect(screen.getByText(/4 agent runs today/)).toBeInTheDocument()
})
