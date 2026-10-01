import { useState } from 'react'

export function ScoreBadge({ total, breakdown }: { total: number; breakdown: Record<string, number> }) {
  const [open, setOpen] = useState(false)
  return (
    <span className="score" onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)}>
      <span className="score-total">{total.toFixed(1)}</span>
      {open && (
        <span className="score-pop">
          {Object.entries(breakdown).sort((a, b) => b[1] - a[1]).map(([k, v]) => (
            <span key={k} className="score-row"><span>{k}</span><span>{v.toFixed(1)}</span></span>
          ))}
        </span>
      )}
    </span>
  )
}
