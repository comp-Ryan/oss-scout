# OSS Scout Stage 2: Analysis Agents Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** For each shortlisted issue, run one Antigravity CLI (`agy`) analysis on Gemini Flash in a shallow clone, stream its events into SQLite, store the structured brief, and move the issue to `analyzed`.

**Architecture:** A `runs` layer (schema v2: `agent_runs`, `agent_events`), a clone helper, an analysis module that builds the prompt and schema, launches `agy` through an injected process factory, stores every stream line as it arrives, and parses the final `result` event. The CLI gets `analyze`, `runs`, `brief`; `scout run` calls analyze after scoring under a daily cap.

**Tech Stack:** Python 3.12, `subprocess.Popen`, git partial clone, `agy` 1.2.14 print mode with `--output-format stream-json --json-schema`.

**Spec:** `oss-scout/docs/superpowers/specs/2026-10-01-oss-scout-design.md` Section 9 (and Sections 4, 5 for tables and states).

## Global Constraints

- Agents never write to GitHub and never modify the clone: the prompt says read-only; the process runs with cwd set to the clone and no `--mode accept-edits`.
- `agy` arguments: `--print` is the last flag, immediately followed by the prompt string (verified 2026-10-01: `--print` consumes the next argument).
- Every state change goes through `transitions.apply`; analysis moves `shortlisted -> analyzed` with actor `agent:<run_id>` only.
- Caps verbatim from the spec: `analysis_daily_cap` 30 runs started per UTC day, `analysis_timeout_s` 600, two attempts per issue, clone timeout 180 s, contributing text truncated to 4,000 characters, clone filter `--depth 1 --filter=blob:limit=200k`.
- No test launches a real `agy` or `git`; process and cloner are injected. The recorded transcript is `tests/fixtures/agy_stream_probe.jsonl`.

## Review Focus

1. A stream line that is not valid JSON (agy prints a warning to stdout): the run must store it raw and keep reading, not crash. Test in Task 4.
2. A `result` event with `status != SUCCESS` or no `structured_output`: run is `failed` with the status in `error`; issue stays `shortlisted`. Test in Task 4.
3. A clone directory that exists but is not a git repo (half-finished clone after a crash): cloner must remove it and clone again. Test in Task 3.
4. A repo whose contributing text is `None`: the prompt says "no contributing file found", never the string "None". Test in Task 4.
5. The daily cap counts runs started today in UTC, including failed ones, so a crash loop cannot burn the budget. Test in Task 4.

---

### Task 1: Schema version 2

**Files:**
- Modify: `src/oss_scout/db.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Produces: tables `agent_runs(id, issue_id, kind, model, status, attempt, started_at, finished_at, conversation_id, input_tokens, output_tokens, duration_s, brief, error)` and `agent_events(id, run_id, seq, at, event)`. `meta.schema_version` becomes `"2"`. `migrate` stays idempotent and upgrades a v1 database in place.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_db.py
def test_schema_v2_tables_and_version(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    names = {r[0] for r in conn.execute("select name from sqlite_master where type='table'")}
    assert {"agent_runs", "agent_events"} <= names
    assert conn.execute("select value from meta where key='schema_version'").fetchone()[0] == "2"

def test_v1_database_upgrades_in_place(tmp_path):
    import sqlite3
    raw = sqlite3.connect(tmp_path / "old.db")
    raw.executescript(db.SCHEMA_V1)
    raw.execute("insert into meta(key, value) values ('schema_version', '1')"); raw.commit(); raw.close()
    conn = db.connect(tmp_path / "old.db")
    assert conn.execute("select value from meta where key='schema_version'").fetchone()[0] == "2"
    conn.execute("select id, run_id, seq, at, event from agent_events")  # exists
```

- [ ] **Step 2: Run** `uv run pytest tests/test_db.py -q` Expected: FAIL (`agent_runs` missing, `SCHEMA_V1` missing).

