import io, json, subprocess
from datetime import datetime, timezone
from pathlib import Path
import pytest
from oss_scout import db, transitions
from oss_scout.agents import analysis
from oss_scout.config import Config

FIX = (Path(__file__).parent / "fixtures" / "agy_stream_probe.jsonl").read_text().splitlines()
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)

class FakeProc:
    def __init__(self, lines, code=0, hang=False):
        self.stdout = io.StringIO("\n".join(lines) + "\n"); self.stderr = io.StringIO("")
        self.returncode = code; self.hang = hang; self.killed = False
    def wait(self, timeout=None):
        if self.hang: raise subprocess.TimeoutExpired("agy", timeout)
        return self.returncode
    def kill(self): self.killed = True

class FakeGH:
    def related_prs(self, node_id): return [{"number": 7, "state": "OPEN", "title": "wip", "url": "u", "will_close": False}]

def _seed(conn, issue_id="I1", state="shortlisted", contributing="Be nice"):
    conn.execute("insert or ignore into repos(full_name, stars, language, contributing_text, ai_policy) values ('o/r', 500, 'Python', ?, 'none')", (contributing,))
    conn.execute("insert into issues(id, repo, number, title, body, labels, url, state, harvested_at, last_seen_at) values (?,'o/r',1,'T','B','[\"bug\"]','https://github.com/o/r/issues/1',?,'x','x')", (issue_id, state))
    conn.execute("insert into scores(issue_id, total, breakdown, weights_hash, scored_at) values (?, 9.0, '{}', 'h', 'x')", (issue_id,))
    conn.commit()

def _cfg(tmp_path, **kw):
    return Config(data_dir=tmp_path, **kw)

def test_prompt_mentions_issue_repo_related_prs_and_missing_contributing():
    issue = {"repo": "o/r", "number": 1, "title": "T", "body": None, "labels": '["bug"]', "url": "u"}
    repo = {"stars": 5, "language": "Python", "contributing_text": None, "ai_policy": "none"}
    p = analysis.build_prompt(issue, repo, [{"number": 7, "state": "OPEN", "title": "wip", "url": "u7", "will_close": False}])
    assert "o/r#1" in p and "#7" in p and "wip" in p and "no contributing file" in p.lower() and "None" not in p
    long = analysis.build_prompt(issue, {**repo, "contributing_text": "x" * 10000}, [])
    assert len(long) < 6000

def test_agy_command_puts_print_last_with_prompt():
    cmd = analysis.agy_command("m", "/s.json", "/c", "PROMPT")
    assert cmd[0] == "agy" and cmd[-2:] == ["--print", "PROMPT"]
    assert cmd[cmd.index("--model") + 1] == "m" and cmd[cmd.index("--json-schema") + 1] == "/s.json"
    assert cmd[cmd.index("--add-dir") + 1] == "/c" and "stream-json" in cmd

def test_successful_run_stores_events_brief_and_transitions(tmp_path):
    conn = db.connect(tmp_path / "t.db"); _seed(conn)
    proc = FakeProc(FIX)
    captured = {}
    def popen(cmd, **kw): captured["cmd"] = cmd; captured["cwd"] = kw.get("cwd"); return proc
    rid = analysis.run_one(conn, _cfg(tmp_path), FakeGH(), "I1", popen=popen, cloner=lambda *a, **k: tmp_path / "clone", now=NOW)
    run = conn.execute("select * from agent_runs where id=?", (rid,)).fetchone()
    assert run["status"] == "done" and run["conversation_id"] and run["input_tokens"] > 0 and run["duration_s"] > 0
    brief = json.loads(run["brief"])
    assert "summary" in brief and isinstance(brief["files"], list)
    assert conn.execute("select count(*) from agent_events where run_id=?", (rid,)).fetchone()[0] == len(FIX)
    assert [r[0] for r in conn.execute("select seq from agent_events where run_id=? order by seq", (rid,))] == list(range(1, len(FIX) + 1))
    assert conn.execute("select state from issues where id='I1'").fetchone()[0] == "analyzed"
    assert conn.execute("select actor from transitions where to_state='analyzed'").fetchone()[0] == f"agent:{rid}"
    assert captured["cwd"] == str(tmp_path / "clone")
    assert analysis.latest_brief(conn, "I1")["summary"] == brief["summary"]

