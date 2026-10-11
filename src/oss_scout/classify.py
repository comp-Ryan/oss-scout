from __future__ import annotations
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import Protocol
from oss_scout import transitions
from oss_scout.config import Config
from oss_scout.db import utcnow
from oss_scout.jev_client import JevClient

CATEGORIES = {
    "quant_trading": "Backtesting, trading bots, market data, technical analysis, portfolio tools",
    "prediction_markets_finance": "Prediction markets, betting exchanges, broker or finance APIs, payments",
    "ai_agents_llm": "LLM agents, MCP servers, prompt tooling, model evals, inference serving",
    "ml_data_science": "Machine learning and deep learning frameworks, training, datasets, notebooks, scientific Python",
    "data_scraping": "Data processing libraries, dataframes, scrapers, ETL",
    "backend_api": "Web backends, REST or GraphQL APIs, ORMs, databases, auth, message queues",
    "web_frontend": "React, UI components, web frontends, design systems",
    "dev_tools_cli": "Developer tools, CLIs, build systems, linters, editors, package managers",
    "devops_cloud": "CI/CD, containers, Kubernetes, infrastructure as code, cloud SDKs, observability",
    "security": "Security tooling, scanners, cryptography libraries, secrets management, hardening",
    "iot_embedded": "Microcontrollers, firmware, home automation, robotics, hardware, sensors, 3D printing",
    "logic_formal_methods": "Proof assistants, theorem provers, SAT/SMT solvers, type theory, formal verification, logic teaching tools",
    "philosophy_religion": "Philosophy, theology, scripture and classical text tools, digital humanities, Bible or Aquinas apps, comparative religion data",
    "math_competitive": "Mathematics libraries, computer algebra, competitive programming judges, problem sets, puzzle solvers",
    "science_simulation": "Physics, astronomy, cosmology, chemistry, biology simulations and scientific instruments",
    "knowledge_reading": "Note taking, personal knowledge management, reading and e-book tools, Obsidian or Zotero plugins, spaced repetition",
    "other": "None of the above",
}
QUESTIONS = {
    "category": {"type": "choice", "instructions": "Which domain does this repository and issue belong to?", "criteria": CATEGORIES},
    "kind": {"type": "choice", "instructions": "What kind of work does this issue ask for?", "criteria": {
        "bug": "Fix incorrect behavior", "feature": "Add new behavior", "docs": "Documentation only",
        "test": "Tests only", "chore": "Maintenance, dependencies, CI"}},
    "difficulty": {"type": "choice", "instructions": "How hard is this for an outside contributor who knows the language but not the codebase?", "criteria": {
        "small": "One file, under two hours", "medium": "A few files, about a day", "large": "Design work or multiple days"}},
    "worth_a_look": {"type": "choice", "instructions": "Could a solo outside contributor plausibly get a PR for this merged?", "criteria": {
        "yes": "Clear ask, maintainers engaged, scoped work",
        "no": "Vague, contentious, needs maintainer design decisions, or likely already handled"}},
}
BODY_CHARS = 1500


def issue_state_text(issue: dict, repo: dict) -> str:
    labels = issue.get("labels") or "[]"
    if isinstance(labels, str):
        labels = json.loads(labels)
    body = (issue.get("body") or "").strip().replace("\r", "")[:BODY_CHARS]
    desc = repo.get("description") or ""
    return (
        f"Repository {issue['repo']} ({repo.get('stars') or 0} stars, {repo.get('language') or 'unknown language'}): {desc}\n"
        f"Issue: {issue['title']}\nLabels: {', '.join(labels) or 'none'}\nComments: {issue.get('comments') or 0}\n"
        f"Body: {body}"
    )


def parse_answers(response: dict, questions: dict) -> dict[str, dict]:
    out = {}
    answers = response.get("answers") or {}
    for q, spec in questions.items():
        a = answers.get(q) or {}
        choice = a.get("choice")
        if choice not in spec["criteria"]:
            choice = None
        out[q] = {"choice": choice, "confidence": a.get("confidence") if choice else None,
                  "probabilities": a.get("probabilities") or {}}
    return out


class Classifier(Protocol):
    model: str
    def classify(self, state_text: str, questions: dict) -> dict[str, dict]: ...


class JevClassifier:
    def __init__(self, client: JevClient):
        self.client = client
        self.model = f"jev:{client.model}"

    def classify(self, state_text: str, questions: dict) -> dict[str, dict]:
        return parse_answers(self.client.ask(state_text, questions), questions)


