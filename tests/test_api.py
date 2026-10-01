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

def _client(tmp_path, state="analyzed", chat_runner=None):
    cfg = Config(data_dir=tmp_path)
    rid = _seed(cfg, state)
    app = create_app(cfg, chat_runner=chat_runner or (lambda cmd, **kw: OK()))
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
    c, cfg, _ = _client(tmp_path, state="shortlisted")
    assert c.post("/api/issues/I1/discuss").status_code == 409
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
