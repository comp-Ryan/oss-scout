from __future__ import annotations
import json
import subprocess
from oss_scout.agents.analysis import latest_brief
from oss_scout.agents.clone import clone_dir
from oss_scout.config import Config
from oss_scout.db import utcnow

CONTRIBUTING_CHARS = 3000


class ChatError(Exception):
    pass


def first_turn_context(issue: dict, repo: dict, brief: dict | None, related: list[dict] | None) -> str:
    labels = issue.get("labels") or "[]"
    if isinstance(labels, str):
        labels = json.loads(labels)
    contributing = (repo.get("contributing_text") or "").strip()[:CONTRIBUTING_CHARS] or "(no contributing file found)"
    prs = "\n".join(f"- #{p['number']} [{p['state']}] {p.get('title') or ''}" for p in (related or [])) or "- none"
    brief_text = json.dumps(brief, indent=1) if brief else "(no brief yet)"
    return f"""You are helping Ryan, a solo outside contributor, decide how to fix a GitHub issue and agree on a plan.
Be concrete and short. You may read, list and search files in the current directory if it is a clone of the
repo (tools: view_file, grep_search, list_dir, find_by_name). Do not call run_command; do not edit files, use
git, or touch GitHub. Nothing is sent to GitHub from here.

Issue {issue['repo']}#{issue['number']}: {issue['title']}
URL: {issue.get('url')}
Labels: {', '.join(labels) or 'none'}
Repository: {issue['repo']} ({repo.get('stars') or 0} stars, {repo.get('language') or 'unknown language'})

Issue body:
{(issue.get('body') or '').strip()[:4000] or '(empty)'}

Analysis brief:
{brief_text}

Pull requests that reference this issue:
{prs}

Contributing docs (truncated):
{contributing}
"""


def agy_chat_command(model: str, prompt: str, conversation_id: str | None = None, clone_path: str | None = None) -> list[str]:
    cmd = ["agy", "--model", model, "--output-format", "json", "--disable-slash-commands"]
    if conversation_id:
        cmd += ["--conversation", conversation_id]
    if clone_path:
        cmd += ["--add-dir", clone_path]
    return cmd + ["--print", prompt]


def thread(conn, issue_id: str) -> dict | None:
    row = conn.execute("select * from threads where issue_id = ?", (issue_id,)).fetchone()
    return dict(row) if row else None


def messages(conn, issue_id: str) -> list[dict]:
    return [dict(r) for r in conn.execute("select * from messages where issue_id = ? order by id", (issue_id,))]


def set_plan(conn, issue_id: str, plan: str) -> None:
    with conn:
        conn.execute("insert into threads(issue_id, created_at, plan) values (?, ?, ?) on conflict(issue_id) do update set plan = excluded.plan",
                     (issue_id, utcnow(), plan))


def _store(conn, issue_id: str, role: str, content: str, usage: dict | None = None) -> dict:
    usage = usage or {}
    with conn:
        cur = conn.execute("insert into messages(issue_id, role, content, at, input_tokens, output_tokens) values (?,?,?,?,?,?)",
                           (issue_id, role, content, utcnow(), usage.get("input_tokens"), usage.get("output_tokens")))
    return dict(conn.execute("select * from messages where id = ?", (cur.lastrowid,)).fetchone())


def send(conn, cfg: Config, issue_id: str, content: str, *, runner=subprocess.run, related: list[dict] | None = None) -> dict:
    """One chat turn. The user message is stored before the agent runs, so a failure never loses it."""
    issue = dict(conn.execute("select * from issues where id = ?", (issue_id,)).fetchone())
    repo = dict(conn.execute("select * from repos where full_name = ?", (issue["repo"],)).fetchone())
    t = thread(conn, issue_id)
    if t is None:
        with conn:
            conn.execute("insert into threads(issue_id, created_at) values (?, ?)", (issue_id, utcnow()))
        t = thread(conn, issue_id)
    user_msg = _store(conn, issue_id, "user", content)
    clone_path = clone_dir(issue["repo"], cfg.clones_dir)
    clone_arg = str(clone_path) if (clone_path / ".git").exists() else None
    if t["conversation_id"]:
        prompt = content
    else:
        prompt = first_turn_context(issue, repo, latest_brief(conn, issue_id), related) + f"\nRyan: {content}"
    cmd = agy_chat_command(cfg.chat_model, prompt, t["conversation_id"], clone_arg)
    cwd = clone_arg or str(cfg.data_dir)
    try:
        r = runner(cmd, cwd=cwd, capture_output=True, text=True, timeout=cfg.chat_timeout_s)
    except subprocess.TimeoutExpired as e:
        raise ChatError(f"chat turn timed out after {cfg.chat_timeout_s}s") from e
    if r.returncode != 0:
        raise ChatError(f"agy failed: {(r.stderr or r.stdout)[-400:]}")
    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError as e:
        raise ChatError(f"unreadable agy output: {r.stdout[-200:]}") from e
    if data.get("status") != "SUCCESS":
        raise ChatError(f"agy status {data.get('status')}")
    if not t["conversation_id"] and data.get("conversation_id"):
        with conn:
            conn.execute("update threads set conversation_id = ? where issue_id = ?", (data["conversation_id"], issue_id))
    assistant = _store(conn, issue_id, "assistant", (data.get("response") or "").strip(), data.get("usage"))
    return {"user": user_msg, "assistant": assistant}
