# OSS Scout Stage 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `scout` CLI that harvests open GitHub issues hourly, classifies them with Jev, scores and shortlists them, and prints a ranked, categorized queue.

**Architecture:** A Python package with a SQLite store. Pure functions for query building, signal computation, exclusions, classification parsing, and scoring; thin I/O wrappers for GitHub (urllib with the `gh` token) and Jev (urllib with a response cache). A click CLI wires them. A systemd user timer runs the three steps hourly.

**Tech Stack:** Python 3.12+, uv, click, rich, pyyaml, python-dotenv, pytest. GitHub REST search plus GraphQL. TypeSafe AI Jev API. Antigravity CLI (`agy`) only as the fallback classifier.

**Spec:** `oss-scout/docs/superpowers/specs/2026-10-01-oss-scout-design.md`

## Global Constraints

- Python `>= 3.12`, src layout, `uv` for env and lockfile. Tests via `uv run pytest`.
- The bot never writes to GitHub. Stage 1 code makes only GET and GraphQL query requests. No `mutation` string may appear in the GitHub client.
- GitHub token from `gh auth token`. Jev key from `jev_model_api_key` in `oss-scout/.env`.
- All state changes go through `transitions.apply`. No other code sets `issues.state`.
- No test touches a real DB file, the network, or the Jev cache; each gets a temp path and recorded fixtures.
- `dismissed` and `stale` issues are never advanced by the harvester, classifier, or scorer.
- Score terms are 0 to 1 before weighting; weights and category values come from `scout.yaml`.
- Defaults verbatim from the spec: `min_stars: 200`, repo idle cutoff 60 days, issue updated within 30 days, 3 pages per query, `shortlist_count: 20`, `shortlist_floor: 4.0`, repo signals refreshed every 7 days, contributing text every 30 days.

## Review Focus

1. An issue whose body is `null` on GitHub (very common): harvester and classifier must treat it as empty text, not crash. Test in Task 4 and Task 5.
2. A repo whose `pullRequests` or `issues` list is empty (new repo): `external_merge_rate` and `median_first_response_hours` must return `None`, and the score must treat `None` as 0 for that term. Test in Task 4 and Task 7.
3. A GitHub 403 whose body says secondary rate limit with no `X-RateLimit-Reset` in the future: the client must sleep a bounded default (60 s) and retry, not spin. Test in Task 3.
4. Jev returning a choice not in the question's criteria (model drift): parser stores `choice = null` and the issue stays `harvested`. Test in Task 5.
5. Re-running `scout harvest` on an issue already `dismissed`: only `last_seen_at` changes. Test in Task 4.

---

### Task 1: Project skeleton and config

**Files:**
- Create: `oss-scout/pyproject.toml`, `oss-scout/scout.yaml`, `oss-scout/.env.example`, `oss-scout/.gitignore`, `oss-scout/src/oss_scout/__init__.py`, `oss-scout/src/oss_scout/config.py`, `oss-scout/src/oss_scout/cli.py`
- Test: `oss-scout/tests/test_config.py`

**Interfaces:**
- Produces: `config.load_config(path: Path | None = None) -> Config` (dataclass; fields below). `Config.data_dir: Path`, `Config.db_path: Path` property.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config.py
from pathlib import Path
from oss_scout.config import load_config, Config

def test_defaults_without_file(tmp_path):
    cfg = load_config(tmp_path / "missing.yaml")
    assert isinstance(cfg, Config)
    assert cfg.min_stars == 200
    assert cfg.shortlist_count == 20
    assert cfg.shortlist_floor == 4.0
    assert "Python" in cfg.languages
    assert cfg.weights["recognition"] == 3.0

def test_yaml_overrides(tmp_path):
    p = tmp_path / "scout.yaml"
    p.write_text("min_stars: 50\nweights:\n  bounty: 2.0\n")
    cfg = load_config(p)
    assert cfg.min_stars == 50
    assert cfg.weights["bounty"] == 2.0
    assert cfg.weights["recognition"] == 3.0  # untouched default survives a partial override
```

- [ ] **Step 2: Create pyproject and run the test to see it fail**

```toml
# pyproject.toml
[project]
name = "oss-scout"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["pyyaml>=6.0", "python-dotenv>=1.0", "click>=8.1", "rich>=13.0"]

[project.optional-dependencies]
dev = ["pytest>=8.0"]

[project.scripts]
scout = "oss_scout.cli:main"

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]
```

Run: `cd oss-scout && uv sync --extra dev && uv run pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: oss_scout.config`

- [ ] **Step 3: Write config.py**

```python
# src/oss_scout/config.py
from __future__ import annotations
import os
from dataclasses import dataclass, field, fields
from pathlib import Path
import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_WEIGHTS = {
    "recognition": 3.0, "mergeability": 3.0, "language_fit": 2.0, "category": 2.0,
    "difficulty_fit": 1.5, "freshness": 1.0, "bounty": 0.5, "ai_policy": 1.0,
}
DEFAULT_CATEGORY_VALUES = {
    "quant_trading": 1.0, "prediction_markets_finance": 1.0, "ai_agents_llm": 1.0,
    "data_scraping": 0.7, "dev_tools_cli": 0.7, "web_frontend": 0.8, "other": 0.3,
}
DEFAULT_LANGUAGE_FIT = {"Python": 1.0, "TypeScript": 0.8, "JavaScript": 0.5}
DEFAULT_CATEGORY_KEYWORDS = {
    "quant_trading": ["backtest", "backtesting", "trading bot", "algorithmic trading"],
    "prediction_markets_finance": ["polymarket", "kalshi", "prediction market"],
    "ai_agents_llm": ["mcp server", "llm agent", "langchain", "tool calling"],
}


@dataclass
class Config:
    min_stars: int = 200
    repo_idle_days: int = 60
    issue_updated_within_days: int = 30
    pages_per_query: int = 3
    per_page: int = 100
    languages: list[str] = field(default_factory=lambda: ["Python", "TypeScript"])
    good_labels: list[str] = field(default_factory=lambda: ["good first issue", "help wanted"])
    bounty_labels: list[str] = field(default_factory=lambda: ["bounty", "💎 bounty", "algora", "💰 bounty"])
    category_keywords: dict[str, list[str]] = field(default_factory=lambda: dict(DEFAULT_CATEGORY_KEYWORDS))
    known_orgs: list[str] = field(default_factory=lambda: [
        "pandas-dev", "numpy", "scipy", "python", "pallets", "psf", "encode", "tiangolo", "pydantic",
        "huggingface", "langchain-ai", "microsoft", "google", "facebook", "vercel", "apache", "polars-rs",
    ])
    ai_policy_banned_patterns: list[str] = field(default_factory=lambda: [
        r"no\s+(ai|llm)[-\s]?(generated|written|assisted)",
        r"(ai|llm)[-\s]?(generated|assisted)\s+(prs?|pull requests?|contributions?|code)\s+(are|is|will be)\s+(not accepted|rejected|closed|banned|prohibited)",
        r"do not (submit|open) (ai|llm)[-\s]?(generated|assisted)",
    ])
    ai_policy_discouraged_patterns: list[str] = field(default_factory=lambda: [
        r"(ai|llm)[-\s]?(generated|assisted)",
        r"copilot|chatgpt|claude|gemini",
    ])
    repo_signal_refresh_days: int = 7
    contributing_refresh_days: int = 30
    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    category_values: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_CATEGORY_VALUES))
    language_fit: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_LANGUAGE_FIT))
    shortlist_count: int = 20
    shortlist_floor: float = 4.0
    dismiss_confidence: float = 0.9
    jev_model: str = "jev-latest"
    dropped_questions: list[str] = field(default_factory=list)
    gemini_model: str = "gemini-3.8-flash-medium"
    data_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "data")

    @property
    def db_path(self) -> Path:
        return self.data_dir / "scout.db"

    @property
    def jev_cache_path(self) -> Path:
        return self.data_dir / "jev_cache.db"

    @property
    def log_path(self) -> Path:
        return self.data_dir / "scout.log"


def load_config(path: Path | None = None) -> Config:
    load_dotenv(PROJECT_ROOT / ".env")
    path = path or PROJECT_ROOT / "scout.yaml"
    raw = {}
    if Path(path).exists():
        raw = yaml.safe_load(Path(path).read_text()) or {}
    cfg = Config()
    for f in fields(Config):
        if f.name not in raw:
            continue
        current = getattr(cfg, f.name)
        value = raw[f.name]
        if isinstance(current, dict) and isinstance(value, dict):
            merged = dict(current)
            merged.update(value)
            setattr(cfg, f.name, merged)
        elif f.name == "data_dir":
            setattr(cfg, f.name, Path(value))
        else:
            setattr(cfg, f.name, value)
    return cfg


def jev_api_key() -> str:
    key = os.environ.get("jev_model_api_key")
    if not key:
        raise RuntimeError("jev_model_api_key not set; copy .env.example to .env")
    return key
```

- [ ] **Step 4: Write scout.yaml, .env.example, .gitignore, cli stub**

`scout.yaml` holds the same defaults as comments plus the keys most likely to be tuned:

```yaml
# OSS Scout config. Any key here overrides the default in src/oss_scout/config.py.
min_stars: 200
languages: [Python, TypeScript]
shortlist_count: 20
shortlist_floor: 4.0
weights:
  recognition: 3.0
  mergeability: 3.0
  language_fit: 2.0
  category: 2.0
  difficulty_fit: 1.5
  freshness: 1.0
  bounty: 0.5
  ai_policy: 1.0
category_values:
  quant_trading: 1.0
  prediction_markets_finance: 1.0
  ai_agents_llm: 1.0
  data_scraping: 0.7
  dev_tools_cli: 0.7
  web_frontend: 0.8
  other: 0.3
# Questions dropped by `scout validate-classifier` go here, e.g. [difficulty]
dropped_questions: []
```

`.env.example`: `jev_model_api_key=`. `.gitignore`: `.venv/`, `data/`, `.env`, `__pycache__/`, `*.egg-info/`.

```python
# src/oss_scout/cli.py
import click

@click.group()
def main():
    """OSS Scout: find open-source issues worth your time."""

@main.command()
def status():
    click.echo("oss-scout: no data yet")
```

- [ ] **Step 5: Run tests, then commit**

Run: `uv run pytest -v` Expected: 2 passed.

```bash
git add oss-scout/pyproject.toml oss-scout/uv.lock oss-scout/scout.yaml oss-scout/.env.example oss-scout/.gitignore oss-scout/src oss-scout/tests
git commit -m "oss-scout: project skeleton and config loader"
```

---

### Task 2: Database schema and state machine

**Files:**
- Create: `src/oss_scout/db.py`, `src/oss_scout/transitions.py`
- Test: `tests/test_db.py`, `tests/test_transitions.py`

**Interfaces:**
- Produces: `db.connect(path: Path) -> sqlite3.Connection` (row_factory = sqlite3.Row, WAL, foreign keys on; runs `migrate`). `db.migrate(conn)`. `db.utcnow() -> str` ISO-8601 UTC with `Z`.
- Produces: `transitions.STATES`, `transitions.apply(conn, issue_id: str, to_state: str, actor: str, note: str = "") -> None`, raises `transitions.InvalidTransition`.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_db.py
import sqlite3
from oss_scout import db

def test_connect_creates_tables(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    names = {r[0] for r in conn.execute("select name from sqlite_master where type='table'")}
    assert {"repos", "issues", "classifications", "scores", "transitions", "meta"} <= names
    assert conn.execute("select value from meta where key='schema_version'").fetchone()[0] == "1"

def test_migrate_is_idempotent(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.migrate(conn)
    db.migrate(conn)
```

