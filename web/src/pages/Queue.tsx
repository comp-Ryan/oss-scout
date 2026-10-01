import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import * as api from '../api'
import type { QueueRow } from '../types'
import { ScoreBadge } from '../components/ScoreBadge'
import { SettingsDrawer } from '../components/SettingsDrawer'
import { CATEGORIES, catLabel, label, stars } from '../labels'

const STATES = ['shortlisted', 'analyzed', 'discussing', 'plan_approved', 'pr_open', 'in_review', 'dismissed']  // drafting shares the 'working on' label with discussing
const DIFFS = ['small', 'medium', 'large']
const SORTS: [string, string][] = [['score', 'best score'], ['easiest', 'easiest first'], ['hardest', 'hardest first'], ['stars', 'most stars'], ['newest', 'newest activity']]
const PAGE = 60

export function Queue() {
  const [rows, setRows] = useState<QueueRow[] | null>(null)
  const [category, setCategory] = useState('')
  const [state, setState] = useState('')
  const [lane, setLane] = useState<'resume' | 'discover'>('resume')
  const [difficulty, setDifficulty] = useState('')
  const [sort, setSort] = useState('score')
  const [all, setAll] = useState(false)
  const [more, setMore] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const params = (offset: number) => ({ category: category || undefined, state: state || undefined, lane, difficulty: difficulty || undefined, sort, all, limit: PAGE, offset })
  const load = () => api.getQueue(params(0)).then(r => { setRows(r); setMore(r.length >= PAGE) }).catch(e => setError(String(e)))
  const loadMore = () => api.getQueue(params(rows?.length ?? 0)).then(r => { setRows(x => [...(x ?? []), ...r]); setMore(r.length >= PAGE) }).catch(e => setError(String(e)))
  useEffect(() => { setError(null); load() }, [category, state, lane, difficulty, sort, all])
  const chip = (text: string, active: boolean, onClick: () => void) => (
    <button key={text} className={active ? 'chip on' : 'chip'} onClick={onClick}>{text}</button>
  )
  const analyze = (r: QueueRow) => api.postAnalyze(r.id)
    .then(x => { setNotice(`Run ${x.run_id} started for ${r.repo}#${r.number}. Watch it on the Agents page.`); load() })
    .catch(e => setError(String(e)))
  const dismiss = (r: QueueRow) => api.postDismiss(r.id, '').then(load).catch(e => setError(String(e)))
  return (
    <div className="queue-page">
      <div className="lane-switch" role="group" aria-label="lane">
        <button className={lane === 'resume' ? 'lane on' : 'lane'} onClick={() => setLane('resume')}>Recognized repos</button>
        <button className={lane === 'discover' ? 'lane on' : 'lane'} onClick={() => setLane('discover')}>Smaller and rising</button>
        <SettingsDrawer onSaved={load} />
      </div>
      <div className="chips">
        {chip('all categories', category === '', () => setCategory(''))}
        {CATEGORIES.map(c => chip(catLabel(c), category === c, () => setCategory(category === c ? '' : c)))}
      </div>
      <div className="chips">
        {chip('active', state === '', () => setState(''))}
        {STATES.map(s => chip(label(s), state === s, () => setState(state === s ? '' : s)))}
      </div>
      <div className="chips tools">
        {chip('any difficulty', difficulty === '', () => setDifficulty(''))}
        {DIFFS.map(d => chip(d, difficulty === d, () => setDifficulty(difficulty === d ? '' : d)))}
        <label className="sort">sort <select aria-label="sort" value={sort} onChange={e => setSort(e.target.value)}>{SORTS.map(([v, t]) => <option key={v} value={v}>{t}</option>)}</select></label>
        {chip(all ? 'show top picks only' : 'show everything classified', all, () => setAll(a => !a))}
      </div>
      {error && <p className="error">{error}</p>}
      {notice && <p className="notice">{notice}</p>}
      {rows && rows.length === 0 && (
        <p className="empty">{all ? 'Nothing matches these filters.' : 'Nothing shortlisted here yet. Try "everything classified" to read past the top picks, or wait for the hourly run.'}</p>
      )}
      {rows && rows.length > 0 && (
        <ol className="cards">
          {rows.map(r => (
            <li key={r.id} className={`card cat-${r.category ?? 'other'}`}>
              <div className="card-main">
                <div className="repo-line">
                  <span className="repo">{r.repo}</span>
                  <span className="facts">{stars(r.stars)}★ {r.language ?? ''}{r.rising ? ', rising' : ''}{r.has_bounty ? ', bounty' : ''}</span>
                </div>
                <Link className="title" to={`/issues/${r.id}`}>#{r.number} {r.title}</Link>
                {r.preview && <p className="preview">{r.preview}</p>}
                <div className="tags">
                  <span className="tag cat">{catLabel(r.category)}</span>
                  {r.kind && <span className="tag">{r.kind}</span>}
                  {r.difficulty && <span className="tag">{r.difficulty}</span>}
                  <span className={`tag state ${r.state}`}>{label(r.state)}</span>
                </div>
              </div>
              <div className="card-side">
                <ScoreBadge total={r.score} breakdown={r.breakdown} />
                <div className="card-actions">
                  {['shortlisted', 'classified'].includes(r.state) && <button onClick={() => analyze(r)}>Analyze</button>}
                  {r.state === 'analyzed' && <Link className="btn" to={`/issues/${r.id}`}>Read brief</Link>}
                  <button className="quiet" onClick={() => dismiss(r)}>Dismiss</button>
                </div>
              </div>
            </li>
          ))}
        </ol>
      )}
      {rows && rows.length > 0 && more && <div className="actions center"><button className="quiet" onClick={loadMore}>Load more</button></div>}
    </div>
  )
}