def test_non_json_line_is_stored_raw_and_run_still_succeeds(tmp_path):
    conn = db.connect(tmp_path / "t.db"); _seed(conn)
    lines = ["warning: something", *FIX]
    rid = analysis.run_one(conn, _cfg(tmp_path), FakeGH(), "I1", popen=lambda cmd, **kw: FakeProc(lines), cloner=lambda *a, **k: tmp_path, now=NOW)
    first = conn.execute("select event from agent_events where run_id=? and seq=1", (rid,)).fetchone()[0]
    assert "warning: something" in first
    assert conn.execute("select status from agent_runs where id=?", (rid,)).fetchone()[0] == "done"

def test_failed_result_or_missing_structured_output_marks_failed(tmp_path):
    conn = db.connect(tmp_path / "t.db"); _seed(conn)
    bad = [json.dumps({"event": "result", "result": {"status": "ERROR", "response": "nope", "conversation_id": "c"}})]
    rid = analysis.run_one(conn, _cfg(tmp_path), FakeGH(), "I1", popen=lambda cmd, **kw: FakeProc(bad), cloner=lambda *a, **k: tmp_path, now=NOW)
    run = conn.execute("select status, error from agent_runs where id=?", (rid,)).fetchone()
    assert run["status"] == "failed" and "ERROR" in run["error"]
    assert conn.execute("select state from issues where id='I1'").fetchone()[0] == "shortlisted"

def test_timeout_kills_process_and_marks_failed(tmp_path):
    conn = db.connect(tmp_path / "t.db"); _seed(conn)
    proc = FakeProc([], hang=True)
    rid = analysis.run_one(conn, _cfg(tmp_path, analysis_timeout_s=1), FakeGH(), "I1", popen=lambda cmd, **kw: proc, cloner=lambda *a, **k: tmp_path, now=NOW)
    assert proc.killed
    assert conn.execute("select status from agent_runs where id=?", (rid,)).fetchone()[0] == "failed"

def test_clone_failure_marks_failed_without_starting_agent(tmp_path):
    from oss_scout.agents.clone import CloneError
    conn = db.connect(tmp_path / "t.db"); _seed(conn)
    def cloner(*a, **k): raise CloneError("nope")
    started = []
    rid = analysis.run_one(conn, _cfg(tmp_path), FakeGH(), "I1", popen=lambda cmd, **kw: started.append(1), cloner=cloner, now=NOW)
    assert started == [] and conn.execute("select status, error from agent_runs where id=?", (rid,)).fetchone()["error"] == "nope"

def test_run_pending_respects_daily_cap_and_attempts(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    for i in range(3):
        _seed(conn, f"I{i}")
    cfg = _cfg(tmp_path, analysis_daily_cap=2)
    bad = [json.dumps({"event": "result", "result": {"status": "ERROR", "conversation_id": "c"}})]
    s = analysis.run_pending(conn, cfg, FakeGH(), popen=lambda cmd, **kw: FakeProc(list(bad)), cloner=lambda *a, **k: tmp_path, now=NOW)
    assert s.started == 2 and s.failed == 2 and s.skipped_cap == 1
    assert analysis.runs_started_today(conn, NOW) == 2
    later = NOW.replace(day=2)
    cfg2 = _cfg(tmp_path, analysis_daily_cap=10)
    s2 = analysis.run_pending(conn, cfg2, FakeGH(), popen=lambda cmd, **kw: FakeProc(list(bad)), cloner=lambda *a, **k: tmp_path, now=later)
    assert s2.started == 3
    s3 = analysis.run_pending(conn, cfg2, FakeGH(), popen=lambda cmd, **kw: FakeProc(list(bad)), cloner=lambda *a, **k: tmp_path, now=later.replace(day=3))
    assert s3.started == 1 and s3.skipped_attempts == 2

def test_run_pending_orders_by_score(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    _seed(conn, "low"); _seed(conn, "high")
    conn.execute("update scores set total = 1.0 where issue_id='low'"); conn.commit()
    order = []
    def popen(cmd, **kw):
        order.append(kw["cwd"]); return FakeProc(FIX)
    analysis.run_pending(conn, _cfg(tmp_path), FakeGH(), popen=popen, cloner=lambda name, *a, **k: tmp_path / name.replace("/", "__"), now=NOW, limit=2)
    assert len(order) == 2
    assert conn.execute("select issue_id from agent_runs order by id").fetchall()[0][0] == "high"
