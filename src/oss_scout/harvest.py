from __future__ import annotations
import json
import re
import statistics
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from oss_scout import transitions
from oss_scout.config import Config

MAINTAINER = {"OWNER", "MEMBER", "COLLABORATOR"}
# Only issues nobody has started on can be marked stale by the harvester. Once Ryan is discussing,
# planning, drafting or has a PR open, his own PR would otherwise look like "someone else got there first".
STALEABLE = {"harvested", "classified", "shortlisted", "analyzed"}
BASE = "is:issue is:open no:assignee"


@dataclass
class HarvestSummary:
    queries: int = 0
    repos_discovered: int = 0
    seen: int = 0
    new_issues: int = 0
    updated_issues: int = 0
    excluded_repos: int = 0
    skipped_linked_pr: int = 0
    marked_stale: int = 0
    prs_closed: int = 0
    seeds: int = 0
    skipped_bots: int = 0
    errors: list[str] = field(default_factory=list)


def _parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def build_queries(cfg: Config, today: date) -> list[str]:
    since = (today - timedelta(days=cfg.issue_updated_within_days)).isoformat()
    base = f"{BASE} updated:>={since}"
    qs = []
    for lang in cfg.languages:
        for label in cfg.good_labels:
            qs.append(f'{base} language:{lang} label:"{label}"')
    for kws in cfg.category_keywords.values():
        for kw in kws:
            qs.append(f'{base} "{kw}" in:title,body')
    for label in cfg.bounty_labels:
        qs.append(f'{base} label:"{label}"')
    return qs


def build_repo_queries(cfg: Config, today: date) -> list[str]:
    """Repo-first discovery: repos above the star floor that advertise good-first or help-wanted issues."""
    pushed = (today - timedelta(days=cfg.repo_idle_days)).isoformat()
    return [f"stars:>={cfg.min_stars} pushed:>={pushed} language:{lang} {qual}:>=1"
            for lang in cfg.languages for qual in cfg.repo_qualifiers]


def record_stars(conn, full_name: str, stars: int, now: datetime) -> None:
    """One star-count sample per repo per hour at most; the history is what velocity is computed from."""
    at = _iso(now)
    last = conn.execute("select at, stars from repo_star_history where full_name = ? order by at desc limit 1", (full_name,)).fetchone()
    if last and last["at"][:13] == at[:13]:
        return
    with conn:
        conn.execute("insert into repo_star_history(full_name, at, stars) values (?,?,?)", (full_name, at, stars))


def star_velocity(conn, full_name: str, stars_now: int, now: datetime, window_days: int = 14) -> float | None:
    """Stars per day over the oldest sample inside the window. None until there is at least one day of history."""
    since = _iso(now - timedelta(days=window_days))
    rows = conn.execute("select at, stars from repo_star_history where full_name = ? and at >= ? order by at asc limit 2", (full_name, since)).fetchall()
    if len(rows) < 2:
        return None
    first = rows[0]
    days = (now - _parse_ts(first["at"])).total_seconds() / 86400
    if days < 0.5:
        return None
    return (stars_now - first["stars"]) / days


def build_discover_queries(cfg: Config, today: date) -> list[tuple[str, str]]:
    """(query, sort) pairs for the discover lane: topic repos above the small floor, plus rising repos."""
    pushed = (today - timedelta(days=cfg.repo_idle_days)).isoformat()
    created = (today - timedelta(days=cfg.rising_days)).isoformat()
    qs: list[tuple[str, str]] = []
    for topics in cfg.discover_topics.values():
        for t in topics:
            qs.append((f"topic:{t} stars:>={cfg.discover_min_stars} pushed:>={pushed}", "updated"))
    for lang in cfg.languages:
        qs.append((f"created:>={created} stars:>={cfg.rising_min_stars} pushed:>={pushed} language:{lang}", "stars"))
    return qs


def external_merge_rate(merged_prs: list[dict]) -> float | None:
    if not merged_prs:
        return None
    ext = sum(1 for p in merged_prs if p.get("authorAssociation") not in MAINTAINER)
    return ext / len(merged_prs)


def median_first_response_hours(issues: list[dict]) -> float | None:
    hours = []
    for i in issues:
        try:
            created = _parse_ts(i["createdAt"])
        except (KeyError, ValueError):
            continue
        for c in i.get("comments", []):
            if c.get("authorAssociation") in MAINTAINER:
                hours.append((_parse_ts(c["createdAt"]) - created).total_seconds() / 3600)
                break
    return statistics.median(hours) if hours else None


