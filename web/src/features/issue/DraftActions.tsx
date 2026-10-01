import { useEffect, useState } from 'react'
import type { IssueDetail } from '@/lib/types'
import { useApprove, useLaunch, usePrep, useSubmit } from '@/lib/queries'
import { Button } from '@/components/ui/button'
import { Input, Textarea } from '@/components/ui/input'

/** The state-specific actions: approve a plan, prepare a worktree, open Claude Code, submit a draft PR. */
export function DraftActions({ d }: { d: IssueDetail }) {
  const id: string = d.issue.id
  const state: string = d.issue.state
  const [plan, setPlan] = useState('')
  const [title, setTitle] = useState('')
  const [body, setBody] = useState('')
  const [cmd, setCmd] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => {
    setPlan(p => p || d.thread?.plan || d.brief?.approach || '')
    setTitle(t => t || d.issue.title)
    setBody(b => b || `${d.thread?.plan || d.brief?.approach || ''}\n\nCloses #${d.issue.number}`)
  }, [d])
  const approve = useApprove(); const prep = usePrep(); const launch = useLaunch(); const submit = useSubmit()
  const onErr = (e: unknown) => setErr(String(e))
  return (
    <div className="space-y-3">
      {err && <pre className="whitespace-pre-wrap rounded-md bg-surface p-2 text-[12px] text-bad">{err}</pre>}
      {state === 'discussing' && (
        <div className="space-y-1.5">
          <label htmlFor="plan" className="text-[12px] font-medium text-faint">Plan</label>
          <Textarea id="plan" rows={4} value={plan} onChange={e => setPlan(e.target.value)} />
          <Button variant="primary" onClick={() => approve.mutate([id, plan], { onError: onErr })}>Approve plan</Button>
        </div>
      )}
      {state === 'plan_approved' && <Button variant="primary" onClick={() => prep.mutate([id], { onError: onErr })} disabled={prep.isPending}>{prep.isPending ? 'Preparing worktree' : 'Prepare worktree'}</Button>}
      {state === 'drafting' && d.draft && (
        <div className="space-y-2 rounded-md border border-line p-3">
          <p className="text-[12.5px] text-muted">Worktree <code>{d.draft.worktree}</code> on <code>{d.draft.branch}</code>{d.draft.test_cmd ? <>, tests <code>{d.draft.test_cmd}</code></> : ', no test command known'}</p>
          <Button onClick={() => launch.mutate(id, { onSuccess: r => setCmd(r.command + (r.launched ? '' : '   (no terminal found; run this yourself)')), onError: onErr })}>Open in Claude Code</Button>
          {cmd && <pre className="whitespace-pre-wrap rounded-md bg-surface p-2 text-[12px]">{cmd}</pre>}
          <label htmlFor="prtitle" className="block text-[12px] font-medium text-faint">PR title</label>
          <Input id="prtitle" value={title} onChange={e => setTitle(e.target.value)} />
          <label htmlFor="prbody" className="block text-[12px] font-medium text-faint">PR body</label>
          <Textarea id="prbody" rows={5} value={body} onChange={e => setBody(e.target.value)} />
          <Button variant="primary" onClick={() => submit.mutate([id, title, body], { onError: onErr })}>Open draft PR</Button>
          {d.draft.last_error && <pre className="whitespace-pre-wrap rounded-md bg-surface p-2 text-[12px] text-bad">{d.draft.last_error}</pre>}
        </div>
      )}
      {['pr_open', 'in_review', 'merged'].includes(state) && d.draft?.pr_url && <p className="text-[13px]">Pull request: <a className="text-violet underline" href={d.draft.pr_url} target="_blank" rel="noreferrer">{d.draft.pr_url.replace('https://github.com/', '')}</a></p>}
    </div>
  )
}
