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