- [ ] **Step 3: Implement.** Rename the existing `SCHEMA` constant to `SCHEMA_V1` and add:

```python
SCHEMA_V2 = """
create table if not exists agent_runs (
  id integer primary key autoincrement,
  issue_id text not null references issues(id),
  kind text not null default 'analysis',
  model text not null,
  status text not null default 'running',
  attempt integer not null default 1,
  started_at text not null, finished_at text,
  conversation_id text, input_tokens integer, output_tokens integer, duration_s real,
  brief text, error text
);
create index if not exists agent_runs_issue on agent_runs(issue_id);
create index if not exists agent_runs_started on agent_runs(started_at);
create table if not exists agent_events (
  id integer primary key autoincrement,
  run_id integer not null references agent_runs(id),
  seq integer not null, at text not null, event text not null
);
create index if not exists agent_events_run on agent_events(run_id, seq);
"""
SCHEMA = SCHEMA_V1 + SCHEMA_V2
SCHEMA_VERSION = "2"


def migrate(conn):
    conn.executescript(SCHEMA)
    conn.execute("insert into meta(key, value) values ('schema_version', ?) on conflict(key) do update set value = excluded.value", (SCHEMA_VERSION,))
    conn.commit()
```

Update the existing `test_connect_creates_tables` assertion from `"1"` to `"2"`.

- [ ] **Step 4: Run** `uv run pytest -q` Expected: all pass. Commit: `git commit -m "oss-scout: schema v2 with agent_runs and agent_events"`.

---

### Task 2: Related pull requests from the issue timeline

**Files:**
- Modify: `src/oss_scout/github_client.py`
- Test: `tests/test_github_client.py`

**Interfaces:**
- Produces: `GitHubClient.related_prs(issue_node_id) -> list[dict]` with `{number, state, title, url, will_close}`; `has_closing_pr` is now `any(p["will_close"] and p["state"] == "OPEN" for p in related_prs(...))`.

- [ ] **Step 1: Write the failing test**

```python
def test_related_prs_returns_every_cross_referenced_pr():
    data = {"data": {"node": {"timelineItems": {"nodes": [
        {"willCloseTarget": True, "source": {"number": 2, "state": "OPEN", "title": "Fix it", "url": "u2"}},
        {"willCloseTarget": False, "source": {"number": 5, "state": "MERGED", "title": "Related", "url": "u5"}},
        {"willCloseTarget": False, "source": {}},  # a cross-referenced issue, not a PR
    ]}}}}
    c, _, _ = make_client([FakeResp(data)])
    prs = c.related_prs("I_x")
    assert prs == [{"number": 2, "state": "OPEN", "title": "Fix it", "url": "u2", "will_close": True},
                   {"number": 5, "state": "MERGED", "title": "Related", "url": "u5", "will_close": False}]
```

- [ ] **Step 2: Run** Expected: AttributeError.

- [ ] **Step 3: Implement.** Extend `CLOSING_PR_QUERY` to request `number state title url` on the PR fragment, add:

```python
    def related_prs(self, issue_node_id: str) -> list[dict]:
        data = self.graphql(self.CLOSING_PR_QUERY, {"id": issue_node_id})
        nodes = (data.get("node") or {}).get("timelineItems", {}).get("nodes", [])
        out = []
        for n in nodes:
            src = n.get("source") or {}
            if "number" not in src:
                continue
            out.append({"number": src["number"], "state": src.get("state"), "title": src.get("title"),
                        "url": src.get("url"), "will_close": bool(n.get("willCloseTarget"))})
        return out

    def has_closing_pr(self, issue_node_id: str) -> bool:
        return any(p["will_close"] and p["state"] == "OPEN" for p in self.related_prs(issue_node_id))
```

- [ ] **Step 4: Run** `uv run pytest -q` Expected: all pass (the existing has_closing_pr tests still pass; their fixtures lack title/url and that is fine). Commit.

