# OSS Scout Stage 3: API, Chat and Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A local web dashboard: the ranked queue with filters and score breakdown, an issue page with the brief, a resumable chat with an agent, and approve/dismiss actions, and an agents page that follows runs live.

**Architecture:** FastAPI (`src/oss_scout/api/`) exposes the existing modules as JSON and serves the built Vite app from `web/dist`. Chat is one agy conversation per issue (`agents/chat.py`), stored in `threads`/`messages` (schema v3). The React app (`web/`) has three routes and fetches `/api` with hooks.

**Tech Stack:** FastAPI, uvicorn, httpx (tests); Vite 5, React 18, TypeScript, react-router-dom 6, Vitest + Testing Library; `agy` print mode with `--conversation`.

**Spec:** `oss-scout/docs/superpowers/specs/2026-10-01-oss-scout-design.md` Section 10 (and 4, 5, 9).

## Global Constraints

- No endpoint writes to GitHub. The only writes are the user's actions (dismiss, discuss, approve, settings) and chat turns.
- Every state change goes through `transitions.apply` with actor `user` (actions) or `agent:chat` is never used: chat does not change state.
- API binds `127.0.0.1:8765` only.
- `web/dist` and `web/node_modules` are git-ignored; `install_service.sh` builds the frontend.
- Chat defaults verbatim from the spec: `chat_model` `claude-sonnet-4-6`, `chat_timeout_s` 300; the first turn carries issue + brief + contributing + related PRs; `--print` stays last.
- Tests never call `agy`, `npm`, or the network. Frontend tests run with `npm test` (Vitest) in `web/`.

## Review Focus

1. A chat turn while the clone directory is missing (deleted to save disk): the chat must still run, from `data_dir`, without `--add-dir`. Test in Task 1.
2. `POST /discuss` on an issue in `shortlisted` (no brief yet): 409 with a message, no transition. Test in Task 2.
3. `PUT /settings` with an unknown weight key or a non-number: 422, and `scout.yaml` untouched. Test in Task 3.
4. The events endpoint with `after` beyond the last seq returns an empty list and the run status, so the poller knows when to stop. Test in Task 2.
5. The queue page with zero rows (fresh install) renders an empty-state line, not a blank table. Test in Task 5.

---

### Task 1: Schema v3 and chat module

**Files:**
- Modify: `src/oss_scout/db.py`, `src/oss_scout/config.py` (add `chat_model: str = "claude-sonnet-4-6"`, `chat_timeout_s: float = 300.0`)
- Create: `src/oss_scout/agents/chat.py`
- Test: `tests/test_db.py`, `tests/test_chat.py`