```python
# tests/test_transitions.py
import pytest
from oss_scout import db, transitions

def _issue(conn, state="harvested"):
    conn.execute("insert into repos(full_name) values ('o/r')")
    conn.execute("insert into issues(id, repo, number, title, url, state, harvested_at, last_seen_at) values ('I1','o/r',1,'t','u',?, 'x','x')", (state,))
    conn.commit()

def test_valid_edge_writes_row_and_updates_state(tmp_path):
    conn = db.connect(tmp_path / "t.db"); _issue(conn)
    transitions.apply(conn, "I1", "classified", "classifier", "ok")
    assert conn.execute("select state from issues where id='I1'").fetchone()[0] == "classified"
    row = conn.execute("select from_state,to_state,actor,note from transitions").fetchone()
    assert tuple(row) == ("harvested", "classified", "classifier", "ok")

def test_invalid_edge_raises_and_changes_nothing(tmp_path):
    conn = db.connect(tmp_path / "t.db"); _issue(conn)
    with pytest.raises(transitions.InvalidTransition):
        transitions.apply(conn, "I1", "pr_open", "user")
    assert conn.execute("select state from issues where id='I1'").fetchone()[0] == "harvested"
    assert conn.execute("select count(*) from transitions").fetchone()[0] == 0

def test_user_can_dismiss_from_any_state_but_classifier_only_from_classified(tmp_path):
    conn = db.connect(tmp_path / "t.db"); _issue(conn, "shortlisted")
    with pytest.raises(transitions.InvalidTransition):
        transitions.apply(conn, "I1", "dismissed", "classifier")
    transitions.apply(conn, "I1", "dismissed", "user")
    assert conn.execute("select state from issues where id='I1'").fetchone()[0] == "dismissed"

def test_terminal_states_cannot_advance(tmp_path):
    conn = db.connect(tmp_path / "t.db"); _issue(conn, "dismissed")
    with pytest.raises(transitions.InvalidTransition):
        transitions.apply(conn, "I1", "classified", "classifier")
```

- [ ] **Step 2: Run to see failure** `uv run pytest tests/test_db.py tests/test_transitions.py -v` Expected: ImportError.

- [ ] **Step 3: Write db.py**

```python
# src/oss_scout/db.py
from __future__ import annotations
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = "1"

SCHEMA = """
create table if not exists meta (key text primary key, value text not null);
create table if not exists repos (
  full_name text primary key,
  stars integer, language text, description text, pushed_at text,
  org_known integer default 0,
  external_merge_rate real, median_first_response_hours real,
  signals_fetched_at text,
  contributing_text text, contributing_fetched_at text,
  ai_policy text default 'none',
  excluded_reason text
);
create table if not exists issues (
  id text primary key,
  repo text not null references repos(full_name),
  number integer not null,
  title text not null, body text, labels text default '[]', url text not null,
  created_at text, updated_at text, comments integer default 0,
  has_bounty integer default 0, bounty_source text,
  linked_pr integer default 0, assignee text,
  state text not null default 'harvested',
  harvested_at text not null, last_seen_at text not null
);
create index if not exists issues_state on issues(state);
create index if not exists issues_repo on issues(repo);
create table if not exists classifications (
  id integer primary key autoincrement,
  issue_id text not null references issues(id),
  question text not null, choice text, confidence real, probabilities text,
  model text not null, asked_at text not null
);
create index if not exists classifications_issue on classifications(issue_id, question);
create table if not exists scores (
  id integer primary key autoincrement,
  issue_id text not null references issues(id),
  total real not null, breakdown text not null, weights_hash text not null, scored_at text not null
);
create index if not exists scores_issue on scores(issue_id, scored_at);
create table if not exists transitions (
  id integer primary key autoincrement,
  issue_id text not null references issues(id),
  from_state text not null, to_state text not null, actor text not null, note text default '',
  at text not null
);
create index if not exists transitions_issue on transitions(issue_id);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def connect(path: Path) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("pragma journal_mode=wal")
    conn.execute("pragma foreign_keys=on")
    migrate(conn)
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.execute("insert or ignore into meta(key, value) values ('schema_version', ?)", (SCHEMA_VERSION,))
    conn.commit()
```

- [ ] **Step 4: Write transitions.py**

```python
# src/oss_scout/transitions.py
from __future__ import annotations
import sqlite3
from oss_scout.db import utcnow

STATES = [
    "harvested", "classified", "shortlisted", "analyzed", "discussing", "plan_approved",
    "drafting", "pr_open", "merged", "closed", "dismissed", "stale",
]
TERMINAL = {"merged", "closed", "dismissed", "stale"}

# (from, to) -> set of actors allowed, "*" meaning any actor. Actor "agent:<id>" matches "agent".
EDGES: dict[tuple[str, str], set[str]] = {
    ("harvested", "classified"): {"classifier"},
    ("classified", "dismissed"): {"classifier", "user"},
    ("classified", "shortlisted"): {"scorer"},
    ("shortlisted", "classified"): {"scorer"},          # fell out of the shortlist on re-score
    ("shortlisted", "analyzed"): {"agent"},
    ("analyzed", "discussing"): {"user"},
    ("discussing", "plan_approved"): {"user"},
    ("plan_approved", "drafting"): {"user", "agent"},
    ("drafting", "discussing"): {"user", "agent"},      # tests failed
    ("drafting", "pr_open"): {"user"},
    ("pr_open", "merged"): {"harvester", "user"},
    ("pr_open", "closed"): {"harvester", "user"},
}
for _s in STATES:
    if _s not in TERMINAL:
        EDGES.setdefault((_s, "dismissed"), set()).add("user")
        EDGES.setdefault((_s, "stale"), set()).add("harvester")


class InvalidTransition(Exception):
    pass


def _actor_kind(actor: str) -> str:
    return "agent" if actor.startswith("agent:") else actor


def apply(conn: sqlite3.Connection, issue_id: str, to_state: str, actor: str, note: str = "") -> None:
    if to_state not in STATES:
        raise InvalidTransition(f"unknown state {to_state}")
    row = conn.execute("select state from issues where id = ?", (issue_id,)).fetchone()
    if row is None:
        raise InvalidTransition(f"unknown issue {issue_id}")
    from_state = row[0]
    allowed = EDGES.get((from_state, to_state), set())
    if _actor_kind(actor) not in allowed:
        raise InvalidTransition(f"{actor} may not move {issue_id} from {from_state} to {to_state}")
    with conn:
        conn.execute(
            "insert into transitions(issue_id, from_state, to_state, actor, note, at) values (?,?,?,?,?,?)",
            (issue_id, from_state, to_state, actor, note, utcnow()),
        )
        conn.execute("update issues set state = ? where id = ?", (to_state, issue_id))
```

- [ ] **Step 5: Run tests, commit** `uv run pytest -v` Expected: all pass.
`git commit -am "oss-scout: sqlite schema and issue state machine"` (add new files first).

---

### Task 3: GitHub client

**Files:**
- Create: `src/oss_scout/github_client.py`
- Test: `tests/test_github_client.py`, fixtures `tests/fixtures/gh_search_page.json`, `tests/fixtures/gh_repo_signals.json`

**Interfaces:**
- Produces: `GitHubClient(token: str, sleep=time.sleep, opener=urllib.request.urlopen, now=time.time)`.
  - `.rest(path: str, params: dict | None = None) -> tuple[Any, dict]` returns (json, headers).
  - `.graphql(query: str, variables: dict) -> dict` returns the `data` object; raises `GitHubError` on `errors`.
  - `.search_issues(q: str, pages: int, per_page: int) -> Iterator[dict]` yields raw item dicts.
  - `.repo_signals(owner: str, name: str) -> dict` with keys `stars, pushed_at, description, language, merged_prs: list[{authorAssociation, mergedAt}], issues: list[{createdAt, comments: list[{createdAt, authorAssociation}]}]`.
  - `.has_closing_pr(issue_node_id: str) -> bool`.
  - `.contributing_text(owner: str, name: str) -> str | None`.
  - `github_token() -> str` runs `gh auth token`.
- `GitHubError(Exception)`, `RateLimited(GitHubError)` only raised internally; public methods sleep and retry.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_github_client.py
import io, json
from urllib.error import HTTPError
from email.message import Message
import pytest
from oss_scout.github_client import GitHubClient, GitHubError

class FakeResp(io.BytesIO):
    def __init__(self, body, headers=None):
        super().__init__(json.dumps(body).encode())
        self.headers = Message()
        for k, v in (headers or {}).items():
            self.headers[k] = v
    def __enter__(self): return self
    def __exit__(self, *a): return False

def make_client(responses):
    """responses: list of callables or FakeResp/HTTPError to return in order."""
    calls = []
    slept = []
    def opener(req, timeout=0):
        calls.append(req)
        r = responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r
    c = GitHubClient("tok", sleep=slept.append, opener=opener, now=lambda: 1000.0)
    return c, calls, slept

def test_rest_sends_token_and_params():
    c, calls, _ = make_client([FakeResp({"ok": 1}, {"X-RateLimit-Remaining": "10"})])
    body, headers = c.rest("search/issues", {"q": "a b", "per_page": 3})
    assert body == {"ok": 1}
    assert calls[0].full_url == "https://api.github.com/search/issues?q=a+b&per_page=3"
    assert calls[0].get_header("Authorization") == "Bearer tok"

def test_rate_limit_sleeps_until_reset_then_retries():
    err = HTTPError("u", 403, "rate", Message(), io.BytesIO(b'{"message":"API rate limit exceeded"}'))
    err.headers["X-RateLimit-Remaining"] = "0"; err.headers["X-RateLimit-Reset"] = "1030"
    c, calls, slept = make_client([err, FakeResp({"ok": 1})])
    c.rest("rate_limit")
    assert slept == [31.0]  # reset - now + 1
    assert len(calls) == 2

def test_secondary_limit_without_reset_sleeps_default():
    err = HTTPError("u", 403, "rate", Message(), io.BytesIO(b'{"message":"You have exceeded a secondary rate limit"}'))
    c, calls, slept = make_client([err, FakeResp({"ok": 1})])
    c.rest("x")
    assert slept == [60.0]

def test_5xx_retries_three_times_then_raises():
    errs = [HTTPError("u", 502, "bad", Message(), io.BytesIO(b"")) for _ in range(4)]
    c, calls, slept = make_client(errs)
    with pytest.raises(GitHubError):
        c.rest("x")
    assert len(calls) == 4

def test_422_raises_without_retry():
    err = HTTPError("u", 422, "bad", Message(), io.BytesIO(b'{"message":"Validation Failed"}'))
    c, calls, _ = make_client([err])
    with pytest.raises(GitHubError):
        c.rest("search/issues", {"q": "bad"})
    assert len(calls) == 1

def test_search_issues_paginates_and_stops_on_short_page():
    page1 = {"total_count": 3, "items": [{"id": 1}, {"id": 2}]}
    page2 = {"total_count": 3, "items": [{"id": 3}]}
    c, calls, _ = make_client([FakeResp(page1), FakeResp(page2)])
    items = list(c.search_issues("q", pages=5, per_page=2))
    assert [i["id"] for i in items] == [1, 2, 3]
    assert len(calls) == 2
    assert "advanced_search=true" in calls[0].full_url

def test_graphql_raises_on_errors():
    c, _, _ = make_client([FakeResp({"errors": [{"message": "nope"}]})])
    with pytest.raises(GitHubError):
        c.graphql("query { x }", {})

def test_has_closing_pr_true_only_for_will_close_target():
    data = {"data": {"node": {"timelineItems": {"nodes": [
        {"willCloseTarget": False, "source": {"number": 1, "state": "OPEN"}},
        {"willCloseTarget": True, "source": {"number": 2, "state": "OPEN"}},
    ]}}}}
    c, _, _ = make_client([FakeResp(data)])
    assert c.has_closing_pr("I_x") is True
    data2 = {"data": {"node": {"timelineItems": {"nodes": [
        {"willCloseTarget": True, "source": {"number": 2, "state": "MERGED"}}]}}}}
    c2, _, _ = make_client([FakeResp(data2)])
    assert c2.has_closing_pr("I_x") is False  # merged PR means the issue will close upstream; harvester marks stale

def test_contributing_text_tries_two_paths_and_decodes_base64():
    import base64
    missing = HTTPError("u", 404, "nf", Message(), io.BytesIO(b"{}"))
    found = FakeResp({"content": base64.b64encode(b"Please no AI PRs").decode(), "encoding": "base64"})
    c, calls, _ = make_client([missing, found])
    assert c.contributing_text("o", "r") == "Please no AI PRs"
    assert calls[0].full_url.endswith("/contents/CONTRIBUTING.md")
    assert calls[1].full_url.endswith("/contents/.github/CONTRIBUTING.md")

def test_contributing_text_none_when_absent():
    missing = HTTPError("u", 404, "nf", Message(), io.BytesIO(b"{}"))
    c, _, _ = make_client([missing, HTTPError("u", 404, "nf", Message(), io.BytesIO(b"{}"))])
    assert c.contributing_text("o", "r") is None
