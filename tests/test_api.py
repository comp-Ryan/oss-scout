import json
from fastapi.testclient import TestClient
from oss_scout import db
from oss_scout.api.app import create_app
from oss_scout.config import Config

def _seed(cfg, state="analyzed"):
    conn = db.connect(cfg.db_path)
    conn.execute("insert into repos(full_name, stars, language, ai_policy) values ('o/r', 500, 'Python', 'none')")
    conn.execute("insert into issues(id, repo, number, title, body, labels, url, state, updated_at, harvested_at, last_seen_at) values ('I1','o/r',1,'T','B','[\"bug\"]','u',?, '2026-10-01T00:00:00Z','x','x')", (state,))
    for q, c in [("category", "quant_trading"), ("kind", "bug"), ("difficulty", "small"), ("worth_a_look", "yes")]:
        conn.execute("insert into classifications(issue_id, question, choice, confidence, probabilities, model, asked_at) values ('I1',?,?,0.9,'{}','s','x')", (q, c))
    conn.execute("insert into scores(issue_id, total, breakdown, weights_hash, scored_at) values ('I1', 9.5, '{\"recognition\": 2.0}', 'h', 'x')")
    conn.execute("insert into agent_runs(issue_id, model, status, started_at, brief) values ('I1','m','done','2026-10-01T00:00:00Z','{\"summary\":\"S\",\"approach\":\"A\",\"files\":[]}')")
    rid = conn.execute("select id from agent_runs").fetchone()[0]
    for i in range(3):
        conn.execute("insert into agent_events(run_id, seq, at, event) values (?,?,?,?)", (rid, i + 1, "x", json.dumps({"event": "step_update", "n": i})))
    conn.commit(); conn.close()
    return rid

class OK:
    stdout = json.dumps({"conversation_id": "c-1", "status": "SUCCESS", "response": "Reply", "usage": {}}); returncode = 0; stderr = ""

def _client(tmp_path, state="analyzed", chat_runner=None, web_dist=None):
    cfg = Config(data_dir=tmp_path)
    rid = _seed(cfg, state)
    app = create_app(cfg, chat_runner=chat_runner or (lambda cmd, **kw: OK()), web_dist=web_dist or tmp_path / "nodist")
    return TestClient(app), cfg, rid

def test_status_and_queue(tmp_path):
    c, cfg, _ = _client(tmp_path)
    assert c.get("/api/status").json()["issues"]["analyzed"] == 1
    rows = c.get("/api/queue", params={"category": "quant_trading"}).json()
    assert rows[0]["number"] == 1 and rows[0]["breakdown"]["recognition"] == 2.0
    assert c.get("/api/queue", params={"category": "other"}).json() == []

def test_issue_detail_has_everything(tmp_path):
    c, _, rid = _client(tmp_path)
    d = c.get("/api/issues/I1").json()
    assert d["issue"]["title"] == "T" and d["repo"]["stars"] == 500
    assert d["classification"]["category"]["choice"] == "quant_trading"
    assert d["score"]["total"] == 9.5 and d["brief"]["summary"] == "S"
    assert d["runs"][0]["id"] == rid and d["thread"] is None and d["messages"] == []
    assert c.get("/api/issues/nope").status_code == 404

def test_discuss_requires_analyzed_then_chat_and_approve(tmp_path):
    c, cfg, _ = _client(tmp_path, state="harvested")
    assert c.post("/api/issues/I1/discuss").status_code == 409  # not even classified yet
    conn = db.connect(cfg.db_path); conn.execute("update issues set state='analyzed'"); conn.commit(); conn.close()
    assert c.post("/api/issues/I1/discuss").json()["state"] == "discussing"
    r = c.post("/api/issues/I1/messages", json={"content": "hi"})
    assert r.status_code == 200 and r.json()["assistant"]["content"] == "Reply"
    assert len(c.get("/api/issues/I1/messages").json()) == 2
    r = c.post("/api/issues/I1/approve", json={"plan": "Do X"})
    assert r.json()["state"] == "plan_approved"
    assert c.get("/api/issues/I1").json()["thread"]["plan"] == "Do X"

def test_dismiss_and_unknown(tmp_path):
    c, _, _ = _client(tmp_path)
    assert c.post("/api/issues/I1/dismiss", json={"note": "meh"}).json()["state"] == "dismissed"
    assert c.post("/api/issues/nope/dismiss", json={}).status_code == 404

def test_chat_failure_is_502_and_keeps_user_message(tmp_path):
    class Bad:
        stdout = ""; returncode = 1; stderr = "boom"
    c, _, _ = _client(tmp_path, chat_runner=lambda cmd, **kw: Bad())
    c.post("/api/issues/I1/discuss")
    assert c.post("/api/issues/I1/messages", json={"content": "hi"}).status_code == 502
    assert [m["role"] for m in c.get("/api/issues/I1/messages").json()] == ["user"]

