import type { Brief } from '../types'

export function BriefCard({ brief }: { brief: Brief }) {
  return (
    <div className="card">
      <h4>Summary</h4><p>{brief.summary}</p>
      <h4>Likely cause</h4><p>{brief.likely_cause}</p>
      <h4>Approach</h4><p>{brief.approach}</p>
      <h4>Files</h4><p>{brief.files.length ? brief.files.map(f => <code key={f} className="tag">{f}</code>) : <span className="muted">none named</span>}</p>
      <h4>Effort · duplicate risk · AI policy</h4>
      <p><span className="tag">{brief.effort}</span><span className="tag">duplicate: {brief.duplicate_risk}</span><span className="tag">AI policy: {brief.ai_policy_note}</span></p>
      {brief.risks.length > 0 && (<><h4>Risks</h4><ul>{brief.risks.map((r, i) => <li key={i}>{r}</li>)}</ul></>)}
    </div>
  )
}
