export type QueueRow = { id: string; rank: number; score: number; repo: string; number: number; title: string; url: string;
  state: string; category: string | null; kind: string | null; difficulty: string | null; has_bounty: number; breakdown: Record<string, number>;
  lane: string; preview: string; stars: number | null; language: string | null; rising: number; labels: string[] }
export type Status = { issues: Record<string, number>; lanes: Record<string, number>; last_run: string | null; next_run: string | null;
  runs_today: number; tokens_in_today: number; tokens_out_today: number; labels: Record<string, string>; board_order: string[] }
export type BoardCard = { id: string; repo: string; number: number; title: string; url: string; state: string; lane: string; stars: number | null; language: string | null; pr_url: string | null; label: string }
export type BoardData = { columns: { name: string; cards: BoardCard[] }[] }
export type Message = { id: number; role: 'user' | 'assistant'; content: string; at: string }
export type Run = { id: number; issue_id?: string; status: string; model: string; started_at: string; finished_at: string | null;
  duration_s: number | null; input_tokens: number | null; output_tokens: number | null; error: string | null; repo?: string; number?: number; title?: string }
export type RunEvent = { seq: number; at: string; event: any }
export type Brief = { summary: string; likely_cause: string; approach: string; files: string[]; effort: string; risks: string[]; duplicate_risk: string; ai_policy_note: string }
export type IssueDetail = { issue: any; repo: any; classification: Record<string, { choice: string; confidence: number | null }>;
  score: { total: number; breakdown: Record<string, number> } | null; brief: Brief | null; runs: Run[];
  transitions: { from_state: string; to_state: string; actor: string; note: string; at: string }[];
  thread: { conversation_id: string | null; plan: string | null } | null; messages: Message[]; draft?: Draft | null }
export type Draft = { worktree: string; branch: string; brief_path: string; test_cmd: string; pr_url: string | null; last_error: string | null }
export type Settings = { weights: Record<string, number>; category_values: Record<string, number> }