def test_runs_and_events_polling(tmp_path):
    c, _, rid = _client(tmp_path)
    assert c.get("/api/runs").json()[0]["id"] == rid
    ev = c.get(f"/api/runs/{rid}/events", params={"after": 1}).json()
    assert [e["seq"] for e in ev["events"]] == [2, 3] and ev["status"] == "done"
    assert c.get(f"/api/runs/{rid}/events", params={"after": 3}).json() == {"status": "done", "events": []}
    assert c.get("/api/runs/999/events").status_code == 404

def test_spa_fallback_when_dist_missing(tmp_path):
    c, _, _ = _client(tmp_path)
    r = c.get("/issues/I1")
    assert r.status_code == 200 and "web/dist" in r.text

def test_spa_serves_index_for_any_route_when_dist_exists(tmp_path):
    dist = tmp_path / "dist"; (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html><div id=root></div></html>"); (dist / "assets" / "a.js").write_text("1")
    c, _, _ = _client(tmp_path, web_dist=dist)
    assert "id=root" in c.get("/issues/I1").text and c.get("/assets/a.js").text == "1"
    assert c.get("/api/status").status_code == 200  # api routes are not shadowed

def test_settings_get_put_validates_and_rescores(tmp_path):
    cfg = Config(data_dir=tmp_path); _seed(cfg, state="shortlisted")  # the scorer only re-ranks classified/shortlisted
    yaml_path = tmp_path / "scout.yaml"; yaml_path.write_text("")
    c = TestClient(create_app(cfg, chat_runner=lambda cmd, **kw: OK(), config_path=yaml_path))
    assert c.get("/api/settings").json()["weights"]["bounty"] == 0.5
    assert c.put("/api/settings", json={"weights": {"nope": 1}}).status_code == 422
    assert c.put("/api/settings", json={"weights": {"bounty": "x"}}).status_code == 422
    assert yaml_path.read_text() == ""
    r = c.put("/api/settings", json={"weights": {"bounty": 2.0}, "category_values": {"other": 0.1}})
    assert r.json()["weights"]["bounty"] == 2.0 and "bounty: 2.0" in yaml_path.read_text()
    conn = db.connect(cfg.db_path)
    assert conn.execute("select count(*) from scores where issue_id='I1'").fetchone()[0] == 2

def test_prep_launch_submit_endpoints(tmp_path, monkeypatch):
    from oss_scout.agents import draft as draft_mod
    cfg = Config(data_dir=tmp_path); _seed(cfg, state="plan_approved")
    calls = []
    def shell(cmd, cwd=None, timeout=600):
        calls.append(cmd)
        if cmd[:3] == ["gh", "repo", "view"]: return 0, json.dumps({"defaultBranchRef": {"name": "main"}}), ""
        if cmd[:2] == ["git", "status"]: return 0, "", ""
        if cmd[:2] == ["git", "rev-list"]: return 0, "1\n", ""
        if cmd[:2] == ["bash", "-lc"]: return 1, "FAILED test_y", ""
        if cmd[:2] == ["git", "worktree"]:
            from pathlib import Path; Path(cmd[3]).mkdir(parents=True, exist_ok=True)
        if cmd[:2] == ["git", "clone"]:
            from pathlib import Path; (Path(cmd[-1]) / ".git").mkdir(parents=True, exist_ok=True)
        return 0, "", ""
    spawned = []
    c = TestClient(create_app(cfg, chat_runner=lambda cmd, **kw: OK(), web_dist=tmp_path / "nodist", shell=shell,
                              popen=lambda cmd, **kw: spawned.append(cmd), github_user="ryan"))
    assert c.post("/api/issues/nope/prep").status_code == 404
    r = c.post("/api/issues/I1/prep")
    assert r.status_code == 200 and r.json()["branch"].startswith("scout/1-")
    assert c.post("/api/issues/I1/prep").status_code == 409  # now drafting, not plan_approved
    d = c.get("/api/issues/I1").json()
    assert d["draft"]["branch"] == r.json()["branch"] and d["issue"]["state"] == "drafting"
    r = c.post("/api/issues/I1/launch")
    assert r.status_code == 200 and "claude" in r.json()["command"] and (spawned or r.json()["launched"] is False)
    r = c.post("/api/issues/I1/submit", json={"title": "t", "body": "b"})
    assert r.status_code == 409 and "FAILED test_y" in r.json()["detail"]
    assert c.get("/api/issues/I1").json()["issue"]["state"] == "discussing"

def test_queue_lane_param_and_status_extras(tmp_path):
    cfg = Config(data_dir=tmp_path); _seed(cfg, state="shortlisted")
    conn = db.connect(cfg.db_path); conn.execute("update issues set lane='discover'"); conn.commit(); conn.close()
    (tmp_path / "scout.log").write_text("2026-10-01 09:46:07,000 INFO harvest HarvestSummary(queries=1)\n2026-10-01 09:47:00,000 INFO score ScoreSummary(scored=3, shortlisted=1, demoted=0)\n")
    def timers(cmd, **kw):
        class R: stdout = "NEXT                        LEFT LAST PASSED UNIT ACTIVATES\nThu 2026-10-01 10:46:07 PDT 40min Thu 2026-10-01 09:46:07 PDT 19min ago oss-scout-run.timer oss-scout-run.service\n"; returncode = 0
        return R()
    c = TestClient(create_app(cfg, chat_runner=lambda cmd, **kw: OK(), web_dist=tmp_path / "nodist", sysrun=timers))
    assert c.get("/api/queue", params={"lane": "resume"}).json() == []
    assert c.get("/api/queue", params={"lane": "discover"}).json()[0]["lane"] == "discover"
    st = c.get("/api/status").json()
    assert st["labels"]["shortlisted"] == "top picks" and st["lanes"] == {"discover": 1}
    assert st["last_run"] == "2026-10-01 09:47:00" and st["next_run"].startswith("Thu 2026-10-01 10:46")
    assert st["board_order"][0] == "Picked"

def test_analyze_endpoint_runs_in_background_and_returns_run_id(tmp_path):
    cfg = Config(data_dir=tmp_path); _seed(cfg, state="shortlisted")
    started = []
    def fake_run_one(conn, cfg_, gh, issue_id, **kw):
        started.append(issue_id)
        with conn:
            cur = conn.execute("insert into agent_runs(issue_id, model, status, started_at) values (?, 'm', 'running', 'x')", (issue_id,))
        return cur.lastrowid
    c = TestClient(create_app(cfg, chat_runner=lambda cmd, **kw: OK(), web_dist=tmp_path / "nodist", analyze_fn=fake_run_one, spawn=lambda fn: fn()))
    r = c.post("/api/issues/I1/analyze")
    assert r.status_code == 200 and r.json()["run_id"] > 0 and started == ["I1"]
    assert c.post("/api/issues/nope/analyze").status_code == 404
    conn = db.connect(cfg.db_path); conn.execute("update issues set state='dismissed'"); conn.commit(); conn.close()
    assert c.post("/api/issues/I1/analyze").status_code == 409

def test_board_and_move(tmp_path):
    cfg = Config(data_dir=tmp_path); _seed(cfg, state="analyzed")
    c = TestClient(create_app(cfg, chat_runner=lambda cmd, **kw: OK(), web_dist=tmp_path / "nodist"))
    b = c.get("/api/board").json()
    assert [col["name"] for col in b["columns"]] == ["Picked", "Working on", "PR opened", "Progressing", "Accepted"]
    assert b["columns"][0]["cards"][0]["id"] == "I1" and b["columns"][0]["cards"][0]["label"] == "brief ready"
    assert c.post("/api/issues/I1/move", json={"column": "Working on"}).json()["state"] == "discussing"
    assert c.post("/api/issues/I1/move", json={"column": "PR opened"}).status_code == 422  # needs pr_url when no draft
    r = c.post("/api/issues/I1/move", json={"column": "PR opened", "pr_url": "https://github.com/o/r/pull/9"})
    assert r.json()["state"] == "pr_open" and c.get("/api/issues/I1").json()["draft"]["pr_url"].endswith("/pull/9")
    assert c.post("/api/issues/I1/move", json={"column": "Progressing"}).json()["state"] == "in_review"
    assert c.post("/api/issues/I1/move", json={"column": "Accepted"}).json()["state"] == "merged"
    assert c.post("/api/issues/I1/move", json={"column": "Picked"}).status_code == 409
    assert c.post("/api/issues/I1/move", json={"column": "Nope"}).status_code == 422
    assert c.get("/api/board").json()["columns"][4]["cards"][0]["id"] == "I1"

def test_queue_api_passes_sort_difficulty_all_and_offset(tmp_path):
    cfg = Config(data_dir=tmp_path); _seed(cfg, state="classified")
    c = TestClient(create_app(cfg, chat_runner=lambda cmd, **kw: OK(), web_dist=tmp_path / "nodist"))
    assert c.get("/api/queue").json() == []
    assert c.get("/api/queue", params={"all": "true", "sort": "easiest", "difficulty": "small"}).json()[0]["id"] == "I1"
    assert c.get("/api/queue", params={"all": "true", "difficulty": "large"}).json() == []
    assert c.get("/api/queue", params={"all": "true", "offset": 1}).json() == []
