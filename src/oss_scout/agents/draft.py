"""Draft PR flow. The only module that writes to GitHub: fork (prep), push and pr create (submit),
each behind an explicit user action. Commits are Ryan's; nothing here runs git commit."""
from __future__ import annotations
import json
import re
import subprocess
from pathlib import Path
from oss_scout import transitions
from oss_scout.agents import chat
from oss_scout.agents.analysis import latest_brief
from oss_scout.config import Config
from oss_scout.db import utcnow


class DraftError(Exception):
    pass


def default_shell(cmd, cwd=None, timeout=600):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout, r.stderr


def slug(title: str) -> str:
    words = re.findall(r"[a-z0-9]+", title.lower())
    s = "-".join(words)[:40].strip("-")
    return s or "issue"


def guess_test_cmd(language: str | None, contributing: str | None) -> str:
    text = (contributing or "").lower()
    if "pytest" in text or language == "Python":
        return "pytest"
    if language in ("TypeScript", "JavaScript"):
        return "npm test"
    return ""


def _run(shell, cmd, cwd=None, timeout=600):
    code, out, err = shell(cmd, cwd=cwd, timeout=timeout)
    if code != 0:
        raise DraftError(f"{' '.join(cmd)} failed: {(err or out)[-400:]}")
    return out


def github_user(cfg: Config, shell) -> str:
    if cfg.github_user:
        return cfg.github_user
    return _run(shell, ["gh", "api", "user", "--jq", ".login"]).strip()


def default_branch(repo: str, shell) -> str:
    out = _run(shell, ["gh", "repo", "view", repo, "--json", "defaultBranchRef"])
    return json.loads(out)["defaultBranchRef"]["name"]


def write_brief(path: Path, issue: dict, repo: dict, plan: str | None, brief: dict | None, test_cmd: str) -> None:
    files = ", ".join((brief or {}).get("files") or []) or "(none named)"
    approach = (brief or {}).get("approach") or "(no brief)"
    contributing = (repo.get("contributing_text") or "").strip()[:3000] or "(no contributing file found)"
    path.write_text(f"""# {issue['repo']}#{issue['number']}: {issue['title']}

{issue['url']}

## Agreed plan
{plan or '(no plan text; see the approach below)'}

## From the analysis brief
Approach: {approach}
Files: {files}
AI policy note: {(brief or {}).get('ai_policy_note') or 'none'}

## Rules
- This worktree is on a branch for a PR that will be opened as a DRAFT under Ryan's account.
- Commits are Ryan's. Nothing here pushes or opens a PR; `scout submit` does that on request.
- Test command: `{test_cmd or '(unknown; find it in the repo docs)'}`

## Issue body
{(issue.get('body') or '').strip()[:6000] or '(empty)'}

## Contributing notes (truncated)
{contributing}
""")


def get(conn, issue_id: str) -> dict | None:
    row = conn.execute("select * from drafts where issue_id = ?", (issue_id,)).fetchone()
    return dict(row) if row else None


def prep(conn, cfg: Config, issue_id: str, *, shell=default_shell, user: str | None = None) -> dict:
    issue = dict(conn.execute("select * from issues where id = ?", (issue_id,)).fetchone())
    if issue["state"] != "plan_approved":
        raise DraftError(f"issue is {issue['state']}, needs plan_approved")
    repo = dict(conn.execute("select * from repos where full_name = ?", (issue["repo"],)).fetchone())
    owner, name = issue["repo"].split("/", 1)
    user = user or github_user(cfg, shell)
    _run(shell, ["gh", "repo", "fork", issue["repo"], "--clone=false"])
    work = cfg.work_dir / f"{owner}__{name}"
    if not (work / ".git").exists():
        work.parent.mkdir(parents=True, exist_ok=True)
        _run(shell, ["git", "clone", "--filter=blob:none", f"https://github.com/{user}/{name}", str(work)], timeout=1800)
        _run(shell, ["git", "remote", "add", "upstream", f"https://github.com/{issue['repo']}"], cwd=str(work))
    base = default_branch(issue["repo"], shell)
    _run(shell, ["git", "fetch", "upstream", base], cwd=str(work), timeout=900)
    branch = f"scout/{issue['number']}-{slug(issue['title'])}"
    worktree = cfg.worktrees_dir / f"{owner}__{name}" / str(issue["number"])
    worktree.parent.mkdir(parents=True, exist_ok=True)
    if not worktree.exists():
        _run(shell, ["git", "worktree", "add", str(worktree), "-b", branch, f"upstream/{base}"], cwd=str(work))
    thread = chat.thread(conn, issue_id) or {}
    test_cmd = guess_test_cmd(repo.get("language"), repo.get("contributing_text"))
    brief_path = worktree / "BRIEF.md"
    write_brief(brief_path, issue, repo, thread.get("plan"), latest_brief(conn, issue_id), test_cmd)
    with conn:
        conn.execute("insert into drafts(issue_id, work_dir, worktree, branch, base_branch, brief_path, test_cmd, created_at) values (?,?,?,?,?,?,?,?) "
                     "on conflict(issue_id) do update set work_dir=excluded.work_dir, worktree=excluded.worktree, branch=excluded.branch, "
                     "base_branch=excluded.base_branch, brief_path=excluded.brief_path, test_cmd=excluded.test_cmd",
                     (issue_id, str(work), str(worktree), branch, base, str(brief_path), test_cmd, utcnow()))
    transitions.apply(conn, issue_id, "drafting", "user", f"worktree {worktree}")
    return get(conn, issue_id)


