from __future__ import annotations
import json
import logging
from pathlib import Path
import click
from rich.console import Console
from oss_scout import classify, db, harvest, queue_view, score, transitions, validate
from oss_scout.agents import analysis, draft
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


def _gh(cfg):
    return GitHubClient(github_token(), min_interval=cfg.github_min_interval, search_min_interval=cfg.github_search_min_interval)


def _issue_id(conn, ref: str) -> str:
    try:
        repo, number = ref.split("#", 1)
        row = conn.execute("select id from issues where repo = ? and number = ?", (repo, int(number))).fetchone()
    except ValueError:
        raise click.BadParameter("expected owner/repo#number")
    if row is None:
        raise click.ClickException(f"no such issue {ref}")
    return row["id"]


def _do_analyze(cfg, conn, limit=None):
    s = analysis.run_pending(conn, cfg, _gh(cfg), limit)
    logging.info("analyze %s", s)
    console.print(f"analyze: started={s.started} done={s.done} failed={s.failed} skipped_cap={s.skipped_cap} skipped_attempts={s.skipped_attempts}")
    return s


def _do_harvest(cfg, conn):
    s = harvest.run_harvest(conn, _gh(cfg), cfg)
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


@click.group()
def main():
    """OSS Scout: find open-source issues worth your time."""


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


@main.command("reclassify")
@click.option("--question", default="category", help="which of the four questions to re-ask")
@click.option("--limit", type=int, default=None)
def reclassify_command(question, limit):
    """Re-ask one question for every classified issue (after changing the category set)."""
    cfg = load_config(); _setup_logging(cfg)
    primary, _ = _classifiers(cfg)
    n = classify.run_reclassify(db.connect(cfg.db_path), cfg, primary, question, limit)
    logging.info("reclassify %s: %d", question, n)
    console.print(f"reclassified {question} for {n} issues")


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
    _do_harvest(cfg, conn); _do_classify(cfg, conn); _do_score(cfg, conn); _do_analyze(cfg, conn)


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
@click.option("--lane", default=None, help="resume (recognized repos) or discover (small and rising repos)")
@click.option("--json", "as_json", is_flag=True)
def queue_command(category, state, limit, lane, as_json):
    """Ranked list of issues worth a look."""
    cfg = load_config()
    rows = queue_view.queue_rows(db.connect(cfg.db_path), category, state, limit, lane)
    if as_json:
        click.echo(json.dumps(rows, indent=1))
    else:
        console.print(queue_view.render_table(rows))
        for r in rows:
            console.print(f"  {r['rank']}. {r['url']}")


@main.command("dismiss")
@click.argument("issue")
@click.option("--note", default="")
def dismiss_command(issue, note):
    """Dismiss owner/repo#number so it never comes back."""
    cfg = load_config(); _setup_logging(cfg)
    conn = db.connect(cfg.db_path)
    transitions.apply(conn, _issue_id(conn, issue), "dismissed", "user", note)
    console.print(f"dismissed {issue}")


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


@main.command("prep")
@click.argument("issue")
def prep_command(issue):
    """Fork, clone, branch and worktree with BRIEF.md for an approved plan (owner/repo#number)."""
    cfg = load_config(); _setup_logging(cfg)
    conn = db.connect(cfg.db_path)
    try:
        d = draft.prep(conn, cfg, _issue_id(conn, issue))
    except draft.DraftError as e:
        raise click.ClickException(str(e))
    console.print(f"worktree {d['worktree']} on {d['branch']}\nbrief {d['brief_path']}")


@main.command("work")
@click.argument("issue")
@click.option("--agent", is_flag=True, help="headless: let agy implement the plan in the worktree (you still review and commit)")
def work_command(issue, agent):
    """Open Claude Code in the issue's worktree with the brief as the first prompt."""
    cfg = load_config(); _setup_logging(cfg)
    conn = db.connect(cfg.db_path)
    try:
        if agent:
            console.print(draft.work_agent(conn, cfg, _issue_id(conn, issue)))
            return
        out = draft.work(conn, cfg, _issue_id(conn, issue))
    except draft.DraftError as e:
        raise click.ClickException(str(e))
    console.print(("launched in a terminal; " if out["launched"] else "no terminal found; run this yourself:\n") + out["command"])


@main.command("submit")
@click.argument("issue")
@click.option("--title", default=None)
@click.option("--body", default=None)
def submit_command(issue, title, body):
    """Run tests, push the branch to your fork, open a DRAFT pull request."""
    cfg = load_config(); _setup_logging(cfg)
    conn = db.connect(cfg.db_path)
    try:
        out = draft.submit(conn, cfg, _issue_id(conn, issue), title=title, body=body)
    except draft.TestsFailed as e:
        raise click.ClickException(f"tests failed; issue moved back to discussing:\n{e}")
    except draft.DraftError as e:
        raise click.ClickException(str(e))
    console.print(f"draft PR: {out['pr_url']} (tests {out['tests']})")
