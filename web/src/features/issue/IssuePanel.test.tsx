import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderApp } from '@/test/render'
import * as api from '@/lib/api'
import { detail } from '@/lib/fixtures'
import { IssuePanel } from './IssuePanel'

test('brief ready: shows the brief, Work on it moves to discussing, chat replies', async () => {
  const get = vi.spyOn(api, 'getIssue').mockResolvedValue(detail())
  const discuss = vi.spyOn(api, 'postDiscuss').mockResolvedValue({ id: 'A', state: 'discussing' })
  vi.spyOn(api, 'postMessage').mockResolvedValue({ user: { id: 1, role: 'user', content: 'hi', at: 'x' }, assistant: { id: 2, role: 'assistant', content: 'Hello back', at: 'x' } })
  renderApp(<IssuePanel id="A" />)
  await waitFor(() => expect(screen.getByText(/Fix the thing/)).toBeInTheDocument())
  expect(screen.getByText('core.py')).toBeInTheDocument()
  get.mockResolvedValue(detail({ issue: { ...detail().issue, state: 'discussing' }, thread: { conversation_id: null, plan: null } }))
  await userEvent.click(screen.getByRole('button', { name: /work on it/i }))
  expect(discuss).toHaveBeenCalledWith('A')
  await userEvent.click(await screen.findByRole('tab', { name: /discussion/i }))
  await userEvent.type(screen.getByPlaceholderText(/ask about/i), 'hi')
  await userEvent.click(screen.getByRole('button', { name: /send/i }))
  await waitFor(() => expect(screen.getByText('Hello back')).toBeInTheDocument())
})

test('discussing: approve sends the plan; drafting: prepare, launch and submit; pr_open: link', async () => {
  const get = vi.spyOn(api, 'getIssue').mockResolvedValue(detail({ issue: { ...detail().issue, state: 'discussing' }, thread: { conversation_id: 'c', plan: null } }))
  const approve = vi.spyOn(api, 'postApprove').mockResolvedValue({ id: 'A', state: 'plan_approved' })
  renderApp(<IssuePanel id="A" />)
  const ta = await screen.findByLabelText(/plan/i)
  expect((ta as HTMLTextAreaElement).value).toBe('A')
  await userEvent.clear(ta); await userEvent.type(ta, 'Do X')
  await userEvent.click(screen.getByRole('button', { name: /approve plan/i }))
  expect(approve).toHaveBeenCalledWith('A', 'Do X')
  get.mockResolvedValue(detail({ issue: { ...detail().issue, state: 'drafting' }, thread: { conversation_id: 'c', plan: 'Do X' },
    draft: { worktree: '/w/42', branch: 'scout/42-x', brief_path: '/w/42/BRIEF.md', test_cmd: 'pytest', pr_url: null, last_error: null } }))
  const launch = vi.spyOn(api, 'postLaunch').mockResolvedValue({ launched: false, command: 'cd /w/42 && claude', cwd: '/w/42' })
  const submit = vi.spyOn(api, 'postSubmit').mockRejectedValue(new Error('409 {"detail":"FAILED test_y"}'))
  renderApp(<IssuePanel id="A" />)
  await userEvent.click(await screen.findByRole('button', { name: /open in claude code/i }))
  expect(launch).toHaveBeenCalledWith('A')
  await waitFor(() => expect(screen.getByText(/cd \/w\/42 && claude/)).toBeInTheDocument())
  await userEvent.click(screen.getByRole('button', { name: /open draft pr/i }))
  expect(submit).toHaveBeenCalledWith('A', 'Fix the thing', expect.stringContaining('Do X'))
  await waitFor(() => expect(screen.getByText(/FAILED test_y/)).toBeInTheDocument())
})

test('the move control is labeled even before a choice is made', async () => {
  vi.spyOn(api, 'getIssue').mockResolvedValue(detail({ issue: { ...detail().issue, state: 'discussing' }, thread: { conversation_id: 'c', plan: 'P' } }))
  renderApp(<IssuePanel id="A" />)
  expect(await screen.findByRole('combobox', { name: /move to/i })).toHaveTextContent(/move to/i)
})