```

- [ ] **Step 2: Run to see failure.** `uv run pytest tests/test_github_client.py -v` Expected: ImportError.

- [ ] **Step 3: Write github_client.py**

```python
# src/oss_scout/github_client.py
from __future__ import annotations
import base64
import json
import subprocess
import time
import urllib.parse
import urllib.request
from typing import Any, Callable, Iterator
from urllib.error import HTTPError

API = "https://api.github.com"
DEFAULT_SECONDARY_SLEEP = 60.0
MAX_5XX_RETRIES = 3


class GitHubError(Exception):
    pass


def github_token() -> str:
    out = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, check=True)
    return out.stdout.strip()


class GitHubClient:
    def __init__(self, token: str, sleep: Callable[[float], None] = time.sleep,
                 opener: Callable = urllib.request.urlopen, now: Callable[[], float] = time.time,
                 timeout: float = 30.0):
        self.token = token
        self.sleep = sleep
        self.opener = opener
        self.now = now
        self.timeout = timeout

    # ---- low level -------------------------------------------------------
    def _request(self, method: str, url: str, body: bytes | None = None) -> tuple[Any, dict]:
        retries_5xx = 0
        while True:
            req = urllib.request.Request(url, data=body, method=method, headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "oss-scout",
                **({"Content-Type": "application/json"} if body else {}),
            })
            try:
                with self.opener(req, timeout=self.timeout) as resp:
                    raw = resp.read()
                    headers = {k: v for k, v in resp.headers.items()}
                    return (json.loads(raw) if raw else None), headers
            except HTTPError as e:
                status = e.code
                try:
                    msg = json.loads(e.read() or b"{}").get("message", "")
                except Exception:
                    msg = ""
                if status in (403, 429) and ("rate limit" in msg.lower() or e.headers.get("X-RateLimit-Remaining") == "0"):
                    reset = e.headers.get("X-RateLimit-Reset")
                    wait = DEFAULT_SECONDARY_SLEEP
                    if reset:
                        wait = max(1.0, float(reset) - self.now() + 1.0)
                    self.sleep(wait)
                    continue
                if 500 <= status < 600 and retries_5xx < MAX_5XX_RETRIES:
                    retries_5xx += 1
                    self.sleep(2.0 ** retries_5xx)
                    continue
                raise GitHubError(f"{method} {url} -> {status} {msg}") from e

    def rest(self, path: str, params: dict | None = None) -> tuple[Any, dict]:
        url = f"{API}/{path.lstrip('/')}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        return self._request("GET", url)

    def graphql(self, query: str, variables: dict) -> dict:
        body = json.dumps({"query": query, "variables": variables}).encode()
        data, _ = self._request("POST", f"{API}/graphql", body)
        if not data or data.get("errors"):
            raise GitHubError(f"graphql: {data.get('errors') if data else 'empty response'}")
        return data["data"]

    # ---- high level ------------------------------------------------------
    def search_issues(self, q: str, pages: int, per_page: int = 100) -> Iterator[dict]:
        for page in range(1, pages + 1):
            data, _ = self.rest("search/issues", {"q": q, "per_page": per_page, "page": page, "advanced_search": "true"})
            items = data.get("items", []) if data else []
            yield from items
            if len(items) < per_page:
                return

    REPO_SIGNALS_QUERY = """
    query($owner:String!,$name:String!){
      repository(owner:$owner,name:$name){
        stargazerCount pushedAt description primaryLanguage{name}
        pullRequests(last:30,states:MERGED){ nodes{ authorAssociation mergedAt } }
        issues(last:20,states:[OPEN,CLOSED]){ nodes{ createdAt comments(first:5){ nodes{ createdAt authorAssociation } } } }
      }
    }"""

    def repo_signals(self, owner: str, name: str) -> dict:
        data = self.graphql(self.REPO_SIGNALS_QUERY, {"owner": owner, "name": name})
        r = data["repository"]
        if r is None:
            raise GitHubError(f"repository {owner}/{name} not found")
        return {
            "stars": r["stargazerCount"],
            "pushed_at": r["pushedAt"],
            "description": r.get("description") or "",
            "language": (r.get("primaryLanguage") or {}).get("name"),
            "merged_prs": r["pullRequests"]["nodes"],
            "issues": [{"createdAt": i["createdAt"], "comments": i["comments"]["nodes"]} for i in r["issues"]["nodes"]],
        }

    CLOSING_PR_QUERY = """
    query($id:ID!){ node(id:$id){ ... on Issue {
      timelineItems(itemTypes:[CROSS_REFERENCED_EVENT],last:20){ nodes{ ... on CrossReferencedEvent {
        willCloseTarget source{ ... on PullRequest { number state } } } } } } } }"""

    def has_closing_pr(self, issue_node_id: str) -> bool:
        data = self.graphql(self.CLOSING_PR_QUERY, {"id": issue_node_id})
        nodes = (data.get("node") or {}).get("timelineItems", {}).get("nodes", [])
        return any(n.get("willCloseTarget") and (n.get("source") or {}).get("state") == "OPEN" for n in nodes)

    def contributing_text(self, owner: str, name: str) -> str | None:
        for path in ("CONTRIBUTING.md", ".github/CONTRIBUTING.md"):
            try:
                data, _ = self.rest(f"repos/{owner}/{name}/contents/{path}")
            except GitHubError as e:
                if "404" in str(e):
                    continue
                raise
            if data and data.get("encoding") == "base64":
                return base64.b64decode(data["content"]).decode("utf-8", errors="replace")
        return None
```

- [ ] **Step 4: Run tests, commit.** `uv run pytest tests/test_github_client.py -v` Expected: 10 passed.
`git add src/oss_scout/github_client.py tests/test_github_client.py && git commit -m "oss-scout: GitHub client with rate-limit handling"`

---

### Task 4: Harvester

**Files:**
- Create: `src/oss_scout/harvest.py`
- Test: `tests/test_harvest.py`

**Interfaces:**
- Consumes: `GitHubClient` methods from Task 3, `db.connect`, `transitions.apply`, `Config`.
- Produces:
  - `build_queries(cfg: Config, today: date) -> list[str]`
  - `external_merge_rate(merged_prs: list[dict]) -> float | None`
  - `median_first_response_hours(issues: list[dict]) -> float | None`
  - `detect_ai_policy(text: str | None, cfg: Config) -> str` (`none|discouraged|banned`)
  - `repo_exclusion_reason(repo: dict, cfg: Config, now: datetime) -> str | None`
  - `issue_from_search_item(item: dict, cfg: Config) -> dict` (normalized row dict)
  - `run_harvest(conn, gh: GitHubClient, cfg: Config, now: datetime | None = None) -> HarvestSummary` dataclass with `queries, seen, new_issues, updated_issues, excluded_repos, skipped_linked_pr, marked_stale, errors: list[str]`.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_harvest.py
from datetime import date, datetime, timezone, timedelta
import pytest
from oss_scout import db, harvest
from oss_scout.config import Config

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)

def test_build_queries_cover_languages_labels_keywords_and_bounty():
    cfg = Config(languages=["Python"], good_labels=["good first issue"],
                 category_keywords={"quant_trading": ["backtest"]}, bounty_labels=["bounty"])
    qs = harvest.build_queries(cfg, date(2026, 10, 1))
    assert 'is:issue is:open no:assignee updated:>=2026-09-01 language:Python label:"good first issue"' in qs
    assert 'is:issue is:open no:assignee updated:>=2026-09-01 "backtest" in:title,body' in qs
    assert 'is:issue is:open no:assignee updated:>=2026-09-01 label:"bounty"' in qs

def test_external_merge_rate():
    prs = [{"authorAssociation": a} for a in ["MEMBER", "CONTRIBUTOR", "NONE", "OWNER", "FIRST_TIME_CONTRIBUTOR"]]
    assert harvest.external_merge_rate(prs) == pytest.approx(3 / 5)
    assert harvest.external_merge_rate([]) is None

def test_median_first_response_hours_uses_first_maintainer_comment():
    issues = [
        {"createdAt": "2026-09-01T00:00:00Z", "comments": [
            {"createdAt": "2026-09-01T01:00:00Z", "authorAssociation": "NONE"},
            {"createdAt": "2026-09-01T04:00:00Z", "authorAssociation": "MEMBER"}]},
        {"createdAt": "2026-09-02T00:00:00Z", "comments": [
            {"createdAt": "2026-09-02T02:00:00Z", "authorAssociation": "COLLABORATOR"}]},
        {"createdAt": "2026-09-03T00:00:00Z", "comments": []},  # no response: ignored
    ]
    assert harvest.median_first_response_hours(issues) == pytest.approx(3.0)
    assert harvest.median_first_response_hours([]) is None
    assert harvest.median_first_response_hours([{"createdAt": "x", "comments": []}]) is None

@pytest.mark.parametrize("text,expected", [
    (None, "none"),
    ("Thanks for contributing! Run the tests.", "none"),
    ("We do not accept AI-generated pull requests.", "banned"),
    ("No LLM generated code please.", "banned"),
    ("If you used Copilot, say so in the PR.", "discouraged"),
    ("AI-assisted contributions must be disclosed.", "discouraged"),
])
def test_detect_ai_policy(text, expected):
    assert harvest.detect_ai_policy(text, Config()) == expected

def test_repo_exclusion_reasons():
    cfg = Config(min_stars=200, repo_idle_days=60)
    base = {"stars": 500, "pushed_at": "2026-09-30T00:00:00Z", "ai_policy": "none"}
    assert harvest.repo_exclusion_reason(base, cfg, NOW) is None
    assert harvest.repo_exclusion_reason({**base, "stars": 10}, cfg, NOW) == "stars<200"
    assert harvest.repo_exclusion_reason({**base, "pushed_at": "2026-01-01T00:00:00Z"}, cfg, NOW) == "idle>60d"
    assert harvest.repo_exclusion_reason({**base, "ai_policy": "banned"}, cfg, NOW) == "ai_policy_banned"
    assert harvest.repo_exclusion_reason({**base, "ai_policy": "discouraged"}, cfg, NOW) is None

def test_issue_from_search_item_handles_null_body_and_bounty_label():
    item = {"node_id": "I1", "number": 7, "title": "t", "body": None, "html_url": "u",
            "repository_url": "https://api.github.com/repos/o/r", "labels": [{"name": "💎 Bounty"}],
            "created_at": "c", "updated_at": "u2", "comments": 2, "assignee": None}
    row = harvest.issue_from_search_item(item, Config())
    assert row["repo"] == "o/r" and row["body"] == "" and row["has_bounty"] == 1 and row["labels"] == ["💎 Bounty"]

class FakeGH:
    def __init__(self, items, signals, closing=None, contributing=None):
        self.items, self.signals, self.closing, self.contributing = items, signals, closing or {}, contributing or {}
        self.signal_calls = 0
    def search_issues(self, q, pages, per_page=100):
        yield from self.items.get(q, [])
    def repo_signals(self, owner, name):
        self.signal_calls += 1
        return self.signals[f"{owner}/{name}"]
    def has_closing_pr(self, node_id):
        return self.closing.get(node_id, False)
    def contributing_text(self, owner, name):
        return self.contributing.get(f"{owner}/{name}")

def _item(node_id, repo, number=1, title="t"):
    return {"node_id": node_id, "number": number, "title": title, "body": "b", "html_url": f"https://github.com/{repo}/issues/{number}",
            "repository_url": f"https://api.github.com/repos/{repo}", "labels": [], "created_at": "2026-09-20T00:00:00Z",
            "updated_at": "2026-09-25T00:00:00Z", "comments": 0, "assignee": None}

GOOD = {"stars": 1000, "pushed_at": "2026-09-30T00:00:00Z", "description": "d", "language": "Python",
        "merged_prs": [{"authorAssociation": "NONE", "mergedAt": "x"}], "issues": []}

def test_run_harvest_inserts_and_excludes(tmp_path):
    cfg = Config(languages=["Python"], good_labels=["good first issue"], category_keywords={}, bounty_labels=[])
    q = harvest.build_queries(cfg, NOW.date())[0]
    gh = FakeGH({q: [_item("I1", "o/good"), _item("I2", "o/small"), _item("I3", "o/good", 2)]},
                {"o/good": GOOD, "o/small": {**GOOD, "stars": 5}}, closing={"I3": True})
    conn = db.connect(tmp_path / "t.db")
    s = harvest.run_harvest(conn, gh, cfg, NOW)
    assert s.new_issues == 1 and s.excluded_repos == 1 and s.skipped_linked_pr == 1
    assert conn.execute("select state from issues where id='I1'").fetchone()[0] == "harvested"
    assert conn.execute("select excluded_reason from repos where full_name='o/small'").fetchone()[0] == "stars<200"
    assert conn.execute("select count(*) from issues").fetchone()[0] == 1

def test_run_harvest_rerun_only_touches_last_seen_on_dismissed(tmp_path):
    from oss_scout import transitions
    cfg = Config(languages=["Python"], good_labels=["good first issue"], category_keywords={}, bounty_labels=[])
    q = harvest.build_queries(cfg, NOW.date())[0]
    gh = FakeGH({q: [_item("I1", "o/good")]}, {"o/good": GOOD})
    conn = db.connect(tmp_path / "t.db")
    harvest.run_harvest(conn, gh, cfg, NOW)
    transitions.apply(conn, "I1", "dismissed", "user")
    gh.items[q][0]["title"] = "changed"
    s = harvest.run_harvest(conn, gh, cfg, NOW + timedelta(hours=1))
    row = conn.execute("select state, title, last_seen_at from issues where id='I1'").fetchone()
    assert row["state"] == "dismissed" and row["title"] == "t"
    assert row["last_seen_at"] == "2026-10-01T13:00:00Z"
    assert gh.signal_calls == 1  # signals refreshed only after 7 days

def test_run_harvest_marks_stale_when_issue_gets_assigned_or_closing_pr(tmp_path):
    cfg = Config(languages=["Python"], good_labels=["good first issue"], category_keywords={}, bounty_labels=[])
    q = harvest.build_queries(cfg, NOW.date())[0]
    gh = FakeGH({q: [_item("I1", "o/good")]}, {"o/good": GOOD})
    conn = db.connect(tmp_path / "t.db")
    harvest.run_harvest(conn, gh, cfg, NOW)
    gh.closing["I1"] = True
    harvest.run_harvest(conn, gh, cfg, NOW + timedelta(hours=1))
    assert conn.execute("select state from issues where id='I1'").fetchone()[0] == "stale"
```

