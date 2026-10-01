import { useSearchParams } from 'react-router-dom'
import type { QueueParams } from '@/lib/queries'

export const PAGE = 60

/** All queue state lives in the URL so links, back, and the phone share one truth. */
export function useQueueParams() {
  const [sp, setSp] = useSearchParams()
  const get = (k: string) => sp.get(k) || ''
  const set = (patch: Record<string, string | null>) => {
    const next = new URLSearchParams(sp)
    for (const [k, v] of Object.entries(patch)) v ? next.set(k, v) : next.delete(k)
    setSp(next, { replace: true })
  }
  const toggle = (k: string, v: string) => set({ [k]: get(k) === v ? null : v })
  const params: QueueParams = {
    lane: get('lane') || 'resume', category: get('category') || undefined, state: get('state') || undefined,
    difficulty: get('difficulty') || undefined, sort: get('sort') || 'score', all: get('all') === '1', limit: PAGE,
  }
  return { get, set, toggle, params, issueId: get('issue') || null, selectIssue: (id: string | null) => set({ issue: id }) }
}
