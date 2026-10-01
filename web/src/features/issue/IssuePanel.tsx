import { useState } from 'react'
import { ExternalLink } from 'lucide-react'
import { useAnalyze, useDiscuss, useDismiss, useIssue, useMove } from '@/lib/queries'
import { catLabel, label, stars } from '@/lib/labels'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Select } from '@/components/ui/select'
import { BriefCard } from './BriefCard'
import { Chat } from './Chat'
import { DraftActions } from './DraftActions'

const COLUMNS = ['Picked', 'Working on', 'PR opened', 'Progressing', 'Accepted']
const TERMINAL = ['dismissed', 'stale', 'merged', 'closed']

export function IssuePanel({ id, onBack }: { id: string; onBack?: () => void }) {
  const { data: d, isLoading, error } = useIssue(id)
  const analyze = useAnalyze(); const discuss = useDiscuss(); const dismiss = useDismiss(); const move = useMove()
  const [notice, setNotice] = useState<string | null>(null)
  if (error) return <p className="p-4 text-bad">{String(error)}</p>
  if (isLoading || !d) return <p className="p-4 text-muted">Loading.</p>
  const state: string = d.issue.state
  const canAnalyze = ['shortlisted', 'classified', 'analyzed'].includes(state)
  return (
    <div className="flex h-full flex-col">
      <header className="border-b border-line px-4 py-3">
        {onBack && <button onClick={onBack} className="mb-2 text-[12.5px] text-muted hover:text-ink">Back to the list</button>}
        <h2 className="max-w-[60ch] text-[16px] font-semibold leading-snug">
          <span className="mono text-[13px] font-normal text-muted">{d.issue.repo}#{d.issue.number}</span><br />{d.issue.title}
          <a href={d.issue.url} target="_blank" rel="noreferrer" className="ml-1.5 inline-block align-middle text-muted hover:text-ink" aria-label="open on GitHub"><ExternalLink className="h-3.5 w-3.5" /></a>
        </h2>
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          <Badge tone="teal">{label(state)}</Badge>
          {d.score && <span className="mono text-[14px] text-signal">{d.score.total.toFixed(1)}</span>}
          <span className="text-[12px] text-muted">{stars(d.repo.stars)}★ {d.repo.language}</span>
          {d.classification.category && <Badge tone="ink">{catLabel(d.classification.category.choice)}</Badge>}
          {d.classification.kind && <Badge>{d.classification.kind.choice}</Badge>}
          {d.classification.difficulty && <Badge>{d.classification.difficulty.choice}</Badge>}
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {canAnalyze && <Button onClick={() => analyze.mutate([id], { onSuccess: r => setNotice(`Run ${r.run_id} started. Watch it on Agents.`) })}>{state === 'analyzed' ? 'Re-analyze' : 'Analyze'}</Button>}
          {['analyzed', 'shortlisted', 'classified'].includes(state) && <Button variant="primary" onClick={() => discuss.mutate([id])}>Work on it</Button>}
          {!TERMINAL.includes(state) && state !== 'shortlisted' && state !== 'classified' && (
            <Select label="move to" value="" onValueChange={c => { const pr = c === 'PR opened' && !d.draft?.pr_url ? window.prompt('Link to the PR') || undefined : undefined; if (c !== 'PR opened' || pr || d.draft?.pr_url) move.mutate([id, c, pr]) }}
              placeholder="Move to" options={COLUMNS.map(c => ({ value: c, label: c }))} />
          )}
          {!TERMINAL.includes(state) && <Button variant="danger" onClick={() => dismiss.mutate([id, ''])}>Dismiss</Button>}
        </div>
        {notice && <p className="mt-2 text-[12.5px] text-teal">{notice}</p>}
      </header>
      <Tabs defaultValue="brief" className="flex min-h-0 flex-1 flex-col">
        <TabsList className="mx-4 mt-3"><TabsTrigger value="brief">Brief</TabsTrigger><TabsTrigger value="discussion">Discussion</TabsTrigger><TabsTrigger value="issue">Issue text</TabsTrigger><TabsTrigger value="history">History</TabsTrigger></TabsList>
        <div className="min-h-0 flex-1 overflow-auto px-4 py-3">
          <TabsContent value="brief">
            <DraftActions d={d} />
            <div className="mt-3">{d.brief ? <BriefCard brief={d.brief} /> : <p className="text-[13px] text-muted">No brief yet. Analyze to get one; it takes one to four minutes.</p>}</div>
          </TabsContent>
          <TabsContent value="discussion"><Chat id={id} messages={d.messages} /></TabsContent>
          <TabsContent value="issue"><pre className="max-w-[80ch] whitespace-pre-wrap text-[13px]">{d.issue.body || '(empty)'}</pre></TabsContent>
          <TabsContent value="history"><ul className="space-y-1 text-[12.5px] text-muted">{d.transitions.map((t, i) => <li key={i}>{t.at.slice(0, 16).replace('T', ' ')}: {label(t.from_state)} to {label(t.to_state)} by {t.actor}{t.note ? `, ${t.note}` : ''}</li>)}</ul>{d.runs.length > 0 && <p className="mt-2 text-[12.5px] text-faint">{d.runs.length} agent run{d.runs.length > 1 ? 's' : ''}.</p>}</TabsContent>
        </div>
      </Tabs>
    </div>
  )
}
