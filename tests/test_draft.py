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

def _prepped(tmp_path, **kw):
    conn = db.connect(tmp_path / "t.db"); _seed(conn)
    cfg = Config(data_dir=tmp_path, github_user="ryan", **kw)
    draft.prep(conn, cfg, "I1", shell=Shell())
    return conn, cfg

def test_work_spawns_terminal_in_worktree(tmp_path):
    conn, cfg = _prepped(tmp_path)
    spawned = {}
    def popen(cmd, **kw): spawned["cmd"] = cmd; spawned["cwd"] = kw.get("cwd"); return object()
    out = draft.work(conn, cfg, "I1", popen=popen, which=lambda n: "/usr/bin/x-terminal-emulator")
    assert out["launched"] is True and spawned["cmd"][:2] == ["x-terminal-emulator", "-e"]
    assert spawned["cwd"] == str(tmp_path / "worktrees" / "up__repo" / "42")
    assert "claude" in out["command"] and "BRIEF.md" in out["command"]

def test_work_without_terminal_returns_command_unlaunched(tmp_path):
    conn, cfg = _prepped(tmp_path)
    out = draft.work(conn, cfg, "I1", popen=lambda *a, **k: (_ for _ in ()).throw(AssertionError("no spawn")), which=lambda n: None)
    assert out["launched"] is False and "claude" in out["command"]

def test_work_agent_runs_agy_in_worktree_and_returns_diffstat(tmp_path):
    conn, cfg = _prepped(tmp_path)
    sh = Shell({("agy",): (0, json.dumps({"status": "SUCCESS"}), ""), ("git", "diff"): (0, " core.py | 2 +-\n", "")})
    stat = draft.work_agent(conn, cfg, "I1", shell=sh)
    agy = [c for c, cwd in sh.calls if c[0] == "agy"][0]
    cwd = [cwd for c, cwd in sh.calls if c[0] == "agy"][0]
    assert agy[agy.index("--mode") + 1] == "accept-edits" and agy[-2] == "--print" and "Do X then Y" in agy[-1]
    assert cwd == str(tmp_path / "worktrees" / "up__repo" / "42") and "core.py" in stat

def test_work_requires_drafting(tmp_path):
    conn = db.connect(tmp_path / "t.db"); _seed(conn)
    with pytest.raises(draft.DraftError):
        draft.work(conn, Config(data_dir=tmp_path), "I1", popen=lambda *a, **k: None, which=lambda n: None)

def _submit_shell(status="", ahead="2", test=(0, "ok", ""), push=(0, "", ""), pr=(0, "https://github.com/up/repo/pull/7\n", "")):
    return Shell({("git", "status"): (0, status, ""), ("git", "rev-list"): (0, ahead + "\n", ""),
                  ("bash", "-lc"): test, ("git", "push"): push, ("gh", "pr", "create"): pr})

def test_submit_refuses_dirty_or_unpushed_worktree(tmp_path):
    conn, cfg = _prepped(tmp_path)
    with pytest.raises(draft.DraftError, match="uncommitted"):
        draft.submit(conn, cfg, "I1", shell=_submit_shell(status=" M core.py"))
    with pytest.raises(draft.DraftError, match="no commits"):
        draft.submit(conn, cfg, "I1", shell=_submit_shell(ahead="0"))
    assert conn.execute("select state from issues where id='I1'").fetchone()[0] == "drafting"

def test_submit_test_failure_goes_back_to_discussing_without_push(tmp_path):
    conn, cfg = _prepped(tmp_path)
    sh = _submit_shell(test=(1, "FAILED test_x", ""))
    with pytest.raises(draft.TestsFailed, match="FAILED test_x"):
        draft.submit(conn, cfg, "I1", shell=sh)
    assert not any(c[:2] == ["git", "push"] for c, _ in sh.calls)
    assert conn.execute("select state from issues where id='I1'").fetchone()[0] == "discussing"
    assert "FAILED test_x" in draft.get(conn, "I1")["last_error"]

def test_submit_happy_path_pushes_and_opens_draft_pr(tmp_path):
    conn, cfg = _prepped(tmp_path)
    sh = _submit_shell()
    out = draft.submit(conn, cfg, "I1", shell=sh)
    cmds = [c for c, _ in sh.calls]
    test_cmd = [c for c in cmds if c[:2] == ["bash", "-lc"]][0]
    assert test_cmd[2] == "pytest"
    assert ["git", "push", "-u", "origin", "scout/42-fix-the-thing"] in cmds
    pr = [c for c in cmds if c[:3] == ["gh", "pr", "create"]][0]
    assert "--draft" in pr and pr[pr.index("--repo") + 1] == "up/repo" and pr[pr.index("--head") + 1] == "ryan:scout/42-fix-the-thing" and pr[pr.index("--base") + 1] == "main"
    assert pr[pr.index("--title") + 1] == "Fix: the Thing!" and pr[pr.index("--body") + 1].rstrip().endswith("Closes #42")
    assert out["pr_url"] == "https://github.com/up/repo/pull/7" and out["tests"] == "passed"
    assert conn.execute("select state from issues where id='I1'").fetchone()[0] == "pr_open"
    assert draft.get(conn, "I1")["pushed_at"] and draft.get(conn, "I1")["submitted_at"]

def test_submit_skips_tests_when_unknown_and_does_not_push_twice(tmp_path):
    conn = db.connect(tmp_path / "t.db"); _seed(conn, language="Rust", contributing="")
    cfg = Config(data_dir=tmp_path, github_user="ryan")
    draft.prep(conn, cfg, "I1", shell=Shell())
    sh = _submit_shell(pr=(1, "", "network down"))
    with pytest.raises(draft.DraftError, match="network down"):
        draft.submit(conn, cfg, "I1", shell=sh)
    assert draft.get(conn, "I1")["pushed_at"] is not None
    sh2 = _submit_shell()
    out = draft.submit(conn, cfg, "I1", shell=sh2)
    assert out["tests"] == "skipped" and not any(c[:2] == ["git", "push"] for c, _ in sh2.calls)
    assert out["pr_url"].endswith("/pull/7")
