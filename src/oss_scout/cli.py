from __future__ import annotations
import json
import logging
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
        for r in rows:
            console.print(f"  {r['rank']}. {r['url']}")


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
