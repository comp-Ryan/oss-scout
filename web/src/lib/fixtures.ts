import type { IssueDetail, QueueRow, Status } from './types'

export const row = (over: Partial<QueueRow> = {}): QueueRow => ({
  id: 'A', rank: 1, score: 9.5, repo: 'o/r', number: 1, title: 'Quant thing', url: 'u', state: 'shortlisted', category: 'quant_trading',
  kind: 'bug', difficulty: 'small', has_bounty: 0, breakdown: { recognition: 2 }, lane: 'resume', preview: 'The first lines of the body',
  stars: 12000, language: 'Python', rising: 0, labels: ['bug'], ...over,
})
export const status: Status = { issues: { shortlisted: 20, analyzed: 3, classified: 1000 }, lanes: { resume: 20, discover: 3 }, last_run: '2026-10-01 09:47:00',
  next_run: 'Thu 2026-10-01 10:46:07 PDT', runs_today: 4, tokens_in_today: 500000, tokens_out_today: 60000, labels: {}, board_order: [] }
export const detail = (over: Partial<IssueDetail> = {}): IssueDetail => ({
  issue: { id: 'A', repo: 'o/r', number: 1, title: 'Fix the thing', url: 'u', labels: '["bug"]', state: 'analyzed', body: 'body' },
  repo: { stars: 500, language: 'Python', ai_policy: 'none' },
  classification: { category: { choice: 'quant_trading', confidence: 0.9 }, kind: { choice: 'bug', confidence: 1 }, difficulty: { choice: 'small', confidence: 1 } },
  score: { total: 9.5, breakdown: { recognition: 2 } },
  brief: { summary: 'S', likely_cause: 'C', approach: 'A', files: ['core.py'], effort: 'small', risks: ['r1'], duplicate_risk: 'none', ai_policy_note: 'none' },
  runs: [], transitions: [], thread: null, messages: [], draft: null, ...over,
})
