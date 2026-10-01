# OSS Scout: Design Spec

Date: 2026-10-01. Status: approved in conversation, awaiting written review.

## 1. Purpose

A local bot that finds open-source issues worth Ryan's time, classifies and ranks them, has an agent analyze the
best ones, lets Ryan discuss each one in a dashboard, and then helps draft the pull request. The point is a
stronger GitHub profile first and learning TypeScript/React second. Paid bounties are a filter tag, not a goal.
Domain reputation (quant, prediction markets, AI agents) is handled by category weights.

Hard rule across every stage: **the bot never writes to GitHub without Ryan clicking approve.** Agents read
freely. Pushing a branch, opening a draft PR, and commenting are each a separate human action.

This spec covers the whole system so later stages have context, but only **Stage 1** (Section 12) is in scope
for the first implementation plan.

## 2. User profile and ranking priorities

- Strong now: Python. Learning: TypeScript and React.
- Tie-break order: GitHub profile value, then learning value, then everything else.
- Categories (one per issue): `quant_trading`, `prediction_markets_finance`, `ai_agents_llm`, `data_scraping`,
  `dev_tools_cli`, `web_frontend`, `other`.

## 3. Architecture

```
GitHub search API  --hourly-->  Harvester  --->  SQLite  <---  Scorer (pure function over stored signals)
                                                  |  ^
                                   Jev classifier |  |  Analysis agent runs (agy, Gemini Flash, stream-json)
                                                  v  |
                                      FastAPI backend  --->  React frontend (queue, issue page, agents page)
                                                  |
                                      Approve  --->  worktree + brief  --->  Claude Code session (Ryan drives)
                                                  |
                                      Submit   --->  tests, push to fork, draft PR via gh
```

Layout: `oss-scout/` beside the other projects.

```
oss-scout/
  pyproject.toml           uv-managed, Python >= 3.12, src layout like jev-trader
  src/oss_scout/
    config.py              weights, filters, limits (loaded from scout.yaml + env)
    db.py                  SQLite schema and migrations
    github_client.py       thin wrapper over `gh api` (REST and GraphQL), rate-limit aware
    harvest.py             search queries, repo signals, exclusions
    jev_client.py          typed-question client (same contract as jev-trader's RealJevClient)
    classify.py            the four questions, parsing, validation harness
    score.py               weighted score with breakdown
    transitions.py         state machine for issues
    agents/                Stage 2
    api/                   Stage 3 (FastAPI)
    cli.py                 `scout` entry point
  web/                     Stage 3 (Vite + React + TS)
  tests/
  fixtures/                recorded GitHub and Jev responses; the 60-issue labeled set
  scout.yaml               editable config
  docs/superpowers/        specs and plans
```

Secrets: GitHub token comes from `gh auth token` at startup (no separate PAT). Jev key from
`jev_model_api_key` in `oss-scout/.env`, same name jev-trader uses, loaded with python-dotenv.

## 4. Data model

SQLite, one file `data/scout.db`. Tables:

- `repos`: `full_name` (pk), `stars`, `language`, `pushed_at`, `org_known` (bool, from a small allowlist in
  config), `external_merge_rate` (float, fraction of the last 30 merged PRs authored by non-members),
  `median_first_response_hours`, `contributing_text` (nullable), `contributing_fetched_at`, `ai_policy`
  (`none` | `discouraged` | `banned`), `excluded_reason` (nullable).
- `issues`: `id` (GitHub node id, pk), `repo`, `number`, `title`, `body`, `labels` (json), `url`,
  `created_at`, `updated_at`, `comments`, `has_bounty` (bool), `bounty_source` (nullable),
  `linked_pr` (bool), `assignee` (nullable), `state` (Section 5), `harvested_at`, `last_seen_at`.
- `classifications`: `issue_id`, `question`, `choice`, `confidence`, `model`, `asked_at`. One row per
  question per model so a Gemini replacement for a dropped Jev question fits the same table.
- `scores`: `issue_id`, `total`, `breakdown` (json), `weights_hash`, `scored_at`. Re-scoring never deletes
  history.
