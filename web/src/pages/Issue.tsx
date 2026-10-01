import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import * as api from '../api'
import type { IssueDetail, Message } from '../types'
import { BriefCard } from '../components/BriefCard'
import { Chat } from '../components/Chat'
import { ScoreBadge } from '../components/ScoreBadge'

const TERMINAL = ['dismissed', 'stale', 'merged', 'closed']

export function Issue() {
  const { id = '' } = useParams()
  const [d, setD] = useState<IssueDetail | null>(null)
  const [plan, setPlan] = useState('')
  const [err, setErr] = useState<string | null>(null)
  const load = () => api.getIssue(id).then(x => { setD(x); setPlan(p => p || x.thread?.plan || x.brief?.approach || '') }).catch(e => setErr(String(e)))
  useEffect(() => { load() }, [id])
  if (err) return <p className="error">{err}</p>
  if (!d) return <p className="muted">Loading…</p>
  const state: string = d.issue.state
  const act = (fn: () => Promise<any>) => () => fn().then(load).catch(e => setErr(String(e)))
  const onSend = async (content: string): Promise<Message[]> => {
    const r = await api.postMessage(id, content)
    return [r.user, r.assistant]
  }
  return (
    <div className="issue">
      <header>
        <h2><a href={d.issue.url} target="_blank" rel="noreferrer">{d.issue.repo}#{d.issue.number}</a> {d.issue.title}</h2>
        <div className="meta">
          <span className={`state ${state}`}>{state}</span>
          {d.score && <ScoreBadge total={d.score.total} breakdown={d.score.breakdown} />}
          <span>{d.repo.stars}★ {d.repo.language}</span>
          {Object.entries(d.classification).map(([q, a]) => <span key={q} className="tag">{q}: {a.choice}</span>)}
        </div>
        <div className="actions">
          {state === 'analyzed' && <button onClick={act(() => api.postDiscuss(id))}>Discuss</button>}
          {state === 'discussing' && (
            <div className="plan">
              <label htmlFor="plan">Plan</label>
              <textarea id="plan" value={plan} onChange={e => setPlan(e.target.value)} rows={4} />
              <div><button onClick={act(() => api.postApprove(id, plan))}>Approve plan</button></div>
            </div>
          )}
          {!TERMINAL.includes(state) && <button className="danger" onClick={act(() => api.postDismiss(id, ''))}>Dismiss</button>}
        </div>
      </header>
      <section className="two-col">
        <div>
          <h3>Brief</h3>
          {d.brief ? <BriefCard brief={d.brief} /> : <p className="muted">No brief yet. Runs so far: {d.runs.length}.</p>}
          <details><summary>Issue body</summary><pre className="body">{d.issue.body}</pre></details>
          <details><summary>History</summary><ul>{d.transitions.map((t, i) => <li key={i}>{t.at} {t.from_state} → {t.to_state} ({t.actor}) {t.note}</li>)}</ul></details>
        </div>
        <div>
          <h3>Discussion</h3>
          <Chat initial={d.messages} onSend={onSend} />
        </div>
      </section>
    </div>
  )
}
