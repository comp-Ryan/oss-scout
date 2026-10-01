import type { QueueRow } from '@/lib/types'
import { catLabel, label, stars } from '@/lib/labels'
import { cn } from '@/lib/utils'
import { Badge } from '@/components/ui/badge'
import { Tip } from '@/components/ui/tooltip'

export const CAT_COLOR: Record<string, string> = {
  quant_trading: 'bg-signal', prediction_markets_finance: 'bg-violet', ai_agents_llm: 'bg-teal', ml_data_science: 'bg-blue', data_scraping: 'bg-blue',
  backend_api: 'bg-green', web_frontend: 'bg-pink', dev_tools_cli: 'bg-green', devops_cloud: 'bg-green', security: 'bg-bad', iot_embedded: 'bg-orange',
  logic_formal_methods: 'bg-violet', philosophy_religion: 'bg-violet', math_competitive: 'bg-blue', science_simulation: 'bg-orange', knowledge_reading: 'bg-violet', other: 'bg-faint',
}
const STATE_TONE: Record<string, 'quiet' | 'teal' | 'signal' | 'violet' | 'green'> = {
  shortlisted: 'quiet', classified: 'quiet', analyzed: 'teal', discussing: 'signal', plan_approved: 'signal', drafting: 'signal', pr_open: 'violet', in_review: 'violet', merged: 'green',
}

export function IssueRow({ r, selected, onSelect }: { r: QueueRow; selected: boolean; onSelect: () => void }) {
  return (
    <li role="option" aria-selected={selected} aria-label={`#${r.number} ${r.title}`} onClick={onSelect}
      className={cn('grid cursor-pointer grid-cols-[3px_1fr_auto] gap-x-3 border-b border-line px-2 py-2.5', selected ? 'bg-raised' : 'hover:bg-surface')}>
      <span className={cn('row-span-3 w-[3px] rounded-full', CAT_COLOR[r.category ?? 'other'])} aria-hidden />
      <div className="flex items-baseline gap-2 text-[12px] text-muted">
        <span className="mono text-ink">{r.repo}</span><span>{stars(r.stars)}★ {r.language ?? ''}{r.stars_per_day && r.stars_per_day >= 1 ? `, +${Math.round(r.stars_per_day)}/day` : r.rising ? ', rising' : ''}</span>
      </div>
      <Tip content={<div className="mono space-y-0.5">{Object.entries(r.breakdown).sort((a, b) => b[1] - a[1]).map(([k, v]) => <div key={k} className="flex justify-between gap-4"><span>{k}</span><span>{v.toFixed(1)}</span></div>)}</div>}>
        <span className="mono row-span-2 self-start text-[20px] font-medium leading-none text-signal">{r.score.toFixed(1)}</span>
      </Tip>
      <div className="truncate text-[14px] font-medium text-ink">#{r.number} {r.title}</div>
      <div className="col-start-2 flex items-center gap-2 overflow-hidden">
        {r.preview ? <span className="truncate text-[12.5px] text-muted">{r.preview}</span> : <span className="text-[12.5px] text-faint">no description</span>}
      </div>
      <div className="col-start-2 col-span-2 mt-1 flex flex-wrap gap-1">
        <Badge tone="ink">{catLabel(r.category)}</Badge>{r.kind && <Badge>{r.kind}</Badge>}{r.difficulty && <Badge>{r.difficulty}</Badge>}
        <Badge tone={STATE_TONE[r.state] ?? 'quiet'}>{label(r.state)}</Badge>{r.has_bounty ? <Badge tone="signal">bounty</Badge> : null}
      </div>
    </li>
  )
}
