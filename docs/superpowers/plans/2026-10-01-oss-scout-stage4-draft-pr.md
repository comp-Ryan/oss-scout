# OSS Scout Stage 4: Draft PR Flow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** From an approved plan to a draft PR under Ryan's account: prepare a fork, work clone, branch and worktree with a BRIEF.md; launch Claude Code there; submit by running tests, pushing to the fork and opening a draft PR, all on explicit user actions.

**Architecture:** `agents/draft.py` holds `prep`, `launch_command`, `work`, `submit`, every git/gh call routed through one injected `shell(cmd, cwd, timeout) -> (code, out, err)`. Schema v4 adds `drafts`. CLI and API expose the three actions; the harvester learns to close the loop on `pr_open`. The issue page shows the state-specific actions.

**Tech Stack:** git 2.53 partial clone and worktrees, `gh` 2.97 (`repo fork`, `repo view`, `pr create`, `pr view`), `claude` 2.1 CLI, `x-terminal-emulator`.

**Spec:** `oss-scout/docs/superpowers/specs/2026-10-01-oss-scout-design.md` Section 11.

## Global Constraints

- GitHub writes happen only inside `draft.prep` (fork), `draft.submit` (push, pr create), each triggered by a user action. No other module may call `gh repo fork`, `git push`, or `gh pr create`.
- Commits are Ryan's: the bot never runs `git commit` (the `--agent` path runs agy in accept-edits mode, which edits files; Ryan still commits).
- Verbatim from the spec: partial clone `--filter=blob:none` (never `--depth`), branch `scout/<N>-<slug>`, worktree `data/worktrees/<owner>__<repo>/<N>`, work clone `data/work/<owner>__<repo>`, submit test timeout 900 s, `--draft` on every PR, body ends with `Closes #N`.
- No test runs a real git, gh, claude, agy or terminal.

## Review Focus

1. A repo whose fork already exists under the user: `gh repo fork` prints "already exists" and exits 0; prep must continue. Test in Task 2.
2. A slug from a title with only punctuation or non-ASCII (e.g. the Chinese title in the queue): branch name must still be valid (`scout/504-issue`). Test in Task 2.
3. Submit when `test_cmd` is empty (unknown stack): skip tests, say so in the result, still require a clean worktree. Test in Task 4.
4. Submit after a push that succeeded but `gh pr create` failed (network): the branch is on the fork; a second submit must not push again blindly but must still create the PR. Store `pushed_at` and skip the push when set. Test in Task 4.
5. Launch when `x-terminal-emulator` is missing: return the command with `launched: false` and no exception. Test in Task 3.

---

### Task 1: Schema v4 and config

**Files:** `src/oss_scout/db.py`, `src/oss_scout/config.py`, `tests/test_db.py`

- [ ] Test: `test_schema_v4_drafts` asserts table `drafts` with columns `issue_id, work_dir, worktree, branch, base_branch, brief_path, test_cmd, pr_url, created_at, pushed_at, submitted_at, last_error` and version `"4"`; bump existing `"3"` assertions to `"4"`.
- [ ] Implement `SCHEMA_V4`, `SCHEMA_VERSION = "4"`; config adds `github_user: str = ""` (empty means ask `gh api user`), `draft_model: str = "claude-sonnet-4-6"`, `submit_test_timeout_s: float = 900.0`, properties `work_dir = data_dir / "work"`, `worktrees_dir = data_dir / "worktrees"`.
- [ ] `uv run pytest -q` green; commit `oss-scout: schema v4 drafts table`.

---

### Task 2: prep

**Files:** `src/oss_scout/agents/draft.py`, `tests/test_draft.py`

**Interfaces:**
- `draft.slug(title) -> str` (lowercase, `[a-z0-9]+` joined by `-`, max 40 chars, `issue` when empty)
- `draft.guess_test_cmd(language, contributing_text) -> str` (`pytest` if contributing mentions pytest or language is Python; `npm test` for TypeScript/JavaScript; else `""`)
- `draft.write_brief(path, issue, repo, plan, brief, test_cmd)`
- `draft.prep(conn, cfg, issue_id, *, shell, user=None) -> dict` (the drafts row). `shell(cmd: list[str], cwd: str | None = None, timeout: float = 600) -> tuple[int, str, str]`. Raises `draft.DraftError` with the failing command's stderr.

- [ ] Tests (write first, watch fail):