class GeminiClassifier:
    """Fallback for questions Jev answers badly: one `agy --print` call with a JSON schema."""

    def __init__(self, model: str, runner=subprocess.run, timeout: float = 120.0):
        self.model = f"gemini:{model}"
        self._model = model
        self.runner = runner
        self.timeout = timeout

    def classify(self, state_text: str, questions: dict) -> dict[str, dict]:
        schema = {"type": "object", "additionalProperties": False, "required": list(questions),
                  "properties": {q: {"type": "string", "enum": list(spec["criteria"])} for q, spec in questions.items()}}
        prompt = "Classify this GitHub issue. Answer each question with exactly one allowed value.\n\n"
        for q, spec in questions.items():
            prompt += f"{q}: {spec['instructions']}\n" + "".join(f"  - {k}: {v}\n" for k, v in spec["criteria"].items())
        prompt += f"\nISSUE:\n{state_text}"
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(schema, f)
            schema_path = f.name
        # agy's --print takes the prompt as its own value, so it goes last with the prompt right after it.
        cmd = ["agy", "--model", self._model, "--output-format", "json", "--json-schema", schema_path,
               "--disable-slash-commands", "--print", prompt]
        r = self.runner(cmd, capture_output=True, text=True, timeout=self.timeout)
        if r.returncode != 0:
            raise RuntimeError(f"agy failed: {r.stderr[-500:]}")
        data = _extract_json(r.stdout)
        return {q: {"choice": data.get(q) if data.get(q) in spec["criteria"] else None, "confidence": None, "probabilities": {}}
                for q, spec in questions.items()}


# Words that point to a category. The first category with the most hits wins; no hits means "other".
CATEGORY_KEYWORDS = {
    "quant_trading": ["backtest", "trading", "ohlc", "candlestick", "portfolio", "broker", "order book", "quant"],
    "prediction_markets_finance": ["prediction market", "polymarket", "kalshi", "betting", "payment", "finance", "stripe"],
    "ai_agents_llm": ["llm", "agent", "mcp", "prompt", "openai", "anthropic", "gpt", "rag", "embedding", "inference"],
    "ml_data_science": ["machine learning", "deep learning", "pytorch", "tensorflow", "training", "dataset", "notebook", "scikit"],
    "data_scraping": ["dataframe", "scrap", "etl", "parquet", "csv", "pandas", "crawler"],
    "backend_api": ["rest api", "graphql", "orm", "database", "sql", "auth", "fastapi", "django", "flask", "queue"],
    "web_frontend": ["react", "frontend", "css", "component", "vue", "svelte", "tailwind", "ui library"],
    "dev_tools_cli": ["cli", "command line", "linter", "build system", "editor", "package manager", "formatter"],
    "devops_cloud": ["kubernetes", "docker", "terraform", "ci/cd", "helm", "observability", "aws", "gcp", "azure"],
    "security": ["security", "vulnerab", "crypto", "secret", "cve", "scanner"],
    "iot_embedded": ["firmware", "microcontroller", "arduino", "esp32", "home assistant", "robot", "sensor"],
    "logic_formal_methods": ["proof", "theorem", "sat solver", "smt", "lean", "coq", "formal verification"],
    "philosophy_religion": ["philosophy", "theology", "bible", "scripture", "aquinas"],
    "math_competitive": ["algebra", "competitive programming", "math library", "puzzle"],
    "science_simulation": ["physics", "astronomy", "chemistry", "biology", "simulation"],
    "knowledge_reading": ["note taking", "obsidian", "zotero", "e-book", "epub", "spaced repetition", "knowledge base"],
}
KIND_LABELS = [("docs", ["doc", "typo", "readme"]), ("test", ["test"]), ("chore", ["chore", "dependenc", "ci", "maintenance"]),
               ("bug", ["bug", "crash", "error", "fix", "broken", "regression"])]


class KeywordClassifier:
    """No-AI fallback: keyword and label rules. Never confident, so it never auto-dismisses an issue."""

    model = "keywords"

    def classify(self, state_text: str, questions: dict) -> dict[str, dict]:
        text = state_text.lower()
        labels = text.split("labels:", 1)[1].split("\n", 1)[0] if "labels:" in text else ""
        title = text.split("issue:", 1)[1].split("\n", 1)[0] if "issue:" in text else text
        body_len = len(text.split("body:", 1)[1]) if "body:" in text else 0
        hits = {c: sum(text.count(w) for w in words) for c, words in CATEGORY_KEYWORDS.items()}
        best = max(hits, key=hits.get)
        answers = {
            "category": best if hits[best] else "other",
            "kind": next((k for k, words in KIND_LABELS if any(w in labels or re.search(rf"\b{w}", title) for w in words)), "feature"),
            "difficulty": "small" if re.search(r"good first issue|easy|beginner|typo", labels + title)
                          else "large" if re.search(r"rfc|design|refactor|epic|breaking", labels + title) or body_len > 1200
                          else "medium",
            "worth_a_look": "no" if re.search(r"wontfix|duplicate|invalid|question|needs[- ]design", labels) else "yes",
        }
        return {q: {"choice": answers[q] if answers.get(q) in spec["criteria"] else None, "confidence": None, "probabilities": {}}
                for q, spec in questions.items()}


