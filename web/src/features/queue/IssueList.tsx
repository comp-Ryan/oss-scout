import { useEffect, useMemo, useState } from 'react'
import { useQueue, useAnalyze, useDismiss } from '@/lib/queries'
import { useKeys } from '@/lib/keys'
import { Button } from '@/components/ui/button'
import { IssueRow } from './IssueRow'
import { PAGE, useQueueParams } from './useQueueParams'
import * as api from '@/lib/api'
import type { QueueRow } from '@/lib/types'

export function IssueList() {
  const { params, issueId, selectIssue } = useQueueParams()
  const { data, isLoading, error } = useQueue(params)
  const [extra, setExtra] = useState<QueueRow[]>([])
  const [cursor, setCursor] = useState(0)
  const [notice, setNotice] = useState<string | null>(null)
  const analyze = useAnalyze()
  const dismiss = useDismiss()
  const rows = useMemo(() => [...(data ?? []), ...extra], [data, extra])
  useEffect(() => { setExtra([]); setCursor(0) }, [params.lane, params.category, params.state, params.difficulty, params.sort, params.all])
  useEffect(() => { if (issueId) { const i = rows.findIndex(r => r.id === issueId); if (i >= 0) setCursor(i) } }, [issueId, rows])
  const current = rows[cursor]
  useKeys({
    j: () => setCursor(c => Math.min(rows.length - 1, c + 1)),
    k: () => setCursor(c => Math.max(0, c - 1)),
    Enter: () => current && selectIssue(current.id),
    a: () => current && analyze.mutate([current.id], { onSuccess: r => setNotice(`Run ${r.run_id} started for ${current.repo}#${current.number}.`) }),
    d: () => current && dismiss.mutate([current.id, '']),
  }, [rows, cursor, current])
  const loadMore = () => api.getQueue({ ...params, offset: rows.length }).then(more => setExtra(x => [...x, ...more]))
  if (error) return <p className="p-4 text-bad">{String(error)}</p>
  if (isLoading) return <p className="p-4 text-muted">Loading the queue.</p>
  if (rows.length === 0) return <p className="max-w-[48ch] p-4 text-muted">Nothing here yet. Widen the filters, switch to everything classified, or wait for the hourly run.</p>
  return (
    <div>
      {notice && <p className="border-b border-line px-3 py-1.5 text-[12.5px] text-teal">{notice}</p>}
      <ul role="listbox" aria-label="issues" className="list-none p-0">
        {rows.map((r, i) => <IssueRow key={r.id} r={r} selected={i === cursor} onSelect={() => { setCursor(i); selectIssue(r.id) }} />)}
      </ul>
      {rows.length % PAGE === 0 && <div className="flex justify-center py-3"><Button variant="outline" size="sm" onClick={loadMore}>Load more</Button></div>}
      <p className="px-3 py-2 text-[11.5px] text-faint">j k move, Enter open, a analyze, d dismiss, Ctrl K command palette</p>
    </div>
  )
}
