from __future__ import annotations
import json
from rich.table import Table

ACTIVE_STATES = ("shortlisted", "analyzed", "discussing", "plan_approved", "drafting")

PREVIEW_CHARS = 200

SQL = """
select i.id, i.repo, i.number, i.title, i.url, i.state, i.has_bounty, i.lane, i.body, i.labels, i.updated_at, i.comments,
       r.stars, r.language, r.rising,
       (select total from scores s where s.issue_id = i.id order by s.id desc limit 1) as score,
       (select breakdown from scores s where s.issue_id = i.id order by s.id desc limit 1) as breakdown,
       (select choice from classifications c where c.issue_id = i.id and c.question='category' order by c.id desc limit 1) as category,
       (select choice from classifications c where c.issue_id = i.id and c.question='kind' order by c.id desc limit 1) as kind,
       (select choice from classifications c where c.issue_id = i.id and c.question='difficulty' order by c.id desc limit 1) as difficulty
from issues i join repos r on r.full_name = i.repo
"""


def preview(body: str | None) -> str:
    text = " ".join((body or "").split())
    return text[:PREVIEW_CHARS] + ("…" if len(text) > PREVIEW_CHARS else "")


def queue_rows(conn, category: str | None, state: str | None, limit: int, lane: str | None = None) -> list[dict]:
    if state:
        sql, params = SQL + " where i.state = ?", [state]
    else:
        sql, params = SQL + f" where i.state in ({','.join('?' * len(ACTIVE_STATES))})", list(ACTIVE_STATES)
    if lane:
        sql += " and i.lane = ?"
        params.append(lane)
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    for r in rows:
        r["preview"] = preview(r.pop("body", None))
        try:
            r["labels"] = json.loads(r.get("labels") or "[]")
        except (TypeError, ValueError):
            r["labels"] = []
    if category:
        rows = [r for r in rows if r["category"] == category]
    rows = [r for r in rows if r["score"] is not None]
    rows.sort(key=lambda r: -r["score"])
    out = []
    for i, r in enumerate(rows[:limit], 1):
        r["rank"] = i
        r["breakdown"] = json.loads(r["breakdown"] or "{}")
        out.append(r)
    return out


def render_table(rows: list[dict]) -> Table:
    t = Table(title="OSS Scout queue", expand=False)
    for col in ("#", "score", "repo", "issue", "cat", "kind", "diff", "state"):
        t.add_column(col, no_wrap=True, overflow="ellipsis")
    t.add_column("title", no_wrap=True, overflow="ellipsis", max_width=60)
    for r in rows:
        t.add_row(str(r["rank"]), f"{r['score']:.1f}", r["repo"], f"#{r['number']}", r.get("category") or "?",
                  r.get("kind") or "?", r.get("difficulty") or "?", r["state"], (r["title"] or "")[:70])
    return t
