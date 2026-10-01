import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import * as api from '../api'
import type { QueueRow } from '../types'
import { ScoreBadge } from '../components/ScoreBadge'

const CATEGORIES = ['quant_trading', 'prediction_markets_finance', 'ai_agents_llm', 'data_scraping', 'dev_tools_cli', 'web_frontend', 'other']
const STATES = ['shortlisted', 'analyzed', 'discussing', 'plan_approved', 'drafting', 'pr_open', 'classified', 'dismissed']

export function Queue() {
  const [rows, setRows] = useState<QueueRow[] | null>(null)
  const [category, setCategory] = useState('')
  const [state, setState] = useState('')
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    setError(null)
    api.getQueue({ category: category || undefined, state: state || undefined, limit: 100 }).then(setRows).catch(e => setError(String(e)))
  }, [category, state])
  const chip = (label: string, active: boolean, onClick: () => void) => (
    <button key={label} className={active ? 'chip active' : 'chip'} onClick={onClick}>{label}</button>
  )
  return (
    <div>
      <div className="chips">
        {chip('all categories', category === '', () => setCategory(''))}
        {CATEGORIES.map(c => chip(c, category === c, () => setCategory(category === c ? '' : c)))}
      </div>
      <div className="chips">
        {chip('active', state === '', () => setState(''))}
        {STATES.map(s => chip(s, state === s, () => setState(state === s ? '' : s)))}
      </div>
      {error && <p className="error">{error}</p>}
      {rows && rows.length === 0 && <p className="muted">Nothing shortlisted yet. Run <code>scout run</code> or widen the filters.</p>}
      {rows && rows.length > 0 && (
        <table className="queue">
          <thead><tr><th>#</th><th>score</th><th>repo</th><th>issue</th><th>cat</th><th>kind</th><th>diff</th><th>state</th><th>title</th></tr></thead>
          <tbody>
            {rows.map(r => (
              <tr key={r.id}>
                <td>{r.rank}</td>
                <td><ScoreBadge total={r.score} breakdown={r.breakdown} /></td>
                <td>{r.repo}</td>
                <td><a href={r.url} target="_blank" rel="noreferrer">#{r.number}</a></td>
                <td>{r.category ?? '?'}</td><td>{r.kind ?? '?'}</td><td>{r.difficulty ?? '?'}</td>
                <td><span className={`state ${r.state}`}>{r.state}</span>{r.has_bounty ? ' 💎' : ''}</td>
                <td><Link to={`/issues/${r.id}`}>{r.title}</Link></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