class TestsFailed(DraftError):
    pass


def _draft_or_error(conn, issue_id: str) -> tuple[dict, dict]:
    issue = dict(conn.execute("select * from issues where id = ?", (issue_id,)).fetchone())
    d = get(conn, issue_id)
    if issue["state"] != "drafting" or d is None:
        raise DraftError(f"issue is {issue['state']}; run prep first")
    return issue, d


def launch_command(worktree: str, brief_path: str) -> str:
    """Interactive Claude Code in the worktree with the brief as its opening prompt."""
    return f'cd {_q(worktree)} && claude "$(cat {_q(brief_path)})"'


def _q(s: str) -> str:
    return "'" + str(s).replace("'", "'\\''") + "'"


def work(conn, cfg: Config, issue_id: str, *, popen=subprocess.Popen, which=None) -> dict:
    import shutil
    which = which or shutil.which
    _, d = _draft_or_error(conn, issue_id)
    command = launch_command(d["worktree"], d["brief_path"])
    term = which("x-terminal-emulator")
    launched = False
    if term:
        popen(["x-terminal-emulator", "-e", "bash", "-c", f"{command}; exec bash"], cwd=d["worktree"])
        launched = True
    return {"launched": launched, "command": command, "cwd": d["worktree"]}


def work_agent(conn, cfg: Config, issue_id: str, *, shell=default_shell) -> str:
    """Headless alternative for small fixes: agy edits files in the worktree; Ryan still reviews and commits."""
    _, d = _draft_or_error(conn, issue_id)
    brief = Path(d["brief_path"]).read_text()
    prompt = ("Implement the agreed plan in this worktree. Edit files only; do not commit, push, or touch GitHub. "
              "Run the test command from the brief if it exists and fix what you broke.\n\n" + brief)
    _run(shell, ["agy", "--mode", "accept-edits", "--model", cfg.draft_model, "--output-format", "json",
                 "--disable-slash-commands", "--print", prompt], cwd=d["worktree"], timeout=3600)
    return _run(shell, ["git", "diff", "--stat"], cwd=d["worktree"])


def submit(conn, cfg: Config, issue_id: str, *, shell=default_shell, title: str | None = None, body: str | None = None) -> dict:
    issue, d = _draft_or_error(conn, issue_id)
    wt = d["worktree"]
    if _run(shell, ["git", "status", "--porcelain"], cwd=wt).strip():
        raise DraftError("worktree has uncommitted changes; commit them first")
    ahead = _run(shell, ["git", "rev-list", "--count", f"upstream/{d['base_branch']}..HEAD"], cwd=wt).strip()
    if ahead in ("", "0"):
        raise DraftError("no commits ahead of upstream; nothing to submit")
    tests = "skipped"
    if d.get("test_cmd"):
        code, out, err = shell(["bash", "-lc", d["test_cmd"]], cwd=wt, timeout=cfg.submit_test_timeout_s)
        if code != 0:
            output = (out + "\n" + err)[-4000:]
            with conn:
                conn.execute("update drafts set last_error = ? where issue_id = ?", (output, issue_id))
            transitions.apply(conn, issue_id, "discussing", "user", "tests failed")
            raise TestsFailed(output)
        tests = "passed"
    if not d.get("pushed_at"):
        _run(shell, ["git", "push", "-u", "origin", d["branch"]], cwd=wt, timeout=900)
        with conn:
            conn.execute("update drafts set pushed_at = ? where issue_id = ?", (utcnow(), issue_id))
    user = github_user(cfg, shell)
    thread = chat.thread(conn, issue_id) or {}
    title = title or issue["title"]
    body = body or f"{thread.get('plan') or ''}\n\nCloses #{issue['number']}"
    if f"#{issue['number']}" not in body:
        body = body.rstrip() + f"\n\nCloses #{issue['number']}"
    out = _run(shell, ["gh", "pr", "create", "--repo", issue["repo"], "--head", f"{user}:{d['branch']}", "--base", d["base_branch"],
                       "--draft", "--title", title, "--body", body], cwd=wt)
    pr_url = out.strip().splitlines()[-1].strip() if out.strip() else ""
    with conn:
        conn.execute("update drafts set pr_url = ?, submitted_at = ?, last_error = null where issue_id = ?", (pr_url, utcnow(), issue_id))
    transitions.apply(conn, issue_id, "pr_open", "user", pr_url)
    return {"pr_url": pr_url, "tests": tests, "branch": d["branch"]}
