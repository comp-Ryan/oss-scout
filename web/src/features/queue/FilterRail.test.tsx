import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderApp } from '@/test/render'
import { FilterRail } from './FilterRail'

test('category, lane, difficulty and sort write to the URL', async () => {
  renderApp(<FilterRail />)
  await userEvent.click(screen.getByRole('button', { name: 'IoT and embedded' }))
  await waitFor(() => expect(screen.getByTestId('location').textContent).toContain('category=iot_embedded'))
  await userEvent.click(screen.getByRole('button', { name: /smaller and rising/i }))
  expect(screen.getByTestId('location').textContent).toContain('lane=discover')
  await userEvent.click(screen.getByRole('button', { name: 'small' }))
  expect(screen.getByTestId('location').textContent).toContain('difficulty=small')
  await userEvent.click(screen.getByRole('button', { name: /everything classified/i }))
  expect(screen.getByTestId('location').textContent).toContain('all=1')
  await userEvent.click(screen.getByRole('button', { name: 'IoT and embedded' }))  // toggles off
  expect(screen.getByTestId('location').textContent).not.toContain('category=')
})
