import { Link } from 'react-router-dom'
import { useBoard, useMove } from '@/lib/queries'
import { stars } from '@/lib/labels'

export function Board() {
  const { data: b, error, isLoading } = useBoard()
  const move = useMove()
  if (error) return <p className="p-4 text-bad">{String(error)}</p>
  if (isLoading || !b) return <p className="p-4 text-muted">Loading.</p>
  const names = b.columns.map(c => c.name)
  const total = b.columns.reduce((n, c) => n + c.cards.length, 0)
  const doMove = (id: string, title: string, column: string, hasPr: boolean) => {
    let pr: string | undefined
    if (column === 'PR opened' && !hasPr) { const v = window.prompt(`Link to the PR you opened for "${title}"`); if (!v) return; pr = v }
    move.mutate([id, column, pr])
  }
  return (
    <div className="p-3">
      {total === 0 && <p className="max-w-[48ch] p-2 text-muted">Nothing on the board yet. Analyze an issue on the queue and it lands in Picked.</p>}
      <div className="grid grid-cols-[repeat(5,minmax(230px,1fr))] gap-3 overflow-x-auto pb-2">
        {b.columns.map(col => (
          <section key={col.name}>
            <h2 className="mb-2 flex justify-between text-[13px] font-semibold">{col.name}<span className="font-normal text-muted">{col.cards.length}</span></h2>
            {col.cards.map(c => (
              <article key={c.id} className="mb-2 rounded-md border border-line bg-surface p-2.5">
                <div className="flex items-baseline justify-between text-[12px] text-muted"><span className="mono text-ink">{c.repo}</span><span>{stars(c.stars)}★</span></div>
                <Link to={`/?issue=${c.id}`} className="my-1 block text-[13.5px] font-medium leading-snug hover:underline">#{c.number} {c.title}</Link>
                {c.pr_url && <a className="mono mb-1 block truncate text-[11.5px] text-violet" href={c.pr_url} target="_blank" rel="noreferrer">{c.pr_url.replace('https://github.com/', '')}</a>}
                <select aria-label={`move ${c.title}`} value={col.name} onChange={e => doMove(c.id, c.title, e.target.value, !!c.pr_url)}
                  className="mt-1 w-full rounded-md border border-line bg-bg px-2 py-1 text-[12.5px] text-muted">
                  {names.map(n => <option key={n} value={n}>{n === col.name ? `In ${n}` : `Move to ${n}`}</option>)}
                </select>
              </article>
            ))}
          </section>
        ))}
      </div>
    </div>
  )
}
