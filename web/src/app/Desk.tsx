import { useState } from 'react'
import { SlidersHorizontal } from 'lucide-react'
import { FilterRail } from '@/features/queue/FilterRail'
import { IssueList } from '@/features/queue/IssueList'
import { IssuePanel } from '@/features/issue/IssuePanel'
import { useQueueParams } from '@/features/queue/useQueueParams'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent } from '@/components/ui/dialog'
import { useIsWide } from './useIsWide'

/** Rail, list, detail. Below 1100px the rail becomes a Filters dialog and the detail replaces the list. */
export function Desk() {
  const { issueId, selectIssue } = useQueueParams()
  const wide = useIsWide(1100)
  const [filtersOpen, setFiltersOpen] = useState(false)
  if (wide) {
    return (
      <div className="grid h-[calc(100vh-49px)] grid-cols-[220px_minmax(380px,1fr)_minmax(420px,46%)]">
        <aside className="overflow-auto border-r border-line">{<FilterRail />}</aside>
        <section className="overflow-auto"><IssueList /></section>
        <section className="overflow-hidden border-l border-line">{issueId ? <IssuePanel id={issueId} /> : <p className="max-w-[40ch] p-6 text-muted">Pick an issue on the left. Press j and k to move, Enter to open.</p>}</section>
      </div>
    )
  }
  return (
    <div className="h-[calc(100vh-49px)] overflow-auto">
      {issueId ? <IssuePanel id={issueId} onBack={() => selectIssue(null)} /> : (
        <>
          <div className="flex items-center justify-between border-b border-line px-3 py-2">
            <Button variant="outline" size="sm" onClick={() => setFiltersOpen(true)}><SlidersHorizontal className="h-3.5 w-3.5" /> Filters</Button>
          </div>
          <IssueList />
          <Dialog open={filtersOpen} onOpenChange={setFiltersOpen}><DialogContent title="Filters"><div className="max-h-[70vh] overflow-auto"><FilterRail /></div></DialogContent></Dialog>
        </>
      )}
    </div>
  )
}
