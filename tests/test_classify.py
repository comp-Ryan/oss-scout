import json
from pathlib import Path
import pytest
from oss_scout import db, classify, transitions
from oss_scout.config import Config

FIX = json.loads((Path(__file__).parent / "fixtures" / "jev_pandas_response.json").read_text())

def test_questions_have_all_categories_and_four_names():
    assert set(classify.QUESTIONS) == {"category", "kind", "difficulty", "worth_a_look"}
    assert len(classify.QUESTIONS["category"]["criteria"]) == 17

def test_issue_state_text_truncates_body_and_handles_none():
    issue = {"repo": "o/r", "title": "T", "labels": "[\"bug\"]", "body": None, "comments": 3}
    repo = {"stars": 10, "language": "Python", "description": None}
    t = classify.issue_state_text(issue, repo)
    assert "o/r" in t and "T" in t and "bug" in t and "None" not in t
    long = classify.issue_state_text({**issue, "body": "x" * 5000}, repo)
    assert len(long) < 2000

def test_parse_answers_recorded_response():
    out = classify.parse_answers(FIX, classify.QUESTIONS)
    assert out["category"]["choice"] == "data_scraping"
    assert out["difficulty"]["confidence"] == pytest.approx(0.66)
    assert out["worth_a_look"]["probabilities"]["yes"] == pytest.approx(0.97)

def test_parse_answers_rejects_out_of_criteria_choice():
    bad = {"answers": {"kind": {"choice": "refactor", "confidence": 0.9}}}
    out = classify.parse_answers(bad, {"kind": classify.QUESTIONS["kind"]})
    assert out["kind"]["choice"] is None

class StubClassifier:
    model = "stub"
    def __init__(self, answers): self.answers = answers; self.calls = 0
    def classify(self, state_text, questions):
        self.calls += 1
        return {q: self.answers[q] for q in questions}

def _seed(conn, issue_id="I1", state="harvested"):
    conn.execute("insert or ignore into repos(full_name, stars, language) values ('o/r', 500, 'Python')")
    conn.execute("insert into issues(id, repo, number, title, body, url, state, harvested_at, last_seen_at) values (?,'o/r',1,'t','b','u',?,'x','x')", (issue_id, state))
    conn.commit()

GOOD = {"category": {"choice": "quant_trading", "confidence": 0.9, "probabilities": {}},
        "kind": {"choice": "bug", "confidence": 0.9, "probabilities": {}},
        "difficulty": {"choice": "small", "confidence": 0.8, "probabilities": {}},
        "worth_a_look": {"choice": "yes", "confidence": 0.9, "probabilities": {}}}

def test_run_classify_stores_rows_and_advances(tmp_path):
    conn = db.connect(tmp_path / "t.db"); _seed(conn)
    s = classify.run_classify(conn, Config(), StubClassifier(GOOD))
    assert s.classified == 1
    assert conn.execute("select state from issues where id='I1'").fetchone()[0] == "classified"
    assert conn.execute("select count(*) from classifications where issue_id='I1'").fetchone()[0] == 4
    assert classify.latest(conn, "I1")["category"]["choice"] == "quant_trading"

def test_confident_no_is_dismissed_by_classifier(tmp_path):
    conn = db.connect(tmp_path / "t.db"); _seed(conn)
    ans = {**GOOD, "worth_a_look": {"choice": "no", "confidence": 0.95, "probabilities": {}}}
    s = classify.run_classify(conn, Config(dismiss_confidence=0.9), StubClassifier(ans))
    assert s.dismissed == 1
    assert conn.execute("select state from issues where id='I1'").fetchone()[0] == "dismissed"
    assert conn.execute("select actor from transitions where to_state='dismissed'").fetchone()[0] == "classifier"

def test_unparseable_choice_leaves_issue_harvested(tmp_path):
    conn = db.connect(tmp_path / "t.db"); _seed(conn)
    ans = {**GOOD, "kind": {"choice": None, "confidence": None, "probabilities": {}}}
    s = classify.run_classify(conn, Config(), StubClassifier(ans))
    assert s.failed == 1
    assert conn.execute("select state from issues where id='I1'").fetchone()[0] == "harvested"

def test_classifier_exception_is_counted_and_issue_stays(tmp_path):
    conn = db.connect(tmp_path / "t.db"); _seed(conn)
    class Boom:
        model = "boom"
        def classify(self, t, q): raise RuntimeError("down")
    s = classify.run_classify(conn, Config(), Boom())
    assert s.failed == 1 and "down" in s.errors[0]
    assert conn.execute("select state from issues where id='I1'").fetchone()[0] == "harvested"