**Interfaces:**
- Produces: tables `threads(issue_id text primary key, conversation_id text, plan text, created_at text)` and `messages(id, issue_id, role, content, at, input_tokens, output_tokens)`; `SCHEMA_VERSION = "3"`.
- `chat.first_turn_context(issue, repo, brief, related) -> str`
- `chat.agy_chat_command(model, prompt, conversation_id=None, clone_path=None) -> list[str]`
- `chat.send(conn, cfg, issue_id, content, *, runner=subprocess.run, related=None, now=None) -> dict` returns `{"user": msg, "assistant": msg}`; creates the thread on first use; raises `chat.ChatError` on agy failure (the user message is still stored).
- `chat.thread(conn, issue_id) -> dict | None`, `chat.messages(conn, issue_id) -> list[dict]`, `chat.set_plan(conn, issue_id, plan)`.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_db.py
def test_schema_v3_tables(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    names = {r[0] for r in conn.execute("select name from sqlite_master where type='table'")}
    assert {"threads", "messages"} <= names
    assert conn.execute("select value from meta where key='schema_version'").fetchone()[0] == "3"
```

(and change the existing `== "2"` assertions to `== "3"`.)

```python
# tests/test_chat.py
import json
from pathlib import Path
from oss_scout import db
from oss_scout.agents import chat
from oss_scout.config import Config

FIRST = {"conversation_id": "c-1", "status": "SUCCESS", "response": "Hello, I read the brief.\n", "usage": {"input_tokens": 100, "output_tokens": 8}}
RESUMED = {"conversation_id": "c-1", "status": "SUCCESS", "response": "The fix goes in core.py.\n", "usage": {"input_tokens": 200, "output_tokens": 9}}

class R:
    def __init__(self, body, code=0): self.stdout, self.returncode, self.stderr = json.dumps(body), code, ""

def _seed(conn, state="analyzed", with_brief=True):
    conn.execute("insert into repos(full_name, stars, language, contributing_text) values ('o/r', 5, 'Python', 'Be nice')")
    conn.execute("insert into issues(id, repo, number, title, body, labels, url, state, harvested_at, last_seen_at) values ('I1','o/r',1,'T','B','[]','u',?,'x','x')", (state,))
    if with_brief:
        conn.execute("insert into agent_runs(issue_id, model, status, started_at, brief) values ('I1','m','done','x','{\"summary\":\"S\",\"approach\":\"A\",\"files\":[\"core.py\"]}')")
    conn.commit()

def test_first_turn_starts_conversation_and_stores_both_messages(tmp_path):
    conn = db.connect(tmp_path / "t.db"); _seed(conn)
    calls = []
    def runner(cmd, **kw): calls.append(cmd); return R(FIRST)
    out = chat.send(conn, Config(data_dir=tmp_path), "I1", "Where should the fix go?", runner=runner)
    assert out["assistant"]["content"].startswith("Hello") and out["user"]["content"] == "Where should the fix go?"
    assert chat.thread(conn, "I1")["conversation_id"] == "c-1"
    assert [m["role"] for m in chat.messages(conn, "I1")] == ["user", "assistant"]
    cmd = calls[0]
    assert "--conversation" not in cmd and cmd[-2] == "--print"
    prompt = cmd[-1]
    assert "T" in prompt and "S" in prompt and "Be nice" in prompt and "Where should the fix go?" in prompt
    assert cmd[cmd.index("--model") + 1] == "claude-sonnet-4-6"

def test_second_turn_resumes_and_sends_only_the_message(tmp_path):
    conn = db.connect(tmp_path / "t.db"); _seed(conn)
    responses = [R(FIRST), R(RESUMED)]
    calls = []
    def runner(cmd, **kw): calls.append(cmd); return responses.pop(0)
    chat.send(conn, Config(data_dir=tmp_path), "I1", "first", runner=runner)
    out = chat.send(conn, Config(data_dir=tmp_path), "I1", "second", runner=runner)
    assert calls[1][calls[1].index("--conversation") + 1] == "c-1"
    assert calls[1][-1] == "second"
    assert out["assistant"]["content"].startswith("The fix")
    assert len(chat.messages(conn, "I1")) == 4

def test_missing_clone_runs_without_add_dir(tmp_path):
    conn = db.connect(tmp_path / "t.db"); _seed(conn)
    calls = []
    chat.send(conn, Config(data_dir=tmp_path), "I1", "hi", runner=lambda cmd, **kw: calls.append((cmd, kw)) or R(FIRST))
    cmd, kw = calls[0]
    assert "--add-dir" not in cmd and kw["cwd"] == str(tmp_path)
    (tmp_path / "clones" / "o__r" / ".git").mkdir(parents=True)
    chat.send(conn, Config(data_dir=tmp_path), "I1", "hi again", runner=lambda cmd, **kw: calls.append((cmd, kw)) or R(RESUMED))
    cmd, kw = calls[1]
    assert cmd[cmd.index("--add-dir") + 1] == str(tmp_path / "clones" / "o__r") and kw["cwd"] == str(tmp_path / "clones" / "o__r")

def test_agy_failure_keeps_user_message_and_raises(tmp_path):
    import pytest
    conn = db.connect(tmp_path / "t.db"); _seed(conn)
    with pytest.raises(chat.ChatError):
        chat.send(conn, Config(data_dir=tmp_path), "I1", "hi", runner=lambda cmd, **kw: R({"status": "ERROR"}, code=1))
    assert [m["role"] for m in chat.messages(conn, "I1")] == ["user"]

def test_set_plan(tmp_path):
    conn = db.connect(tmp_path / "t.db"); _seed(conn)
    chat.send(conn, Config(data_dir=tmp_path), "I1", "hi", runner=lambda cmd, **kw: R(FIRST))
    chat.set_plan(conn, "I1", "Do X then Y")
    assert chat.thread(conn, "I1")["plan"] == "Do X then Y"
```

- [ ] **Step 2: Run** Expected: ImportError / version mismatch.

- [ ] **Step 3: Implement.** In `db.py` add `SCHEMA_V3` and bump the version:

```python
SCHEMA_V3 = """
create table if not exists threads (
  issue_id text primary key references issues(id),
  conversation_id text, plan text, created_at text not null
);
create table if not exists messages (
  id integer primary key autoincrement,
  issue_id text not null references issues(id),
  role text not null, content text not null, at text not null,
  input_tokens integer, output_tokens integer
);
create index if not exists messages_issue on messages(issue_id, id);
"""
SCHEMA = SCHEMA_V1 + SCHEMA_V2 + SCHEMA_V3
SCHEMA_VERSION = "3"
```

```python
# src/oss_scout/agents/chat.py
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
repo; you must not edit files, run commands, use git, or touch GitHub. Nothing is sent to GitHub from here.

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


def send(conn, cfg: Config, issue_id: str, content: str, *, runner=subprocess.run, related: list[dict] | None = None,
         now=None) -> dict:
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
```

- [ ] **Step 4: Run** `uv run pytest -q` Expected: pass. Commit `git commit -m "oss-scout: schema v3 threads/messages and chat via resumable agy conversations"`.

---

### Task 2: API

**Files:**
- Modify: `pyproject.toml` (add `fastapi>=0.115`, `uvicorn>=0.30`, `httpx>=0.27` in dev)
- Create: `src/oss_scout/api/__init__.py`, `src/oss_scout/api/app.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Produces: `app.create_app(cfg: Config, *, chat_runner=subprocess.run, gh_factory=None) -> FastAPI`. Endpoints as in spec 10.1. `gh_factory` returns a client with `related_prs`; when None, `/messages` passes `related=None`.
- Each request opens its own connection via `db.connect(cfg.db_path)` (SQLite per-request is cheap; no shared connection across threads).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_api.py
import json
from fastapi.testclient import TestClient
from oss_scout import db
from oss_scout.api.app import create_app
from oss_scout.config import Config

def _seed(cfg, state="analyzed"):
    conn = db.connect(cfg.db_path)
    conn.execute("insert into repos(full_name, stars, language, ai_policy) values ('o/r', 500, 'Python', 'none')")
    conn.execute("insert into issues(id, repo, number, title, body, labels, url, state, updated_at, harvested_at, last_seen_at) values ('I1','o/r',1,'T','B','[\"bug\"]','u',?, '2026-10-01T00:00:00Z','x','x')", (state,))
    for q, c in [("category", "quant_trading"), ("kind", "bug"), ("difficulty", "small"), ("worth_a_look", "yes")]:
        conn.execute("insert into classifications(issue_id, question, choice, confidence, probabilities, model, asked_at) values ('I1',?,?,0.9,'{}','s','x')", (q, c))
    conn.execute("insert into scores(issue_id, total, breakdown, weights_hash, scored_at) values ('I1', 9.5, '{\"recognition\": 2.0}', 'h', 'x')")
    conn.execute("insert into agent_runs(issue_id, model, status, started_at, brief) values ('I1','m','done','2026-10-01T00:00:00Z','{\"summary\":\"S\",\"approach\":\"A\",\"files\":[]}')")
    rid = conn.execute("select id from agent_runs").fetchone()[0]
    for i in range(3):
        conn.execute("insert into agent_events(run_id, seq, at, event) values (?,?,?,?)", (rid, i + 1, "x", json.dumps({"event": "step_update", "n": i})))
    conn.commit(); conn.close()
    return rid

def _client(tmp_path, state="analyzed", chat_runner=None):
    cfg = Config(data_dir=tmp_path)
    rid = _seed(cfg, state)
    class R:
        stdout = json.dumps({"conversation_id": "c-1", "status": "SUCCESS", "response": "Reply", "usage": {}}); returncode = 0; stderr = ""
    app = create_app(cfg, chat_runner=chat_runner or (lambda cmd, **kw: R()))
    return TestClient(app), cfg, rid

def test_status_and_queue(tmp_path):
    c, cfg, _ = _client(tmp_path)
    assert c.get("/api/status").json()["issues"]["analyzed"] == 1
    rows = c.get("/api/queue", params={"category": "quant_trading"}).json()
    assert rows[0]["number"] == 1 and rows[0]["breakdown"]["recognition"] == 2.0
    assert c.get("/api/queue", params={"category": "other"}).json() == []

def test_issue_detail_has_everything(tmp_path):
    c, _, rid = _client(tmp_path)
    d = c.get("/api/issues/I1").json()
    assert d["issue"]["title"] == "T" and d["repo"]["stars"] == 500
    assert d["classification"]["category"]["choice"] == "quant_trading"
    assert d["score"]["total"] == 9.5 and d["brief"]["summary"] == "S"
    assert d["runs"][0]["id"] == rid and d["thread"] is None and d["messages"] == []
    assert c.get("/api/issues/nope").status_code == 404

def test_discuss_requires_analyzed_then_chat_and_approve(tmp_path):
    c, cfg, _ = _client(tmp_path, state="shortlisted")
    assert c.post("/api/issues/I1/discuss").status_code == 409
    conn = db.connect(cfg.db_path); conn.execute("update issues set state='analyzed'"); conn.commit(); conn.close()
    assert c.post("/api/issues/I1/discuss").json()["state"] == "discussing"
    r = c.post("/api/issues/I1/messages", json={"content": "hi"})
    assert r.status_code == 200 and r.json()["assistant"]["content"] == "Reply"
    assert len(c.get("/api/issues/I1/messages").json()) == 2
    r = c.post("/api/issues/I1/approve", json={"plan": "Do X"})
    assert r.json()["state"] == "plan_approved"
    assert c.get("/api/issues/I1").json()["thread"]["plan"] == "Do X"

def test_dismiss_and_unknown(tmp_path):
    c, _, _ = _client(tmp_path)
    assert c.post("/api/issues/I1/dismiss", json={"note": "meh"}).json()["state"] == "dismissed"
    assert c.post("/api/issues/nope/dismiss", json={}).status_code == 404

def test_chat_failure_is_502_and_keeps_user_message(tmp_path):
    class Bad:
        stdout = ""; returncode = 1; stderr = "boom"
    c, _, _ = _client(tmp_path, chat_runner=lambda cmd, **kw: Bad())
    c.post("/api/issues/I1/discuss")
    assert c.post("/api/issues/I1/messages", json={"content": "hi"}).status_code == 502
    assert [m["role"] for m in c.get("/api/issues/I1/messages").json()] == ["user"]

def test_runs_and_events_polling(tmp_path):
    c, _, rid = _client(tmp_path)
    assert c.get("/api/runs").json()[0]["id"] == rid
    ev = c.get(f"/api/runs/{rid}/events", params={"after": 1}).json()
    assert [e["seq"] for e in ev["events"]] == [2, 3] and ev["status"] == "done"
    assert c.get(f"/api/runs/{rid}/events", params={"after": 3}).json() == {"status": "done", "events": []}
    assert c.get("/api/runs/999/events").status_code == 404

def test_spa_fallback_when_dist_missing(tmp_path):
    c, _, _ = _client(tmp_path)
    r = c.get("/issues/I1")
    assert r.status_code == 200 and "web/dist" in r.text  # a hint page until the frontend is built
```

- [ ] **Step 2: Run** `uv add fastapi uvicorn && uv add --dev httpx && uv run pytest tests/test_api.py -q` Expected: ImportError.

- [ ] **Step 3: Implement**

```python
# src/oss_scout/api/app.py
from __future__ import annotations
import json
import subprocess
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from oss_scout import classify, db, queue_view, score, transitions
from oss_scout.agents import analysis, chat
from oss_scout.config import Config
from oss_scout.transitions import InvalidTransition

WEB_DIST = Path(__file__).resolve().parents[3] / "web" / "dist"


class Note(BaseModel):
    note: str = ""


class Content(BaseModel):
    content: str


class Plan(BaseModel):
    plan: str


def create_app(cfg: Config, *, chat_runner=subprocess.run, gh_factory=None) -> FastAPI:
    app = FastAPI(title="OSS Scout")

    def conn():
        return db.connect(cfg.db_path)

    def issue_or_404(c, issue_id):
        row = c.execute("select * from issues where id = ?", (issue_id,)).fetchone()
        if row is None:
            raise HTTPException(404, f"no issue {issue_id}")
        return dict(row)

    @app.get("/api/status")
    def status():
        c = conn()
        counts = {r["state"]: r["n"] for r in c.execute("select state, count(*) n from issues group by state")}
        last = c.execute("select at from transitions order by id desc limit 1").fetchone()
        repos = c.execute("select count(*), sum(excluded_reason is not null) from repos").fetchone()
        return {"issues": counts, "last_transition": last[0] if last else None, "repos": repos[0], "repos_excluded": repos[1] or 0}

    @app.get("/api/queue")
    def queue(category: str | None = None, state: str | None = None, limit: int = 50):
        return queue_view.queue_rows(conn(), category, state, limit)

    @app.get("/api/issues/{issue_id}")
    def issue_detail(issue_id: str):
        c = conn()
        issue = issue_or_404(c, issue_id)
        repo = dict(c.execute("select * from repos where full_name = ?", (issue["repo"],)).fetchone() or {})
        repo.pop("contributing_text", None)
        sc = score.latest_score(c, issue_id)
        runs = [dict(r) for r in c.execute("select id, status, model, attempt, started_at, finished_at, duration_s, input_tokens, output_tokens, error from agent_runs where issue_id = ? order by id desc", (issue_id,))]
        trans = [dict(r) for r in c.execute("select from_state, to_state, actor, note, at from transitions where issue_id = ? order by id", (issue_id,))]
        return {"issue": issue, "repo": repo, "classification": classify.latest(c, issue_id),
                "score": {"total": sc[0], "breakdown": sc[1]} if sc else None,
                "brief": analysis.latest_brief(c, issue_id), "runs": runs, "transitions": trans,
                "thread": chat.thread(c, issue_id), "messages": chat.messages(c, issue_id)}

    def _move(issue_id, to_state, note=""):
        c = conn()
        issue_or_404(c, issue_id)
        try:
            transitions.apply(c, issue_id, to_state, "user", note)
        except InvalidTransition as e:
            raise HTTPException(409, str(e))
        return {"id": issue_id, "state": to_state}

    @app.post("/api/issues/{issue_id}/dismiss")
    def dismiss(issue_id: str, body: Note):
        return _move(issue_id, "dismissed", body.note)

    @app.post("/api/issues/{issue_id}/discuss")
    def discuss(issue_id: str):
        return _move(issue_id, "discussing")

    @app.post("/api/issues/{issue_id}/approve")
    def approve(issue_id: str, body: Plan):
        out = _move(issue_id, "plan_approved", "plan approved")
        chat.set_plan(conn(), issue_id, body.plan)
        return out

    @app.get("/api/issues/{issue_id}/messages")
    def get_messages(issue_id: str):
        c = conn()
        issue_or_404(c, issue_id)
        return chat.messages(c, issue_id)

    @app.post("/api/issues/{issue_id}/messages")
    def post_message(issue_id: str, body: Content):
        c = conn()
        issue_or_404(c, issue_id)
        related = None
        if gh_factory is not None:
            try:
                related = gh_factory().related_prs(issue_id)
            except Exception:
                related = None
        try:
            return chat.send(c, cfg, issue_id, body.content, runner=chat_runner, related=related)
        except chat.ChatError as e:
            raise HTTPException(502, str(e))

    @app.get("/api/runs")
    def runs(limit: int = 50):
        c = conn()
        return [dict(r) for r in c.execute(
            "select a.id, a.issue_id, a.status, a.model, a.started_at, a.finished_at, a.duration_s, a.input_tokens, a.output_tokens, a.error, i.repo, i.number, i.title "
            "from agent_runs a join issues i on i.id = a.issue_id order by a.id desc limit ?", (limit,))]

    @app.get("/api/runs/{run_id}/events")
    def events(run_id: int, after: int = 0):
        c = conn()
        run = c.execute("select status from agent_runs where id = ?", (run_id,)).fetchone()
        if run is None:
            raise HTTPException(404, f"no run {run_id}")
        evs = []
        for r in c.execute("select seq, at, event from agent_events where run_id = ? and seq > ? order by seq", (run_id, after)):
            try:
                payload = json.loads(r["event"])
            except json.JSONDecodeError:
                payload = {"raw": r["event"]}
            evs.append({"seq": r["seq"], "at": r["at"], "event": payload})
        return {"status": run["status"], "events": evs}

    if WEB_DIST.exists():
        app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

        @app.get("/{path:path}")
        def spa(path: str):
            return FileResponse(WEB_DIST / "index.html")
    else:
        @app.get("/{path:path}")
        def hint(path: str):
            return HTMLResponse("<p>OSS Scout API is running. Build the frontend: <code>cd web && npm ci && npm run build</code> (writes web/dist).</p>")

    return app
```

- [ ] **Step 4: Run** `uv run pytest -q` Expected: pass. Commit `git commit -m "oss-scout: FastAPI app with queue, issue, actions, chat, runs, events"`.

---

### Task 3: Settings endpoint

**Files:**
- Modify: `src/oss_scout/api/app.py`, `src/oss_scout/config.py` (add `config.update_yaml(path, section: str, values: dict)`)
- Test: `tests/test_api.py`, `tests/test_config.py`

**Interfaces:**
- `GET /api/settings -> {"weights": {...}, "category_values": {...}}`; `PUT /api/settings` with the same shape (either key optional) validates keys against the current config and numeric values, writes `scout.yaml`, re-scores, returns the new settings. `create_app` takes `config_path: Path | None` (default `PROJECT_ROOT / "scout.yaml"`) so tests use a temp file.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_config.py
def test_update_yaml_merges_section_and_keeps_other_keys(tmp_path):
    from oss_scout.config import update_yaml, load_config
    p = tmp_path / "scout.yaml"; p.write_text("min_stars: 50\nweights:\n  bounty: 2.0\n")
    update_yaml(p, "weights", {"bounty": 1.0, "freshness": 3.0})
    cfg = load_config(p)
    assert cfg.min_stars == 50 and cfg.weights["bounty"] == 1.0 and cfg.weights["freshness"] == 3.0

# append to tests/test_api.py
def test_settings_get_put_validates_and_rescores(tmp_path):
    cfg = Config(data_dir=tmp_path); _seed(cfg)
    yaml_path = tmp_path / "scout.yaml"; yaml_path.write_text("")
    class R:
        stdout = "{}"; returncode = 0; stderr = ""
    c = TestClient(create_app(cfg, chat_runner=lambda cmd, **kw: R(), config_path=yaml_path))
    assert c.get("/api/settings").json()["weights"]["bounty"] == 0.5
    assert c.put("/api/settings", json={"weights": {"nope": 1}}).status_code == 422
    assert c.put("/api/settings", json={"weights": {"bounty": "x"}}).status_code == 422
    assert yaml_path.read_text() == ""
    r = c.put("/api/settings", json={"weights": {"bounty": 2.0}, "category_values": {"other": 0.1}})
    assert r.json()["weights"]["bounty"] == 2.0 and "bounty: 2.0" in yaml_path.read_text()
    conn = db.connect(cfg.db_path)
    assert conn.execute("select count(*) from scores where issue_id='I1'").fetchone()[0] == 2  # re-scored
```

- [ ] **Step 2: Run** Expected: FAIL.

- [ ] **Step 3: Implement.** In `config.py`:

```python
def update_yaml(path: Path, section: str, values: dict) -> None:
    raw = yaml.safe_load(Path(path).read_text()) if Path(path).exists() and Path(path).read_text().strip() else {}
    raw = raw or {}
    merged = dict(raw.get(section) or {})
    merged.update(values)
    raw[section] = merged
    Path(path).write_text(yaml.safe_dump(raw, sort_keys=False))
```

In `app.py`, `create_app(cfg, *, chat_runner=..., gh_factory=None, config_path: Path | None = None)` and:

```python
    class Settings(BaseModel):
        weights: dict[str, float] | None = None
        category_values: dict[str, float] | None = None

    def current():
        return load_config(config_path) if config_path else cfg

    @app.get("/api/settings")
    def get_settings():
        cur = current()
        return {"weights": cur.weights, "category_values": cur.category_values}

    @app.put("/api/settings")
    def put_settings(body: Settings):
        cur = current()
        for section, allowed in (("weights", cur.weights), ("category_values", cur.category_values)):
            values = getattr(body, section)
            if values:
                unknown = set(values) - set(allowed)
                if unknown:
                    raise HTTPException(422, f"unknown {section}: {sorted(unknown)}")
        path = config_path or (PROJECT_ROOT / "scout.yaml")
        if body.weights:
            update_yaml(path, "weights", body.weights)
        if body.category_values:
            update_yaml(path, "category_values", body.category_values)
        new = load_config(path)
        score.run_scoring(conn(), new)
        return {"weights": new.weights, "category_values": new.category_values}
```

(`dict[str, float]` in the Pydantic model makes `"x"` a 422 automatically.) Import `load_config`, `update_yaml`, `PROJECT_ROOT` from `oss_scout.config`.

- [ ] **Step 4: Run** `uv run pytest -q` Expected: pass. Commit.

---

### Task 4: `scout serve`, `scout open`, service unit

**Files:**
- Modify: `src/oss_scout/cli.py`, `install_service.sh`, `.gitignore` (add `web/node_modules/`, `web/dist/`)
- Create: `systemd/oss-scout.service`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing test**

```python
def test_help_lists_serve_and_open():
    r = CliRunner().invoke(cli.main, ["--help"])
    assert "serve" in r.output and "open" in r.output

def test_service_unit_exists_with_path():
    from pathlib import Path
    unit = (Path(__file__).resolve().parents[1] / "systemd" / "oss-scout.service").read_text()
    assert "uv run scout serve" in unit and "Environment=PATH=/home/ryan/.local/bin:" in unit and "Restart=on-failure" in unit
```

- [ ] **Step 2: Run** Expected: FAIL.

- [ ] **Step 3: Implement**

```python
@main.command("serve")
@click.option("--host", default="127.0.0.1")
@click.option("--port", type=int, default=8765)
def serve_command(host, port):
    """Run the dashboard API (and the built frontend) locally."""
    import uvicorn
    from oss_scout.api.app import create_app
    cfg = load_config(); _setup_logging(cfg)
    uvicorn.run(create_app(cfg, gh_factory=lambda: _gh(cfg)), host=host, port=port, log_level="info")


@main.command("open")
def open_command():
    """Open the dashboard in the browser."""
    import subprocess
    subprocess.Popen(["xdg-open", "http://127.0.0.1:8765"])
```

```ini
# systemd/oss-scout.service
[Unit]
Description=OSS Scout dashboard API
After=network-online.target

[Service]
Type=simple
WorkingDirectory=/home/ryan/Coding_Projects/business projects/oss-scout
Environment=PATH=/home/ryan/.local/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=/home/ryan/.local/bin/uv run scout serve
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
```

`install_service.sh` additionally: `(cd web && npm ci && npm run build)` when `web/package.json` exists, copies `oss-scout.service`, `systemctl --user enable --now oss-scout.service`.

- [ ] **Step 4: Run** `uv run pytest -q` Expected: pass. Commit `git commit -m "oss-scout: scout serve/open, dashboard service unit"`.

---

### Task 5: Frontend scaffold, API client, Queue page

**Files:**
- Create: `web/package.json`, `web/vite.config.ts`, `web/tsconfig.json`, `web/index.html`, `web/src/main.tsx`, `web/src/api.ts`, `web/src/types.ts`, `web/src/App.tsx`, `web/src/pages/Queue.tsx`, `web/src/components/ScoreBadge.tsx`, `web/src/styles.css`, `web/src/test/setup.ts`
- Test: `web/src/pages/Queue.test.tsx`, `web/src/components/ScoreBadge.test.tsx`

**Interfaces:**
- `api.ts`: `getQueue(params) -> Promise<QueueRow[]>`, `getIssue(id)`, `postDismiss(id, note)`, `postDiscuss(id)`, `getMessages(id)`, `postMessage(id, content)`, `postApprove(id, plan)`, `getRuns()`, `getEvents(runId, after)`, `getSettings()`, `putSettings(body)`, `getStatus()`. All call `fetch` against `/api/...` and throw on non-2xx with the response text.
- `types.ts`: `QueueRow { id, rank, score, repo, number, title, url, state, category, kind, difficulty, has_bounty, breakdown: Record<string, number> }`, `IssueDetail`, `Run`, `RunEvent`, `Message`, `Settings`.

- [ ] **Step 1: Scaffold.** `cd web && npm create vite@latest . -- --template react-ts` (answer yes to the non-empty dir) then `npm i react-router-dom && npm i -D vitest @testing-library/react @testing-library/jest-dom @testing-library/user-event jsdom`. Add to `package.json` scripts: `"test": "vitest run"`. In `vite.config.ts`:

```ts
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
export default defineConfig({
  plugins: [react()],
  server: { proxy: { '/api': 'http://127.0.0.1:8765' } },
  test: { environment: 'jsdom', setupFiles: './src/test/setup.ts', globals: true },
})
```

`src/test/setup.ts`: `import '@testing-library/jest-dom/vitest'`.

- [ ] **Step 2: Write the failing tests**

```tsx
// src/components/ScoreBadge.test.tsx
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ScoreBadge } from './ScoreBadge'

test('shows the total and reveals the breakdown on hover', async () => {
  render(<ScoreBadge total={9.5} breakdown={{ recognition: 2.0, bounty: 0 }} />)
  expect(screen.getByText('9.5')).toBeInTheDocument()
  expect(screen.queryByText(/recognition/)).not.toBeInTheDocument()
  await userEvent.hover(screen.getByText('9.5'))
  expect(screen.getByText(/recognition/)).toBeInTheDocument()
  expect(screen.getByText(/2\.0/)).toBeInTheDocument()
})
```

```tsx
// src/pages/Queue.test.tsx
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { vi } from 'vitest'
import * as api from '../api'
import { Queue } from './Queue'

const rows = [
  { id: 'A', rank: 1, score: 9.5, repo: 'o/r', number: 1, title: 'Quant thing', url: 'u', state: 'shortlisted', category: 'quant_trading', kind: 'bug', difficulty: 'small', has_bounty: 0, breakdown: {} },
  { id: 'B', rank: 2, score: 8.0, repo: 'o/s', number: 2, title: 'Web thing', url: 'u', state: 'shortlisted', category: 'web_frontend', kind: 'feature', difficulty: 'medium', has_bounty: 1, breakdown: {} },
]

test('renders rows and filters by category chip', async () => {
  const spy = vi.spyOn(api, 'getQueue').mockResolvedValue(rows)
  render(<MemoryRouter><Queue /></MemoryRouter>)
  await waitFor(() => expect(screen.getByText('Quant thing')).toBeInTheDocument())
  expect(screen.getByText('Web thing')).toBeInTheDocument()
  spy.mockResolvedValue([rows[0]])
  await userEvent.click(screen.getByRole('button', { name: 'quant_trading' }))
  await waitFor(() => expect(screen.queryByText('Web thing')).not.toBeInTheDocument())
  expect(spy).toHaveBeenLastCalledWith(expect.objectContaining({ category: 'quant_trading' }))
})

test('empty queue shows an empty-state line', async () => {
  vi.spyOn(api, 'getQueue').mockResolvedValue([])
  render(<MemoryRouter><Queue /></MemoryRouter>)
  await waitFor(() => expect(screen.getByText(/nothing shortlisted yet/i)).toBeInTheDocument())
})
```

- [ ] **Step 3: Run** `npm test` Expected: FAIL (modules missing).

- [ ] **Step 4: Implement**

```ts
// src/types.ts
export type QueueRow = { id: string; rank: number; score: number; repo: string; number: number; title: string; url: string;
  state: string; category: string | null; kind: string | null; difficulty: string | null; has_bounty: number; breakdown: Record<string, number> }
export type Message = { id: number; role: 'user' | 'assistant'; content: string; at: string }
export type Run = { id: number; issue_id?: string; status: string; model: string; started_at: string; finished_at: string | null;
  duration_s: number | null; input_tokens: number | null; output_tokens: number | null; error: string | null; repo?: string; number?: number; title?: string }
export type RunEvent = { seq: number; at: string; event: any }
export type Brief = { summary: string; likely_cause: string; approach: string; files: string[]; effort: string; risks: string[]; duplicate_risk: string; ai_policy_note: string }
export type IssueDetail = { issue: any; repo: any; classification: Record<string, { choice: string; confidence: number | null }>;
  score: { total: number; breakdown: Record<string, number> } | null; brief: Brief | null; runs: Run[];
  transitions: { from_state: string; to_state: string; actor: string; note: string; at: string }[];
  thread: { conversation_id: string | null; plan: string | null } | null; messages: Message[] }
export type Settings = { weights: Record<string, number>; category_values: Record<string, number> }
```

```ts
// src/api.ts
import type { IssueDetail, Message, QueueRow, Run, RunEvent, Settings } from './types'

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, { headers: { 'Content-Type': 'application/json' }, ...init })
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`)
  return r.json()
}
const qs = (p: Record<string, string | number | undefined>) =>
  Object.entries(p).filter(([, v]) => v !== undefined && v !== '').map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`).join('&')

export const getStatus = () => call<any>('/api/status')
export const getQueue = (p: { category?: string; state?: string; limit?: number }) => call<QueueRow[]>(`/api/queue?${qs(p)}`)
export const getIssue = (id: string) => call<IssueDetail>(`/api/issues/${id}`)
export const postDismiss = (id: string, note: string) => call<any>(`/api/issues/${id}/dismiss`, { method: 'POST', body: JSON.stringify({ note }) })
export const postDiscuss = (id: string) => call<any>(`/api/issues/${id}/discuss`, { method: 'POST' })
export const postApprove = (id: string, plan: string) => call<any>(`/api/issues/${id}/approve`, { method: 'POST', body: JSON.stringify({ plan }) })
export const getMessages = (id: string) => call<Message[]>(`/api/issues/${id}/messages`)
export const postMessage = (id: string, content: string) => call<{ user: Message; assistant: Message }>(`/api/issues/${id}/messages`, { method: 'POST', body: JSON.stringify({ content }) })
export const getRuns = () => call<Run[]>('/api/runs')
export const getEvents = (runId: number, after: number) => call<{ status: string; events: RunEvent[] }>(`/api/runs/${runId}/events?after=${after}`)
export const getSettings = () => call<Settings>('/api/settings')
export const putSettings = (s: Partial<Settings>) => call<Settings>('/api/settings', { method: 'PUT', body: JSON.stringify(s) })
```

