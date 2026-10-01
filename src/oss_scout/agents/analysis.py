from __future__ import annotations
import json
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from oss_scout import transitions
from oss_scout.agents import clone
from oss_scout.config import Config
from oss_scout.db import utcnow

CONTRIBUTING_CHARS = 4000
BODY_CHARS = 4000

BRIEF_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["summary", "likely_cause", "approach", "files", "effort", "risks", "duplicate_risk", "ai_policy_note"],
    "properties": {
        "summary": {"type": "string"}, "likely_cause": {"type": "string"}, "approach": {"type": "string"},
        "files": {"type": "array", "items": {"type": "string"}},
        "effort": {"type": "string", "enum": ["small", "medium", "large"]},
        "risks": {"type": "array", "items": {"type": "string"}},
        "duplicate_risk": {"type": "string", "enum": ["none", "possible", "likely"]},
        "ai_policy_note": {"type": "string"},
    },
}


def build_prompt(issue: dict, repo: dict, related: list[dict]) -> str:
    labels = issue.get("labels") or "[]"
    if isinstance(labels, str):
        labels = json.loads(labels)
    contributing = (repo.get("contributing_text") or "").strip()
    contributing = contributing[:CONTRIBUTING_CHARS] if contributing else "(no contributing file found)"
    prs = "\n".join(
        f"- #{p['number']} [{p['state']}] {p.get('title') or ''}{' (would close this issue)' if p.get('will_close') else ''}"
        for p in related) or "- none"
    body = (issue.get("body") or "").strip()[:BODY_CHARS] or "(empty)"
    return f"""You are analyzing a GitHub issue for a solo outside contributor who is deciding whether to work on it.
You are READ-ONLY: you may list, search and read files in the current directory (a shallow clone of the repo).
Do not run build, test, install or network commands. Do not edit files. Do not use git.

Issue {issue['repo']}#{issue['number']}: {issue['title']}
URL: {issue.get('url')}
Labels: {', '.join(labels) or 'none'}
Repository: {issue['repo']} ({repo.get('stars') or 0} stars, {repo.get('language') or 'unknown language'})
AI policy detected in contributing docs: {repo.get('ai_policy') or 'none'}

Issue body:
{body}

Pull requests that reference this issue:
{prs}

Contributing docs (truncated):
{contributing}

Produce the brief as JSON matching the schema: summary (2-3 sentences), likely_cause, approach (the concrete
change you would make), files (repo-relative paths you expect to touch, found by reading the code), effort
(small: one file, under two hours; medium: a few files, about a day; large: design work or multiple days),
risks, duplicate_risk (none/possible/likely, judged from the referenced pull requests), ai_policy_note (what
the contributing docs say about AI-assisted contributions, or "none")."""


def agy_command(model: str, schema_path: str, clone_path: str, prompt: str) -> list[str]:
    # --print consumes the next argument as the prompt, so it stays last.
    return ["agy", "--model", model, "--output-format", "stream-json", "--json-schema", schema_path,
            "--add-dir", clone_path, "--disable-slash-commands", "--print", prompt]


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def runs_started_today(conn, now: datetime) -> int:
    day = now.astimezone(timezone.utc).date().isoformat()
    return conn.execute("select count(*) from agent_runs where kind='analysis' and started_at >= ? and started_at <= ?",
                        (f"{day}T00:00:00Z", f"{day}T23:59:59Z")).fetchone()[0]


def attempts(conn, issue_id: str) -> int:
    return conn.execute("select count(*) from agent_runs where kind='analysis' and issue_id = ?", (issue_id,)).fetchone()[0]


def latest_brief(conn, issue_id: str) -> dict | None:
    row = conn.execute("select brief from agent_runs where issue_id = ? and status='done' order by id desc limit 1", (issue_id,)).fetchone()
    return json.loads(row[0]) if row and row[0] else None


def _finish(conn, run_id: int, status: str, **fields) -> None:
    sets = ", ".join(f"{k} = ?" for k in fields)
    with conn:
        conn.execute(f"update agent_runs set status = ?, finished_at = ?{', ' + sets if sets else ''} where id = ?",
                     (status, utcnow(), *fields.values(), run_id))