- [ ] **Step 2: Run to see failure.** Expected: ImportError.

- [ ] **Step 3: Write harvest.py**

```python
# src/oss_scout/harvest.py
from __future__ import annotations
import json
import math
import re
import statistics
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from oss_scout import transitions
from oss_scout.config import Config
from oss_scout.db import utcnow

MAINTAINER = {"OWNER", "MEMBER", "COLLABORATOR"}
BASE = "is:issue is:open no:assignee"


@dataclass
class HarvestSummary:
    queries: int = 0
    seen: int = 0
    new_issues: int = 0
    updated_issues: int = 0
    excluded_repos: int = 0
    skipped_linked_pr: int = 0
    marked_stale: int = 0
    errors: list[str] = field(default_factory=list)


def _parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def build_queries(cfg: Config, today: date) -> list[str]:
    since = (today - timedelta(days=cfg.issue_updated_within_days)).isoformat()
    base = f"{BASE} updated:>={since}"
    qs = []
    for lang in cfg.languages:
        for label in cfg.good_labels:
            qs.append(f'{base} language:{lang} label:"{label}"')
    for kws in cfg.category_keywords.values():
        for kw in kws:
            qs.append(f'{base} "{kw}" in:title,body')
    for label in cfg.bounty_labels:
        qs.append(f'{base} label:"{label}"')
    return qs


def external_merge_rate(merged_prs: list[dict]) -> float | None:
    if not merged_prs:
        return None
    ext = sum(1 for p in merged_prs if p.get("authorAssociation") not in MAINTAINER)
    return ext / len(merged_prs)


def median_first_response_hours(issues: list[dict]) -> float | None:
    hours = []
    for i in issues:
        try:
            created = _parse_ts(i["createdAt"])
        except (KeyError, ValueError):
            continue
        for c in i.get("comments", []):
            if c.get("authorAssociation") in MAINTAINER:
                hours.append((_parse_ts(c["createdAt"]) - created).total_seconds() / 3600)
                break
    return statistics.median(hours) if hours else None


def detect_ai_policy(text: str | None, cfg: Config) -> str:
    if not text:
        return "none"
    for pat in cfg.ai_policy_banned_patterns:
        if re.search(pat, text, re.IGNORECASE):
            return "banned"
    for pat in cfg.ai_policy_discouraged_patterns:
        if re.search(pat, text, re.IGNORECASE):
            return "discouraged"
    return "none"


def repo_exclusion_reason(repo: dict, cfg: Config, now: datetime) -> str | None:
    if (repo.get("stars") or 0) < cfg.min_stars:
        return f"stars<{cfg.min_stars}"
    pushed = repo.get("pushed_at")
    if pushed and (now - _parse_ts(pushed)) > timedelta(days=cfg.repo_idle_days):
        return f"idle>{cfg.repo_idle_days}d"
    if repo.get("ai_policy") == "banned":
        return "ai_policy_banned"
    return None


def issue_from_search_item(item: dict, cfg: Config) -> dict:
    labels = [l["name"] for l in item.get("labels", [])]
    lower = {l.lower() for l in labels}
    bounty_labels = {b.lower() for b in cfg.bounty_labels}
    has_bounty = int(bool(lower & bounty_labels))
    return {
        "id": item["node_id"],
        "repo": item["repository_url"].split("/repos/", 1)[1],
        "number": item["number"],
        "title": item["title"],
        "body": item.get("body") or "",
        "labels": labels,
        "url": item["html_url"],
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
        "comments": item.get("comments", 0),
        "has_bounty": has_bounty,
        "bounty_source": "label" if has_bounty else None,
        "assignee": (item.get("assignee") or {}).get("login"),
    }


def _ensure_repo(conn, gh, full_name: str, cfg: Config, now: datetime, summary: HarvestSummary) -> dict:
    row = conn.execute("select * from repos where full_name = ?", (full_name,)).fetchone()
    repo = dict(row) if row else {"full_name": full_name}
    owner, name = full_name.split("/", 1)
    stale_signals = not repo.get("signals_fetched_at") or (now - _parse_ts(repo["signals_fetched_at"])) > timedelta(days=cfg.repo_signal_refresh_days)
    if stale_signals:
        sig = gh.repo_signals(owner, name)
        repo.update({
            "stars": sig["stars"], "pushed_at": sig["pushed_at"], "description": sig["description"],
            "language": sig["language"], "org_known": int(owner in cfg.known_orgs),
            "external_merge_rate": external_merge_rate(sig["merged_prs"]),
            "median_first_response_hours": median_first_response_hours(sig["issues"]),
            "signals_fetched_at": _iso(now),
        })
    stale_contrib = not repo.get("contributing_fetched_at") or (now - _parse_ts(repo["contributing_fetched_at"])) > timedelta(days=cfg.contributing_refresh_days)
    if stale_contrib:
        text = gh.contributing_text(owner, name)
        repo.update({"contributing_text": text, "contributing_fetched_at": _iso(now), "ai_policy": detect_ai_policy(text, cfg)})
    repo["excluded_reason"] = repo_exclusion_reason(repo, cfg, now)
    cols = ["full_name", "stars", "language", "description", "pushed_at", "org_known", "external_merge_rate",
            "median_first_response_hours", "signals_fetched_at", "contributing_text", "contributing_fetched_at",
            "ai_policy", "excluded_reason"]
    values = [repo.get(c) for c in cols]
    with conn:
        conn.execute(
            f"insert into repos({','.join(cols)}) values ({','.join('?' * len(cols))}) "
            f"on conflict(full_name) do update set {','.join(f'{c}=excluded.{c}' for c in cols[1:])}", values)
    return repo


def run_harvest(conn, gh, cfg: Config, now: datetime | None = None) -> HarvestSummary:
    now = now or datetime.now(timezone.utc)
    s = HarvestSummary()
    repo_cache: dict[str, dict] = {}
    seen_ids: set[str] = set()
    for q in build_queries(cfg, now.date()):
        s.queries += 1
        try:
            items = list(gh.search_issues(q, pages=cfg.pages_per_query, per_page=cfg.per_page))
        except Exception as e:  # one bad query never aborts the pass
            s.errors.append(f"{q}: {e}")
            continue
        for item in items:
            if item.get("pull_request"):
                continue
            row = issue_from_search_item(item, cfg)
            if row["id"] in seen_ids:
                continue
            seen_ids.add(row["id"])
            s.seen += 1
            try:
                _harvest_one(conn, gh, cfg, now, row, repo_cache, s)
            except Exception as e:
                s.errors.append(f"{row['repo']}#{row['number']}: {e}")
    return s


def _harvest_one(conn, gh, cfg, now, row, repo_cache, s: HarvestSummary) -> None:
    existing = conn.execute("select state from issues where id = ?", (row["id"],)).fetchone()
    if existing and existing["state"] in transitions.TERMINAL:
        with conn:
            conn.execute("update issues set last_seen_at = ? where id = ?", (_iso(now), row["id"]))
        return
    if row["repo"] not in repo_cache:
        repo_cache[row["repo"]] = _ensure_repo(conn, gh, row["repo"], cfg, now, s)
        if repo_cache[row["repo"]].get("excluded_reason"):
            s.excluded_repos += 1
    repo = repo_cache[row["repo"]]
    if repo.get("excluded_reason"):
        return
    linked = gh.has_closing_pr(row["id"])
    if existing:
        if linked or row["assignee"]:
            transitions.apply(conn, row["id"], "stale", "harvester", "linked PR" if linked else "assigned")
            s.marked_stale += 1
            return
        with conn:
            conn.execute(
                "update issues set title=?, body=?, labels=?, updated_at=?, comments=?, has_bounty=?, bounty_source=?, last_seen_at=? where id=?",
                (row["title"], row["body"], json.dumps(row["labels"]), row["updated_at"], row["comments"],
                 row["has_bounty"], row["bounty_source"], _iso(now), row["id"]))
        s.updated_issues += 1
        return
    if linked:
        s.skipped_linked_pr += 1
        return
    with conn:
        conn.execute(
            "insert into issues(id, repo, number, title, body, labels, url, created_at, updated_at, comments, has_bounty, bounty_source, linked_pr, assignee, state, harvested_at, last_seen_at) "
            "values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,'harvested',?,?)",
            (row["id"], row["repo"], row["number"], row["title"], row["body"], json.dumps(row["labels"]), row["url"],
             row["created_at"], row["updated_at"], row["comments"], row["has_bounty"], row["bounty_source"], 0,
             row["assignee"], _iso(now), _iso(now)))
    s.new_issues += 1
```

- [ ] **Step 4: Run tests, commit.** `uv run pytest tests/test_harvest.py -v` Expected: all pass.
`git add src/oss_scout/harvest.py tests/test_harvest.py && git commit -m "oss-scout: harvester with repo signals, AI-policy detection, exclusions"`

---

### Task 5: Jev client and classifier

**Files:**
- Create: `src/oss_scout/jev_client.py`, `src/oss_scout/classify.py`
- Test: `tests/test_jev_client.py`, `tests/test_classify.py`, fixture `tests/fixtures/jev_pandas_response.json` (the recorded response from the 2026-10-01 probe: choices `data_scraping`, `bug`, `medium` 0.66, `yes` 0.95)

**Interfaces:**
- Produces: `JevClient(api_key, model="jev-latest", cache_path: Path | None = None, opener=urllib.request.urlopen, timeout=20.0).ask(state: str, questions: dict) -> dict` (raw response).
- Produces: `classify.QUESTIONS: dict[str, dict]` (the four questions), `classify.issue_state_text(issue: dict, repo: dict) -> str`, `classify.parse_answers(response: dict, questions: dict) -> dict[str, dict]` with values `{choice, confidence, probabilities}` and `choice=None` for an out-of-criteria answer, `classify.Classifier` protocol with `.classify(state_text, questions) -> dict[str, dict]` and `.model: str`, `classify.JevClassifier(JevClient)`, `classify.GeminiClassifier(model: str, runner=subprocess.run)`, `classify.run_classify(conn, cfg, primary: Classifier, fallback: Classifier | None = None, limit: int | None = None) -> ClassifySummary(classified, dismissed, failed, errors)`.
- `classify.latest(conn, issue_id) -> dict[str, dict]` returns the latest row per question.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_jev_client.py
import io, json
from email.message import Message
from oss_scout.jev_client import JevClient

