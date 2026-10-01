import type { BoardData, Draft, IssueDetail, Message, QueueRow, Run, RunEvent, Settings, Status } from './types'

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, { headers: { 'Content-Type': 'application/json' }, ...init })
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`)
  return r.json()
}
const qs = (p: Record<string, string | number | undefined>) =>
  Object.entries(p).filter(([, v]) => v !== undefined && v !== '').map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`).join('&')

export const getStatus = () => call<Status>('/api/status')
export const getQueue = (p: { category?: string; state?: string; limit?: number; lane?: string; difficulty?: string; sort?: string; offset?: number; all?: boolean }) => call<QueueRow[]>(`/api/queue?${qs({ ...p, all: p.all ? 'true' : undefined })}`)
export const getIssue = (id: string) => call<IssueDetail>(`/api/issues/${id}`)
export const postDismiss = (id: string, note: string) => call<any>(`/api/issues/${id}/dismiss`, { method: 'POST', body: JSON.stringify({ note }) })
export const postDiscuss = (id: string) => call<any>(`/api/issues/${id}/discuss`, { method: 'POST' })
export const postApprove = (id: string, plan: string) => call<any>(`/api/issues/${id}/approve`, { method: 'POST', body: JSON.stringify({ plan }) })
export const getMessages = (id: string) => call<Message[]>(`/api/issues/${id}/messages`)
export const postMessage = (id: string, content: string) => call<{ user: Message; assistant: Message }>(`/api/issues/${id}/messages`, { method: 'POST', body: JSON.stringify({ content }) })
export const getRuns = () => call<Run[]>('/api/runs')
export const getEvents = (runId: number, after: number) => call<{ status: string; events: RunEvent[] }>(`/api/runs/${runId}/events?after=${after}`)
export const getSettings = () => call<Settings>('/api/settings')
export const putSettings = (s: Partial<Settings>) => call<Settings>('/api/settings', { method: 'PUT', body: JSON.stringify(s) })
export const postPrep = (id: string) => call<Draft>(`/api/issues/${id}/prep`, { method: 'POST' })
export const postLaunch = (id: string) => call<{ launched: boolean; command: string; cwd: string }>(`/api/issues/${id}/launch`, { method: 'POST' })
export const postSubmit = (id: string, title: string, body: string) => call<{ pr_url: string; tests: string }>(`/api/issues/${id}/submit`, { method: 'POST', body: JSON.stringify({ title, body }) })
export const postAnalyze = (id: string) => call<{ run_id: number; issue: string }>(`/api/issues/${id}/analyze`, { method: 'POST' })
export const getBoard = () => call<BoardData>('/api/board')
export const postMove = (id: string, column: string, pr_url?: string) => call<{ id: string; state: string }>(`/api/issues/${id}/move`, { method: 'POST', body: JSON.stringify({ column, pr_url }) })
