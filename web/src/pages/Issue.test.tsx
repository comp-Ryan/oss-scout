import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import * as api from '../api'
import { Issue } from './Issue'

const detail = {
  issue: { id: 'A', repo: 'o/r', number: 1, title: 'Fix the thing', url: 'u', labels: '["bug"]', state: 'analyzed', body: 'body' },
  repo: { stars: 500, language: 'Python', ai_policy: 'none' },
  classification: { category: { choice: 'quant_trading', confidence: 0.9 }, kind: { choice: 'bug', confidence: 1 } },
  score: { total: 9.5, breakdown: { recognition: 2 } },
  brief: { summary: 'S', likely_cause: 'C', approach: 'A', files: ['core.py'], effort: 'small', risks: ['r1'], duplicate_risk: 'none', ai_policy_note: 'none' },
  runs: [], transitions: [], thread: null, messages: [],
}
function mount() {
  return render(<MemoryRouter initialEntries={['/issues/A']}><Routes><Route path="/issues/:id" element={<Issue />} /></Routes></MemoryRouter>)
}

test('shows brief and the Discuss action when analyzed; discuss then chat', async () => {
  vi.spyOn(api, 'getIssue').mockResolvedValue(detail as any)
  const discuss = vi.spyOn(api, 'postDiscuss').mockResolvedValue({ state: 'discussing' })
  vi.spyOn(api, 'postMessage').mockResolvedValue({ user: { id: 1, role: 'user', content: 'hi', at: 'x' }, assistant: { id: 2, role: 'assistant', content: 'Hello back', at: 'x' } })
  mount()
  await waitFor(() => expect(screen.getByText('Fix the thing')).toBeInTheDocument())
  expect(screen.getByText('core.py')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: /discuss/i }))
  expect(discuss).toHaveBeenCalledWith('A')
  await userEvent.type(screen.getByPlaceholderText(/ask about/i), 'hi')
  await userEvent.click(screen.getByRole('button', { name: /send/i }))
  await waitFor(() => expect(screen.getByText('Hello back')).toBeInTheDocument())
})

test('approve sends the plan text', async () => {
  vi.spyOn(api, 'getIssue').mockResolvedValue({ ...detail, issue: { ...detail.issue, state: 'discussing' }, thread: { conversation_id: 'c', plan: null } } as any)
  const approve = vi.spyOn(api, 'postApprove').mockResolvedValue({ state: 'plan_approved' })
  mount()
  await waitFor(() => expect(screen.getByRole('button', { name: /approve plan/i })).toBeInTheDocument())
  const ta = screen.getByLabelText(/plan/i)
  expect((ta as HTMLTextAreaElement).value).toBe('A')
  await userEvent.clear(ta); await userEvent.type(ta, 'Do X')
  await userEvent.click(screen.getByRole('button', { name: /approve plan/i }))
  expect(approve).toHaveBeenCalledWith('A', 'Do X')
})
