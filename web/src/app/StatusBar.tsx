import { NavLink } from 'react-router-dom'
import { useStatus } from '@/lib/queries'
import { cn } from '@/lib/utils'

const clock = (s: string | null) => (s ? s.replace(/^\S+ \S+ /, '').replace(/^\d{4}-\d{2}-\d{2} /, '').slice(0, 5) : null)

export function StatusBar() {
  const { data: st } = useStatus()
  const picks = st ? (st.issues.shortlisted || 0) + (st.issues.analyzed || 0) : null
  const sentence = st ? [
    `${picks} picks (${st.lanes.resume || 0} recognized, ${st.lanes.discover || 0} smaller and rising)`,
    st.issues.classified ? `${st.issues.classified.toLocaleString()} more classified` : null,
    st.last_run ? `last run ${clock(st.last_run)}` : 'no run yet',
    st.next_run ? `next ${clock(st.next_run)}` : null,
    `${st.runs_today} agent runs today, ${st.tokens_in_today >= 1e6 ? (st.tokens_in_today / 1e6).toFixed(1) + 'M' : Math.round(st.tokens_in_today / 1000) + 'k'} tokens in`,
  ].filter(Boolean).join('. ') + '.' : 'Loading status.'
  const link = ({ isActive }: { isActive: boolean }) => cn('border-b-2 pb-0.5 text-[13px]', isActive ? 'border-signal text-ink' : 'border-transparent text-muted hover:text-ink')
  return (
    <header className="flex flex-wrap items-baseline gap-x-5 gap-y-1 border-b border-line px-4 py-2.5">
      <span className="text-[15px] font-semibold tracking-tight">OSS Scout</span>
      <nav className="flex gap-4"><NavLink to="/" end className={link}>Queue</NavLink><NavLink to="/board" className={link}>Board</NavLink><NavLink to="/agents" className={link}>Agents</NavLink></nav>
      <p className="m-0 text-[12.5px] text-muted">{sentence}</p>
      <kbd className="ml-auto hidden rounded-sm border border-line px-1.5 text-[11px] text-faint md:inline">Ctrl K</kbd>
    </header>
  )
}
