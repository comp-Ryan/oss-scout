import { useEffect, useRef, useState } from 'react'
import { useRuns } from '@/lib/queries'
import * as api from '@/lib/api'
import type { RunEvent } from '@/lib/types'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'

function describe(e: RunEvent): string {
  const ev = e.event
  if (ev?.event === 'step_update') {
    const s = ev.step_update || {}
    if (s.step_type === 'tool') return `tool ${s.tool_name || ''} ${s.state || ''} ${s.tool_info?.parameters ? JSON.stringify(s.tool_info.parameters).slice(0, 160) : ''}`
    if (s.text_delta) return `text ${String(s.text_delta).slice(0, 160)}`
    return `${s.step_type || 'step'} ${s.state || ''}`
  }
  if (ev?.event === 'result') return `result ${ev.result?.status || ''} in=${ev.result?.usage?.input_tokens ?? '?'} out=${ev.result?.usage?.output_tokens ?? '?'}`
  if (ev?.event === 'init') return `init model=${ev.init?.model || ''}`
  if (ev?.raw) return `raw ${String(ev.raw).slice(0, 160)}`
  return JSON.stringify(ev).slice(0, 160)
}

export function Agents({ pollMs = 2000 }: { pollMs?: number }) {
  const { data: runs = [] } = useRuns()
  const [sel, setSel] = useState<number | null>(null)
  const [events, setEvents] = useState<RunEvent[]>([])
  const [status, setStatus] = useState('')
  const after = useRef(0)
  useEffect(() => {
    if (sel === null) return
    let alive = true; after.current = 0; setEvents([])
    const tick = async () => {
      if (!alive) return
      try {
        const r = await api.getEvents(sel, after.current)
        if (!alive) return
        if (r.events.length) { after.current = r.events[r.events.length - 1].seq; setEvents(ev => [...ev, ...r.events]) }
        setStatus(r.status)
        if (r.status === 'running') setTimeout(tick, pollMs)
      } catch { if (alive) setTimeout(tick, pollMs * 2) }
    }
    tick()
    return () => { alive = false }
  }, [sel, pollMs])
  const today = new Date().toISOString().slice(0, 10)
  const todays = runs.filter(r => r.started_at.startsWith(today))
  const sum = (k: 'input_tokens' | 'output_tokens') => todays.reduce((a, r) => a + (r[k] || 0), 0)
  return (
    <div className="grid h-full grid-cols-[380px_1fr] max-[900px]:grid-cols-1">
      <div className="overflow-auto border-r border-line">
        <p className="border-b border-line px-3 py-2 text-[12.5px] text-muted">Today: {todays.length} runs, {sum('input_tokens').toLocaleString()} tokens in, {sum('output_tokens').toLocaleString()} out.</p>
        {runs.length === 0 && <p className="p-3 text-muted">No runs yet. Press a on an issue, or run scout analyze.</p>}
        {runs.map(r => (
          <button key={r.id} onClick={() => setSel(r.id)} className={cn('block w-full border-b border-line px-3 py-2 text-left', sel === r.id && 'bg-raised')}>
            <div className="flex items-center gap-2"><Badge tone={r.status === 'done' ? 'teal' : r.status === 'failed' ? 'bad' : 'signal'}>{r.status}</Badge><span className="mono text-[12.5px]">{r.repo}#{r.number}</span></div>
            <div className="mt-0.5 text-[12px] text-muted">{(r.duration_s || 0).toFixed(0)}s, {r.input_tokens || 0} in, {r.output_tokens || 0} out, {r.model}</div>
            {r.error && <div className="mt-0.5 text-[12px] text-bad">{r.error}</div>}
          </button>
        ))}
      </div>
      <div className="min-h-0 overflow-auto p-3">
        {sel === null ? <p className="text-muted">Select a run to follow its events.</p> : <p className="mb-2 text-[12.5px] text-muted">run {sel}, {status}{status === 'running' ? ', polling' : ''}</p>}
        <div className="mono space-y-0.5 text-[12px]">{events.map(e => <div key={e.seq} className="border-b border-dashed border-line py-0.5 break-words">{e.seq}. {describe(e)}</div>)}</div>
      </div>
    </div>
  )
}
