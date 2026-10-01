import re
from oss_scout import validate, classify

def _idx(text):
    return int(re.search(r"Issue: t(\d+)", text).group(1))

def _labeled(n, kind_label):
    return [{"issue": {"repo": "o/r", "title": f"t{i}", "labels": "[]", "body": "b", "comments": 0},
             "repo": {"stars": 1, "language": "Python", "description": ""},
             "labels": {"kind": kind_label(i)}} for i in range(n)]

class Fixed:
    model = "fixed"
    def __init__(self, fn): self.fn = fn
    def classify(self, text, questions):
        return {q: self.fn(text, q) for q in questions}

def test_keeps_question_with_good_agreement_and_spread():
    data = _labeled(10, lambda i: "bug" if i % 2 else "docs")
    clf = Fixed(lambda text, q: {"choice": "bug" if _idx(text) % 2 else "docs", "confidence": 0.9, "probabilities": {}})
    rep = validate.evaluate(data, clf, {"kind": classify.QUESTIONS["kind"]})
    qr = rep.per_question["kind"]
    assert qr.agreement == 1.0 and qr.decision == "keep" and qr.n == 10

def test_drops_near_constant_question():
    data = _labeled(10, lambda i: "bug" if i % 2 else "docs")
    clf = Fixed(lambda text, q: {"choice": "bug", "confidence": 0.5, "probabilities": {}})
    rep = validate.evaluate(data, clf, {"kind": classify.QUESTIONS["kind"]})
    qr = rep.per_question["kind"]
    assert qr.decision == "drop" and "constant" in qr.reason
    assert rep.dropped() == ["kind"]

def test_drops_low_agreement_question():
    data = _labeled(10, lambda i: "bug" if i % 2 else "docs")
    clf = Fixed(lambda text, q: {"choice": "feature" if _idx(text) % 3 else "bug", "confidence": 0.5, "probabilities": {}})
    rep = validate.evaluate(data, clf, {"kind": classify.QUESTIONS["kind"]})
    assert rep.per_question["kind"].decision == "drop"
    assert "agreement" in rep.per_question["kind"].reason

def test_render_mentions_each_question_and_decision():
    data = _labeled(4, lambda i: "bug")
    clf = Fixed(lambda text, q: {"choice": "bug", "confidence": 0.9, "probabilities": {}})
    rep = validate.evaluate(data, clf, {"kind": classify.QUESTIONS["kind"]})
    out = rep.render()
    assert "kind" in out and ("keep" in out.lower() or "drop" in out.lower())

def test_unlabeled_items_are_skipped_per_question():
    data = _labeled(4, lambda i: "bug")
    data[0]["labels"]["kind"] = None
    clf = Fixed(lambda text, q: {"choice": "bug", "confidence": 0.9, "probabilities": {}})
    rep = validate.evaluate(data, clf, {"kind": classify.QUESTIONS["kind"]})
    assert rep.per_question["kind"].n == 3
