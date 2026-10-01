from __future__ import annotations
import json
import statistics
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from oss_scout import classify

CONSTANT_SHARE = 0.85
MIN_AGREEMENT = 0.60


@dataclass
class QuestionReport:
    n: int
    agreement: float
    distribution: dict[str, float]
    confidence_std: float | None
    decision: str
    reason: str


@dataclass
class Report:
    per_question: dict[str, QuestionReport] = field(default_factory=dict)
    model: str = ""

    def dropped(self) -> list[str]:
        return [q for q, r in self.per_question.items() if r.decision == "drop"]

    def render(self) -> str:
        lines = [f"Classifier validation ({self.model})", ""]
        for q, r in self.per_question.items():
            dist = ", ".join(f"{k} {v:.0%}" for k, v in sorted(r.distribution.items(), key=lambda kv: -kv[1]))
            std = f"{r.confidence_std:.2f}" if r.confidence_std is not None else "n/a"
            lines.append(f"{q}: n={r.n} agreement={r.agreement:.0%} confidence_std={std} -> {r.decision.upper()} ({r.reason})")
            lines.append(f"  distribution: {dist}")
        d = self.dropped()
        lines.append("")
        lines.append(f"dropped_questions: {d}  (put this list in scout.yaml)" if d else "No questions dropped.")
        return "\n".join(lines)


def load_labeled(path: Path) -> list[dict]:
    return json.loads(Path(path).read_text())


def evaluate(labeled: list[dict], classifier, questions: dict | None = None) -> Report:
    questions = questions or classify.QUESTIONS
    rep = Report(model=getattr(classifier, "model", "?"))
    answers = [classifier.classify(classify.issue_state_text(item["issue"], item["repo"]), questions) for item in labeled]
    for q in questions:
        pairs = [(a[q]["choice"], item["labels"].get(q)) for a, item in zip(answers, labeled) if item["labels"].get(q) is not None]
        n = len(pairs)
        if n == 0:
            rep.per_question[q] = QuestionReport(0, 0.0, {}, None, "drop", "no labels")
            continue
        agreement = sum(1 for c, l in pairs if c == l) / n
        counts = Counter(c for c, _ in pairs)
        distribution = {str(k): v / n for k, v in counts.items()}
        confs = [a[q]["confidence"] for a in answers if a[q]["confidence"] is not None]
        std = statistics.pstdev(confs) if len(confs) > 1 else None
        top_share = max(distribution.values())
        if top_share > CONSTANT_SHARE:
            decision, reason = "drop", f"near-constant: top choice {top_share:.0%} > {CONSTANT_SHARE:.0%}"
        elif agreement < MIN_AGREEMENT:
            decision, reason = "drop", f"agreement {agreement:.0%} < {MIN_AGREEMENT:.0%}"
        else:
            decision, reason = "keep", "passes both rules"
        rep.per_question[q] = QuestionReport(n, agreement, distribution, std, decision, reason)
    return rep