```python
# tests/test_draft.py
import json
from pathlib import Path
import pytest
from oss_scout import db, transitions
from oss_scout.agents import draft
from oss_scout.config import Config

def _seed(conn, state="plan_approved", plan="Do X then Y", language="Python", contributing="Run pytest before a PR."):
    conn.execute("insert into repos(full_name, stars, language, contributing_text, ai_policy) values ('up/repo', 5, ?, ?, 'none')", (language, contributing))
    conn.execute("insert into issues(id, repo, number, title, body, labels, url, state, harvested_at, last_seen_at) values ('I1','up/repo',42,'Fix: the Thing!','B','[]','https://github.com/up/repo/issues/42',?,'x','x')", (state,))
    conn.execute("insert into agent_runs(issue_id, model, status, started_at, brief) values ('I1','m','done','x','{\"summary\":\"S\",\"approach\":\"A\",\"files\":[\"core.py\"]}')")
    conn.execute("insert into threads(issue_id, created_at, plan) values ('I1','x',?)", (plan,))
    conn.commit()

class Shell:
    """Records commands; scripted results by command prefix."""
    def __init__(self, results=None): self.calls = []; self.results = results or {}
    def __call__(self, cmd, cwd=None, timeout=600):
        self.calls.append((list(cmd), cwd))
        for prefix, res in self.results.items():
            if cmd[: len(prefix)] == list(prefix):
                return res if not callable(res) else res(cmd, cwd)
        if cmd[:3] == ["gh", "repo", "view"]:
            return 0, json.dumps({"defaultBranchRef": {"name": "main"}}), ""
        if cmd[:2] == ["git", "worktree"]:
            Path(cmd[3]).mkdir(parents=True, exist_ok=True)
        if cmd[:2] == ["git", "clone"]:
            (Path(cmd[-1]) / ".git").mkdir(parents=True, exist_ok=True)
        return 0, "", ""

def test_slug_and_test_cmd():
    assert draft.slug("Fix: the Thing!") == "fix-the-thing"
    assert draft.slug("【推荐内容】合集") == "issue"
    assert len(draft.slug("x " * 100)) <= 40
    assert draft.guess_test_cmd("Python", None) == "pytest"
    assert draft.guess_test_cmd("TypeScript", "") == "npm test"
    assert draft.guess_test_cmd("Rust", "") == ""

def test_prep_forks_clones_fetches_worktree_brief_and_transitions(tmp_path):
    conn = db.connect(tmp_path / "t.db"); _seed(conn)
    sh = Shell()
    cfg = Config(data_dir=tmp_path, github_user="ryan")
    row = draft.prep(conn, cfg, "I1", shell=sh)
    cmds = [c for c, _ in sh.calls]
    assert ["gh", "repo", "fork", "up/repo", "--clone=false"] in cmds
    clone = [c for c in cmds if c[:2] == ["git", "clone"]][0]
    assert "--filter=blob:none" in clone and "--depth" not in " ".join(clone) and clone[-2] == "https://github.com/ryan/repo"
    assert ["git", "remote", "add", "upstream", "https://github.com/up/repo"] in cmds
    assert ["git", "fetch", "upstream", "main"] in cmds
    wt = [c for c in cmds if c[:2] == ["git", "worktree"]][0]
    assert wt[2:] == ["add", str(tmp_path / "worktrees" / "up__repo" / "42"), "-b", "scout/42-fix-the-thing", "upstream/main"]
    assert row["branch"] == "scout/42-fix-the-thing" and row["base_branch"] == "main" and row["test_cmd"] == "pytest"
    brief = Path(row["brief_path"]).read_text()
    assert "Do X then Y" in brief and "core.py" in brief and "#42" in brief and "None" not in brief and "pytest" in brief
    assert conn.execute("select state from issues where id='I1'").fetchone()[0] == "drafting"

def test_prep_is_idempotent_on_existing_fork_and_clone(tmp_path):
    conn = db.connect(tmp_path / "t.db"); _seed(conn)
    (tmp_path / "work" / "up__repo" / ".git").mkdir(parents=True)
    sh = Shell({("gh", "repo", "fork"): (0, "ryan/repo already exists", "")})
    draft.prep(conn, Config(data_dir=tmp_path, github_user="ryan"), "I1", shell=sh)
    cmds = [c for c, _ in sh.calls]
    assert not any(c[:2] == ["git", "clone"] for c in cmds)
    assert ["git", "fetch", "upstream", "main"] in cmds

def test_prep_requires_plan_approved_and_reports_failing_command(tmp_path):
    conn = db.connect(tmp_path / "t.db"); _seed(conn, state="discussing")
    with pytest.raises(draft.DraftError):
        draft.prep(conn, Config(data_dir=tmp_path, github_user="ryan"), "I1", shell=Shell())
    conn2 = db.connect(tmp_path / "t2.db"); _seed(conn2)
    sh = Shell({("git", "fetch"): (128, "", "fatal: could not read")})
    with pytest.raises(draft.DraftError, match="could not read"):
        draft.prep(conn2, Config(data_dir=tmp_path / "d2", github_user="ryan"), "I1", shell=sh)
    assert conn2.execute("select state from issues where id='I1'").fetchone()[0] == "plan_approved"
```

