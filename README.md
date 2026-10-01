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
