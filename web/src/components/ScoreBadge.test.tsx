import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ScoreBadge } from './ScoreBadge'

test('shows the total and reveals the breakdown on hover', async () => {
  render(<ScoreBadge total={9.5} breakdown={{ recognition: 2.0, bounty: 0 }} />)
  expect(screen.getByText('9.5')).toBeInTheDocument()
  expect(screen.queryByText(/recognition/)).not.toBeInTheDocument()
  await userEvent.hover(screen.getByText('9.5'))
  expect(screen.getByText(/recognition/)).toBeInTheDocument()
  expect(screen.getByText(/2\.0/)).toBeInTheDocument()
})