- [ ] Implement `draft.py` (prep part):

```python
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
- This worktree is on branch for a PR that will be opened as a DRAFT under Ryan's account.
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
                     "on conflict(issue_id) do update set work_dir=excluded.work_dir, worktree=excluded.worktree, branch=excluded.branch, base_branch=excluded.base_branch, brief_path=excluded.brief_path, test_cmd=excluded.test_cmd",
                     (issue_id, str(work), str(worktree), branch, base, str(brief_path), test_cmd, utcnow()))
    transitions.apply(conn, issue_id, "drafting", "user", f"worktree {worktree}")
    return get(conn, issue_id)
```

- [ ] Green; commit `oss-scout: draft prep (fork, partial clone, worktree, BRIEF.md)`.

---

### Task 3: work (launch Claude Code; headless agent option)

**Interfaces:** `draft.launch_command(worktree, brief_path) -> list[str]` = `["claude", <brief text>]` run in the worktree; `draft.work(conn, cfg, issue_id, *, popen=subprocess.Popen, which=shutil.which) -> dict(launched: bool, command: str, cwd: str)`; `draft.work_agent(conn, cfg, issue_id, *, shell) -> str` runs `agy --mode accept-edits --model <draft_model> --output-format json --print <brief>` in the worktree and returns `git diff --stat`.

- [ ] Tests: `test_work_spawns_terminal_in_worktree` (popen called with `["x-terminal-emulator", "-e", "bash", "-c", <cmd>]`, cwd worktree, the command contains `claude` and `BRIEF.md`); `test_work_without_terminal_returns_command_unlaunched` (which returns None → `launched False`, no popen); `test_work_agent_runs_agy_in_worktree_and_returns_diffstat` (shell sees agy with `--mode accept-edits` and `--print` last; returns the diff stat string); `test_work_requires_drafting`.
- [ ] Implement; green; commit `oss-scout: draft work (terminal launch of Claude Code; headless agy option)`.

---

### Task 4: submit

**Interfaces:** `draft.submit(conn, cfg, issue_id, *, shell, title=None, body=None) -> dict(pr_url, tests: "passed"|"skipped", ...)`; raises `DraftError` for dirty worktree / no commits; on test failure stores `last_error`, transitions `drafting -> discussing` and raises `draft.TestsFailed(output)`.

- [ ] Tests: dirty worktree refused (`git status --porcelain` non-empty → DraftError, no push); no commits ahead (`git rev-list --count upstream/main..HEAD` = 0 → DraftError); tests fail → `discussing`, `last_error` has output, no push; happy path → commands in order: status, rev-list, test_cmd via `bash -lc`, `git push -u origin <branch>`, `gh pr create --repo up/repo --head ryan:scout/42-fix-the-thing --base main --draft --title ... --body ...`, body ends with `Closes #42`, `pr_url` stored, state `pr_open`; empty `test_cmd` → `tests: "skipped"`; push already recorded (`pushed_at` set) → no second push, PR still created.
- [ ] Implement; green; commit `oss-scout: draft submit (tests, push to fork, draft PR)`.

---

### Task 5: CLI, API, harvester close-out

- [ ] CLI: `scout prep ISSUE`, `scout work ISSUE [--agent]`, `scout submit ISSUE [--title] [--body]`; tests via CliRunner with `draft` functions monkeypatched.
- [ ] API: `POST /api/issues/{id}/prep`, `POST /api/issues/{id}/launch`, `POST /api/issues/{id}/submit {title, body}`, `GET /api/issues/{id}` now includes `draft`. `create_app` takes `shell=` and `popen=` for injection. Tests: prep 409 when not plan_approved; submit 409 with the test output when tests fail; happy path returns `pr_url`.
- [ ] Harvester: for issues in `pr_open` with a `drafts.pr_url`, `gh pr view <url> --json state,merged` (through the injected shell, default `default_shell`) and transition to `merged` or `closed`. Test with a fake shell.
- [ ] Green; commit.

---

### Task 6: Issue page actions

- [ ] Tests (`Issue.test.tsx`): `plan_approved` shows **Prepare worktree** and calls `postPrep`; `drafting` shows the worktree path, a command box, **Open in Claude Code** (calls `postLaunch`), and a submit form prefilled with the issue title and the plan that calls `postSubmit(id, title, body)`; `pr_open` shows the PR link; a 409 from submit renders its text.
- [ ] Implement `api.ts` additions and the `Issue.tsx` action block; green; build; commit.

---

### Task 7: README and close-out

- [ ] README "Draft PR flow" section: the three actions, where worktrees live, what is and is not pushed, the first-run note that forking is Ryan's action, and the `--agent` option.
- [ ] No live fork/push/PR is run by the implementer. Record in the ledger that the live path is untested by design and is Ryan's to exercise.
- [ ] Commit; ledger.
