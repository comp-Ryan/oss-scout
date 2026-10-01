from __future__ import annotations
import json
import re
from rich.table import Table

ACTIVE_STATES = ("shortlisted", "analyzed", "discussing", "plan_approved", "drafting")

PREVIEW_CHARS = 200

SQL = """
select i.id, i.repo, i.number, i.title, i.url, i.state, i.has_bounty, i.lane, i.body, i.labels, i.updated_at, i.comments,
       r.stars, r.language, r.rising, r.stars_per_day,
       (select total from scores s where s.issue_id = i.id order by s.id desc limit 1) as score,
       (select breakdown from scores s where s.issue_id = i.id order by s.id desc limit 1) as breakdown,
       (select choice from classifications c where c.issue_id = i.id and c.question='category' order by c.id desc limit 1) as category,
       (select choice from classifications c where c.issue_id = i.id and c.question='kind' order by c.id desc limit 1) as kind,
       (select choice from classifications c where c.issue_id = i.id and c.question='difficulty' order by c.id desc limit 1) as difficulty
from issues i join repos r on r.full_name = i.repo
"""


_COMMENT = re.compile(r"<!--.*?-->", re.S)
_FENCE = re.compile(r"```.*?```", re.S)
_NO_RESPONSE = re.compile(r"^\s*_?no response_?\s*$", re.I)
_SKIP_LINE = re.compile(r"^\s*(#{1,6}\s|[-*]\s*\[[ xX]\]|\[[ xX]\]|!\[|<[a-z]|\|)")  # headings, checklists, images, html, tables
_LABEL_LINE = re.compile(r"^\s*\*\*[^*]+\*\*:?\s*$")  # bold-only lines like **Describe the bug**


def preview(body: str | None) -> str:
    """First real prose of an issue body: no HTML comments, template headings, checklists, images or tables."""
    text = _FENCE.sub("", _COMMENT.sub("", body or ""))
    kept = []
    for line in text.splitlines():
        if not line.strip() or _SKIP_LINE.match(line) or _LABEL_LINE.match(line) or _NO_RESPONSE.match(line):
            continue
        kept.append(line.strip())
        if sum(len(k) for k in kept) > PREVIEW_CHARS * 2:
            break
    out = " ".join(" ".join(kept).split())
    return out[:PREVIEW_CHARS] + ("…" if len(out) > PREVIEW_CHARS else "")


SCORED_STATES = ACTIVE_STATES + ("classified",)
DIFF_RANK = {"small": 0, "medium": 1, "large": 2}
SORTS = ("score", "easiest", "hardest", "stars", "newest", "rising")


def queue_rows(conn, category: str | None, state: str | None, limit: int, lane: str | None = None, *,
               difficulty: str | None = None, sort: str = "score", offset: int = 0, all_scored: bool = False) -> list[dict]:
    """Ranked rows. all_scored widens from the shortlist to every classified issue, so there is always more to read."""
    if state:
        sql, params = SQL + " where i.state = ?", [state]
    else:
        states = SCORED_STATES if all_scored else ACTIVE_STATES
        sql, params = SQL + f" where i.state in ({','.join('?' * len(states))})", list(states)
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
    if difficulty:
        rows = [r for r in rows if r["difficulty"] == difficulty]
    rows = [r for r in rows if r["score"] is not None]
    if sort == "easiest":
        rows.sort(key=lambda r: (DIFF_RANK.get(r["difficulty"], 9), -r["score"]))
    elif sort == "hardest":
        rows.sort(key=lambda r: (-DIFF_RANK.get(r["difficulty"], -1), -r["score"]))
    elif sort == "stars":
        rows.sort(key=lambda r: (-(r["stars"] or 0), -r["score"]))
    elif sort == "newest":
        rows.sort(key=lambda r: ((r["updated_at"] or ""), r["score"]), reverse=True)
    elif sort == "rising":
        rows.sort(key=lambda r: (-(r["stars_per_day"] or 0), -r["score"]))
    else:
        rows.sort(key=lambda r: -r["score"])
    out = []
    for i, r in enumerate(rows[offset:offset + limit], offset + 1):
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