def test_dropped_questions_go_to_fallback(tmp_path):
    conn = db.connect(tmp_path / "t.db"); _seed(conn)
    primary = StubClassifier(GOOD); fallback = StubClassifier({"difficulty": {"choice": "large", "confidence": 0.7, "probabilities": {}}})
    fallback.model = "gemini"
    classify.run_classify(conn, Config(dropped_questions=["difficulty"]), primary, fallback)
    rows = conn.execute("select question, model, choice from classifications order by question").fetchall()
    assert ("difficulty", "gemini", "large") in [tuple(r) for r in rows]
    assert fallback.calls == 1
    assert classify.latest(conn, "I1")["difficulty"]["choice"] == "large"

def test_gemini_classifier_builds_agy_command_and_parses_real_json_shape(tmp_path):
    captured = {}
    # Recorded 2026-10-01 from `agy --output-format json --json-schema ... --print ...`
    real = {"conversation_id": "a18", "status": "SUCCESS", "response": "docs\n{\"kind\":\"docs\"}\n",
            "structured_output": {"kind": "docs"}, "usage": {"input_tokens": 27194}}
    class R: returncode = 0; stdout = json.dumps(real); stderr = ""
    def runner(cmd, **kw): captured["cmd"] = cmd; return R()
    g = classify.GeminiClassifier("gemini-3.8-flash-medium", runner=runner)
    out = g.classify("state", {"kind": classify.QUESTIONS["kind"]})
    assert out["kind"]["choice"] == "docs" and out["kind"]["confidence"] is None
    cmd = captured["cmd"]
    assert cmd[0] == "agy" and "--json-schema" in cmd
    # agy's --print consumes the next argument as the prompt, so the prompt must follow it directly
    assert cmd[cmd.index("--print") + 1].startswith("Classify this GitHub issue")
    assert cmd[cmd.index("--model") + 1] == "gemini-3.8-flash-medium"

def test_category_set_covers_ryans_interests():
    cats = set(classify.CATEGORIES)
    for c in ("backend_api", "iot_embedded", "devops_cloud", "security", "logic_formal_methods", "philosophy_religion",
              "ml_data_science", "math_competitive", "science_simulation", "knowledge_reading", "other"):
        assert c in cats
    assert len(classify.QUESTIONS["category"]["criteria"]) == len(cats)

def test_run_reclassify_asks_one_question_for_classified_issues(tmp_path):
    conn = db.connect(tmp_path / "t.db"); _seed(conn, "I1", state="shortlisted"); _seed(conn, "I2", state="harvested")
    conn.execute("insert into classifications(issue_id, question, choice, confidence, probabilities, model, asked_at) values ('I1','category','other',0.5,'{}','old','x')"); conn.commit()
    clf = StubClassifier({"category": {"choice": "iot_embedded", "confidence": 0.9, "probabilities": {}}})
    n = classify.run_reclassify(conn, Config(), clf, "category")
    assert n == 1 and clf.calls == 1  # the harvested one is left for the normal pass
    assert classify.latest(conn, "I1")["category"]["choice"] == "iot_embedded"


def _text(title, body="", labels=(), desc="", stars=500):
    issue = {"repo": "o/r", "title": title, "labels": json.dumps(list(labels)), "body": body, "comments": 1}
    return classify.issue_state_text(issue, {"stars": stars, "language": "Python", "description": desc})

def test_keyword_classifier_answers_every_question_with_allowed_values():
    out = classify.KeywordClassifier().classify(_text("Crash when parsing CSV", labels=["bug"]), classify.QUESTIONS)
    for q, spec in classify.QUESTIONS.items():
        assert out[q]["choice"] in spec["criteria"]
        assert out[q]["confidence"] is None  # never confident enough to auto-dismiss

def test_keyword_classifier_reads_labels_and_description():
    k = classify.KeywordClassifier()
    out = k.classify(_text("Typo in README", labels=["documentation", "good first issue"], desc="An LLM agent framework"),
                     classify.QUESTIONS)
    assert out["kind"]["choice"] == "docs"
    assert out["difficulty"]["choice"] == "small"
    assert out["category"]["choice"] == "ai_agents_llm"
    assert k.model == "keywords"

def test_keyword_classifier_falls_back_to_other_and_feature():
    out = classify.KeywordClassifier().classify(_text("Something", desc="zzz"), classify.QUESTIONS)
    assert out["category"]["choice"] == "other" and out["kind"]["choice"] == "feature"

def test_pick_classifier_order(monkeypatch):
    from oss_scout.config import Config
    cfg = Config()
    monkeypatch.delenv("jev_model_api_key", raising=False)
    monkeypatch.setattr(classify.shutil, "which", lambda name: None)
    assert isinstance(classify.pick_primary(cfg), classify.KeywordClassifier)
    monkeypatch.setattr(classify.shutil, "which", lambda name: "/usr/bin/agy")
    assert isinstance(classify.pick_primary(cfg), classify.GeminiClassifier)
    monkeypatch.setenv("jev_model_api_key", "k")
    assert isinstance(classify.pick_primary(cfg), classify.JevClassifier)
    cfg.classifier = "keywords"
    assert isinstance(classify.pick_primary(cfg), classify.KeywordClassifier)
