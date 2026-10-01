from __future__ import annotations
import json
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
    "ai_agents_llm": "LLM agents, MCP servers, prompt tooling, model evals, inference",
    "data_scraping": "Data processing libraries, dataframes, scrapers, ETL, databases",
    "dev_tools_cli": "Developer tools, CLIs, build systems, linters, editors",
    "web_frontend": "React, UI components, web frontends, design systems",
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
        cmd = ["agy", "--print", "--model", self._model, "--output-format", "json", "--json-schema", schema_path,
               "--disable-slash-commands", prompt]
        r = self.runner(cmd, capture_output=True, text=True, timeout=self.timeout)
        if r.returncode != 0:
            raise RuntimeError(f"agy failed: {r.stderr[-500:]}")
        data = _extract_json(r.stdout)
        return {q: {"choice": data.get(q) if data.get(q) in spec["criteria"] else None, "confidence": None, "probabilities": {}}
                for q, spec in questions.items()}


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