def detect_ai_policy(text: str | None, cfg: Config) -> str:
    if not text:
        return "none"
    for pat in cfg.ai_policy_banned_patterns:
        if re.search(pat, text, re.IGNORECASE):
            return "banned"
    for pat in cfg.ai_policy_discouraged_patterns:
        if re.search(pat, text, re.IGNORECASE):
            return "discouraged"
    return "none"


def repo_exclusion_reason(repo: dict, cfg: Config, now: datetime) -> str | None:
    # Anything not found by the main search (topic discovery, seed lists) only has to clear the small floor.
    floor = cfg.min_stars if (repo.get("source") or "search") == "search" else cfg.discover_min_stars
    if (repo.get("stars") or 0) < floor:
        return f"stars<{floor}"
    pushed = repo.get("pushed_at")
    if pushed and (now - _parse_ts(pushed)) > timedelta(days=cfg.repo_idle_days):
        return f"idle>{cfg.repo_idle_days}d"
    if repo.get("ai_policy") == "banned":
        return "ai_policy_banned"
    return None


def is_bot(row: dict) -> bool:
    """Bot-opened issues (CI trackers, dependabot, renovate) are not contribution opportunities."""
    author = (row.get("author") or "")
    return row.get("author_type") == "Bot" or author.endswith("[bot]")


def issue_from_search_item(item: dict, cfg: Config) -> dict:
    labels = [l["name"] for l in item.get("labels", [])]
    lower = {l.lower() for l in labels}
    bounty_labels = {b.lower() for b in cfg.bounty_labels}
    has_bounty = int(bool(lower & bounty_labels))
    return {
        "id": item["node_id"],
        "repo": item["repository_url"].split("/repos/", 1)[1],
        "number": item["number"],
        "title": item["title"],
        "body": item.get("body") or "",
        "labels": labels,
        "url": item["html_url"],
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
        "comments": item.get("comments", 0),
        "has_bounty": has_bounty,
        "bounty_source": "label" if has_bounty else None,
        "assignee": (item.get("assignee") or {}).get("login"),
        "author": (item.get("user") or {}).get("login"),
        "author_type": (item.get("user") or {}).get("type"),
    }


REPO_COLS = ["full_name", "stars", "language", "description", "pushed_at", "org_known", "external_merge_rate",
             "median_first_response_hours", "signals_fetched_at", "contributing_text", "contributing_fetched_at",
             "ai_policy", "excluded_reason", "source", "rising", "created_at", "stars_per_day"]


def _ensure_repo(conn, gh, full_name: str, cfg: Config, now: datetime, meta: dict | None = None) -> dict:
    row = conn.execute("select * from repos where full_name = ?", (full_name,)).fetchone()
    repo = dict(row) if row else {"full_name": full_name, "source": "search", "rising": 0}
    if meta:
        repo.update({k: v for k, v in meta.items() if v is not None})
    owner, name = full_name.split("/", 1)
    stale_signals = not repo.get("signals_fetched_at") or (now - _parse_ts(repo["signals_fetched_at"])) > timedelta(days=cfg.repo_signal_refresh_days)
    if stale_signals:
        sig = gh.repo_signals(owner, name)
        repo.update({
            "stars": sig["stars"], "pushed_at": sig["pushed_at"], "description": sig["description"],
            "language": sig["language"], "org_known": int(owner in cfg.known_orgs),
            "external_merge_rate": external_merge_rate(sig["merged_prs"]),
            "median_first_response_hours": median_first_response_hours(sig["issues"]),
            "signals_fetched_at": _iso(now),
        })
        if row:  # the row must exist before history can reference it; new repos get their first sample after the upsert
            record_stars(conn, full_name, sig["stars"], now)
        v = star_velocity(conn, full_name, sig["stars"], now)
        repo["stars_per_day"] = v
        if v is not None and v >= cfg.rising_stars_per_day:
            repo["rising"] = 1
    # Star and idle exclusions need no contributing text: skip that fetch for repos already out.
    cheap_reason = repo_exclusion_reason({**repo, "ai_policy": "none"}, cfg, now)
    stale_contrib = not repo.get("contributing_fetched_at") or (now - _parse_ts(repo["contributing_fetched_at"])) > timedelta(days=cfg.contributing_refresh_days)
    if stale_contrib and not cheap_reason:
        text = gh.contributing_text(owner, name)
        repo.update({"contributing_text": text, "contributing_fetched_at": _iso(now), "ai_policy": detect_ai_policy(text, cfg)})
    repo["excluded_reason"] = repo_exclusion_reason(repo, cfg, now)
    values = [repo.get(c) for c in REPO_COLS]
    with conn:
        conn.execute(
            f"insert into repos({','.join(REPO_COLS)}) values ({','.join('?' * len(REPO_COLS))}) "
            f"on conflict(full_name) do update set {','.join(f'{c}=excluded.{c}' for c in REPO_COLS[1:])}", values)
    if not row and repo.get("stars") is not None:
        record_stars(conn, full_name, repo["stars"], now)
    return repo