def run_one(conn, cfg: Config, gh, issue_id: str, *, popen=subprocess.Popen, cloner=clone.ensure_clone,
            now: datetime | None = None) -> int:
    """One analysis run. Agent failures are recorded on the run row, never raised."""
    now = now or datetime.now(timezone.utc)
    issue = dict(conn.execute("select * from issues where id = ?", (issue_id,)).fetchone())
    repo = dict(conn.execute("select * from repos where full_name = ?", (issue["repo"],)).fetchone())
    with conn:
        cur = conn.execute(
            "insert into agent_runs(issue_id, kind, model, status, attempt, started_at) values (?, 'analysis', ?, 'running', ?, ?)",
            (issue_id, cfg.analysis_model, attempts(conn, issue_id) + 1, _iso(now)))
        run_id = cur.lastrowid
    try:
        related = gh.related_prs(issue_id)
        clone_path = cloner(issue["repo"], cfg.clones_dir)
    except Exception as e:
        _finish(conn, run_id, "failed", error=str(e))
        return run_id
    prompt = build_prompt(issue, repo, related)
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(BRIEF_SCHEMA, f)
        schema_path = f.name
    cmd = agy_command(cfg.analysis_model, schema_path, str(clone_path), prompt)
    t0 = time.monotonic()
    try:
        proc = popen(cmd, cwd=str(clone_path), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except Exception as e:
        _finish(conn, run_id, "failed", error=f"could not start agy: {e}")
        return run_id
    result = None
    seq = 0
    for line in proc.stdout:
        line = line.rstrip("\n")
        if not line:
            continue
        seq += 1
        with conn:
            conn.execute("insert into agent_events(run_id, seq, at, event) values (?,?,?,?)", (run_id, seq, utcnow(), line))
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(ev, dict) and ev.get("event") == "result":
            result = ev.get("result") or {}
        if time.monotonic() - t0 > cfg.analysis_timeout_s:
            break
    remaining = max(1.0, cfg.analysis_timeout_s - (time.monotonic() - t0))
    try:
        proc.wait(timeout=remaining)
    except subprocess.TimeoutExpired:
        proc.kill()
        _finish(conn, run_id, "failed", error=f"timeout after {cfg.analysis_timeout_s}s", duration_s=time.monotonic() - t0)
        return run_id
    duration = time.monotonic() - t0
    if not result or result.get("status") != "SUCCESS" or not isinstance(result.get("structured_output"), dict):
        status = (result or {}).get("status", "no result event")
        _finish(conn, run_id, "failed", error=f"agent result status: {status}", duration_s=duration,
                conversation_id=(result or {}).get("conversation_id"))
        return run_id
    usage = result.get("usage") or {}
    brief = result["structured_output"]
    _finish(conn, run_id, "done", brief=json.dumps(brief), duration_s=duration, conversation_id=result.get("conversation_id"),
            input_tokens=usage.get("input_tokens"), output_tokens=usage.get("output_tokens"))
    transitions.apply(conn, issue_id, "analyzed", f"agent:{run_id}", f"effort {brief.get('effort')}")
    return run_id


@dataclass
class AnalysisSummary:
    started: int = 0
    done: int = 0
    failed: int = 0
    skipped_cap: int = 0
    skipped_attempts: int = 0


def run_pending(conn, cfg: Config, gh, limit: int | None = None, *, now: datetime | None = None, **inject) -> AnalysisSummary:
    now = now or datetime.now(timezone.utc)
    s = AnalysisSummary()
    rows = conn.execute(
        "select i.id, (select total from scores sc where sc.issue_id = i.id order by sc.id desc limit 1) as total "
        "from issues i where i.state = 'shortlisted' order by total desc").fetchall()
    for row in (rows[:limit] if limit else rows):
        if attempts(conn, row["id"]) >= cfg.analysis_max_attempts:
            s.skipped_attempts += 1
            continue
        if runs_started_today(conn, now) >= cfg.analysis_daily_cap:
            s.skipped_cap += 1
            continue
        s.started += 1
        rid = run_one(conn, cfg, gh, row["id"], now=now, **inject)
        status = conn.execute("select status from agent_runs where id = ?", (rid,)).fetchone()[0]
        if status == "done":
            s.done += 1
        else:
            s.failed += 1
    return s
