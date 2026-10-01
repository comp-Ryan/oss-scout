# OSS Scout

Finds open GitHub issues worth your time, classifies them with Jev, scores them against your profile, and
prints a ranked queue. Stage 1 is a CLI; later stages add analysis agents, a dashboard, and a draft-PR flow.
See `docs/superpowers/specs/2026-10-01-oss-scout-design.md` for the full design.

**Rule:** nothing here writes to GitHub. Stage 1 only reads: search, repo metadata, issue timelines, and
contributing files.

## Install

```bash
cd oss-scout
uv sync --extra dev
cp .env.example .env   # then put your Jev key in jev_model_api_key=
gh auth status         # the GitHub token comes from `gh auth token`
```

## Commands

| command | what it does |
|---|---|
| `uv run scout harvest` | run the GitHub searches, store new issues, refresh repo signals, mark stale ones |
| `uv run scout classify [--limit N]` | ask Jev the four questions for every `harvested` issue |
| `uv run scout score` | score every `classified` or `shortlisted` issue and refresh the top-N shortlist |
| `uv run scout run` | the three above in order (what the hourly timer calls) |
| `uv run scout queue [--category C] [--state S] [--limit N] [--json]` | the ranked list with URLs |
| `uv run scout dismiss owner/repo#N [--note ...]` | never show that issue again |
| `uv run scout validate-classifier [--labeled PATH]` | compare Jev against the hand-labeled fixture |
| `uv run scout status` | counts per state, last activity |

Categories: `quant_trading`, `prediction_markets_finance`, `ai_agents_llm`, `data_scraping`,
`dev_tools_cli`, `web_frontend`, `other`.

## Analysis agents (Stage 2)

| command | what it does |
|---|---|
| `uv run scout analyze [--limit N] [--issue owner/repo#N]` | run the Antigravity CLI (`agy`, Gemini Flash) on shortlisted issues in score order |
| `uv run scout runs [--limit N]` | recent agent runs with status, duration, tokens, errors |
| `uv run scout brief owner/repo#N` | the latest brief: summary, likely cause, approach, files, effort, risks, duplicate risk, AI-policy note |

Each run makes a shallow partial clone under `data/clones/<owner>__<name>` and gives the agent read-only
instructions; it may read, list and search files and nothing else. Events stream into `agent_events` as they
arrive. Caps: `analysis_daily_cap` (30 runs per UTC day), `analysis_timeout_s` (600), two attempts per issue.
`scout run` analyzes after scoring, so the hourly timer keeps briefs flowing under the cap.

## Dashboard (Stage 3)

```bash
./install_service.sh        # builds web/dist, installs the timer and the dashboard service
uv run scout open           # http://127.0.0.1:8765
```

Three pages. **Queue**: filter chips for category and state, score breakdown on hover, row links to the
issue. **Issue**: the brief, classification and score, the issue body and history, a chat thread with an
agent that already knows the issue and brief and can read the clone, and the actions Discuss (`analyzed ->
discussing`), Approve plan (`discussing -> plan_approved`, stores the agreed plan text), Dismiss. **Agents**:
recent runs with tokens and errors; selecting a running run follows its events live.

Chat is one Antigravity conversation per issue (`chat_model`, default `claude-sonnet-4-6`), resumed by ID on
every turn. Nothing on the dashboard writes to GitHub.

Dev loop: `uv run scout serve` in one terminal, `cd web && npm run dev` in another (proxies `/api`),
`npm test` for the Vitest suite. Services: `oss-scout-run.timer` (hourly pipeline) and `oss-scout.service`
(the API); `journalctl --user -u oss-scout -n 50` for logs.

## Draft PR flow (Stage 4)

The only part that writes to GitHub, and only on these explicit actions (buttons on the issue page or CLI):

| action | state change | what happens |
|---|---|---|
| `uv run scout prep owner/repo#N` | `plan_approved -> drafting` | `gh repo fork` (idempotent), a partial clone of your fork under `data/work/`, `upstream` remote, a branch `scout/N-slug` in a worktree under `data/worktrees/`, and `BRIEF.md` with the issue, the agreed plan, the brief and the test command |
| `uv run scout work owner/repo#N` | none | opens a terminal running `claude` in the worktree with the brief as the first prompt, and prints the command in case the terminal cannot open. `--agent` instead lets `agy` implement the plan headlessly (`accept-edits`); you still review and commit |
| `uv run scout submit owner/repo#N [--title] [--body]` | `drafting -> pr_open`, or `-> discussing` if tests fail | refuses on uncommitted changes or no commits; runs the test command; pushes the branch to your fork; `gh pr create --draft` against upstream with "Closes #N" |