```tsx
// src/components/ScoreBadge.tsx
import { useState } from 'react'
export function ScoreBadge({ total, breakdown }: { total: number; breakdown: Record<string, number> }) {
  const [open, setOpen] = useState(false)
  return (
    <span className="score" onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)}>
      <span className="score-total">{total.toFixed(1)}</span>
      {open && (
        <span className="score-pop">
          {Object.entries(breakdown).sort((a, b) => b[1] - a[1]).map(([k, v]) => (
            <span key={k} className="score-row"><span>{k}</span><span>{v.toFixed(1)}</span></span>
          ))}
        </span>
      )}
    </span>
  )
}
```

```tsx
// src/pages/Queue.tsx
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import * as api from '../api'
import type { QueueRow } from '../types'
import { ScoreBadge } from '../components/ScoreBadge'

const CATEGORIES = ['quant_trading', 'prediction_markets_finance', 'ai_agents_llm', 'data_scraping', 'dev_tools_cli', 'web_frontend', 'other']
const STATES = ['shortlisted', 'analyzed', 'discussing', 'plan_approved', 'drafting', 'pr_open', 'classified', 'dismissed']

export function Queue() {
  const [rows, setRows] = useState<QueueRow[] | null>(null)
  const [category, setCategory] = useState<string>('')
  const [state, setState] = useState<string>('')
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    setError(null)
    api.getQueue({ category: category || undefined, state: state || undefined, limit: 100 }).then(setRows).catch(e => setError(String(e)))
  }, [category, state])
  const chip = (label: string, active: boolean, onClick: () => void) => (
    <button key={label} className={active ? 'chip active' : 'chip'} onClick={onClick}>{label}</button>
  )
  return (
    <div>
      <div className="chips">
        {chip('all categories', category === '', () => setCategory(''))}
        {CATEGORIES.map(c => chip(c, category === c, () => setCategory(category === c ? '' : c)))}
      </div>
      <div className="chips">
        {chip('active', state === '', () => setState(''))}
        {STATES.map(s => chip(s, state === s, () => setState(state === s ? '' : s)))}
      </div>
      {error && <p className="error">{error}</p>}
      {rows && rows.length === 0 && <p className="muted">Nothing shortlisted yet. Run <code>scout run</code> or widen the filters.</p>}
      {rows && rows.length > 0 && (
        <table className="queue">
          <thead><tr><th>#</th><th>score</th><th>repo</th><th>issue</th><th>cat</th><th>kind</th><th>diff</th><th>state</th><th>title</th></tr></thead>
          <tbody>
            {rows.map(r => (
              <tr key={r.id}>
                <td>{r.rank}</td>
                <td><ScoreBadge total={r.score} breakdown={r.breakdown} /></td>
                <td>{r.repo}</td>
                <td><a href={r.url} target="_blank" rel="noreferrer">#{r.number}</a></td>
                <td>{r.category ?? '?'}</td><td>{r.kind ?? '?'}</td><td>{r.difficulty ?? '?'}</td>
                <td><span className={`state ${r.state}`}>{r.state}</span>{r.has_bounty ? ' 💎' : ''}</td>
                <td><Link to={`/issues/${r.id}`}>{r.title}</Link></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
```