def _prefilter_repos(conn, gh, names: list[str], cfg: Config, now: datetime, s: HarvestSummary,
                     meta: dict[str, dict] | None = None) -> dict[str, dict]:
    """One cheap batch call for repos we have never seen: anything under the star floor, idle, or gone is
    recorded as excluded without the expensive per-repo signal and contributing fetches. `meta` carries
    what the discover search already told us (source, rising, created_at, stars, pushed_at)."""
    meta = meta or {}
    excluded: dict[str, dict] = {}
    if not names:
        return excluded
    need = [n for n in names if not (meta.get(n) and meta[n].get("stars") is not None)]
    basics = gh.repo_basics(need) if need else {}
    for full_name in names:
        m = meta.get(full_name) or {}
        b = basics.get(full_name) if full_name in basics else ({"stars": m["stars"], "pushed_at": m.get("pushed_at")} if m.get("stars") is not None else None)
        if b is None:
            reason = "not_found"
            repo = {"full_name": full_name, "excluded_reason": reason}
        else:
            repo = {"full_name": full_name, "stars": b["stars"], "pushed_at": b["pushed_at"], "source": m.get("source", "search")}
            reason = repo_exclusion_reason(repo, cfg, now)
            repo["excluded_reason"] = reason
        if reason:
            # signals_fetched_at marks the cheap check as done so a later pass does not refetch this repo
            with conn:
                conn.execute("insert into repos(full_name, stars, pushed_at, excluded_reason, signals_fetched_at, source, rising, created_at) values (?,?,?,?,?,?,?,?) "
                             "on conflict(full_name) do update set stars=excluded.stars, pushed_at=excluded.pushed_at, "
                             "excluded_reason=excluded.excluded_reason, signals_fetched_at=excluded.signals_fetched_at",
                             (full_name, repo.get("stars"), repo.get("pushed_at"), reason, _iso(now), m.get("source", "search"), int(m.get("rising") or 0), m.get("created_at")))
            excluded[full_name] = repo
            s.excluded_repos += 1
            if repo.get("stars") is not None:
                record_stars(conn, full_name, repo["stars"], now)
    return excluded


def close_open_prs(conn, s: HarvestSummary, shell) -> None:
    """For issues with a draft PR open, read the PR state and finish the loop."""
    rows = conn.execute("select i.id, d.pr_url from issues i join drafts d on d.issue_id = i.id where i.state = 'pr_open' and d.pr_url is not null").fetchall()
    for r in rows:
        try:
            code, out, err = shell(["gh", "pr", "view", r["pr_url"], "--json", "state,merged"], None, 60)
            if code != 0:
                raise RuntimeError(err or out)
            st = json.loads(out)
        except Exception as e:
            s.errors.append(f"pr {r['pr_url']}: {e}")
            continue
        if st.get("merged") or st.get("state") == "MERGED":
            transitions.apply(conn, r["id"], "merged", "harvester", r["pr_url"]); s.prs_closed += 1
        elif st.get("state") == "CLOSED":
            transitions.apply(conn, r["id"], "closed", "harvester", r["pr_url"]); s.prs_closed += 1