def pick_primary(cfg: Config) -> Classifier:
    """`classifier` in scout.yaml: jev, agy or keywords. "auto" uses Jev if its key is set, then agy if it is on PATH,
    then the keyword rules, so the scout runs with nothing configured."""
    choice = getattr(cfg, "classifier", "auto")
    if choice == "auto":
        choice = "jev" if os.environ.get("jev_model_api_key") else "agy" if shutil.which("agy") else "keywords"
    if choice == "jev":
        from oss_scout.config import jev_api_key
        return JevClassifier(JevClient(jev_api_key(), cfg.jev_model, cfg.jev_cache_path))
    if choice == "agy":
        return GeminiClassifier(cfg.gemini_model)
    return KeywordClassifier()


def _extract_json(text: str) -> dict:
    text = text.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        data = json.loads(text[start:end + 1])
    # agy's json output may wrap the structured result; unwrap common shapes
    for key in ("result", "structured_output", "response"):
        if isinstance(data, dict) and key in data and isinstance(data[key], (dict, str)):
            inner = data[key]
            data = json.loads(inner) if isinstance(inner, str) else inner
    return data


def run_reclassify(conn, cfg: Config, classifier: Classifier, question: str, limit: int | None = None) -> int:
    """Re-ask one question for every already-classified issue (used when the category set changes). Latest row wins."""
    if question not in QUESTIONS:
        raise ValueError(f"unknown question {question}")
    sql = ("select i.*, r.stars, r.language, r.description from issues i join repos r on r.full_name = i.repo "
           "where i.state not in ('harvested', 'dismissed', 'stale') order by i.id")
    rows = conn.execute(sql + (f" limit {int(limit)}" if limit else "")).fetchall()
    n = 0
    for row in rows:
        issue = dict(row)
        try:
            answers = classifier.classify(issue_state_text(issue, issue), {question: QUESTIONS[question]})
        except Exception:
            continue
        if answers[question]["choice"] is None:
            continue
        _store(conn, issue["id"], answers, classifier.model)
        n += 1
    return n


@dataclass
class ClassifySummary:
    classified: int = 0
    dismissed: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)


def _store(conn, issue_id: str, answers: dict[str, dict], model: str) -> None:
    now = utcnow()
    with conn:
        for q, a in answers.items():
            conn.execute(
                "insert into classifications(issue_id, question, choice, confidence, probabilities, model, asked_at) values (?,?,?,?,?,?,?)",
                (issue_id, q, a["choice"], a["confidence"], json.dumps(a.get("probabilities") or {}), model, now))


def latest(conn, issue_id: str) -> dict[str, dict]:
    rows = conn.execute(
        "select question, choice, confidence, probabilities, model from classifications where issue_id = ? order by id", (issue_id,)).fetchall()
    out = {}
    for r in rows:  # later rows overwrite earlier: latest wins
        out[r["question"]] = {"choice": r["choice"], "confidence": r["confidence"],
                              "probabilities": json.loads(r["probabilities"] or "{}"), "model": r["model"]}
    return out


def run_classify(conn, cfg: Config, primary: Classifier, fallback: Classifier | None = None,
                 limit: int | None = None) -> ClassifySummary:
    s = ClassifySummary()
    sql = ("select i.*, r.stars, r.language, r.description from issues i join repos r on r.full_name = i.repo "
           "where i.state = 'harvested' order by i.harvested_at")
    rows = conn.execute(sql + (f" limit {int(limit)}" if limit else "")).fetchall()
    primary_qs = {q: spec for q, spec in QUESTIONS.items() if q not in cfg.dropped_questions}
    fallback_qs = {q: spec for q, spec in QUESTIONS.items() if q in cfg.dropped_questions}
    for row in rows:
        issue = dict(row)
        text = issue_state_text(issue, issue)
        try:
            answers = primary.classify(text, primary_qs) if primary_qs else {}
            _store(conn, issue["id"], answers, primary.model)
            if fallback_qs:
                if fallback is None:
                    raise RuntimeError(f"questions {list(fallback_qs)} dropped but no fallback classifier configured")
                fb = fallback.classify(text, fallback_qs)
                _store(conn, issue["id"], fb, fallback.model)
                answers = {**answers, **fb}
        except Exception as e:
            s.failed += 1
            s.errors.append(f"{issue['repo']}#{issue['number']}: {e}")
            continue
        if any(a["choice"] is None for a in answers.values()):
            s.failed += 1
            continue
        transitions.apply(conn, issue["id"], "classified", "classifier")
        s.classified += 1
        w = answers.get("worth_a_look")
        if w and w["choice"] == "no" and (w["confidence"] or 0) >= cfg.dismiss_confidence:
            transitions.apply(conn, issue["id"], "dismissed", "classifier", f"worth_a_look=no @ {w['confidence']:.2f}")
            s.dismissed += 1
    return s
