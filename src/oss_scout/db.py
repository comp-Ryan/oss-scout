from __future__ import annotations
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = "2"

SCHEMA_V1 = """
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
    conn.execute("insert into meta(key, value) values ('schema_version', ?) on conflict(key) do update set value = excluded.value", (SCHEMA_VERSION,))
    conn.commit()