class FakeResp(io.BytesIO):
    def __init__(self, body): super().__init__(json.dumps(body).encode()); self.headers = Message()
    def __enter__(self): return self
    def __exit__(self, *a): return False

def test_ask_posts_contract_and_caches(tmp_path):
    calls = []
    def opener(req, timeout=0):
        calls.append(json.loads(req.data))
        return FakeResp({"model": "jev-1", "answers": {"q": {"choice": "a", "confidence": 1.0}}})
    c = JevClient("k", cache_path=tmp_path / "c.db", opener=opener)
    qs = {"q": {"type": "choice", "instructions": "i", "criteria": {"a": "A", "b": "B"}}}
    r1 = c.ask("state", qs)
    r2 = c.ask("state", qs)
    assert r1 == r2 and len(calls) == 1
    assert calls[0] == {"model": "jev-latest", "state": "state", "questions": qs}
```

```python
# tests/test_classify.py
import json
from pathlib import Path
import pytest
from oss_scout import db, classify, transitions
from oss_scout.config import Config

FIX = json.loads((Path(__file__).parent / "fixtures" / "jev_pandas_response.json").read_text())

def test_questions_have_seven_categories_and_four_names():
    assert set(classify.QUESTIONS) == {"category", "kind", "difficulty", "worth_a_look"}
    assert len(classify.QUESTIONS["category"]["criteria"]) == 7

def test_issue_state_text_truncates_body_and_handles_none():
    issue = {"repo": "o/r", "title": "T", "labels": "[\"bug\"]", "body": None, "comments": 3}
    repo = {"stars": 10, "language": "Python", "description": None}
    t = classify.issue_state_text(issue, repo)
    assert "o/r" in t and "T" in t and "bug" in t and "None" not in t
    long = classify.issue_state_text({**issue, "body": "x" * 5000}, repo)
    assert len(long) < 2000

def test_parse_answers_recorded_response():
    out = classify.parse_answers(FIX, classify.QUESTIONS)
    assert out["category"]["choice"] == "data_scraping"
    assert out["difficulty"]["confidence"] == pytest.approx(0.66)
    assert out["worth_a_look"]["probabilities"]["yes"] == pytest.approx(0.97)

def test_parse_answers_rejects_out_of_criteria_choice():
    bad = {"answers": {"kind": {"choice": "refactor", "confidence": 0.9}}}
    out = classify.parse_answers(bad, {"kind": classify.QUESTIONS["kind"]})
    assert out["kind"]["choice"] is None

class StubClassifier:
    model = "stub"
    def __init__(self, answers): self.answers = answers; self.calls = 0
    def classify(self, state_text, questions):
        self.calls += 1
        return {q: self.answers[q] for q in questions}

def _seed(conn, issue_id="I1", state="harvested"):
    conn.execute("insert or ignore into repos(full_name, stars, language) values ('o/r', 500, 'Python')")
    conn.execute("insert into issues(id, repo, number, title, body, url, state, harvested_at, last_seen_at) values (?,'o/r',1,'t','b','u',?,'x','x')", (issue_id, state))
    conn.commit()

GOOD = {"category": {"choice": "quant_trading", "confidence": 0.9, "probabilities": {}},
        "kind": {"choice": "bug", "confidence": 0.9, "probabilities": {}},
        "difficulty": {"choice": "small", "confidence": 0.8, "probabilities": {}},
        "worth_a_look": {"choice": "yes", "confidence": 0.9, "probabilities": {}}}

def test_run_classify_stores_rows_and_advances(tmp_path):
    conn = db.connect(tmp_path / "t.db"); _seed(conn)
    s = classify.run_classify(conn, Config(), StubClassifier(GOOD))
    assert s.classified == 1
    assert conn.execute("select state from issues where id='I1'").fetchone()[0] == "classified"
    assert conn.execute("select count(*) from classifications where issue_id='I1'").fetchone()[0] == 4
    assert classify.latest(conn, "I1")["category"]["choice"] == "quant_trading"

def test_confident_no_is_dismissed_by_classifier(tmp_path):
    conn = db.connect(tmp_path / "t.db"); _seed(conn)
    ans = {**GOOD, "worth_a_look": {"choice": "no", "confidence": 0.95, "probabilities": {}}}
    s = classify.run_classify(conn, Config(dismiss_confidence=0.9), StubClassifier(ans))
    assert s.dismissed == 1
    assert conn.execute("select state from issues where id='I1'").fetchone()[0] == "dismissed"
    assert conn.execute("select actor from transitions where to_state='dismissed'").fetchone()[0] == "classifier"

def test_unparseable_choice_leaves_issue_harvested(tmp_path):
    conn = db.connect(tmp_path / "t.db"); _seed(conn)
    ans = {**GOOD, "kind": {"choice": None, "confidence": None, "probabilities": {}}}
    s = classify.run_classify(conn, Config(), StubClassifier(ans))
    assert s.failed == 1
    assert conn.execute("select state from issues where id='I1'").fetchone()[0] == "harvested"

def test_dropped_questions_go_to_fallback(tmp_path):
    conn = db.connect(tmp_path / "t.db"); _seed(conn)
    primary = StubClassifier(GOOD); fallback = StubClassifier({"difficulty": {"choice": "large", "confidence": 0.7, "probabilities": {}}})
    fallback.model = "gemini"
    classify.run_classify(conn, Config(dropped_questions=["difficulty"]), primary, fallback)
    rows = conn.execute("select question, model, choice from classifications order by question").fetchall()
    assert ("difficulty", "gemini", "large") in [tuple(r) for r in rows]
    assert fallback.calls == 1

def test_gemini_classifier_builds_agy_command_and_parses_json(tmp_path):
    captured = {}
    class R: returncode = 0; stdout = json.dumps({"kind": "docs"}); stderr = ""
    def runner(cmd, **kw): captured["cmd"] = cmd; return R()
    g = classify.GeminiClassifier("gemini-3.8-flash-medium", runner=runner)
    out = g.classify("state", {"kind": classify.QUESTIONS["kind"]})
    assert out["kind"]["choice"] == "docs" and out["kind"]["confidence"] is None
    assert captured["cmd"][:3] == ["agy", "--print", "--model"] or "--print" in captured["cmd"]
    assert "--json-schema" in captured["cmd"]
```

- [ ] **Step 2: Save the fixture.** Write `tests/fixtures/jev_pandas_response.json` with the recorded response (model `jev-1.13.0`, four answers with `probabilities`, `usage`). Run tests: ImportError expected.

- [ ] **Step 3: Write jev_client.py**

```python
# src/oss_scout/jev_client.py
from __future__ import annotations
import hashlib
import json
import sqlite3
import urllib.request
from pathlib import Path
from typing import Callable

JEV_API_URL = "https://api.typesafe.ai/v1/systemone"


class JevError(Exception):
    pass


class JevClient:
    """Typed-decision client. Same wire contract as jev-trader's RealJevClient:
    POST {model, state, questions} -> {model, answers: {name: {choice, confidence, probabilities}}, usage}."""

    def __init__(self, api_key: str, model: str = "jev-latest", cache_path: Path | None = None,
                 opener: Callable = urllib.request.urlopen, timeout: float = 20.0):
        self.api_key = api_key
        self.model = model
        self.opener = opener
        self.timeout = timeout
        self._cache = None
        if cache_path:
            Path(cache_path).parent.mkdir(parents=True, exist_ok=True)
            self._cache = sqlite3.connect(cache_path, timeout=30.0)
            self._cache.execute("create table if not exists cache (key text primary key, response text)")
            self._cache.commit()

    def ask(self, state: str, questions: dict) -> dict:
        payload = {"model": self.model, "state": state, "questions": questions}
        key = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        if self._cache:
            row = self._cache.execute("select response from cache where key = ?", (key,)).fetchone()
            if row:
                return json.loads(row[0])
        req = urllib.request.Request(JEV_API_URL, data=json.dumps(payload).encode(), headers={
            "Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"})
        last = None
        for attempt in range(3):
            try:
                with self.opener(req, timeout=self.timeout) as resp:
                    data = json.loads(resp.read())
                break
            except Exception as e:  # timeouts and 5xx retry twice
                last = e
        else:
            raise JevError(f"jev request failed: {last}")
        if self._cache:
            self._cache.execute("insert or replace into cache(key, response) values (?, ?)", (key, json.dumps(data)))
            self._cache.commit()
        return data
```

- [ ] **Step 4: Write classify.py**

```python
# src/oss_scout/classify.py
from __future__ import annotations
import json
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import Protocol
from oss_scout import transitions
from oss_scout.config import Config
from oss_scout.db import utcnow
from oss_scout.jev_client import JevClient

CATEGORIES = {
    "quant_trading": "Backtesting, trading bots, market data, technical analysis, portfolio tools",
    "prediction_markets_finance": "Prediction markets, betting exchanges, broker or finance APIs, payments",
    "ai_agents_llm": "LLM agents, MCP servers, prompt tooling, model evals, inference",
    "data_scraping": "Data processing libraries, dataframes, scrapers, ETL, databases",
    "dev_tools_cli": "Developer tools, CLIs, build systems, linters, editors",
    "web_frontend": "React, UI components, web frontends, design systems",
    "other": "None of the above",
}
QUESTIONS = {
    "category": {"type": "choice", "instructions": "Which domain does this repository and issue belong to?", "criteria": CATEGORIES},
    "kind": {"type": "choice", "instructions": "What kind of work does this issue ask for?", "criteria": {
        "bug": "Fix incorrect behavior", "feature": "Add new behavior", "docs": "Documentation only",
        "test": "Tests only", "chore": "Maintenance, dependencies, CI"}},
    "difficulty": {"type": "choice", "instructions": "How hard is this for an outside contributor who knows the language but not the codebase?", "criteria": {
        "small": "One file, under two hours", "medium": "A few files, about a day", "large": "Design work or multiple days"}},
    "worth_a_look": {"type": "choice", "instructions": "Could a solo outside contributor plausibly get a PR for this merged?", "criteria": {
        "yes": "Clear ask, maintainers engaged, scoped work",
        "no": "Vague, contentious, needs maintainer design decisions, or likely already handled"}},
}
BODY_CHARS = 1500


def issue_state_text(issue: dict, repo: dict) -> str:
    labels = issue.get("labels") or "[]"
    if isinstance(labels, str):
        labels = json.loads(labels)
    body = (issue.get("body") or "").strip().replace("\r", "")[:BODY_CHARS]
    desc = repo.get("description") or ""
    return (
        f"Repository {issue['repo']} ({repo.get('stars') or 0} stars, {repo.get('language') or 'unknown language'}): {desc}\n"
        f"Issue: {issue['title']}\nLabels: {', '.join(labels) or 'none'}\nComments: {issue.get('comments') or 0}\n"
        f"Body: {body}"
    )


def parse_answers(response: dict, questions: dict) -> dict[str, dict]:
    out = {}
    answers = response.get("answers") or {}
    for q, spec in questions.items():
        a = answers.get(q) or {}
        choice = a.get("choice")
        if choice not in spec["criteria"]:
            choice = None
        out[q] = {"choice": choice, "confidence": a.get("confidence") if choice else None,
                  "probabilities": a.get("probabilities") or {}}
    return out


class Classifier(Protocol):
    model: str
    def classify(self, state_text: str, questions: dict) -> dict[str, dict]: ...


class JevClassifier:
    def __init__(self, client: JevClient):
        self.client = client
        self.model = f"jev:{client.model}"

    def classify(self, state_text: str, questions: dict) -> dict[str, dict]:
        return parse_answers(self.client.ask(state_text, questions), questions)


class GeminiClassifier:
    """Fallback for questions Jev answers badly: one `agy --print` call with a JSON schema."""

    def __init__(self, model: str, runner=subprocess.run, timeout: float = 120.0):
        self.model = f"gemini:{model}"
        self._model = model
        self.runner = runner
        self.timeout = timeout

    def classify(self, state_text: str, questions: dict) -> dict[str, dict]:
        schema = {"type": "object", "additionalProperties": False, "required": list(questions),
                  "properties": {q: {"type": "string", "enum": list(spec["criteria"])} for q, spec in questions.items()}}
        prompt = "Classify this GitHub issue. Answer each question with exactly one allowed value.\n\n"
        for q, spec in questions.items():
            prompt += f"{q}: {spec['instructions']}\n" + "".join(f"  - {k}: {v}\n" for k, v in spec["criteria"].items())
        prompt += f"\nISSUE:\n{state_text}"
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(schema, f)
            schema_path = f.name
        cmd = ["agy", "--print", "--model", self._model, "--output-format", "json", "--json-schema", schema_path,
               "--disable-slash-commands", prompt]
        r = self.runner(cmd, capture_output=True, text=True, timeout=self.timeout)
        if r.returncode != 0:
            raise RuntimeError(f"agy failed: {r.stderr[-500:]}")
        data = _extract_json(r.stdout)
        return {q: {"choice": data.get(q) if data.get(q) in spec["criteria"] else None, "confidence": None, "probabilities": {}}
                for q, spec in questions.items()}