`App.tsx` renders a header with three `NavLink`s (Queue `/`, Agents `/agents`) and `<Routes>` for `/`, `/issues/:id` (Task 6), `/agents` (Task 7); `main.tsx` wraps in `BrowserRouter`. `styles.css`: a dark, compact table style; `.chip.active` highlighted; `.score-pop` absolutely positioned; `.state.shortlisted` etc. colored.

- [ ] **Step 5: Run** `npm test` Expected: pass. `npm run build` succeeds. Commit (`web/` sources, lockfile; not `node_modules` or `dist`).

---

### Task 6: Issue page with brief, chat, actions

**Files:**
- Create: `web/src/pages/Issue.tsx`, `web/src/components/Chat.tsx`, `web/src/components/BriefCard.tsx`
- Test: `web/src/pages/Issue.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// src/pages/Issue.test.tsx
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { vi } from 'vitest'
import * as api from '../api'
import { Issue } from './Issue'

const detail = {
  issue: { id: 'A', repo: 'o/r', number: 1, title: 'Fix the thing', url: 'u', labels: '["bug"]', state: 'analyzed', body: 'body' },
  repo: { stars: 500, language: 'Python', ai_policy: 'none' },
  classification: { category: { choice: 'quant_trading', confidence: 0.9 }, kind: { choice: 'bug', confidence: 1 } },
  score: { total: 9.5, breakdown: { recognition: 2 } },
  brief: { summary: 'S', likely_cause: 'C', approach: 'A', files: ['core.py'], effort: 'small', risks: ['r1'], duplicate_risk: 'none', ai_policy_note: 'none' },
  runs: [], transitions: [], thread: null, messages: [],
}
function mount() {
  return render(<MemoryRouter initialEntries={['/issues/A']}><Routes><Route path="/issues/:id" element={<Issue />} /></Routes></MemoryRouter>)
}

test('shows brief and the Discuss action when analyzed; discuss then chat', async () => {
  vi.spyOn(api, 'getIssue').mockResolvedValue(detail as any)
  const discuss = vi.spyOn(api, 'postDiscuss').mockResolvedValue({ state: 'discussing' })
  vi.spyOn(api, 'postMessage').mockResolvedValue({ user: { id: 1, role: 'user', content: 'hi', at: 'x' }, assistant: { id: 2, role: 'assistant', content: 'Hello back', at: 'x' } })
  mount()
  await waitFor(() => expect(screen.getByText('Fix the thing')).toBeInTheDocument())
  expect(screen.getByText('core.py')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: /discuss/i }))
  expect(discuss).toHaveBeenCalledWith('A')
  await userEvent.type(screen.getByPlaceholderText(/ask about/i), 'hi')
  await userEvent.click(screen.getByRole('button', { name: /send/i }))
  await waitFor(() => expect(screen.getByText('Hello back')).toBeInTheDocument())
})

test('approve sends the plan text', async () => {
  vi.spyOn(api, 'getIssue').mockResolvedValue({ ...detail, issue: { ...detail.issue, state: 'discussing' }, thread: { conversation_id: 'c', plan: null } } as any)
  const approve = vi.spyOn(api, 'postApprove').mockResolvedValue({ state: 'plan_approved' })
  mount()
  await waitFor(() => expect(screen.getByRole('button', { name: /approve plan/i })).toBeInTheDocument())
  const ta = screen.getByLabelText(/plan/i)
  expect((ta as HTMLTextAreaElement).value).toBe('A')  // prefilled from the brief's approach
  await userEvent.clear(ta); await userEvent.type(ta, 'Do X')
  await userEvent.click(screen.getByRole('button', { name: /approve plan/i }))
  expect(approve).toHaveBeenCalledWith('A', 'Do X')
})
```

