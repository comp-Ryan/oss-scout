import { CATEGORIES, catLabel, label } from '@/lib/labels'
import { cn } from '@/lib/utils'
import { Select } from '@/components/ui/select'
import { useQueueParams } from './useQueueParams'

const STATES = ['shortlisted', 'analyzed', 'discussing', 'plan_approved', 'pr_open', 'in_review', 'dismissed']
const DIFFS = ['small', 'medium', 'large']
const SORTS = [['score', 'best score'], ['easiest', 'easiest first'], ['hardest', 'hardest first'], ['stars', 'most stars'], ['newest', 'newest activity']]

function Group({ title, children }: { title: string; children: React.ReactNode }) {
  return <section className="mb-4"><h3 className="mb-1.5 text-[12px] font-medium text-faint">{title}</h3>{children}</section>
}
function Row({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button onClick={onClick} className={cn('flex w-full items-center gap-2 rounded-sm px-2 py-1 text-left text-[13px]', active ? 'bg-raised text-ink' : 'text-muted hover:text-ink')}>
      <span className={cn('h-1.5 w-1.5 rounded-full', active ? 'bg-signal' : 'bg-line')} aria-hidden />{children}
    </button>
  )
}

export function FilterRail() {
  const { get, set, toggle, params } = useQueueParams()
  const lane = params.lane
  return (
    <div className="px-2 py-3">
      <Group title="Lane">
        <Row active={lane === 'resume'} onClick={() => set({ lane: null })}>Recognized repos</Row>
        <Row active={lane === 'discover'} onClick={() => set({ lane: 'discover' })}>Smaller and rising</Row>
      </Group>
      <Group title="Category">
        <Row active={!get('category')} onClick={() => set({ category: null })}>all categories</Row>
        {CATEGORIES.map(c => <Row key={c} active={get('category') === c} onClick={() => toggle('category', c)}>{catLabel(c)}</Row>)}
      </Group>
      <Group title="Difficulty">
        <div className="flex gap-1 px-1">
          {DIFFS.map(d => (
            <button key={d} onClick={() => toggle('difficulty', d)} className={cn('rounded-sm border px-2 py-0.5 text-[12px]', get('difficulty') === d ? 'border-ink text-ink' : 'border-line text-muted hover:text-ink')}>{d}</button>
          ))}
        </div>
      </Group>
      <Group title="Order">
        <div className="px-1"><Select label="sort" value={params.sort!} onValueChange={v => set({ sort: v === 'score' ? null : v })} options={SORTS.map(([v, l]) => ({ value: v, label: l }))} /></div>
      </Group>
      <Group title="Scope">
        <Row active={!params.all} onClick={() => set({ all: null })}>top picks</Row>
        <Row active={!!params.all} onClick={() => set({ all: '1' })}>everything classified</Row>
      </Group>
      <Group title="State">
        <Row active={!get('state')} onClick={() => set({ state: null })}>active</Row>
        {STATES.map(s => <Row key={s} active={get('state') === s} onClick={() => toggle('state', s)}>{label(s)}</Row>)}
      </Group>
    </div>
  )
}