def run_harvest(conn, gh, cfg: Config, now: datetime | None = None, shell=None, seeds_fn=None) -> HarvestSummary:
    now = now or datetime.now(timezone.utc)
    s = HarvestSummary()
    if seeds_fn is None:
        from oss_scout import seeds as _seeds
        seeds_fn = lambda cfg_: _seeds.fetch_all(cfg_, log=lambda m: s.errors.append(m))
    if shell is None:
        from oss_scout.agents.draft import default_shell as shell
    close_open_prs(conn, s, shell)
    seen_ids: set[str] = set()
    rows: list[dict] = []

    def take(item: dict) -> None:
        if item.get("pull_request"):
            return
        row = issue_from_search_item(item, cfg)
        if row["id"] in seen_ids:
            return
        seen_ids.add(row["id"])
        s.seen += 1
        rows.append(row)

    # Source 1: issue search by label and keyword (finds niche repos the star floor would otherwise hide).
    for q in build_queries(cfg, now.date()):
        s.queries += 1
        try:
            for item in gh.search_issues(q, pages=cfg.pages_per_query, per_page=cfg.per_page):
                take(item)
        except Exception as e:  # one bad query never aborts the pass
            s.errors.append(f"{q}: {e}")

    # Source 2: repo-first. Recognized repos above the star floor, then their labelled open issues.
    since = f"{(now.date() - timedelta(days=cfg.issue_updated_within_days)).isoformat()}T00:00:00Z"
    discovered: dict[str, None] = {}
    for q in build_repo_queries(cfg, now.date()):
        s.queries += 1
        try:
            for repo in gh.search_repos(q, pages=cfg.repo_pages_per_query, per_page=cfg.per_page):
                discovered.setdefault(repo["full_name"], None)
        except Exception as e:
            s.errors.append(f"{q}: {e}")
    for full_name in discovered:
        owner, name = full_name.split("/", 1)
        for label in cfg.good_labels:
            try:
                for item in gh.list_issues(owner, name, label, since):
                    take(item)
            except Exception as e:
                s.errors.append(f"{full_name} [{label}]: {e}")

    # Source 3: discover lane. Topic and rising repos above the small floor; every open unassigned issue counts.
    meta: dict[str, dict] = {}
    rising_cutoff = (now.date() - timedelta(days=cfg.rising_days)).isoformat()
    for q, sort in build_discover_queries(cfg, now.date()):
        s.queries += 1
        try:
            for repo in gh.search_repos(q, pages=cfg.discover_pages_per_query, per_page=cfg.per_page, sort=sort):
                full_name = repo["full_name"]
                if full_name in discovered:
                    continue
                created = (repo.get("created_at") or "")[:10]
                meta.setdefault(full_name, {"source": "discover", "stars": repo.get("stargazers_count"), "pushed_at": repo.get("pushed_at"),
                                            "created_at": repo.get("created_at"), "rising": int(bool(created and created >= rising_cutoff))})
        except Exception as e:
            s.errors.append(f"{q}: {e}")
    # Source 4: seed lists (sites and repos that point at repos). No stars known up front: the pre-filter fetches them.
    seed_labels: dict[str, str | None] = {}
    try:
        seed_rows = [r for r in seeds_fn(cfg) if r["full_name"] not in discovered and r["full_name"] not in meta]
        known_at = {r["full_name"]: (r["signals_fetched_at"] or "") for r in conn.execute("select full_name, signals_fetched_at from repos")}
        # Unknown repos first, then known ones with the oldest signals, up to the per-run cap.
        seed_rows.sort(key=lambda r: (r["full_name"] in known_at, known_at.get(r["full_name"], "")))
        for r in seed_rows[: cfg.seed_repos_per_run]:
            full_name = r["full_name"]
            known = conn.execute("select stars, pushed_at, created_at from repos where full_name = ?", (full_name,)).fetchone()
            meta[full_name] = {"source": r.get("source", "seed"), "stars": known["stars"] if known else None,
                               "pushed_at": known["pushed_at"] if known else None, "created_at": known["created_at"] if known else None, "rising": 0}
            seed_labels[full_name] = r.get("label")
            s.seeds += 1
    except Exception as e:
        s.errors.append(f"seeds: {e}")
    s.repos_discovered = len(discovered) + len(meta)
    # Seed repos have no star count yet: one batched basics call fills it so the floor applies before any listing.
    need = [n for n, m in meta.items() if m.get("stars") is None]
    if need:
        try:
            for n, b in gh.repo_basics(need).items():
                if b:
                    meta[n].update({"stars": b["stars"], "pushed_at": b["pushed_at"]})
                else:
                    meta[n]["stars"] = 0
        except Exception as e:
            s.errors.append(f"seed basics: {e}")
    for full_name, m in meta.items():
        if (m.get("stars") or 0) < cfg.discover_min_stars:
            continue
        owner, name = full_name.split("/", 1)
        try:
            label = seed_labels.get(full_name)
            for item in gh.list_issues(owner, name, label, since, pages=1, per_page=cfg.discover_issues_per_repo):
                take(item)
        except Exception as e:
            s.errors.append(f"{full_name} [discover]: {e}")

    known = {r[0] for r in conn.execute("select full_name from repos")}
    # Discover repos under the floor are listed here too: they get an excluded row without any API call.
    unknown = [name for name in dict.fromkeys([r["repo"] for r in rows] + list(meta)) if name not in known]
    # Repos the discover search found but never saw before carry their metadata into the pre-filter.
    try:
        repo_cache: dict[str, dict] = _prefilter_repos(conn, gh, unknown, cfg, now, s, meta)
    except Exception as e:
        s.errors.append(f"prefilter: {e}")
        repo_cache = {}
    for row in rows:
        try:
            _harvest_one(conn, gh, cfg, now, row, repo_cache, s, meta.get(row["repo"]))
        except Exception as e:
            s.errors.append(f"{row['repo']}#{row['number']}: {e}")
    return s