def _extract_json(text: str) -> dict:
    text = text.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        data = json.loads(text[start:end + 1])
    # agy's json output may wrap the structured result; unwrap common shapes
    for key in ("result", "structured_output", "response"):
        if isinstance(data, dict) and key in data and isinstance(data[key], (dict, str)):
            inner = data[key]
            data = json.loads(inner) if isinstance(inner, str) else inner
    return data


@dataclass
class ClassifySummary:
    classified: int = 0
    dismissed: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)


def _store(conn, issue_id: str, answers: dict[str, dict], model: str) -> None:
    now = utcnow()
    with conn:
        for q, a in answers.items():
            conn.execute(
                "insert into classifications(issue_id, question, choice, confidence, probabilities, model, asked_at) values (?,?,?,?,?,?,?)",
                (issue_id, q, a["choice"], a["confidence"], json.dumps(a.get("probabilities") or {}), model, now))


def latest(conn, issue_id: str) -> dict[str, dict]:
    rows = conn.execute(
        "select question, choice, confidence, probabilities, model from classifications where issue_id = ? order by id", (issue_id,)).fetchall()
    out = {}
    for r in rows:  # later rows overwrite earlier: latest wins
        out[r["question"]] = {"choice": r["choice"], "confidence": r["confidence"],
                              "probabilities": json.loads(r["probabilities"] or "{}"), "model": r["model"]}
    return out


def run_classify(conn, cfg: Config, primary: Classifier, fallback: Classifier | None = None,
                 limit: int | None = None) -> ClassifySummary:
    s = ClassifySummary()
    sql = "select i.*, r.stars, r.language, r.description from issues i join repos r on r.full_name = i.repo where i.state = 'harvested' order by i.harvested_at"
    rows = conn.execute(sql + (f" limit {int(limit)}" if limit else "")).fetchall()
    primary_qs = {q: spec for q, spec in QUESTIONS.items() if q not in cfg.dropped_questions}
    fallback_qs = {q: spec for q, spec in QUESTIONS.items() if q in cfg.dropped_questions}
    for row in rows:
        issue = dict(row)
        text = issue_state_text(issue, issue)
        try:
            answers = primary.classify(text, primary_qs) if primary_qs else {}
            _store(conn, issue["id"], answers, primary.model)
            if fallback_qs:
                if fallback is None:
                    raise RuntimeError(f"questions {list(fallback_qs)} dropped but no fallback classifier configured")
                fb = fallback.classify(text, fallback_qs)
                _store(conn, issue["id"], fb, fallback.model)
                answers = {**answers, **fb}
        except Exception as e:
            s.failed += 1
            s.errors.append(f"{issue['repo']}#{issue['number']}: {e}")
            continue
        if any(a["choice"] is None for a in answers.values()):
            s.failed += 1
            continue
        transitions.apply(conn, issue["id"], "classified", "classifier")
        s.classified += 1
        w = answers.get("worth_a_look")
        if w and w["choice"] == "no" and (w["confidence"] or 0) >= cfg.dismiss_confidence:
            transitions.apply(conn, issue["id"], "dismissed", "classifier", f"worth_a_look=no @ {w['confidence']:.2f}")
            s.dismissed += 1
    return s
```

- [ ] **Step 5: Run tests, commit.** `uv run pytest tests/test_jev_client.py tests/test_classify.py -v` Expected: all pass.
`git add src/oss_scout/jev_client.py src/oss_scout/classify.py tests/test_jev_client.py tests/test_classify.py tests/fixtures && git commit -m "oss-scout: Jev client, four-question classifier, Gemini fallback"`

---

### Task 6: Classifier validation harness

**Files:**
- Create: `src/oss_scout/validate.py`, `fixtures/labeled_issues.json` (committed later in Task 10 with real data; the test uses an inline sample)
- Test: `tests/test_validate.py`

**Interfaces:**
- Consumes: `classify.Classifier`, `classify.QUESTIONS`, `classify.issue_state_text`.
- Produces: `validate.load_labeled(path) -> list[dict]` (each `{issue: {...}, repo: {...}, labels: {question: choice}}`), `validate.evaluate(labeled, classifier, questions=QUESTIONS) -> Report`, `Report.per_question: dict[str, QuestionReport(agreement: float, distribution: dict[str, float], confidence_std: float | None, n: int, decision: "keep"|"drop", reason: str)]`, `Report.render() -> str`, `Report.dropped() -> list[str]`.
- Drop rules verbatim from spec: drop if one choice is above 85% of answers, or agreement below 60%.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_validate.py
import re
from oss_scout import validate, classify

def _idx(text):
    return int(re.search(r"Issue: t(\d+)", text).group(1))

def _labeled(n, kind_label):
    return [{"issue": {"repo": "o/r", "title": f"t{i}", "labels": "[]", "body": "b", "comments": 0},
             "repo": {"stars": 1, "language": "Python", "description": ""},
             "labels": {"kind": kind_label(i)}} for i in range(n)]

class Fixed:
    model = "fixed"
    def __init__(self, fn): self.fn = fn
    def classify(self, text, questions):
        return {q: self.fn(text, q) for q in questions}

def test_keeps_question_with_good_agreement_and_spread():
    data = _labeled(10, lambda i: "bug" if i % 2 else "docs")
    clf = Fixed(lambda text, q: {"choice": "bug" if _idx(text) % 2 else "docs", "confidence": 0.9, "probabilities": {}})
    rep = validate.evaluate(data, clf, {"kind": classify.QUESTIONS["kind"]})
    qr = rep.per_question["kind"]
    assert qr.agreement == 1.0 and qr.decision == "keep" and qr.n == 10

def test_drops_near_constant_question():
    data = _labeled(10, lambda i: "bug" if i % 2 else "docs")
    clf = Fixed(lambda text, q: {"choice": "bug", "confidence": 0.5, "probabilities": {}})
    rep = validate.evaluate(data, clf, {"kind": classify.QUESTIONS["kind"]})
    qr = rep.per_question["kind"]
    assert qr.decision == "drop" and "constant" in qr.reason
    assert rep.dropped() == ["kind"]

def test_drops_low_agreement_question():
    data = _labeled(10, lambda i: "bug" if i % 2 else "docs")
    clf = Fixed(lambda text, q: {"choice": "feature" if _idx(text) % 3 else "bug", "confidence": 0.5, "probabilities": {}})
    rep = validate.evaluate(data, clf, {"kind": classify.QUESTIONS["kind"]})
    assert rep.per_question["kind"].decision == "drop"
    assert "agreement" in rep.per_question["kind"].reason

def test_render_mentions_each_question_and_decision():
    data = _labeled(4, lambda i: "bug")
    clf = Fixed(lambda text, q: {"choice": "bug", "confidence": 0.9, "probabilities": {}})
    rep = validate.evaluate(data, clf, {"kind": classify.QUESTIONS["kind"]})
    out = rep.render()
    assert "kind" in out and ("keep" in out or "drop" in out)
```

- [ ] **Step 2: Run to see failure.** Expected: ImportError.

- [ ] **Step 3: Write validate.py**

```python
# src/oss_scout/validate.py
from __future__ import annotations
import json
import statistics
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from oss_scout import classify

CONSTANT_SHARE = 0.85
MIN_AGREEMENT = 0.60


@dataclass
class QuestionReport:
    n: int
    agreement: float
    distribution: dict[str, float]
    confidence_std: float | None
    decision: str
    reason: str


@dataclass
class Report:
    per_question: dict[str, QuestionReport] = field(default_factory=dict)
    model: str = ""

    def dropped(self) -> list[str]:
        return [q for q, r in self.per_question.items() if r.decision == "drop"]

    def render(self) -> str:
        lines = [f"Classifier validation ({self.model})", ""]
        for q, r in self.per_question.items():
            dist = ", ".join(f"{k} {v:.0%}" for k, v in sorted(r.distribution.items(), key=lambda kv: -kv[1]))
            std = f"{r.confidence_std:.2f}" if r.confidence_std is not None else "n/a"
            lines.append(f"{q}: n={r.n} agreement={r.agreement:.0%} confidence_std={std} -> {r.decision.upper()} ({r.reason})")
            lines.append(f"  distribution: {dist}")
        d = self.dropped()
        lines.append("")
        lines.append(f"dropped_questions: {d}  (put this list in scout.yaml)" if d else "No questions dropped.")
        return "\n".join(lines)


def load_labeled(path: Path) -> list[dict]:
    return json.loads(Path(path).read_text())


def evaluate(labeled: list[dict], classifier, questions: dict | None = None) -> Report:
    questions = questions or classify.QUESTIONS
    rep = Report(model=getattr(classifier, "model", "?"))
    answers = [classifier.classify(classify.issue_state_text(item["issue"], item["repo"]), questions) for item in labeled]
    for q in questions:
        pairs = [(a[q]["choice"], item["labels"].get(q)) for a, item in zip(answers, labeled) if item["labels"].get(q) is not None]
        n = len(pairs)
        if n == 0:
            rep.per_question[q] = QuestionReport(0, 0.0, {}, None, "drop", "no labels")
            continue
        agreement = sum(1 for c, l in pairs if c == l) / n
        counts = Counter(c for c, _ in pairs)
        distribution = {k: v / n for k, v in counts.items()}
        confs = [a[q]["confidence"] for a in answers if a[q]["confidence"] is not None]
        std = statistics.pstdev(confs) if len(confs) > 1 else None
        top_share = max(distribution.values())
        if top_share > CONSTANT_SHARE:
            decision, reason = "drop", f"near-constant: top choice {top_share:.0%} > {CONSTANT_SHARE:.0%}"
        elif agreement < MIN_AGREEMENT:
            decision, reason = "drop", f"agreement {agreement:.0%} < {MIN_AGREEMENT:.0%}"
        else:
            decision, reason = "keep", "passes both rules"
        rep.per_question[q] = QuestionReport(n, agreement, distribution, std, decision, reason)
    return rep
```

- [ ] **Step 4: Run tests, commit.** Expected: 4 passed.
`git add src/oss_scout/validate.py tests/test_validate.py && git commit -m "oss-scout: classifier validation harness with drop rules"`

---

### Task 7: Scorer and shortlist

**Files:**
- Create: `src/oss_scout/score.py`
- Test: `tests/test_score.py`

**Interfaces:**
- Consumes: `classify.latest`, `transitions.apply`, `Config`.
- Produces: `score.compute(issue: dict, repo: dict, cls: dict[str, dict], cfg: Config, now: datetime) -> tuple[float, dict[str, float]]` (breakdown maps term -> weighted contribution), `score.weights_hash(cfg) -> str`, `score.run_scoring(conn, cfg, now=None) -> ScoreSummary(scored, shortlisted, demoted)`, `score.MAX_TOTAL(cfg) -> float` (sum of positive weights except the penalty-only `ai_policy`; 13.5 with defaults).

- [ ] **Step 1: Write failing tests**