- [ ] **Step 2: Run** Expected: FAIL.

- [ ] **Step 3: Implement** `BriefCard` (summary, likely cause, approach, files as code chips, effort, risks list, duplicate risk, AI policy note), `Chat` (message list with role classes, textarea with placeholder "Ask about the issue or the fix…", Send button disabled while a turn is in flight, error line on failure), and `Issue`:

```tsx
// src/pages/Issue.tsx
import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import * as api from '../api'
import type { IssueDetail, Message } from '../types'
import { BriefCard } from '../components/BriefCard'
import { Chat } from '../components/Chat'
import { ScoreBadge } from '../components/ScoreBadge'

export function Issue() {
  const { id = '' } = useParams()
  const [d, setD] = useState<IssueDetail | null>(null)
  const [plan, setPlan] = useState('')
  const [err, setErr] = useState<string | null>(null)
  const load = () => api.getIssue(id).then(x => { setD(x); setPlan(p => p || x.thread?.plan || x.brief?.approach || '') }).catch(e => setErr(String(e)))
  useEffect(() => { load() }, [id])
  if (err) return <p className="error">{err}</p>
  if (!d) return <p className="muted">Loading…</p>
  const state = d.issue.state
  const act = (fn: () => Promise<any>) => () => fn().then(load).catch(e => setErr(String(e)))
  const onSend = async (content: string): Promise<Message[]> => {
    const r = await api.postMessage(id, content)
    return [r.user, r.assistant]
  }
  return (
    <div className="issue">
      <header>
        <h2><a href={d.issue.url} target="_blank" rel="noreferrer">{d.issue.repo}#{d.issue.number}</a> {d.issue.title}</h2>
        <div className="meta">
          <span className={`state ${state}`}>{state}</span>
          {d.score && <ScoreBadge total={d.score.total} breakdown={d.score.breakdown} />}
          <span>{d.repo.stars}★ {d.repo.language}</span>
          {Object.entries(d.classification).map(([q, a]) => <span key={q} className="tag">{q}: {a.choice}</span>)}
        </div>
        <div className="actions">
          {state === 'analyzed' && <button onClick={act(() => api.postDiscuss(id))}>Discuss</button>}
          {state === 'discussing' && (
            <div className="plan">
              <label htmlFor="plan">Plan</label>
              <textarea id="plan" value={plan} onChange={e => setPlan(e.target.value)} rows={4} />
              <button onClick={act(() => api.postApprove(id, plan))}>Approve plan</button>
            </div>
          )}
          {!['dismissed', 'stale', 'merged', 'closed'].includes(state) && <button className="danger" onClick={act(() => api.postDismiss(id, ''))}>Dismiss</button>}
        </div>
      </header>
      <section className="two-col">
        <div>
          <h3>Brief</h3>
          {d.brief ? <BriefCard brief={d.brief} /> : <p className="muted">No brief yet. Runs: {d.runs.length}.</p>}
          <details><summary>Issue body</summary><pre className="body">{d.issue.body}</pre></details>
          <details><summary>History</summary><ul>{d.transitions.map((t, i) => <li key={i}>{t.at} {t.from_state} → {t.to_state} ({t.actor}) {t.note}</li>)}</ul></details>
        </div>
        <div>
          <h3>Discussion</h3>
          <Chat initial={d.messages} onSend={onSend} />
        </div>
      </section>
    </div>
  )
}
```

