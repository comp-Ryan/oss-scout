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
- `agent_runs` (Stage 2), `threads` and `messages` (Stage 3), `drafts` (Stage 4): defined in those specs.

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

## 9. Analysis agents (Stage 2, summary)

One agy run per shortlisted issue: `agy --print --model gemini-3.8-flash-medium --output-format stream-json
--json-schema brief.schema.json --add-dir <temp clone>`. The prompt gives it the issue, the contributing
text, and the task: summarize, find the likely cause, propose an approach, list files, estimate effort, note
risks and AI policy, and look for similar open PRs. Stream events are appended to `agent_runs` as they
arrive so the frontend can show live progress. Caps: per-day run count, per-run timeout, per-issue retry
limit of 2. On success the issue moves to `analyzed`.

## 10. Frontend and discussion (Stage 3, summary)

Vite, React, TypeScript, served by FastAPI after `npm run build`. Screens: queue (filters for category, state,
difficulty, bounty, with score breakdown), issue page (brief, chat thread, approve, dismiss), agents page
(live runs, token counts, errors). Chat is an agy conversation resumed by `--conversation <id>`, one per
issue, stored in `threads` and `messages`.

## 11. Draft PR flow (Stage 4, summary)

On approve: fork if needed, create a worktree under `data/worktrees/<repo>/<issue>`, write `BRIEF.md`
(issue, agreed plan from the thread, contributing rules, test command). The issue page shows "open in
Claude Code", which launches a Claude Code session in that worktree with the brief as the first prompt in a
terminal. Ryan and Claude Code implement there. "Submit" runs the repo's tests, pushes the branch to the
fork, and opens a draft PR via `gh pr create --draft` with a body Ryan edits first. Commits are authored by
Ryan. An agy headless run (Sonnet or Opus, accept-edits mode) is the per-issue fallback for small fixes. If
tests fail, the issue returns to `discussing` with the output attached. The bot never marks a PR ready.

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
