import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import * as api from '../api'
import type { BoardData } from '../types'
import { stars } from '../labels'

export function Board() {
  const [b, setB] = useState<BoardData | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const load = () => api.getBoard().then(setB).catch(e => setErr(String(e)))
  useEffect(() => { load() }, [])
  const move = (id: string, title: string, column: string, hasPr: boolean) => {
    let pr: string | undefined
    if (column === 'PR opened' && !hasPr) {
      const v = window.prompt(`Link to the PR you opened for "${title}"`)
      if (!v) return
      pr = v
    }
    api.postMove(id, column, pr).then(load).catch(e => setErr(String(e)))
  }
  if (err) return <p className="error">{err}</p>
  if (!b) return <p className="muted">Loading.</p>
  const names = b.columns.map(c => c.name)
  const total = b.columns.reduce((n, c) => n + c.cards.length, 0)
  return (
    <div className="board-page">
      {total === 0 && <p className="empty">Nothing on the board yet. Analyze an issue on the Queue and it lands in Picked.</p>}
      <div className="board">
        {b.columns.map(col => (
          <section key={col.name} className="column">
            <h2>{col.name} <span className="count">{col.cards.length}</span></h2>
            {col.cards.map(c => (
              <article key={c.id} className="bcard">
                <div className="repo-line"><span className="repo">{c.repo}</span><span className="facts">{stars(c.stars)}★</span></div>
                <Link className="title" to={`/issues/${c.id}`}>#{c.number} {c.title}</Link>
                {c.pr_url && <a className="pr" href={c.pr_url} target="_blank" rel="noreferrer">{c.pr_url.replace('https://github.com/', '')}</a>}
                <label className="move">
                  <span className="sr">move {c.title}</span>
                  <select aria-label={`move ${c.title}`} value={col.name} onChange={e => move(c.id, c.title, e.target.value, !!c.pr_url)}>
                    {names.map(n => <option key={n} value={n}>{n === col.name ? `In ${n}` : `Move to ${n}`}</option>)}
                  </select>
                </label>
              </article>
            ))}
          </section>
        ))}
      </div>
    </div>
  )
}