- [ ] **Step 4: Run** `npm test` Expected: pass. Commit.

---

### Task 7: Agents page with live events

**Files:**
- Create: `web/src/pages/Agents.tsx`
- Test: `web/src/pages/Agents.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// src/pages/Agents.test.tsx
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { vi } from 'vitest'
import * as api from '../api'
import { Agents } from './Agents'

test('lists runs and polls events for the selected running run until it finishes', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  vi.spyOn(api, 'getRuns').mockResolvedValue([
    { id: 5, status: 'running', model: 'm', started_at: 'x', finished_at: null, duration_s: null, input_tokens: null, output_tokens: null, error: null, repo: 'o/r', number: 1, title: 'T' },
  ])
  const ev = vi.spyOn(api, 'getEvents')
    .mockResolvedValueOnce({ status: 'running', events: [{ seq: 1, at: 'x', event: { event: 'step_update', step_update: { step_type: 'tool', tool_name: 'view_file' } } }] })
    .mockResolvedValueOnce({ status: 'done', events: [{ seq: 2, at: 'x', event: { event: 'result', result: { status: 'SUCCESS' } } }] })
  render(<Agents />)
  await waitFor(() => expect(screen.getByText(/o\/r#1/)).toBeInTheDocument())
  await userEvent.click(screen.getByText(/o\/r#1/))
  await waitFor(() => expect(screen.getByText(/view_file/)).toBeInTheDocument())
  await waitFor(() => expect(screen.getByText(/result/)).toBeInTheDocument(), { timeout: 5000 })
  expect(ev).toHaveBeenLastCalledWith(5, 1)
  vi.useRealTimers()
})
```

