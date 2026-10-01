import { useEffect, useRef, useState } from 'react'
import * as api from '../api'
import type { Run, RunEvent } from '../types'

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
  const [runs, setRuns] = useState<Run[]>([])
  const [sel, setSel] = useState<number | null>(null)
  const [events, setEvents] = useState<RunEvent[]>([])
  const [status, setStatus] = useState<string>('')
  const after = useRef(0)
  useEffect(() => {
    const load = () => api.getRuns().then(setRuns).catch(() => {})
    load()
    const t = setInterval(load, 10000)
    return () => clearInterval(t)
  }, [])
  useEffect(() => {
    if (sel === null) return
    let alive = true
    after.current = 0
    setEvents([])
    const tick = async () => {
      if (!alive) return
      try {
        const r = await api.getEvents(sel, after.current)
        if (!alive) return
        if (r.events.length) {
          after.current = r.events[r.events.length - 1].seq
          setEvents(ev => [...ev, ...r.events])
        }
        setStatus(r.status)
        if (r.status === 'running') setTimeout(tick, pollMs)
      } catch {
        if (alive) setTimeout(tick, pollMs * 2)
      }
    }
    tick()
    return () => { alive = false }
  }, [sel, pollMs])
  const today = new Date().toISOString().slice(0, 10)
  const todays = runs.filter(r => r.started_at.startsWith(today))
  const sum = (k: 'input_tokens' | 'output_tokens') => todays.reduce((a, r) => a + (r[k] || 0), 0)
  return (
    <div>
      <p className="muted">Today: {todays.length} runs, {sum('input_tokens').toLocaleString()} tokens in, {sum('output_tokens').toLocaleString()} out.</p>
      <div className="runs">
        <div className="runlist">
          {runs.length === 0 && <p className="muted">No runs yet. Run <code>scout analyze</code>.</p>}
          {runs.map(r => (
            <div key={r.id} className={sel === r.id ? 'sel' : ''} onClick={() => setSel(r.id)}>
              <span className={`tag state ${r.status}`}>{r.status}</span> <strong>{r.repo}#{r.number}</strong> <span className="muted">{(r.duration_s || 0).toFixed(0)}s, {r.input_tokens || 0} in, {r.output_tokens || 0} out, {r.model}</span>
              {r.error && <div className="error">{r.error}</div>}
            </div>
          ))}
        </div>
        <div>
          {sel !== null && <p className="muted">run {sel} · {status}{status === 'running' ? ' · polling' : ''}</p>}
          <div className="events">
            {events.map(e => <div key={e.seq}>{e.seq}. {describe(e)}</div>)}
          </div>
        </div>
      </div>
    </div>
  )
}
