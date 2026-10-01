import { useEffect, useState } from 'react'
import * as api from '../api'
import type { Status } from '../types'

const clock = (s: string | null) => (s ? s.replace(/^\S+ \S+ /, '').replace(/^\d{4}-\d{2}-\d{2} /, '').slice(0, 5) : null)

export function StatusStrip() {
  const [st, setSt] = useState<Status | null>(null)
  useEffect(() => {
    const load = () => api.getStatus().then(setSt).catch(() => {})
    load()
    const t = setInterval(load, 60000)
    return () => clearInterval(t)
  }, [])
  if (!st) return <p className="strip">Loading status.</p>
  const picks = (st.issues.shortlisted || 0) + (st.issues.analyzed || 0)
  const parts = [
    `${picks} top picks (${st.lanes.resume || 0} recognized, ${st.lanes.discover || 0} smaller and rising)`,
    st.issues.classified ? `${st.issues.classified.toLocaleString()} more classified` : null,
    st.last_run ? `last run ${clock(st.last_run)}` : 'no run yet',
    st.next_run ? `next ${clock(st.next_run)}` : null,
    `${st.runs_today} agent runs today, ${st.tokens_in_today >= 1e6 ? (st.tokens_in_today / 1e6).toFixed(1) + 'M' : Math.round(st.tokens_in_today / 1000) + 'k'} tokens in`,
  ].filter(Boolean)
  return <p className="strip">{parts.join('. ')}.</p>
}