- [ ] **Step 2: Run** Expected: FAIL.

- [ ] **Step 3: Implement** `Agents.tsx`: loads runs (refresh every 10 s), a list on the left (id, status, repo#number, duration, tokens, error), and on the right the selected run's events. A `useEffect` on the selected run keeps `after` in state and calls `getEvents(id, after)` every 2 s while the returned status is `running`; stops otherwise. Each event is rendered as one compact line: `step_update` shows `step_type` and `tool_name` or the first 120 characters of `text_delta`; `result` shows the status and token usage; unknown shapes show the raw JSON truncated. A totals line sums `input_tokens` and `output_tokens` of runs started today.

- [ ] **Step 4: Run** `npm test` Expected: pass. Commit.

---

### Task 8: Build, serve, live check, README

- [ ] **Step 1:** `cd web && npm run build` then `uv run scout serve` in one terminal and `uv run scout open`. Confirm: the queue renders real rows; clicking a row shows its brief; Discuss on an analyzed issue works and a chat turn returns a reply (this is the first live `--conversation` use from the API); the agents page follows a run started with `uv run scout analyze --limit 1`.
- [ ] **Step 2:** `bash install_service.sh` installs `oss-scout.service`; `systemctl --user status oss-scout` is active; the URL works after a service restart.
- [ ] **Step 3:** README: dashboard section (how to open it, the three pages, what each action does, that nothing reaches GitHub), dev loop (`npm run dev` with the proxy), and the two services.
- [ ] **Step 4:** Commit `git commit -m "oss-scout: stage 3 dashboard live, service, README"`.
