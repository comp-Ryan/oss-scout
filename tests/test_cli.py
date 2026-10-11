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
    r = CliRunner().invoke(cli.main, ["dismiss", "garbage"])
    assert r.exit_code != 0

def test_help_lists_all_commands():
    r = CliRunner().invoke(cli.main, ["--help"])
    for name in ("harvest", "classify", "score", "run", "validate-classifier", "queue", "dismiss", "status"):
        assert name in r.output

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

def test_help_lists_serve_and_open():
    r = CliRunner().invoke(cli.main, ["--help"])
    assert "serve" in r.output and "open" in r.output

def test_service_unit_exists_with_path():
    from pathlib import Path
    unit = (Path(__file__).resolve().parents[1] / "systemd" / "oss-scout.service").read_text()
    assert "@UV@ run scout serve" in unit and "Environment=PATH=@UV_DIR@:" in unit and "Restart=on-failure" in unit

def test_help_lists_prep_work_submit():
    r = CliRunner().invoke(cli.main, ["--help"])
    for name in ("prep", "work", "submit"):
        assert name in r.output

def test_prep_work_submit_commands_call_draft(tmp_path, monkeypatch):
    cfg = Config(data_dir=tmp_path)
    monkeypatch.setattr(cli, "load_config", lambda: cfg)
    conn = db.connect(cfg.db_path)
    conn.execute("insert into repos(full_name) values ('o/r')")
    conn.execute("insert into issues(id, repo, number, title, url, state, harvested_at, last_seen_at) values ('I1','o/r',4,'t','u','plan_approved','x','x')")
    conn.commit(); conn.close()
    from oss_scout.agents import draft
    seen = []
    monkeypatch.setattr(draft, "prep", lambda conn, cfg, iid, **kw: seen.append(("prep", iid)) or {"worktree": "/w", "branch": "b", "brief_path": "/w/BRIEF.md"})
    monkeypatch.setattr(draft, "work", lambda conn, cfg, iid, **kw: seen.append(("work", iid)) or {"launched": False, "command": "cd /w && claude", "cwd": "/w"})
    monkeypatch.setattr(draft, "submit", lambda conn, cfg, iid, **kw: seen.append(("submit", iid, kw.get("title"))) or {"pr_url": "https://x/pull/1", "tests": "passed", "branch": "b"})
    assert CliRunner().invoke(cli.main, ["prep", "o/r#4"]).exit_code == 0
    r = CliRunner().invoke(cli.main, ["work", "o/r#4"])
    assert r.exit_code == 0 and "claude" in r.output
    r = CliRunner().invoke(cli.main, ["submit", "o/r#4", "--title", "T"])
    assert r.exit_code == 0 and "pull/1" in r.output
    assert [s[0] for s in seen] == ["prep", "work", "submit"] and seen[2][2] == "T"


def test_analyze_skips_cleanly_without_agy(monkeypatch):
    called = []
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)
    monkeypatch.setattr(cli.analysis, "run_pending", lambda *a, **k: called.append(1))
    assert cli._do_analyze(Config(), None) is None
    assert called == []
