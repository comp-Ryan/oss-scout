from __future__ import annotations
import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from oss_scout import classify, transitions
from oss_scout.config import Config
from oss_scout.db import utcnow

DIFFICULTY_FIT = {"small": 1.0, "medium": 0.8, "large": 0.3}
AI_POLICY_VALUE = {"none": 0.0, "discouraged": -0.5, "banned": -1.0}
FRESHNESS_DAYS = 30.0
STARS_LOG_CAP = math.log10(100_000)  # 100k stars -> 1.0


def MAX_TOTAL(cfg: Config) -> float:
    """Best possible total: every term at 1.0 except ai_policy, which can only subtract."""
    return sum(w for t, w in cfg.weights.items() if w > 0 and t != "ai_policy")


def weights_hash(cfg: Config) -> str:
    blob = json.dumps({"w": cfg.weights, "c": cfg.category_values, "l": cfg.language_fit}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


def _parse_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def compute(issue: dict, repo: dict, cls: dict[str, dict], cfg: Config, now: datetime) -> tuple[float, dict[str, float]]:
    stars = repo.get("stars") or 0
    recognition = 0.5 * _clamp(math.log10(max(stars, 1)) / STARS_LOG_CAP) + 0.5 * (1.0 if repo.get("org_known") else 0.0)

    emr = repo.get("external_merge_rate")
    resp = repo.get("median_first_response_hours")
    merge_term = _clamp(emr) if emr is not None else 0.0
    resp_term = _clamp(1.0 - math.log10(max(resp, 1.0)) / 3.0) if resp is not None else 0.0  # 1h->1.0, 1000h->0
    mergeability = 0.5 * merge_term + 0.5 * resp_term

    language_fit = cfg.language_fit.get(repo.get("language") or "", 0.2)
    category = cfg.category_values.get((cls.get("category") or {}).get("choice") or "other", 0.3)
    difficulty_fit = DIFFICULTY_FIT.get((cls.get("difficulty") or {}).get("choice") or "", 0.5)

    updated = _parse_ts(issue.get("updated_at"))
    age_days = (now - updated).total_seconds() / 86400 if updated else FRESHNESS_DAYS
    freshness = _clamp(1.0 - age_days / FRESHNESS_DAYS)

    bounty = 1.0 if issue.get("has_bounty") else 0.0
    ai_policy = AI_POLICY_VALUE.get(repo.get("ai_policy") or "none", 0.0)

    raw = {"recognition": recognition, "mergeability": mergeability, "language_fit": language_fit, "category": category,
           "difficulty_fit": difficulty_fit, "freshness": freshness, "bounty": bounty, "ai_policy": ai_policy}
    breakdown = {term: round(raw[term] * cfg.weights.get(term, 0.0), 4) for term in cfg.weights}
    return round(sum(breakdown.values()), 4), breakdown


@dataclass
class ScoreSummary:
    scored: int = 0
    shortlisted: int = 0
    demoted: int = 0


def run_scoring(conn, cfg: Config, now: datetime | None = None) -> ScoreSummary:
    now = now or datetime.now(timezone.utc)
    s = ScoreSummary()
    h = weights_hash(cfg)
    rows = conn.execute(
        "select i.*, r.stars, r.org_known, r.external_merge_rate, r.median_first_response_hours, r.language, r.ai_policy "
        "from issues i join repos r on r.full_name = i.repo where i.state in ('classified', 'shortlisted')").fetchall()
    ranked = []
    for row in rows:
        issue = dict(row)
        cls = classify.latest(conn, issue["id"])
        total, breakdown = compute(issue, issue, cls, cfg, now)
        with conn:
            conn.execute("insert into scores(issue_id, total, breakdown, weights_hash, scored_at) values (?,?,?,?,?)",
                         (issue["id"], total, json.dumps(breakdown), h, utcnow()))
        s.scored += 1
        ranked.append((total, issue["id"], issue["state"], issue["repo"]))
    ranked.sort(key=lambda t: -t[0])
    keep: set[str] = set()
    per_repo: dict[str, int] = {}
    for total, iid, _, repo in ranked:
        if len(keep) >= cfg.shortlist_count or total < cfg.shortlist_floor:
            break
        if per_repo.get(repo, 0) >= cfg.shortlist_per_repo_cap:
            continue
        keep.add(iid)
        per_repo[repo] = per_repo.get(repo, 0) + 1
    for total, iid, state, _ in ranked:
        if iid in keep and state == "classified":
            transitions.apply(conn, iid, "shortlisted", "scorer", f"score {total}")
            s.shortlisted += 1
        elif iid not in keep and state == "shortlisted":
            transitions.apply(conn, iid, "classified", "scorer", f"score {total} fell out")
            s.demoted += 1
    return s


def latest_score(conn, issue_id: str) -> tuple[float, dict] | None:
    row = conn.execute("select total, breakdown from scores where issue_id = ? order by id desc limit 1", (issue_id,)).fetchone()
    return (row["total"], json.loads(row["breakdown"])) if row else None
