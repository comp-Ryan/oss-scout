export const STATE_LABEL: Record<string, string> = {
  harvested: 'found', classified: 'classified', shortlisted: 'top picks', analyzed: 'brief ready', discussing: 'working on',
  plan_approved: 'plan agreed', drafting: 'working on', pr_open: 'PR opened', in_review: 'progressing', merged: 'accepted',
  closed: 'closed', dismissed: 'dismissed', stale: 'taken',
}
export const CATEGORY_LABEL: Record<string, string> = {
  quant_trading: 'quant trading', prediction_markets_finance: 'prediction markets', ai_agents_llm: 'AI agents',
  data_scraping: 'data tools', dev_tools_cli: 'dev tools', web_frontend: 'web frontend', other: 'other',
}
export const CATEGORIES = Object.keys(CATEGORY_LABEL)
export const label = (s: string | null | undefined) => (s ? STATE_LABEL[s] ?? s.replace(/_/g, ' ') : '')
export const catLabel = (c: string | null | undefined) => (c ? CATEGORY_LABEL[c] ?? c.replace(/_/g, ' ') : 'uncategorized')
export const stars = (n: number | null | undefined) => (n == null ? '' : n >= 1000 ? `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k` : String(n))