---

### Task 3: Clone helper

**Files:**
- Create: `src/oss_scout/agents/__init__.py`, `src/oss_scout/agents/clone.py`
- Test: `tests/test_clone.py`

**Interfaces:**
- Produces: `clone.ensure_clone(full_name: str, clones_dir: Path, runner=subprocess.run, timeout: float = 180.0) -> Path`. Raises `clone.CloneError` on a non-zero exit or timeout. Reuses an existing directory that contains `.git`; removes and re-clones one that does not.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_clone.py
import subprocess
from pathlib import Path
import pytest
from oss_scout.agents import clone

class R:
    def __init__(self, code=0, err=""): self.returncode, self.stderr, self.stdout = code, err, ""

def test_clone_runs_partial_shallow_clone_into_named_dir(tmp_path):
    calls = []
    def runner(cmd, **kw):
        calls.append(cmd); (tmp_path / "o__r" / ".git").mkdir(parents=True); return R()
    p = clone.ensure_clone("o/r", tmp_path, runner=runner)
    assert p == tmp_path / "o__r"
    assert calls[0][:2] == ["git", "clone"] and "--depth" in calls[0] and "--filter=blob:limit=200k" in calls[0]
    assert calls[0][-2:] == ["https://github.com/o/r", str(tmp_path / "o__r")]

def test_existing_git_dir_is_reused_without_running_git(tmp_path):
    (tmp_path / "o__r" / ".git").mkdir(parents=True)
    calls = []
    clone.ensure_clone("o/r", tmp_path, runner=lambda cmd, **kw: calls.append(cmd) or R())
    assert calls == []

def test_half_finished_dir_is_removed_and_recloned(tmp_path):
    (tmp_path / "o__r").mkdir(); (tmp_path / "o__r" / "junk").write_text("x")
    def runner(cmd, **kw):
        assert not (tmp_path / "o__r").exists()
        (tmp_path / "o__r" / ".git").mkdir(parents=True); return R()
    clone.ensure_clone("o/r", tmp_path, runner=runner)

def test_failure_and_timeout_raise_clone_error(tmp_path):
    with pytest.raises(clone.CloneError):
        clone.ensure_clone("o/r", tmp_path, runner=lambda cmd, **kw: R(128, "fatal: not found"))
    def slow(cmd, **kw): raise subprocess.TimeoutExpired(cmd, 1)
    with pytest.raises(clone.CloneError):
        clone.ensure_clone("o/r", tmp_path, runner=slow)
```

- [ ] **Step 2: Run** Expected: ImportError.

- [ ] **Step 3: Implement**

```python
# src/oss_scout/agents/clone.py
from __future__ import annotations
import shutil
import subprocess
from pathlib import Path


class CloneError(Exception):
    pass


def clone_dir(full_name: str, clones_dir: Path) -> Path:
    return Path(clones_dir) / full_name.replace("/", "__")