```python
# tests/test_score.py
import math
from datetime import datetime, timezone, timedelta
import pytest
from oss_scout import db, score, transitions
from oss_scout.config import Config

NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)
CLS = {"category": {"choice": "quant_trading"}, "kind": {"choice": "bug"}, "difficulty": {"choice": "small"}, "worth_a_look": {"choice": "yes"}}

def _issue(**kw):
    base = {"updated_at": "2026-10-01T00:00:00Z", "has_bounty": 0}
    base.update(kw); return base

def _repo(**kw):
    base = {"stars": 10000, "org_known": 1, "external_merge_rate": 0.5, "median_first_response_hours": 12.0, "language": "Python", "ai_policy": "none"}
    base.update(kw); return base

def test_ideal_issue_scores_near_max():
    cfg = Config()
    total, br = score.compute(_issue(has_bounty=1), _repo(stars=100000, external_merge_rate=1.0, median_first_response_hours=1.0), CLS, cfg, NOW)
    assert total == pytest.approx(score.MAX_TOTAL(cfg), abs=0.3)
    assert set(br) == set(cfg.weights)

def test_terms_are_bounded_and_none_signals_count_zero():
    cfg = Config()
    total, br = score.compute(_issue(), _repo(external_merge_rate=None, median_first_response_hours=None, org_known=0, stars=200), CLS, cfg, NOW)
    assert br["mergeability"] == 0.0
    for term, w in cfg.weights.items():
        assert -abs(w) <= br[term] <= abs(w)

def test_language_and_category_and_difficulty_values():
    cfg = Config()
    _, py = score.compute(_issue(), _repo(language="Python"), CLS, cfg, NOW)
    _, ts = score.compute(_issue(), _repo(language="TypeScript"), CLS, cfg, NOW)
    _, rs = score.compute(_issue(), _repo(language="Rust"), CLS, cfg, NOW)
    assert py["language_fit"] > ts["language_fit"] > rs["language_fit"]
    _, other = score.compute(_issue(), _repo(), {**CLS, "category": {"choice": "other"}}, cfg, NOW)
    assert other["category"] < py["category"]
    _, large = score.compute(_issue(), _repo(), {**CLS, "difficulty": {"choice": "large"}}, cfg, NOW)
    assert large["difficulty_fit"] < py["difficulty_fit"]

def test_freshness_decays_and_ai_policy_penalizes():
    cfg = Config()
    _, fresh = score.compute(_issue(updated_at="2026-10-01T00:00:00Z"), _repo(), CLS, cfg, NOW)
    _, old = score.compute(_issue(updated_at="2026-09-01T00:00:00Z"), _repo(), CLS, cfg, NOW)
    assert fresh["freshness"] > old["freshness"] >= 0
    _, disc = score.compute(_issue(), _repo(ai_policy="discouraged"), CLS, cfg, NOW)
    assert disc["ai_policy"] == pytest.approx(-0.5 * cfg.weights["ai_policy"])

def test_weights_hash_changes_with_weights():
    assert score.weights_hash(Config()) != score.weights_hash(Config(weights={**Config().weights, "bounty": 9}))

def _seed(conn, issue_id, stars, category="quant_trading", state="classified"):
    repo = f"o/{issue_id}"
    conn.execute("insert into repos(full_name, stars, language, org_known, external_merge_rate, median_first_response_hours, ai_policy) values (?,?,?,?,?,?,?)",
                 (repo, stars, "Python", 0, 0.5, 10.0, "none"))
    conn.execute("insert into issues(id, repo, number, title, url, state, updated_at, harvested_at, last_seen_at) values (?,?,1,'t','u',?, '2026-10-01T00:00:00Z','x','x')", (issue_id, repo, state))
    for q, c in [("category", category), ("kind", "bug"), ("difficulty", "small"), ("worth_a_look", "yes")]:
        conn.execute("insert into classifications(issue_id, question, choice, confidence, probabilities, model, asked_at) values (?,?,?,0.9,'{}','stub','x')", (issue_id, q, c))
    conn.commit()

def test_run_scoring_shortlists_top_n_above_floor_and_demotes(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    _seed(conn, "A", 50000); _seed(conn, "B", 300); _seed(conn, "C", 250, category="other")
    cfg = Config(shortlist_count=2, shortlist_floor=4.0)
    s = score.run_scoring(conn, cfg, NOW)
    assert s.scored == 3
    states = {r["id"]: r["state"] for r in conn.execute("select id, state from issues")}
    assert states["A"] == "shortlisted" and states["B"] == "shortlisted" and states["C"] == "classified"
    # now A and B are shortlisted; shrink the list to 1 and B must be demoted
    s2 = score.run_scoring(conn, Config(shortlist_count=1, shortlist_floor=4.0), NOW)
    states = {r["id"]: r["state"] for r in conn.execute("select id, state from issues")}
    assert states["A"] == "shortlisted" and states["B"] == "classified" and s2.demoted == 1
    assert conn.execute("select count(*) from scores where issue_id='A'").fetchone()[0] == 2  # history kept

def test_run_scoring_ignores_dismissed(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    _seed(conn, "A", 50000, state="dismissed")
    s = score.run_scoring(conn, Config(), NOW)
    assert s.scored == 0
```

- [ ] **Step 2: Run to see failure.** Expected: ImportError.

- [ ] **Step 3: Write score.py**

```python
# src/oss_scout/score.py
from __future__ import annotations
import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from oss_scout import classify, transitions
from oss_scout.config import Config
from oss_scout.db import utcnow

DIFFICULTY_FIT = {"small": 1.0, "medium": 0.8, "large": 0.3}
AI_POLICY_VALUE = {"none": 0.0, "discouraged": -0.5, "banned": -1.0}
FRESHNESS_DAYS = 30.0
STARS_LOG_CAP = math.log10(100_000)  # 100k stars -> 1.0


def MAX_TOTAL(cfg: Config) -> float:
    """Best possible total: every term at 1.0 except ai_policy, which can only subtract."""
    return sum(w for t, w in cfg.weights.items() if w > 0 and t != "ai_policy")


def weights_hash(cfg: Config) -> str:
    blob = json.dumps({"w": cfg.weights, "c": cfg.category_values, "l": cfg.language_fit}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


def _parse_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def compute(issue: dict, repo: dict, cls: dict[str, dict], cfg: Config, now: datetime) -> tuple[float, dict[str, float]]:
    stars = repo.get("stars") or 0
    recognition = 0.5 * _clamp(math.log10(max(stars, 1)) / STARS_LOG_CAP) + 0.5 * (1.0 if repo.get("org_known") else 0.0)

    emr = repo.get("external_merge_rate")
    resp = repo.get("median_first_response_hours")
    merge_term = _clamp(emr) if emr is not None else 0.0
    resp_term = _clamp(1.0 - math.log10(max(resp, 1.0)) / 3.0) if resp is not None else 0.0  # 1h->1.0, 1000h->0
    mergeability = 0.5 * merge_term + 0.5 * resp_term

    language_fit = cfg.language_fit.get(repo.get("language") or "", 0.2)
    category = cfg.category_values.get((cls.get("category") or {}).get("choice") or "other", 0.3)
    difficulty_fit = DIFFICULTY_FIT.get((cls.get("difficulty") or {}).get("choice") or "", 0.5)

    updated = _parse_ts(issue.get("updated_at"))
    age_days = (now - updated).total_seconds() / 86400 if updated else FRESHNESS_DAYS
    freshness = _clamp(1.0 - age_days / FRESHNESS_DAYS)

    bounty = 1.0 if issue.get("has_bounty") else 0.0
    ai_policy = AI_POLICY_VALUE.get(repo.get("ai_policy") or "none", 0.0)

    raw = {"recognition": recognition, "mergeability": mergeability, "language_fit": language_fit, "category": category,
           "difficulty_fit": difficulty_fit, "freshness": freshness, "bounty": bounty, "ai_policy": ai_policy}
    breakdown = {term: round(raw[term] * cfg.weights.get(term, 0.0), 4) for term in cfg.weights}
    return round(sum(breakdown.values()), 4), breakdown


@dataclass
class ScoreSummary:
    scored: int = 0
    shortlisted: int = 0
    demoted: int = 0


def run_scoring(conn, cfg: Config, now: datetime | None = None) -> ScoreSummary:
    now = now or datetime.now(timezone.utc)
    s = ScoreSummary()
    h = weights_hash(cfg)
    rows = conn.execute(
        "select i.*, r.stars, r.org_known, r.external_merge_rate, r.median_first_response_hours, r.language, r.ai_policy "
        "from issues i join repos r on r.full_name = i.repo where i.state in ('classified', 'shortlisted')").fetchall()
    ranked = []
    for row in rows:
        issue = dict(row)
        cls = classify.latest(conn, issue["id"])
        total, breakdown = compute(issue, issue, cls, cfg, now)
        with conn:
            conn.execute("insert into scores(issue_id, total, breakdown, weights_hash, scored_at) values (?,?,?,?,?)",
                         (issue["id"], total, json.dumps(breakdown), h, utcnow()))
        s.scored += 1
        ranked.append((total, issue["id"], issue["state"]))
    ranked.sort(key=lambda t: -t[0])
    keep = {iid for total, iid, _ in ranked[: cfg.shortlist_count] if total >= cfg.shortlist_floor}
    for total, iid, state in ranked:
        if iid in keep and state == "classified":
            transitions.apply(conn, iid, "shortlisted", "scorer", f"score {total}")
            s.shortlisted += 1
        elif iid not in keep and state == "shortlisted":
            transitions.apply(conn, iid, "classified", "scorer", f"score {total} fell out")
            s.demoted += 1
    return s


def latest_score(conn, issue_id: str) -> tuple[float, dict] | None:
    row = conn.execute("select total, breakdown from scores where issue_id = ? order by id desc limit 1", (issue_id,)).fetchone()
    return (row["total"], json.loads(row["breakdown"])) if row else None
```

- [ ] **Step 4: Run tests, commit.** Expected: all pass.
`git add src/oss_scout/score.py tests/test_score.py && git commit -m "oss-scout: weighted scorer with breakdown and shortlist"`

---

### Task 8: CLI

**Files:**
- Modify: `src/oss_scout/cli.py` (replace the stub)
- Create: `src/oss_scout/queue_view.py`
- Test: `tests/test_cli.py`, `tests/test_queue_view.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `queue_view.queue_rows(conn, category: str | None, state: str | None, limit: int) -> list[dict]` with keys `rank, score, repo, number, title, category, kind, difficulty, state, url, breakdown`. `queue_view.render_table(rows) -> rich.table.Table`.
- CLI commands: `scout harvest`, `scout classify [--limit N]`, `scout validate-classifier [--labeled PATH]`, `scout score`, `scout run` (harvest+classify+score, used by the timer), `scout queue [--category C] [--state S] [--limit N] [--json]`, `scout dismiss ISSUE` (ISSUE is `owner/repo#number`), `scout status`.
- Logging: every command appends to `cfg.log_path` via `logging` with timestamp, level, message; unhandled exceptions log the traceback and exit 1.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_queue_view.py
from oss_scout import db, queue_view

def _seed(conn, iid, total, category, state="shortlisted"):
    conn.execute("insert or ignore into repos(full_name, stars) values ('o/r', 1)")
    conn.execute("insert into issues(id, repo, number, title, url, state, harvested_at, last_seen_at) values (?,'o/r',?, 't','u',?, 'x','x')", (iid, int(iid[1:]), state))
    conn.execute("insert into classifications(issue_id, question, choice, confidence, probabilities, model, asked_at) values (?,'category',?,0.9,'{}','s','x')", (iid, category))
    conn.execute("insert into scores(issue_id, total, breakdown, weights_hash, scored_at) values (?,?,'{\"a\":1}','h','x')", (iid, total))
    conn.commit()