Commits are yours. The bot never runs `git commit`, never marks a PR ready, never comments. The hourly
run checks open draft PRs and moves the issue to `merged` or `closed` when GitHub says so.

**First run note (2026-10-01):** the live fork, push and PR path has not been exercised; every git and gh
call is covered by tests against a fake shell, but the first real `scout prep` and `scout submit` are
yours to run and watch.

## Config

`scout.yaml` overrides defaults in `src/oss_scout/config.py`. The keys you will actually tune:
`min_stars`, `languages`, `category_keywords`, `weights`, `category_values`, `shortlist_count`,
`shortlist_floor`, `dropped_questions`.

## How the score is built

Each term is 0 to 1, times its weight. Max total with default weights is 13.5.

| term | source | weight |
|---|---|---|
| recognition | log-scaled stars, plus known org | 3.0 |
| mergeability | share of recent merged PRs from outsiders, maintainer response time | 3.0 |
| language_fit | Python 1.0, TypeScript 0.8, JavaScript 0.5, other 0.2 | 2.0 |
| category | per-category value from config | 2.0 |
| difficulty_fit | small 1.0, medium 0.8, large 0.3 | 1.5 |
| freshness | decays to 0 over 30 days since last update | 1.0 |
| bounty | 1 if a bounty label is present | 0.5 |
| ai_policy | 0 normally, -0.5 if contributing docs discourage AI help | 1.0 |

Issues come from two sources: GitHub issue search by label and category keyword (finds niche repos), and a
repo-first pass that searches repositories above the star floor advertising good-first-issue or help-wanted
counts and lists their open unassigned issues (this is what reaches the recognized repos). Repos never seen
before are checked for stars and activity in batches of 50 before any expensive per-repo call.

Two more sources feed the discover lane. **Seed lists** (`scout seeds`): gittrend.io's rising and hidden-gems
pages, up-for-grabs.net's project files, goodfirstissue.dev's list, and awesome-for-beginners, fetched once
a day and cached under `data/seeds/`, worked through `seed_repos_per_run` repos per hourly run. **Star
velocity**: every star count the harvester sees is kept in `repo_star_history`; after two samples a day apart
the repo gets `stars_per_day`, shown on cards as "+N/day", sortable as "rising fast", and counted as rising
above `rising_stars_per_day`.

Repos whose contributing docs ban AI-generated PRs are excluded entirely, as are repos under the star floor,
repos idle for 60 days, and issues that already have an open PR that would close them.

## Classifier validation

Jev answers four typed questions per issue: category, kind, difficulty, worth_a_look. Before trusting it,
`scout validate-classifier` runs the 60 hand-labeled issues in `fixtures/labeled_issues.json` and prints
agreement per question. A question is dropped when one answer dominates (above 85%) or agreement is under
60%. Dropped questions are listed in `scout.yaml` under `dropped_questions` and answered by Gemini Flash
through the `agy` CLI instead. The latest report is in `fixtures/`.

The labels in the fixture were made by Claude on 2026-10-01 and are provisional until Ryan reviews them.

## Running it hourly

```bash
./install_service.sh
systemctl --user status oss-scout-run.timer
journalctl --user -u oss-scout-run -n 30
```

Logs: `data/scout.log`. Database: `data/scout.db` (SQLite). Jev responses are cached in `data/jev_cache.db`.

## Tests

`uv run pytest`. No test touches the network or a real database.

## Known issues (2026-10-01, first live run)

- The score does not yet see closed PRs. Issue langchain-ai/langchain#40592 ranked third with seven closed
  PRs against it, which usually means a contested or harder-than-it-looks issue. Stage 2's brief reports
  `duplicate_risk` from the related PRs; a "closed attempts" penalty in the score is a candidate tweak.
- Keyword queries let in non-Python/TypeScript repos (Rust, C++, docs-only). They score low on language fit
  but still cost classification calls. A language allowlist on keyword hits is a candidate tweak.
- Every hourly run re-checks each known open issue for a closing PR (one GraphQL call per issue). At ~1,000
  issues that is most of the run's calls. Checking only issues whose `updated_at` changed would cut it.
- The 60 labels in `fixtures/labeled_issues.json` are Claude's, not Ryan's. Re-run `scout validate-classifier`
  after reviewing them.
