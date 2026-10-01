from datetime import datetime, timezone
import pytest
from oss_scout import db, score
from oss_scout.config import Config

NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)
CLS = {"category": {"choice": "quant_trading"}, "kind": {"choice": "bug"}, "difficulty": {"choice": "small"}, "worth_a_look": {"choice": "yes"}}

def _issue(**kw):
    base = {"updated_at": "2026-10-01T00:00:00Z", "has_bounty": 0}
    base.update(kw); return base

def _repo(**kw):
    base = {"stars": 10000, "org_known": 1, "external_merge_rate": 0.5, "median_first_response_hours": 12.0, "language": "Python", "ai_policy": "none"}
    base.update(kw); return base

def test_ideal_issue_scores_near_max():
    cfg = Config()
    total, br = score.compute(_issue(has_bounty=1), _repo(stars=100000, external_merge_rate=1.0, median_first_response_hours=1.0), CLS, cfg, NOW)
    assert total == pytest.approx(score.MAX_TOTAL(cfg), abs=0.3)
    assert set(br) == set(cfg.weights)

def test_terms_are_bounded_and_none_signals_count_zero():
    cfg = Config()
    total, br = score.compute(_issue(), _repo(external_merge_rate=None, median_first_response_hours=None, org_known=0, stars=200), CLS, cfg, NOW)
    assert br["mergeability"] == 0.0
    for term, w in cfg.weights.items():
        assert -abs(w) <= br[term] <= abs(w)

def test_language_and_category_and_difficulty_values():
    cfg = Config()
    _, py = score.compute(_issue(), _repo(language="Python"), CLS, cfg, NOW)
    _, ts = score.compute(_issue(), _repo(language="TypeScript"), CLS, cfg, NOW)
    _, rs = score.compute(_issue(), _repo(language="Rust"), CLS, cfg, NOW)
    assert py["language_fit"] > ts["language_fit"] > rs["language_fit"]
    _, other = score.compute(_issue(), _repo(), {**CLS, "category": {"choice": "other"}}, cfg, NOW)
    assert other["category"] < py["category"]
    _, large = score.compute(_issue(), _repo(), {**CLS, "difficulty": {"choice": "large"}}, cfg, NOW)
    assert large["difficulty_fit"] < py["difficulty_fit"]
    _, missing = score.compute(_issue(), _repo(), {}, cfg, NOW)  # no classification rows at all
    assert 0 <= missing["category"] and 0 <= missing["difficulty_fit"]

def test_freshness_decays_and_ai_policy_penalizes():
    cfg = Config()
    _, fresh = score.compute(_issue(updated_at="2026-10-01T00:00:00Z"), _repo(), CLS, cfg, NOW)
    _, old = score.compute(_issue(updated_at="2026-09-01T00:00:00Z"), _repo(), CLS, cfg, NOW)
    assert fresh["freshness"] > old["freshness"] >= 0
    _, disc = score.compute(_issue(), _repo(ai_policy="discouraged"), CLS, cfg, NOW)
    assert disc["ai_policy"] == pytest.approx(-0.5 * cfg.weights["ai_policy"])

def test_weights_hash_changes_with_weights():
    assert score.weights_hash(Config()) != score.weights_hash(Config(weights={**Config().weights, "bounty": 9}))

def _seed(conn, issue_id, stars, category="quant_trading", state="classified"):
    repo = f"o/{issue_id}"
    conn.execute("insert into repos(full_name, stars, language, org_known, external_merge_rate, median_first_response_hours, ai_policy) values (?,?,?,?,?,?,?)",
                 (repo, stars, "Python", 0, 0.5, 10.0, "none"))
    conn.execute("insert into issues(id, repo, number, title, url, state, updated_at, harvested_at, last_seen_at) values (?,?,1,'t','u',?, '2026-10-01T00:00:00Z','x','x')", (issue_id, repo, state))
    for q, c in [("category", category), ("kind", "bug"), ("difficulty", "small"), ("worth_a_look", "yes")]:
        conn.execute("insert into classifications(issue_id, question, choice, confidence, probabilities, model, asked_at) values (?,?,?,0.9,'{}','stub','x')", (issue_id, q, c))
    conn.commit()

def test_run_scoring_shortlists_top_n_above_floor_and_demotes(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    _seed(conn, "A", 50000); _seed(conn, "B", 300); _seed(conn, "C", 250, category="other")
    cfg = Config(shortlist_count=2, shortlist_floor=4.0)
    s = score.run_scoring(conn, cfg, NOW)
    assert s.scored == 3
    states = {r["id"]: r["state"] for r in conn.execute("select id, state from issues")}
    assert states["A"] == "shortlisted" and states["B"] == "shortlisted" and states["C"] == "classified"
    s2 = score.run_scoring(conn, Config(shortlist_count=1, shortlist_floor=4.0), NOW)
    states = {r["id"]: r["state"] for r in conn.execute("select id, state from issues")}
    assert states["A"] == "shortlisted" and states["B"] == "classified" and s2.demoted == 1
    assert conn.execute("select count(*) from scores where issue_id='A'").fetchone()[0] == 2  # history kept

def test_run_scoring_respects_floor(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    _seed(conn, "A", 50000)
    s = score.run_scoring(conn, Config(shortlist_count=5, shortlist_floor=99.0), NOW)
    assert s.shortlisted == 0
    assert conn.execute("select state from issues where id='A'").fetchone()[0] == "classified"

def test_run_scoring_ignores_dismissed(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    _seed(conn, "A", 50000, state="dismissed")
    s = score.run_scoring(conn, Config(), NOW)
    assert s.scored == 0
