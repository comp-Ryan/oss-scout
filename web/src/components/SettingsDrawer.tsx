import { useEffect, useState } from 'react'
import * as api from '../api'
import type { Settings } from '../types'
import { catLabel } from '../labels'

const WEIGHT_HELP: Record<string, string> = {
  recognition: 'how famous the repo is', mergeability: 'how often outsiders get merged', language_fit: 'Python first, TypeScript second',
  category: 'your categories', difficulty_fit: 'small over large', freshness: 'recently active', bounty: 'has a bounty label', ai_policy: 'penalty when AI help is discouraged',
}

export function SettingsDrawer({ onSaved }: { onSaved: () => void }) {
  const [open, setOpen] = useState(false)
  const [s, setS] = useState<Settings | null>(null)
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => { if (open && !s) api.getSettings().then(setS).catch(e => setErr(String(e))) }, [open])
  const save = () => {
    if (!s) return
    setSaving(true); setErr(null)
    api.putSettings(s).then(x => { setS(x); setSaving(false); onSaved() }).catch(e => { setErr(String(e)); setSaving(false) })
  }
  const slider = (group: 'weights' | 'category_values', k: string, max: number, help: string) => (
    <label key={k} className="slider">
      <span>{group === 'weights' ? k.replace(/_/g, ' ') : catLabel(k)} <small>{help}</small></span>
      <input type="range" min={0} max={max} step={0.1} value={s![group][k]} onChange={e => setS({ ...s!, [group]: { ...s![group], [k]: Number(e.target.value) } })} />
      <output>{s![group][k].toFixed(1)}</output>
    </label>
  )
  return (
    <div className="settings">
      <button className="quiet" onClick={() => setOpen(o => !o)} aria-expanded={open}>{open ? 'Close weights' : 'Weights'}</button>
      {open && (
        <div className="drawer">
          {!s && !err && <p className="muted">Loading.</p>}
          {err && <p className="error">{err}</p>}
          {s && (
            <>
              <h3>Score weights</h3>
              {Object.keys(s.weights).map(k => slider('weights', k, 5, WEIGHT_HELP[k] || ''))}
              <h3>Category value</h3>
              {Object.keys(s.category_values).map(k => slider('category_values', k, 1, ''))}
              <div className="actions"><button onClick={save} disabled={saving}>{saving ? 'Saving and re-scoring' : 'Save and re-score'}</button></div>
            </>
          )}
        </div>
      )}
    </div>
  )
}