- `transitions`: `issue_id`, `from_state`, `to_state`, `actor` (`harvester` | `classifier` | `scorer` |
  `agent:<run_id>` | `user`), `note`, `at`.
- `agent_runs` and `agent_events` (Stage 2, Section 9.1), `threads` and `messages` (Stage 3, Section 10.2), `drafts` (Stage 4, Section 11.1).

## 5. Issue states

`harvested -> classified -> shortlisted -> analyzed -> discussing -> plan_approved -> drafting -> pr_open ->
(merged | closed)`, plus `dismissed`, reachable from any state by the user and from `classified` by the classifier (Section 7), and `stale` set by the harvester
when an issue is closed or assigned upstream. `dismissed` and `stale` issues stay in the DB and are never
re-surfaced; a re-harvest only updates `last_seen_at`.

Transitions are only made through `transitions.apply(issue_id, to_state, actor, note)`, which validates the
edge, writes the row, and updates `issues.state` in one SQLite transaction. Invalid edges raise.

## 6. Harvester

Runs hourly (systemd timer in production, `scout harvest` by hand).

Queries, each run through GitHub's search API via `gh api` with pagination, 100 per page, stopping at the
configured page cap (default 3 pages per query):

- For each language in config (`Python`, `TypeScript`): `is:issue is:open no:assignee updated:>=<30 days ago>
  label:"good first issue"` and the same with `label:"help wanted"`.
- For each category keyword list in config (for example `backtest`, `polymarket`, `kalshi`, `mcp server`,
  `langchain`): `is:issue is:open no:assignee updated:>=<30 days ago> <keyword> in:title,body`.
- Bounty sweep: `label:bounty`. Algora has no public JSON feed (checked 2026-10-01), so bounty detection is
  label-only.
- Repo-first source (added after the first live run, 2026-10-01): repository search
  `stars:>=<min_stars> pushed:>=<idle cutoff> language:<lang> good-first-issues:>=1` (and
  `help-wanted-issues:>=1`), sorted by stars, `repo_pages_per_query` pages (default 1). For each repo found,
  the REST issue list filtered to open, unassigned, one good label, updated within the window. This is the
  source that reaches recognized repos; the issue search above is dominated by repos under the star floor.

Before any per-repo signal fetch, repos never seen before go through one batched GraphQL call (50 per call)
for stars and push date. Repos under the floor, idle, or missing are recorded as excluded without further
calls. The first live run excluded 92% of repos this way.

Per new repo, fetch once and refresh every 7 days: stars, language, pushed_at, the last 30 merged PRs with
author association (to compute `external_merge_rate`), first-maintainer-response times on the last 20 issues,
and `CONTRIBUTING.md` (or `.github/CONTRIBUTING.md`), refreshed every 30 days.

Exclusions, applied before classification and recorded in `excluded_reason` or by skipping the issue:

- Repo stars below `min_stars` (default 200).
- Repo not pushed in 60 days.
- `ai_policy = banned`: contributing text matches a configurable regex list such as
  `no (AI|LLM)[- ]generated`, `AI-assisted .* not accepted`. `discouraged` (softer wording) is kept but
  penalized in the score and shown on the issue.
- Issue has a linked PR (checked via the GraphQL timeline `CrossReferencedEvent` with `willCloseTarget`,
  and via `closingIssuesReferences` on open PRs).
- Issue already in a terminal, `dismissed`, or `stale` state.

Rate limits: the client reads `X-RateLimit-Remaining` and the search bucket (30 per minute) and sleeps until
reset when it would overrun. A harvest that stops early for rate reasons logs how far it got and resumes next
run; it never fails the whole batch.

## 7. Jev classifier