def _harvest_one(conn, gh, cfg, now, row, repo_cache, s: HarvestSummary, meta: dict | None = None) -> None:
    existing = conn.execute("select state from issues where id = ?", (row["id"],)).fetchone()
    if existing and existing["state"] in transitions.TERMINAL:
        with conn:
            conn.execute("update issues set last_seen_at = ? where id = ?", (_iso(now), row["id"]))
        return
    if row["repo"] not in repo_cache:
        try:
            repo_cache[row["repo"]] = _ensure_repo(conn, gh, row["repo"], cfg, now, meta)
        except Exception as e:
            # One failing repo must cost one call and one error, not one per issue.
            repo_cache[row["repo"]] = {"full_name": row["repo"], "excluded_reason": f"fetch_error: {e}"}
            s.errors.append(f"{row['repo']}: {e}")
        if repo_cache[row["repo"]].get("excluded_reason"):
            s.excluded_repos += 1
    repo = repo_cache[row["repo"]]
    if repo.get("excluded_reason"):
        return
    if existing and existing["state"] not in STALEABLE:
        with conn:
            conn.execute("update issues set last_seen_at = ? where id = ?", (_iso(now), row["id"]))
        return
    if existing and is_bot(row):
        transitions.apply(conn, row["id"], "stale", "harvester", "bot-authored")
        s.marked_stale += 1
        return
    if not existing and is_bot(row):
        s.skipped_bots += 1
        return
    linked = gh.has_closing_pr(row["id"])
    if existing:
        if linked or row["assignee"]:
            transitions.apply(conn, row["id"], "stale", "harvester", "linked PR" if linked else "assigned")
            s.marked_stale += 1
            return
        with conn:
            conn.execute(
                "update issues set title=?, body=?, labels=?, updated_at=?, comments=?, has_bounty=?, bounty_source=?, last_seen_at=?, author=?, author_type=? where id=?",
                (row["title"], row["body"], json.dumps(row["labels"]), row["updated_at"], row["comments"],
                 row["has_bounty"], row["bounty_source"], _iso(now), row["author"], row["author_type"], row["id"]))
        s.updated_issues += 1
        return
    if linked:
        s.skipped_linked_pr += 1
        return
    lane = "discover" if (repo.get("stars") or 0) < cfg.min_stars else "resume"
    with conn:
        conn.execute(
            "insert into issues(id, repo, number, title, body, labels, url, created_at, updated_at, comments, has_bounty, bounty_source, linked_pr, assignee, state, harvested_at, last_seen_at, lane, author, author_type) "
            "values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,'harvested',?,?,?,?,?)",
            (row["id"], row["repo"], row["number"], row["title"], row["body"], json.dumps(row["labels"]), row["url"],
             row["created_at"], row["updated_at"], row["comments"], row["has_bounty"], row["bounty_source"], 0,
             row["assignee"], _iso(now), _iso(now), lane, row["author"], row["author_type"]))
    s.new_issues += 1