def test_queue_rows_sorted_filtered_and_ranked(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    _seed(conn, "I1", 5.0, "quant_trading"); _seed(conn, "I2", 9.0, "other"); _seed(conn, "I3", 7.0, "quant_trading", state="dismissed")
    rows = queue_view.queue_rows(conn, None, None, 10)
    assert [r["score"] for r in rows] == [9.0, 5.0] and rows[0]["rank"] == 1
    assert [r["number"] for r in queue_view.queue_rows(conn, "quant_trading", None, 10)] == [1]
    assert [r["number"] for r in queue_view.queue_rows(conn, None, "dismissed", 10)] == [3]

def test_render_table_has_a_row_per_item(tmp_path):
    conn = db.connect(tmp_path / "t.db"); _seed(conn, "I1", 5.0, "quant_trading")
    t = queue_view.render_table(queue_view.queue_rows(conn, None, None, 10))
    assert t.row_count == 1
```

```python
# tests/test_cli.py
import json
from click.testing import CliRunner
from oss_scout import cli, db
from oss_scout.config import Config

def test_status_and_queue_on_empty_db(tmp_path, monkeypatch):
    cfg = Config(data_dir=tmp_path)
    monkeypatch.setattr(cli, "load_config", lambda: cfg)
    r = CliRunner().invoke(cli.main, ["status"])
    assert r.exit_code == 0 and "issues: 0" in r.output
    r = CliRunner().invoke(cli.main, ["queue", "--json"])
    assert r.exit_code == 0 and json.loads(r.output) == []

def test_dismiss_by_repo_and_number(tmp_path, monkeypatch):
    cfg = Config(data_dir=tmp_path)
    monkeypatch.setattr(cli, "load_config", lambda: cfg)
    conn = db.connect(cfg.db_path)
    conn.execute("insert into repos(full_name) values ('o/r')")
    conn.execute("insert into issues(id, repo, number, title, url, state, harvested_at, last_seen_at) values ('I1','o/r',4,'t','u','shortlisted','x','x')")
    conn.commit(); conn.close()
    r = CliRunner().invoke(cli.main, ["dismiss", "o/r#4", "--note", "not for me"])
    assert r.exit_code == 0, r.output
    conn = db.connect(cfg.db_path)
    assert conn.execute("select state from issues where id='I1'").fetchone()[0] == "dismissed"
    r = CliRunner().invoke(cli.main, ["dismiss", "o/r#99"])
    assert r.exit_code != 0
```

- [ ] **Step 2: Run to see failure.** Expected: ImportError / missing commands.

- [ ] **Step 3: Write queue_view.py**

```python
# src/oss_scout/queue_view.py
from __future__ import annotations
import json
from rich.table import Table

SQL = """
select i.id, i.repo, i.number, i.title, i.url, i.state, i.has_bounty,
       (select total from scores s where s.issue_id = i.id order by s.id desc limit 1) as score,
       (select breakdown from scores s where s.issue_id = i.id order by s.id desc limit 1) as breakdown,
       (select choice from classifications c where c.issue_id = i.id and c.question='category' order by c.id desc limit 1) as category,
       (select choice from classifications c where c.issue_id = i.id and c.question='kind' order by c.id desc limit 1) as kind,
       (select choice from classifications c where c.issue_id = i.id and c.question='difficulty' order by c.id desc limit 1) as difficulty
from issues i
"""


def queue_rows(conn, category: str | None, state: str | None, limit: int) -> list[dict]:
    where, params = [], []
    if state:
        where.append("i.state = ?"); params.append(state)
    else:
        where.append("i.state in ('shortlisted', 'analyzed', 'discussing', 'plan_approved', 'drafting')")
    sql = SQL + (" where " + " and ".join(where) if where else "")
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
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
    t = Table(title="OSS Scout queue")
    for col in ("#", "score", "repo", "issue", "cat", "kind", "diff", "state", "title"):
        t.add_column(col)
    for r in rows:
        t.add_row(str(r["rank"]), f"{r['score']:.1f}", r["repo"], f"#{r['number']}", r.get("category") or "?",
                  r.get("kind") or "?", r.get("difficulty") or "?", r["state"], (r["title"] or "")[:70])
    return t
```

- [ ] **Step 4: Write cli.py**

```python
# src/oss_scout/cli.py
from __future__ import annotations
import json
import logging
import sys
from pathlib import Path
import click
from rich.console import Console
from oss_scout import classify, db, harvest, queue_view, score, transitions, validate
from oss_scout.config import load_config as _load_config, jev_api_key
from oss_scout.github_client import GitHubClient, github_token
from oss_scout.jev_client import JevClient

console = Console()


def load_config():
    return _load_config()


def _setup_logging(cfg):
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=cfg.log_path, level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def _classifiers(cfg):
    primary = classify.JevClassifier(JevClient(jev_api_key(), cfg.jev_model, cfg.jev_cache_path))
    fallback = classify.GeminiClassifier(cfg.gemini_model) if cfg.dropped_questions else None
    return primary, fallback


@click.group()
def main():
    """OSS Scout: find open-source issues worth your time."""


def _do_harvest(cfg, conn):
    s = harvest.run_harvest(conn, GitHubClient(github_token()), cfg)
    logging.info("harvest %s", s)
    console.print(f"harvest: queries={s.queries} seen={s.seen} new={s.new_issues} updated={s.updated_issues} "
                  f"excluded_repos={s.excluded_repos} linked_pr={s.skipped_linked_pr} stale={s.marked_stale} errors={len(s.errors)}")
    for e in s.errors[:10]:
        console.print(f"  [red]{e}")
    return s


def _do_classify(cfg, conn, limit=None):
    primary, fallback = _classifiers(cfg)
    s = classify.run_classify(conn, cfg, primary, fallback, limit)
    logging.info("classify %s", s)
    console.print(f"classify: classified={s.classified} dismissed={s.dismissed} failed={s.failed}")
    for e in s.errors[:10]:
        console.print(f"  [red]{e}")
    return s


def _do_score(cfg, conn):
    s = score.run_scoring(conn, cfg)
    logging.info("score %s", s)
    console.print(f"score: scored={s.scored} shortlisted={s.shortlisted} demoted={s.demoted}")
    return s


@main.command("harvest")
def harvest_command():
    """Search GitHub and store new issues."""
    cfg = load_config(); _setup_logging(cfg)
    _do_harvest(cfg, db.connect(cfg.db_path))


@main.command("classify")
@click.option("--limit", type=int, default=None)
def classify_command(limit):
    """Ask Jev the four questions for every harvested issue."""
    cfg = load_config(); _setup_logging(cfg)
    _do_classify(cfg, db.connect(cfg.db_path), limit)


@main.command("score")
def score_command():
    """Score classified issues and refresh the shortlist."""
    cfg = load_config(); _setup_logging(cfg)
    _do_score(cfg, db.connect(cfg.db_path))


@main.command("run")
def run_command():
    """harvest, classify, score. What the hourly timer calls."""
    cfg = load_config(); _setup_logging(cfg)
    conn = db.connect(cfg.db_path)
    _do_harvest(cfg, conn); _do_classify(cfg, conn); _do_score(cfg, conn)


@main.command("validate-classifier")
@click.option("--labeled", type=click.Path(exists=True, path_type=Path), default=None)
def validate_command(labeled):
    """Compare Jev's answers with the hand-labeled fixture and print drop decisions."""
    cfg = load_config(); _setup_logging(cfg)
    path = labeled or (Path(__file__).resolve().parents[2] / "fixtures" / "labeled_issues.json")
    primary, _ = _classifiers(cfg)
    rep = validate.evaluate(validate.load_labeled(path), primary)
    console.print(rep.render())


@main.command("queue")
@click.option("--category", default=None)
@click.option("--state", default=None)
@click.option("--limit", type=int, default=30)
@click.option("--json", "as_json", is_flag=True)
def queue_command(category, state, limit, as_json):
    """Ranked list of issues worth a look."""
    cfg = load_config()
    rows = queue_view.queue_rows(db.connect(cfg.db_path), category, state, limit)
    if as_json:
        click.echo(json.dumps(rows, indent=1))
    else:
        console.print(queue_view.render_table(rows))


@main.command("dismiss")
@click.argument("issue")
@click.option("--note", default="")
def dismiss_command(issue, note):
    """Dismiss owner/repo#number so it never comes back."""
    cfg = load_config(); _setup_logging(cfg)
    conn = db.connect(cfg.db_path)
    try:
        repo, number = issue.split("#", 1)
        row = conn.execute("select id from issues where repo = ? and number = ?", (repo, int(number))).fetchone()
    except ValueError:
        raise click.BadParameter("expected owner/repo#number")
    if row is None:
        raise click.ClickException(f"no such issue {issue}")
    transitions.apply(conn, row["id"], "dismissed", "user", note)
    console.print(f"dismissed {issue}")


@main.command("status")
def status_command():
    """Counts per state and the last run."""
    cfg = load_config()
    conn = db.connect(cfg.db_path)
    total = conn.execute("select count(*) from issues").fetchone()[0]
    console.print(f"issues: {total}")
    for r in conn.execute("select state, count(*) n from issues group by state order by n desc"):
        console.print(f"  {r['state']}: {r['n']}")
    last = conn.execute("select at from transitions order by id desc limit 1").fetchone()
    console.print(f"last transition: {last[0] if last else 'never'}")
    repos = conn.execute("select count(*), sum(excluded_reason is not null) from repos").fetchone()
    console.print(f"repos: {repos[0]} (excluded {repos[1] or 0})")
```

- [ ] **Step 5: Run tests, try the CLI, commit.** `uv run pytest -v` Expected: all pass. `uv run scout --help` lists eight commands.
`git add src/oss_scout/cli.py src/oss_scout/queue_view.py tests/test_cli.py tests/test_queue_view.py && git commit -m "oss-scout: scout CLI with queue, dismiss, status, run"`

---

### Task 9: systemd timer and README

**Files:**
- Create: `oss-scout/systemd/oss-scout-run.service`, `oss-scout/systemd/oss-scout-run.timer`, `oss-scout/install_service.sh`, `oss-scout/README.md`

- [ ] **Step 1: Write the unit files**

```ini
# systemd/oss-scout-run.service
[Unit]
Description=OSS Scout hourly harvest, classify, score
After=network-online.target

[Service]
Type=oneshot
WorkingDirectory=/home/ryan/Coding_Projects/business projects/oss-scout
ExecStart=/home/ryan/.local/bin/uv run scout run
```

```ini
# systemd/oss-scout-run.timer
[Unit]
Description=Run OSS Scout every hour

[Timer]
OnBootSec=5min
OnUnitActiveSec=1h
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
# install_service.sh
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p ~/.config/systemd/user
cp systemd/oss-scout-run.service systemd/oss-scout-run.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now oss-scout-run.timer
systemctl --user list-timers oss-scout-run.timer
```

- [ ] **Step 2: Write README.md** covering: what it does in three sentences, install (`uv sync`, `.env`), the eight commands, the config file, how the score is built (table from the spec), the validation step and what `dropped_questions` means, the timer install, where logs are, and the rule that nothing writes to GitHub.

- [ ] **Step 3: Install and verify** `bash install_service.sh` then `systemctl --user status oss-scout-run.timer` shows active, and `journalctl --user -u oss-scout-run -n 20` after the first run shows the three summary lines.

- [ ] **Step 4: Commit** `git add systemd install_service.sh README.md && git commit -m "oss-scout: hourly systemd timer and README"`

---

### Task 10: First live run and the labeled fixture

**Files:**
- Create: `fixtures/labeled_issues.json`, `fixtures/validation_report_2026-10-01.txt`
- Modify: `scout.yaml` (`dropped_questions` if any)

- [ ] **Step 1: Live harvest.** `uv run scout harvest`. Expect several hundred seen, tens to low hundreds new after exclusions, errors near zero. Record the summary line in the commit message.

- [ ] **Step 2: Build the labeled set.** `uv run scout queue --state harvested --json` is empty by design (no scores yet), so pull directly:

```bash
uv run python - <<'EOF'
import json, random
from oss_scout import db
from oss_scout.config import load_config
conn = db.connect(load_config().db_path)
rows = [dict(r) for r in conn.execute("select i.id, i.repo, i.number, i.title, i.body, i.labels, i.comments, i.url, r.stars, r.language, r.description from issues i join repos r on r.full_name=i.repo where i.state='harvested'")]
random.seed(1); random.shuffle(rows)
sample = rows[:60]
out = [{"issue": {k: r[k] for k in ("repo","number","title","body","labels","comments","url")}, "repo": {k: r[k] for k in ("stars","language","description")}, "labels": {"category": None, "kind": None, "difficulty": None, "worth_a_look": None}} for r in sample]
json.dump(out, open("fixtures/labeled_issues.json","w"), indent=1)
print(len(out))
EOF
```

Then label all 60 by reading each issue. Labels are provisional (made by Claude on 2026-10-01) until Ryan reviews them; say so in the fixture's commit message and in README. Spread: the sample must contain at least 10 of each `kind` choice that exists and both `worth_a_look` choices, or draw a second batch.

- [ ] **Step 3: Validate.** `uv run scout validate-classifier | tee fixtures/validation_report_2026-10-01.txt`. Apply the drop decisions to `scout.yaml`.

- [ ] **Step 4: Classify and score live.** `uv run scout classify && uv run scout score && uv run scout queue`. Confirm the table shows ranked, categorized rows. Check the top 5 by hand: open each URL, confirm it is open, unassigned, has no closing PR, and the category looks right. Note any miss in the README's known-issues section.

- [ ] **Step 5: Commit.** `git add fixtures scout.yaml README.md && git commit -m "oss-scout: labeled fixture (provisional), validation report, first live queue"`

- [ ] **Step 6: Leave the timer running** and record in the handoff note that 24 unattended hours are the done condition.
