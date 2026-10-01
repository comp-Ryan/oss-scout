import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import * as api from '../api'
import { Queue } from './Queue'

const rows = [
  { id: 'A', rank: 1, score: 9.5, repo: 'o/r', number: 1, title: 'Quant thing', url: 'u', state: 'shortlisted', category: 'quant_trading', kind: 'bug', difficulty: 'small', has_bounty: 0, breakdown: {}, lane: 'resume', preview: 'The first lines of the body', stars: 12000, language: 'Python', rising: 0, labels: ['bug'] },
  { id: 'B', rank: 2, score: 8.0, repo: 'o/s', number: 2, title: 'Web thing', url: 'u', state: 'shortlisted', category: 'web_frontend', kind: 'feature', difficulty: 'medium', has_bounty: 1, breakdown: {}, lane: 'resume', preview: '', stars: 300, language: 'TypeScript', rising: 1, labels: [] },
]

test('renders cards with repo facts and preview, filters by category chip', async () => {
  const spy = vi.spyOn(api, 'getQueue').mockResolvedValue(rows)
  render(<MemoryRouter><Queue /></MemoryRouter>)
  await waitFor(() => expect(screen.getByText(/Quant thing/)).toBeInTheDocument())
  expect(screen.getByText('The first lines of the body')).toBeInTheDocument()
  expect(screen.getByText(/12k/)).toBeInTheDocument()
  expect(screen.getByText(/Web thing/)).toBeInTheDocument()
  spy.mockResolvedValue([rows[0]])
  await userEvent.click(screen.getByRole('button', { name: 'quant trading' }))
  await waitFor(() => expect(screen.queryByText(/Web thing/)).not.toBeInTheDocument())
  expect(spy).toHaveBeenLastCalledWith(expect.objectContaining({ category: 'quant_trading' }))
})

test('lane switch asks for the discover lane; analyze button starts a run', async () => {
  const spy = vi.spyOn(api, 'getQueue').mockResolvedValue(rows)
  const analyze = vi.spyOn(api, 'postAnalyze').mockResolvedValue({ run_id: 5, issue: 'A' })
  render(<MemoryRouter><Queue /></MemoryRouter>)
  await waitFor(() => expect(screen.getByText(/Quant thing/)).toBeInTheDocument())
  await userEvent.click(screen.getByRole('button', { name: /smaller and rising/i }))
  await waitFor(() => expect(spy).toHaveBeenLastCalledWith(expect.objectContaining({ lane: 'discover' })))
  await userEvent.click(screen.getAllByRole('button', { name: /analyze/i })[0])
  expect(analyze).toHaveBeenCalledWith('A')
  await waitFor(() => expect(screen.getByText(/run 5 started/i)).toBeInTheDocument())
})

test('empty queue shows an empty-state line', async () => {
  vi.spyOn(api, 'getQueue').mockResolvedValue([])
  render(<MemoryRouter><Queue /></MemoryRouter>)
  await waitFor(() => expect(screen.getByText(/nothing shortlisted here yet/i)).toBeInTheDocument())
})

test('difficulty chips, sort control and show-all toggle reach the API', async () => {
  const spy = vi.spyOn(api, 'getQueue').mockResolvedValue(rows)
  render(<MemoryRouter><Queue /></MemoryRouter>)
  await waitFor(() => expect(screen.getByText(/Quant thing/)).toBeInTheDocument())
  await userEvent.click(screen.getByRole('button', { name: 'small' }))
  await waitFor(() => expect(spy).toHaveBeenLastCalledWith(expect.objectContaining({ difficulty: 'small' })))
  await userEvent.selectOptions(screen.getByLabelText(/sort/i), 'easiest')
  await waitFor(() => expect(spy).toHaveBeenLastCalledWith(expect.objectContaining({ sort: 'easiest' })))
  await userEvent.click(screen.getByRole('button', { name: /everything classified/i }))
  await waitFor(() => expect(spy).toHaveBeenLastCalledWith(expect.objectContaining({ all: true })))
})

test('load more appends the next page', async () => {
  const many = Array.from({ length: 60 }, (_, i) => ({ ...rows[0], id: `R${i}`, rank: i + 1, number: i + 1, title: `Issue ${i + 1}` }))
  const spy = vi.spyOn(api, 'getQueue').mockResolvedValueOnce(many).mockResolvedValueOnce([{ ...rows[1], id: 'X', rank: 61, number: 61, title: 'Issue 61' }])
  render(<MemoryRouter><Queue /></MemoryRouter>)
  await waitFor(() => expect(screen.getByText(/Issue 60/)).toBeInTheDocument())
  await userEvent.click(screen.getByRole('button', { name: /load more/i }))
  await waitFor(() => expect(screen.getByText(/Issue 61/)).toBeInTheDocument())
  expect(spy).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 60 }))
  expect(screen.getByText(/Issue 60/)).toBeInTheDocument()
})