def ensure_clone(full_name: str, clones_dir: Path, runner=subprocess.run, timeout: float = 180.0) -> Path:
    target = clone_dir(full_name, clones_dir)
    if (target / ".git").exists():
        return target
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["git", "clone", "--depth", "1", "--filter=blob:limit=200k", "--quiet",
           f"https://github.com/{full_name}", str(target)]
    try:
        r = runner(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        shutil.rmtree(target, ignore_errors=True)
        raise CloneError(f"clone of {full_name} timed out after {timeout}s") from e
    if r.returncode != 0:
        shutil.rmtree(target, ignore_errors=True)
        raise CloneError(f"clone of {full_name} failed: {r.stderr[-300:]}")
    return target
```

- [ ] **Step 4: Run** `uv run pytest -q` Expected: pass. Commit.

---

### Task 4: Analysis run

**Files:**
- Create: `src/oss_scout/agents/analysis.py`
- Modify: `src/oss_scout/config.py` (add `analysis_model: str = "gemini-3.8-flash-medium"`, `analysis_daily_cap: int = 30`, `analysis_timeout_s: float = 600.0`, `analysis_max_attempts: int = 2`, `clones_dir` property = `data_dir / "clones"`)
- Test: `tests/test_analysis.py`

**Interfaces:**
- Consumes: `clone.ensure_clone`, `GitHubClient.related_prs`, `transitions.apply`, `score.latest_score`.
- Produces:
  - `analysis.BRIEF_SCHEMA: dict`
  - `analysis.build_prompt(issue: dict, repo: dict, related: list[dict]) -> str`
  - `analysis.agy_command(model: str, schema_path: str, clone_path: str, prompt: str) -> list[str]`
  - `analysis.runs_started_today(conn, now) -> int`, `analysis.attempts(conn, issue_id) -> int`
  - `analysis.run_one(conn, cfg, gh, issue_id, *, popen=subprocess.Popen, cloner=clone.ensure_clone, now=None) -> int` returns the run id; never raises for agent failures (records them).
  - `analysis.run_pending(conn, cfg, gh, limit=None, **inject) -> AnalysisSummary(started, done, failed, skipped_cap, skipped_attempts)`
  - `analysis.latest_brief(conn, issue_id) -> dict | None`
- The process contract: `popen(cmd, cwd=..., stdout=PIPE, stderr=PIPE, text=True)`; `run_one` reads `proc.stdout` line by line, then `proc.wait(timeout=remaining)`; on `TimeoutExpired` it calls `proc.kill()`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_analysis.py
import io, json, subprocess
from datetime import datetime, timezone
from pathlib import Path
import pytest
from oss_scout import db, transitions
from oss_scout.agents import analysis
from oss_scout.config import Config

FIX = (Path(__file__).parent / "fixtures" / "agy_stream_probe.jsonl").read_text().splitlines()
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)

class FakeProc:
    def __init__(self, lines, code=0, hang=False):
        self.stdout = io.StringIO("\n".join(lines) + "\n"); self.stderr = io.StringIO("")
        self.returncode = code; self.hang = hang; self.killed = False
    def wait(self, timeout=None):
        if self.hang: raise subprocess.TimeoutExpired("agy", timeout)
        return self.returncode
    def kill(self): self.killed = True

class FakeGH:
    def related_prs(self, node_id): return [{"number": 7, "state": "OPEN", "title": "wip", "url": "u", "will_close": False}]

def _seed(conn, issue_id="I1", state="shortlisted", contributing="Be nice"):
    conn.execute("insert or ignore into repos(full_name, stars, language, contributing_text, ai_policy) values ('o/r', 500, 'Python', ?, 'none')", (contributing,))
    conn.execute("insert into issues(id, repo, number, title, body, labels, url, state, harvested_at, last_seen_at) values (?,'o/r',1,'T','B','[\"bug\"]','https://github.com/o/r/issues/1',?,'x','x')", (issue_id, state))
    conn.execute("insert into scores(issue_id, total, breakdown, weights_hash, scored_at) values (?, 9.0, '{}', 'h', 'x')", (issue_id,))
    conn.commit()

def _cfg(tmp_path, **kw):
    return Config(data_dir=tmp_path, **kw)

def test_prompt_mentions_issue_repo_related_prs_and_missing_contributing():
    issue = {"repo": "o/r", "number": 1, "title": "T", "body": None, "labels": '["bug"]', "url": "u"}
    repo = {"stars": 5, "language": "Python", "contributing_text": None, "ai_policy": "none"}
    p = analysis.build_prompt(issue, repo, [{"number": 7, "state": "OPEN", "title": "wip", "url": "u7", "will_close": False}])
    assert "o/r#1" in p and "#7" in p and "wip" in p and "no contributing file" in p.lower() and "None" not in p
    long = analysis.build_prompt(issue, {**repo, "contributing_text": "x" * 10000}, [])
    assert len(long) < 6000

def test_agy_command_puts_print_last_with_prompt():
    cmd = analysis.agy_command("m", "/s.json", "/c", "PROMPT")
    assert cmd[0] == "agy" and cmd[-2:] == ["--print", "PROMPT"]
    assert cmd[cmd.index("--model") + 1] == "m" and cmd[cmd.index("--json-schema") + 1] == "/s.json"
    assert cmd[cmd.index("--add-dir") + 1] == "/c" and "stream-json" in cmd

def test_successful_run_stores_events_brief_and_transitions(tmp_path):
    conn = db.connect(tmp_path / "t.db"); _seed(conn)
    proc = FakeProc(FIX)
    captured = {}
    def popen(cmd, **kw): captured["cmd"] = cmd; captured["cwd"] = kw.get("cwd"); return proc
    rid = analysis.run_one(conn, _cfg(tmp_path), FakeGH(), "I1", popen=popen, cloner=lambda *a, **k: tmp_path / "clone", now=NOW)
    run = conn.execute("select * from agent_runs where id=?", (rid,)).fetchone()
    assert run["status"] == "done" and run["conversation_id"] and run["input_tokens"] > 0 and run["duration_s"] > 0
    brief = json.loads(run["brief"])
    assert "summary" in brief and isinstance(brief["files"], list)
    assert conn.execute("select count(*) from agent_events where run_id=?", (rid,)).fetchone()[0] == len(FIX)
    assert [r[0] for r in conn.execute("select seq from agent_events where run_id=? order by seq", (rid,))] == list(range(1, len(FIX) + 1))
    assert conn.execute("select state from issues where id='I1'").fetchone()[0] == "analyzed"
    assert conn.execute("select actor from transitions where to_state='analyzed'").fetchone()[0] == f"agent:{rid}"
    assert captured["cwd"] == str(tmp_path / "clone")
    assert analysis.latest_brief(conn, "I1")["summary"] == brief["summary"]

def test_non_json_line_is_stored_raw_and_run_still_succeeds(tmp_path):
    conn = db.connect(tmp_path / "t.db"); _seed(conn)
    lines = ["warning: something", *FIX]
    rid = analysis.run_one(conn, _cfg(tmp_path), FakeGH(), "I1", popen=lambda cmd, **kw: FakeProc(lines), cloner=lambda *a, **k: tmp_path, now=NOW)
    first = conn.execute("select event from agent_events where run_id=? and seq=1", (rid,)).fetchone()[0]
    assert "warning: something" in first
    assert conn.execute("select status from agent_runs where id=?", (rid,)).fetchone()[0] == "done"

def test_failed_result_or_missing_structured_output_marks_failed(tmp_path):
    conn = db.connect(tmp_path / "t.db"); _seed(conn)
    bad = [json.dumps({"event": "result", "result": {"status": "ERROR", "response": "nope", "conversation_id": "c"}})]
    rid = analysis.run_one(conn, _cfg(tmp_path), FakeGH(), "I1", popen=lambda cmd, **kw: FakeProc(bad), cloner=lambda *a, **k: tmp_path, now=NOW)
    run = conn.execute("select status, error from agent_runs where id=?", (rid,)).fetchone()
    assert run["status"] == "failed" and "ERROR" in run["error"]
    assert conn.execute("select state from issues where id='I1'").fetchone()[0] == "shortlisted"

def test_timeout_kills_process_and_marks_failed(tmp_path):
    conn = db.connect(tmp_path / "t.db"); _seed(conn)
    proc = FakeProc([], hang=True)
    rid = analysis.run_one(conn, _cfg(tmp_path, analysis_timeout_s=1), FakeGH(), "I1", popen=lambda cmd, **kw: proc, cloner=lambda *a, **k: tmp_path, now=NOW)
    assert proc.killed
    assert conn.execute("select status from agent_runs where id=?", (rid,)).fetchone()[0] == "failed"

def test_clone_failure_marks_failed_without_starting_agent(tmp_path):
    from oss_scout.agents.clone import CloneError
    conn = db.connect(tmp_path / "t.db"); _seed(conn)
    def cloner(*a, **k): raise CloneError("nope")
    started = []
    rid = analysis.run_one(conn, _cfg(tmp_path), FakeGH(), "I1", popen=lambda cmd, **kw: started.append(1), cloner=cloner, now=NOW)
    assert started == [] and conn.execute("select status, error from agent_runs where id=?", (rid,)).fetchone()["error"] == "nope"

def test_run_pending_respects_daily_cap_and_attempts(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    for i in range(3):
        _seed(conn, f"I{i}")
    cfg = _cfg(tmp_path, analysis_daily_cap=2)
    bad = [json.dumps({"event": "result", "result": {"status": "ERROR", "conversation_id": "c"}})]
    s = analysis.run_pending(conn, cfg, FakeGH(), popen=lambda cmd, **kw: FakeProc(list(bad)), cloner=lambda *a, **k: tmp_path, now=NOW)
    assert s.started == 2 and s.failed == 2 and s.skipped_cap == 1
    assert analysis.runs_started_today(conn, NOW) == 2
    # next day, the two failed issues get their second attempt, then are skipped for good
    later = NOW.replace(day=2)
    cfg2 = _cfg(tmp_path, analysis_daily_cap=10)
    s2 = analysis.run_pending(conn, cfg2, FakeGH(), popen=lambda cmd, **kw: FakeProc(list(bad)), cloner=lambda *a, **k: tmp_path, now=later)
    assert s2.started == 3
    s3 = analysis.run_pending(conn, cfg2, FakeGH(), popen=lambda cmd, **kw: FakeProc(list(bad)), cloner=lambda *a, **k: tmp_path, now=later.replace(day=3))
    assert s3.started == 1 and s3.skipped_attempts == 2  # I2 had one attempt; I0 and I1 are exhausted

def test_run_pending_orders_by_score(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    _seed(conn, "low"); _seed(conn, "high")
    conn.execute("update scores set total = 1.0 where issue_id='low'"); conn.commit()
    order = []
    def popen(cmd, **kw):
        order.append(kw["cwd"]); return FakeProc(FIX)
    analysis.run_pending(conn, _cfg(tmp_path), FakeGH(), popen=popen, cloner=lambda name, *a, **k: tmp_path / name.replace("/", "__"), now=NOW, limit=2)
    assert len(order) == 2
    assert conn.execute("select issue_id from agent_runs order by id").fetchall()[0][0] == "high"
```

- [ ] **Step 2: Run** Expected: ImportError.

- [ ] **Step 3: Implement**

```python
# src/oss_scout/agents/analysis.py
from __future__ import annotations
import json
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
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
    prs = "\n".join(f"- #{p['number']} [{p['state']}] {p.get('title') or ''} {'(would close this issue)' if p.get('will_close') else ''}".rstrip()
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
    return ["agy", "--model", model, "--output-format", "stream-json", "--json-schema", schema_path,
            "--add-dir", clone_path, "--disable-slash-commands", "--print", prompt]


def runs_started_today(conn, now: datetime) -> int:
    day = now.astimezone(timezone.utc).date().isoformat()
    return conn.execute("select count(*) from agent_runs where kind='analysis' and started_at >= ? and started_at < ?",
                        (f"{day}T00:00:00Z", f"{day}T23:59:59Z")).fetchone()[0]


def attempts(conn, issue_id: str) -> int:
    return conn.execute("select count(*) from agent_runs where kind='analysis' and issue_id = ?", (issue_id,)).fetchone()[0]


def latest_brief(conn, issue_id: str) -> dict | None:
    row = conn.execute("select brief from agent_runs where issue_id = ? and status='done' order by id desc limit 1", (issue_id,)).fetchone()
    return json.loads(row[0]) if row and row[0] else None


def _finish(conn, run_id: int, status: str, **fields) -> None:
    cols = ", ".join(f"{k} = ?" for k in fields)
    with conn:
        conn.execute(f"update agent_runs set status = ?, finished_at = ?{', ' + cols if cols else ''} where id = ?",
                     (status, utcnow(), *fields.values(), run_id))


def run_one(conn, cfg: Config, gh, issue_id: str, *, popen=subprocess.Popen, cloner=clone.ensure_clone,
            now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    issue = dict(conn.execute("select * from issues where id = ?", (issue_id,)).fetchone())
    repo = dict(conn.execute("select * from repos where full_name = ?", (issue["repo"],)).fetchone())
    with conn:
        cur = conn.execute(
            "insert into agent_runs(issue_id, kind, model, status, attempt, started_at) values (?, 'analysis', ?, 'running', ?, ?)",
            (issue_id, cfg.analysis_model, attempts(conn, issue_id) + 1, now.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")))
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
        if ev.get("event") == "result":
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
    _finish(conn, run_id, "done", brief=json.dumps(result["structured_output"]), duration_s=duration,
            conversation_id=result.get("conversation_id"), input_tokens=usage.get("input_tokens"), output_tokens=usage.get("output_tokens"))
    transitions.apply(conn, issue_id, "analyzed", f"agent:{run_id}", f"effort {result['structured_output'].get('effort')}")
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
    for row in rows[: limit] if limit else rows:
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
```

Note on `test_run_pending_respects_daily_cap_and_attempts`: with `limit=None` all rows are considered; on day 1 two start and the third is `skipped_cap`. On day 2 all three run (I0 and I1 attempt 2, I2 attempt 1). On day 3 I0 and I1 are at two attempts (`skipped_attempts`), I2 runs its second. The test asserts exactly this.

- [ ] **Step 4: Run** `uv run pytest -q` Expected: pass. Commit: `git commit -m "oss-scout: analysis runs via agy with streamed events, caps, and briefs"`.

---

### Task 5: CLI commands and `scout run`

**Files:**
- Modify: `src/oss_scout/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- `scout analyze [--limit N] [--issue owner/repo#N]`, `scout runs [--limit N]`, `scout brief owner/repo#N`. `scout run` calls `_do_analyze` after `_do_score`.

- [ ] **Step 1: Write the failing tests**

```python
def test_runs_and_brief_commands(tmp_path, monkeypatch):
    cfg = Config(data_dir=tmp_path)
    monkeypatch.setattr(cli, "load_config", lambda: cfg)
    conn = db.connect(cfg.db_path)
    conn.execute("insert into repos(full_name) values ('o/r')")
    conn.execute("insert into issues(id, repo, number, title, url, state, harvested_at, last_seen_at) values ('I1','o/r',4,'t','u','analyzed','x','x')")
    conn.execute("insert into agent_runs(issue_id, model, status, started_at, finished_at, duration_s, input_tokens, brief) values ('I1','m','done','2026-10-01T00:00:00Z','2026-10-01T00:00:10Z',10.0,100,'{\"summary\":\"S\",\"files\":[\"a.py\"],\"effort\":\"small\"}')")
    conn.commit(); conn.close()
    r = CliRunner().invoke(cli.main, ["runs"])
    assert r.exit_code == 0 and "o/r#4" in r.output and "done" in r.output
    r = CliRunner().invoke(cli.main, ["brief", "o/r#4"])
    assert r.exit_code == 0 and "S" in r.output and "a.py" in r.output
    r = CliRunner().invoke(cli.main, ["brief", "o/r#99"])
    assert r.exit_code != 0

def test_help_lists_analyze_runs_brief():
    r = CliRunner().invoke(cli.main, ["--help"])
    for name in ("analyze", "runs", "brief"):
        assert name in r.output
```

- [ ] **Step 2: Run** Expected: FAIL (commands missing).

- [ ] **Step 3: Implement** in `cli.py`:

```python
from oss_scout.agents import analysis

def _gh(cfg):
    return GitHubClient(github_token(), min_interval=cfg.github_min_interval, search_min_interval=cfg.github_search_min_interval)

def _do_analyze(cfg, conn, limit=None):
    s = analysis.run_pending(conn, cfg, _gh(cfg), limit)
    logging.info("analyze %s", s)
    console.print(f"analyze: started={s.started} done={s.done} failed={s.failed} skipped_cap={s.skipped_cap} skipped_attempts={s.skipped_attempts}")
    return s

def _issue_id(conn, ref: str) -> str:
    try:
        repo, number = ref.split("#", 1)
        row = conn.execute("select id from issues where repo = ? and number = ?", (repo, int(number))).fetchone()
    except ValueError:
        raise click.BadParameter("expected owner/repo#number")
    if row is None:
        raise click.ClickException(f"no such issue {ref}")
    return row["id"]

@main.command("analyze")
@click.option("--limit", type=int, default=None)
@click.option("--issue", default=None, help="owner/repo#number: analyze this one issue")
def analyze_command(limit, issue):
    """Run the analysis agent on shortlisted issues (score order, daily cap)."""
    cfg = load_config(); _setup_logging(cfg)
    conn = db.connect(cfg.db_path)
    if issue:
        rid = analysis.run_one(conn, cfg, _gh(cfg), _issue_id(conn, issue))
        row = conn.execute("select status, error from agent_runs where id = ?", (rid,)).fetchone()
        console.print(f"run {rid}: {row['status']} {row['error'] or ''}")
    else:
        _do_analyze(cfg, conn, limit)

@main.command("runs")
@click.option("--limit", type=int, default=20)
def runs_command(limit):
    """Recent agent runs."""
    cfg = load_config()
    conn = db.connect(cfg.db_path)
    for r in conn.execute("select a.id, a.status, a.model, a.started_at, a.duration_s, a.input_tokens, a.output_tokens, a.error, i.repo, i.number "
                          "from agent_runs a join issues i on i.id = a.issue_id order by a.id desc limit ?", (limit,)):
        console.print(f"{r['id']:>4} {r['status']:<7} {r['repo']}#{r['number']} {r['model']} {r['started_at']} "
                      f"{(r['duration_s'] or 0):.0f}s in={r['input_tokens'] or 0} out={r['output_tokens'] or 0} {r['error'] or ''}")

@main.command("brief")
@click.argument("issue")
def brief_command(issue):
    """Print the latest analysis brief for owner/repo#number."""
    cfg = load_config()
    conn = db.connect(cfg.db_path)
    b = analysis.latest_brief(conn, _issue_id(conn, issue))
    if b is None:
        raise click.ClickException(f"no brief for {issue}")
    console.print(json.dumps(b, indent=1))
```

Replace the inline `GitHubClient(...)` in `_do_harvest` with `_gh(cfg)`, replace the body of `dismiss_command`'s lookup with `_issue_id`, and append `_do_analyze(cfg, conn)` to `run_command`.

- [ ] **Step 4: Run** `uv run pytest -q` Expected: pass. Commit.

---

### Task 6: Live run and README

- [ ] **Step 1:** `uv run scout analyze --limit 3`. Expected: three runs, `scout runs` shows `done` rows with token counts, `scout brief` prints a brief with repo-relative files. Open the first brief's issue URL and judge the brief by hand; note any miss in the README known-issues section.
- [ ] **Step 2:** README: add the three commands, the caps, where clones live (`data/clones`), and that agents are read-only.
- [ ] **Step 3:** Commit `git commit -m "oss-scout: stage 2 live run, README"`.