Jev is TypeSafe AI's typed-decision API: POST `https://api.typesafe.ai/v1/systemone` with `{model, state,
questions}` where each question is `{type: "choice", instructions, criteria: {choice: description}}`, returning
`answers[name] = {choice, confidence}`. The client is a copy of jev-trader's `RealJevClient._ask` with the same
SQLite response cache, generalized to accept any state text and question set.

State text per issue: repo full name and description, stars, language, title, labels, first 1,500 characters of
the body, comment count.

Four questions:

| name | choices |
|---|---|
| `category` | the seven categories from Section 2 |
| `kind` | `bug`, `feature`, `docs`, `test`, `chore` |
| `difficulty` | `small` (one file, under two hours), `medium` (a few files, a day), `large` (design work or multi-day) |
| `worth_a_look` | `yes` (a solo outside contributor could plausibly get this merged), `no` |

**Validation before the classifier is trusted.** A fixture of 60 harvested issues is hand-labeled by Ryan
with Claude's help and committed to `fixtures/labeled_issues.json`. `scout validate-classifier` runs Jev on
all 60 and prints, per question, agreement with the labels, the distribution of choices, and the standard
deviation of confidence. A question is **dropped** if its choices are near-constant (one choice above 85% on
a set hand-labeled to be balanced) or its agreement is below 60%. A dropped question is answered by a Gemini
Flash call through agy in print mode with a JSON schema, stored with `model = gemini` in the same table.
This is the same lesson jev-trader learned, where two of four Jev questions returned noise.

Classification runs after every harvest on all `harvested` issues, moves them to `classified`, and issues
with `worth_a_look = no` and confidence above a configured threshold go straight to `dismissed` with actor
`classifier` so Ryan can still see them under a filter.

## 8. Score

`score.compute(issue, repo, classification, weights) -> (total, breakdown)`. Pure function, no I/O. Every
term is a 0 to 1 value times its weight. Default weights in `scout.yaml`:

| term | source | default weight |
|---|---|---|
| recognition | log-scaled stars, plus 1.0 if `org_known` | 3.0 |
| mergeability | `external_merge_rate` and inverse of `median_first_response_hours` | 3.0 |
| language fit | Python 1.0, TypeScript 0.8 (learning boost), other 0.2 | 2.0 |
| category | per-category value from config (quant and AI agents 1.0, web 0.8, other 0.3) | 2.0 |
| difficulty fit | small 1.0, medium 0.8, large 0.3 | 1.5 |
| freshness | decays over 30 days since `updated_at` | 1.0 |
| bounty | 1.0 if `has_bounty` | 0.5 |
| ai policy | 0 if `none`, minus 0.5 if `discouraged` | 1.0 |

After each scoring pass, the top `shortlist_count` (default 20) `classified` issues whose total is at least `shortlist_floor` (default 4.0 of a 13.5 maximum, since the AI-policy term only subtracts) move to `shortlisted`; the rest stay `classified` and compete again next pass.
The breakdown is stored so the frontend shows why an item ranks where it does. Changing weights re-scores
everything on the next run; old scores stay in the table with their `weights_hash`.

## 9. Analysis agents (Stage 2)

One Antigravity CLI run per shortlisted issue, on Gemini Flash, in print mode with streaming JSON. Verified
2026-10-01: `agy --model <m> --output-format stream-json --json-schema <file> --add-dir <dir> --print <prompt>`
reads files through its own `view_file` tool without any permission prompt and returns the parsed answer in
the final `result` event under `structured_output`. `--print` must come last with the prompt right after it.

### 9.1 Tables (schema version 2)

- `agent_runs`: `id`, `issue_id`, `kind` (`analysis`), `model`, `status` (`running` | `done` | `failed`),
  `attempt`, `started_at`, `finished_at`, `conversation_id`, `input_tokens`, `output_tokens`,
  `duration_s`, `brief` (json, nullable), `error` (nullable).
- `agent_events`: `id`, `run_id`, `seq`, `at`, `event` (the raw stream-json line). Written as each line
  arrives so a reader can follow a run live. The Stage 3 frontend polls this table.

### 9.2 Inputs gathered before the run

- The issue row and its repo row (stars, language, contributing text truncated to 4,000 characters, AI policy).
- Related pull requests: every PR cross-referenced from the issue's timeline, open or closed, with number,
  state, title and whether it would close the issue. The closing check in Section 6 is the same query.
- A shallow partial clone, `git clone --depth 1 --filter=blob:limit=200k`, into `data/clones/<owner>__<name>`,
  reused on later runs. Clone timeout 180 s; a failed clone fails the run.

### 9.3 The brief

Fixed JSON schema, enforced by `--json-schema`:

| field | type |
|---|---|
| `summary` | string, two or three sentences |
| `likely_cause` | string |
| `approach` | string, the proposed change |
| `files` | array of repo-relative paths |
| `effort` | `small` / `medium` / `large` |
| `risks` | array of strings |
| `duplicate_risk` | `none` / `possible` / `likely`, judged from the related PRs |
| `ai_policy_note` | string, what the contributing docs say about AI help, or "none" |

The prompt tells the agent it is read-only: it may read, list and search files in the clone and nothing
else. It must not run build or network commands, edit files, or use git.

### 9.4 Run control

- `scout analyze [--limit N] [--issue owner/repo#N]` runs analysis for `shortlisted` issues in score order.
  `scout run` calls it after scoring.
- Caps from config: `analysis_daily_cap` (default 30 runs started per UTC day), `analysis_timeout_s`
  (default 600), two attempts per issue. An issue with two failed runs is left `shortlisted` and skipped.
- On success the issue moves `shortlisted -> analyzed` with actor `agent:<run_id>`. On failure the run row
  holds the error and the issue state does not change.
- `scout runs [--limit N]` lists recent runs with status, tokens and duration. `scout brief owner/repo#N`
  prints the latest brief.

### 9.5 Testing

A recorded stream-json transcript (from the 2026-10-01 probe) is the fixture. Tests inject a fake process
and a fake cloner: events are stored in order, the brief is parsed from the `result` event, the transition
fires, a timeout kills the process and records `failed`, the daily cap and the attempt limit are enforced,
and a clone failure records `failed` without starting the agent. The GitHub related-PR query is tested
against a recorded GraphQL response.

## 10. Frontend and discussion (Stage 3)

### 10.1 Backend API

FastAPI app in `src/oss_scout/api/`, started by `scout serve` (uvicorn on `127.0.0.1:8765`). It serves JSON
under `/api` and the built frontend from `web/dist` with a single-page fallback. All endpoints read the
SQLite file through the same modules the CLI uses; no logic lives only in the API.

| method and path | what it does |
|---|---|
| `GET /api/status` | counts per state, last transition, timer health (last `scout run` summary from the log) |
| `GET /api/queue?category&state&limit` | `queue_view.queue_rows` |
| `GET /api/issues/{id}` | issue, repo, latest classification, latest score with breakdown, latest brief, runs, transitions, thread |
| `POST /api/issues/{id}/dismiss` `{note}` | user dismiss |
| `POST /api/issues/{id}/discuss` | `analyzed -> discussing`; creates the thread |
| `GET /api/issues/{id}/messages` | the thread |
| `POST /api/issues/{id}/messages` `{content}` | appends the user message, runs one chat turn, appends the reply, returns both |
| `POST /api/issues/{id}/approve` `{plan}` | `discussing -> plan_approved`, stores the agreed plan text on the thread |
| `GET /api/runs?limit` | recent agent runs |
| `GET /api/runs/{id}/events?after=seq` | events with `seq > after`; the agents page polls this every 2 s while a run is `running` |
| `GET /api/settings` | current weights and category values |
| `PUT /api/settings` | writes `weights` and `category_values` into `scout.yaml` and re-scores |

### 10.2 Chat

Tables: `threads(issue_id pk, conversation_id, plan, created_at)` and `messages(id, issue_id, role
(`user` | `assistant`), content, at, input_tokens, output_tokens)`. Schema version 3.

One agy conversation per issue, verified 2026-10-01: a first `agy --output-format json --print ...` call returns
a `conversation_id`, and `agy --conversation <id> --print ...` continues it with memory of earlier turns. The
first turn carries the issue, the brief, the contributing notes and the related PRs as context, and runs with
cwd in the clone and `--add-dir` so the agent can read code when asked. Later turns send only the user's
message. Chat model from config `chat_model`, default `claude-sonnet-4-6`, because discussion quality matters
more than cost here; the per-turn timeout is `chat_timeout_s` (300). The chat agent is told the same
read-only rule as the analysis agent.

### 10.3 Frontend

Vite + React + TypeScript in `web/`, plain CSS, `react-router-dom` for three routes. No state library; each
page fetches from `/api` with hooks.

- **Queue** (`/`): filter chips for category, state, difficulty, bounty; a table of rank, score, repo, issue,
  category, kind, difficulty, state, title; hovering the score shows the breakdown; the row links to the
  issue page. A small settings drawer edits weights and category values and calls `PUT /api/settings`.
- **Issue** (`/issues/:id`): header with title, repo, labels, link to GitHub; the classification and score
  breakdown; the brief; the related runs; the chat thread with an input box; actions: Discuss (when
  `analyzed`), Approve plan (when `discussing`, with a textarea prefilled from the brief's approach), Dismiss.
- **Agents** (`/agents`): the runs list; selecting a run shows its events, newest at the bottom, polling while
  running; token totals per day.

The build output is committed? No: `web/dist` is git-ignored and built by `install_service.sh` (`npm ci &&
npm run build`). `scout open` runs `xdg-open http://127.0.0.1:8765`.

### 10.4 Services

`systemd/oss-scout.service` (simple, `uv run scout serve`, restart on failure) alongside the existing timer.
The API process is read-mostly; writes are the user's actions and chat turns, all short transactions.

### 10.5 Testing

FastAPI `TestClient` against a temp database for every endpoint, including the state transitions they cause
and the 404 and 409 paths (dismiss an unknown issue; discuss an issue that is not `analyzed`). Chat with an
injected runner that returns recorded agy JSON for the first and a resumed turn. Settings PUT against a temp
`scout.yaml`. Frontend: Vitest with Testing Library for the queue filters and the score breakdown.

## 11. Draft PR flow (Stage 4)

The only stage that writes to GitHub, and only on three explicit user actions: fork, push a branch, open a
draft PR. Each is a button on the issue page or a CLI command. The bot never marks a PR ready and never
comments.

### 11.1 Table (schema version 4)

`drafts(issue_id pk, work_dir, worktree, branch, base_branch, brief_path, test_cmd, pr_url, created_at,
submitted_at, last_error)`.

### 11.2 Prepare (`plan_approved -> drafting`)

`scout prep owner/repo#N` or `POST /api/issues/{id}/prep`:

1. Fork: `gh repo fork owner/repo --clone=false` (idempotent; GitHub returns the existing fork).
2. Work clone: `git clone --filter=blob:none https://github.com/<user>/<repo> data/work/<owner>__<repo>`,
   reused on later issues of the same repo, with an `upstream` remote pointing at the original. A partial
   clone, not a shallow one, because GitHub rejects pushes from shallow clones.
3. Default branch from `gh repo view owner/repo --json defaultBranchRef`; `git fetch upstream <default>`.
4. Worktree: `git worktree add data/worktrees/<owner>__<repo>/<N> -b scout/<N>-<slug> upstream/<default>`.
5. `BRIEF.md` in the worktree: issue title, URL and body, the approved plan from the thread, the brief's
   files and approach, contributing notes (truncated), the AI policy note, the rule that commits are Ryan's and
   nothing is pushed by the bot, and the test command guess (`test_cmd`: from contributing text if it names
   one, else by language: `pytest` for Python, `npm test` for TypeScript/JavaScript).
6. Insert the `drafts` row; transition with actor `user`.

### 11.3 Work

`scout work owner/repo#N` or `POST /api/issues/{id}/launch` opens a terminal (`x-terminal-emulator -e`) in
the worktree running `claude` with the brief as the opening prompt, and returns the exact command so the user
can run it elsewhere (Orca, tmux) if the launcher cannot open a window. Claude Code is interactive; Ryan
drives it. The optional headless path, CLI only: `scout work owner/repo#N --agent` runs
`agy --mode accept-edits --model <draft_model> --print <brief>` in the worktree and reports the diff stat.

### 11.4 Submit (`drafting -> pr_open`, or back to `discussing`)

`scout submit owner/repo#N [--title] [--body]` or `POST /api/issues/{id}/submit {title, body}`:

1. Refuse (409) if the worktree has uncommitted changes or no commits ahead of `upstream/<default>`.
2. Run `test_cmd` in the worktree (timeout 900 s). On failure: store the tail of the output in
   `last_error`, transition `drafting -> discussing` with the note "tests failed", and return 409 with the
   output. Nothing is pushed.
3. `git push -u origin <branch>` (origin is the fork), then
   `gh pr create --repo owner/repo --head <user>:<branch> --base <default> --draft --title --body`.
   Title defaults to the issue title; body defaults to the approved plan plus "Closes #N". Store `pr_url`,
   `submitted_at`; transition `drafting -> pr_open`.
4. The harvester later moves `pr_open -> merged | closed` by reading the PR state (Section 6 gains one
   check per `pr_open` issue).

### 11.5 Frontend

Issue page actions by state: `plan_approved` shows **Prepare worktree**; `drafting` shows the worktree
path, the launch command in a copyable box, **Open in Claude Code**, and a submit form (title and body
prefilled) with **Open draft PR**; `pr_open` shows the PR link. Test output from a failed submit is shown
on the page.

### 11.6 Testing

All git and gh calls go through one injected shell function; tests assert the exact command sequences,
the refusal on a dirty worktree, the test-failure path and its transition, idempotent fork and clone reuse,
and BRIEF.md content (plan present, "None" never present). No test forks, pushes or opens anything.
The first real fork, push and draft PR are Ryan's to run.

## 12. Stage 1 scope

In scope:

- Sections 3 to 8 in full: project skeleton, config, DB and migrations, GitHub client, harvester with
  exclusions and repo signals, Jev client and classifier, the validation harness and the 60-issue labeled
  fixture, scorer, state machine.
- `scout` CLI: `harvest`, `classify`, `validate-classifier`, `score`, `queue` (ranked table in the terminal
  with filters by category and state), `dismiss <issue>`, `status`.
- systemd user unit and timer for the hourly harvest-classify-score pass, following the `polycopy-*` pattern.

Out of scope for Stage 1: agents, API, frontend, chat, worktrees, PRs. The `agent_runs`, `threads`,
`messages`, and `drafts` tables are not created until their stage.

Done when: `scout harvest && scout classify && scout score && scout queue` prints a ranked, categorized
list from real GitHub data, the classifier validation report exists with its drop decisions recorded, and
the timer has run unattended for 24 hours.

## 13. Error handling

- GitHub errors: 403 rate limit sleeps until reset; 5xx retries three times with backoff; 422 (bad query)
  logs and skips that query. The harvest never aborts on a single query.
- Jev errors: timeouts and 5xx retry twice, then the issue stays `harvested` and is retried next pass. A
  malformed answer is stored raw with `choice = null` and the issue is not advanced.
- Every CLI command exits non-zero on an unhandled error and writes the traceback to `data/scout.log`.
- The DB is written in short transactions per issue so a crash mid-pass loses at most one issue's update.

## 14. Testing

- Unit tests with pytest for: query builder, exclusion rules (each rule has a positive and negative case),
  `external_merge_rate` and response-time computation, AI-policy regex against sample contributing texts,
  score terms and the total, state-machine edges (valid and invalid), Jev response parsing.
- The GitHub client is tested against recorded JSON fixtures; one opt-in live test hits the real API.
- The Jev client is tested against recorded responses; `validate-classifier` runs live by hand and its
  report is committed under `fixtures/`.
- No test touches the real DB file; each test gets a temp SQLite path.

## 15. Operations

- Install: `uv sync` in `oss-scout/`, copy `.env.example` to `.env`, add the Jev key.
- Run by hand: `uv run scout harvest`, then `classify`, `score`, `queue`.
- Production: `systemctl --user enable --now oss-scout-harvest.timer` runs the three steps hourly.
- Logs: `data/scout.log` and `journalctl --user -u oss-scout-harvest`.
- Stage 3 adds `oss-scout.service` for the API and the single localhost URL to open.
