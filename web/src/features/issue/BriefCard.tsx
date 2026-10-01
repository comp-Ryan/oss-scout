import type { Brief } from '@/lib/types'
import { Badge } from '@/components/ui/badge'

function Field({ name, children }: { name: string; children: React.ReactNode }) {
  return <div className="mb-3"><div className="mb-0.5 text-[12px] font-medium text-faint">{name}</div><div className="max-w-[72ch] text-[13.5px]">{children}</div></div>
}
export function BriefCard({ brief }: { brief: Brief }) {
  return (
    <div>
      <Field name="Summary">{brief.summary}</Field>
      <Field name="Likely cause">{brief.likely_cause}</Field>
      <Field name="Approach">{brief.approach}</Field>
      <Field name="Files">{brief.files.length ? <div className="flex flex-wrap gap-1">{brief.files.map(f => <code key={f} className="rounded-sm bg-raised px-1.5 py-0.5 text-[12px]">{f}</code>)}</div> : <span className="text-faint">none named</span>}</Field>
      <Field name="Effort, duplicate risk, AI policy"><div className="flex gap-1"><Badge tone="ink">{brief.effort}</Badge><Badge>duplicate {brief.duplicate_risk}</Badge><Badge>AI policy {brief.ai_policy_note}</Badge></div></Field>
      {brief.risks.length > 0 && <Field name="Risks"><ul className="list-disc pl-5">{brief.risks.map((r, i) => <li key={i}>{r}</li>)}</ul></Field>}
    </div>
  )
}
