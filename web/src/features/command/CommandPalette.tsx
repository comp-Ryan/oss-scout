import { useEffect, useState } from 'react'
import { Command } from 'cmdk'
import { useNavigate } from 'react-router-dom'
import { useQueueParams } from '@/features/queue/useQueueParams'
import { useAnalyze, useDismiss, useDiscuss, useMove, useQueue } from '@/lib/queries'
import { useKeys } from '@/lib/keys'

const COLUMNS = ['Picked', 'Working on', 'PR opened', 'Progressing', 'Accepted']

export function CommandPalette() {
  const [open, setOpen] = useState(false)
  const nav = useNavigate()
  const { params, issueId, selectIssue } = useQueueParams()
  const { data: rows = [] } = useQueue({ ...params, limit: 200 })
  const analyze = useAnalyze(); const dismiss = useDismiss(); const discuss = useDiscuss(); const move = useMove()
  useKeys({ 'mod+k': () => setOpen(o => !o), Escape: () => setOpen(false) }, [])
  useEffect(() => { if (!open) return }, [open])
  const run = (fn: () => void) => { fn(); setOpen(false) }
  return (
    <Command.Dialog open={open} onOpenChange={setOpen} label="Command palette" className="fixed left-1/2 top-[12vh] z-50 w-[min(640px,94vw)] -translate-x-1/2 overflow-hidden rounded-lg border border-line bg-surface shadow-2xl"
      overlayClassName="fixed inset-0 z-40 bg-black/60">
      <Command.Input autoFocus placeholder="Jump to an issue, or type an action" className="w-full border-b border-line bg-transparent px-4 py-3 text-[14px] text-ink outline-none placeholder:text-faint" />
      <Command.List className="max-h-[50vh] overflow-auto p-1.5">
        <Command.Empty className="px-3 py-4 text-[13px] text-muted">Nothing matches.</Command.Empty>
        <Command.Group heading="Pages" className="text-[11.5px] text-faint [&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1">
          <Item onSelect={() => run(() => nav('/'))}>Go to queue</Item>
          <Item onSelect={() => run(() => nav('/board'))}>Go to board</Item>
          <Item onSelect={() => run(() => nav('/agents'))}>Go to agents</Item>
        </Command.Group>
        {issueId && (
          <Command.Group heading="Selected issue" className="text-[11.5px] text-faint [&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1">
            <Item onSelect={() => run(() => analyze.mutate([issueId]))}>Analyze</Item>
            <Item onSelect={() => run(() => discuss.mutate([issueId]))}>Work on it</Item>
            <Item onSelect={() => run(() => dismiss.mutate([issueId, '']))}>Dismiss</Item>
            {COLUMNS.map(c => <Item key={c} onSelect={() => run(() => move.mutate([issueId, c]))}>Move to {c}</Item>)}
          </Command.Group>
        )}
        <Command.Group heading="Issues" className="text-[11.5px] text-faint [&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1">
          {rows.map(r => <Item key={r.id} value={`${r.repo} ${r.number} ${r.title}`} onSelect={() => run(() => { nav('/'); selectIssue(r.id) })}><span className="mono mr-2 text-muted">{r.repo}#{r.number}</span>{r.title}</Item>)}
        </Command.Group>
      </Command.List>
    </Command.Dialog>
  )
}

function Item({ children, onSelect, value }: { children: React.ReactNode; onSelect: () => void; value?: string }) {
  return <Command.Item value={value} onSelect={onSelect} className="cursor-pointer rounded-sm px-2.5 py-1.5 text-[13px] text-ink data-[selected=true]:bg-raised">{children}</Command.Item>
}
