from datetime import date, datetime, timezone, timedelta
import pytest
from oss_scout import db, harvest
from oss_scout.config import Config

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)

def test_build_queries_cover_languages_labels_keywords_and_bounty():
    cfg = Config(languages=["Python"], good_labels=["good first issue"],
                 category_keywords={"quant_trading": ["backtest"]}, bounty_labels=["bounty"])
    qs = harvest.build_queries(cfg, date(2026, 10, 1))
    assert 'is:issue is:open no:assignee updated:>=2026-09-01 language:Python label:"good first issue"' in qs
    assert 'is:issue is:open no:assignee updated:>=2026-09-01 "backtest" in:title,body' in qs
    assert 'is:issue is:open no:assignee updated:>=2026-09-01 label:"bounty"' in qs

def test_external_merge_rate():
    prs = [{"authorAssociation": a} for a in ["MEMBER", "CONTRIBUTOR", "NONE", "OWNER", "FIRST_TIME_CONTRIBUTOR"]]
    assert harvest.external_merge_rate(prs) == pytest.approx(3 / 5)
    assert harvest.external_merge_rate([]) is None

def test_median_first_response_hours_uses_first_maintainer_comment():
    issues = [
        {"createdAt": "2026-09-01T00:00:00Z", "comments": [
            {"createdAt": "2026-09-01T01:00:00Z", "authorAssociation": "NONE"},
            {"createdAt": "2026-09-01T04:00:00Z", "authorAssociation": "MEMBER"}]},
        {"createdAt": "2026-09-02T00:00:00Z", "comments": [
            {"createdAt": "2026-09-02T02:00:00Z", "authorAssociation": "COLLABORATOR"}]},
        {"createdAt": "2026-09-03T00:00:00Z", "comments": []},  # no response: ignored
    ]
    assert harvest.median_first_response_hours(issues) == pytest.approx(3.0)
    assert harvest.median_first_response_hours([]) is None
    assert harvest.median_first_response_hours([{"createdAt": "x", "comments": []}]) is None

@pytest.mark.parametrize("text,expected", [
    (None, "none"),
    ("Thanks for contributing! Run the tests.", "none"),
    ("We do not accept AI-generated pull requests.", "banned"),
    ("No LLM generated code please.", "banned"),
    ("If you used Copilot, say so in the PR.", "discouraged"),
    ("AI-assisted contributions must be disclosed.", "discouraged"),
])
def test_detect_ai_policy(text, expected):
    assert harvest.detect_ai_policy(text, Config()) == expected

def test_repo_exclusion_reasons():
    cfg = Config(min_stars=200, repo_idle_days=60)
    base = {"stars": 500, "pushed_at": "2026-09-30T00:00:00Z", "ai_policy": "none"}
    assert harvest.repo_exclusion_reason(base, cfg, NOW) is None
    assert harvest.repo_exclusion_reason({**base, "stars": 10}, cfg, NOW) == "stars<200"
    assert harvest.repo_exclusion_reason({**base, "pushed_at": "2026-01-01T00:00:00Z"}, cfg, NOW) == "idle>60d"
    assert harvest.repo_exclusion_reason({**base, "ai_policy": "banned"}, cfg, NOW) == "ai_policy_banned"
    assert harvest.repo_exclusion_reason({**base, "ai_policy": "discouraged"}, cfg, NOW) is None

def test_issue_from_search_item_handles_null_body_and_bounty_label():
    item = {"node_id": "I1", "number": 7, "title": "t", "body": None, "html_url": "u",
            "repository_url": "https://api.github.com/repos/o/r", "labels": [{"name": "💎 Bounty"}],
            "created_at": "c", "updated_at": "u2", "comments": 2, "assignee": None}
    row = harvest.issue_from_search_item(item, Config())
    assert row["repo"] == "o/r" and row["body"] == "" and row["has_bounty"] == 1 and row["labels"] == ["💎 Bounty"]

class FakeGH:
    def __init__(self, items, signals, closing=None, contributing=None):
        self.items, self.signals, self.closing, self.contributing = items, signals, closing or {}, contributing or {}
        self.signal_calls = 0
    def search_issues(self, q, pages, per_page=100):
        yield from self.items.get(q, [])
    def repo_signals(self, owner, name):
        self.signal_calls += 1
        return self.signals[f"{owner}/{name}"]
    def has_closing_pr(self, node_id):
        return self.closing.get(node_id, False)
    def contributing_text(self, owner, name):
        return self.contributing.get(f"{owner}/{name}")
    def repo_basics(self, names):
        return {n: ({"stars": self.signals[n]["stars"], "pushed_at": self.signals[n]["pushed_at"]} if n in self.signals else None) for n in names}
    def search_repos(self, q, pages, per_page=100):
        yield from ()
    def list_issues(self, owner, name, label, since, pages=2, per_page=100):
        return []

def _item(node_id, repo, number=1, title="t"):
    return {"node_id": node_id, "number": number, "title": title, "body": "b", "html_url": f"https://github.com/{repo}/issues/{number}",
            "repository_url": f"https://api.github.com/repos/{repo}", "labels": [], "created_at": "2026-09-20T00:00:00Z",
            "updated_at": "2026-09-25T00:00:00Z", "comments": 0, "assignee": None}

GOOD = {"stars": 1000, "pushed_at": "2026-09-30T00:00:00Z", "description": "d", "language": "Python",
        "merged_prs": [{"authorAssociation": "NONE", "mergedAt": "x"}], "issues": []}

def _cfg():
    return Config(languages=["Python"], good_labels=["good first issue"], category_keywords={}, bounty_labels=[])

def test_run_harvest_inserts_and_excludes(tmp_path):
    cfg = _cfg()
    q = harvest.build_queries(cfg, NOW.date())[0]
    gh = FakeGH({q: [_item("I1", "o/good"), _item("I2", "o/small"), _item("I3", "o/good", 2)]},
                {"o/good": GOOD, "o/small": {**GOOD, "stars": 5}}, closing={"I3": True})
    conn = db.connect(tmp_path / "t.db")
    s = harvest.run_harvest(conn, gh, cfg, NOW)
    assert s.new_issues == 1 and s.excluded_repos == 1 and s.skipped_linked_pr == 1
    assert conn.execute("select state from issues where id='I1'").fetchone()[0] == "harvested"
    assert conn.execute("select excluded_reason from repos where full_name='o/small'").fetchone()[0] == "stars<200"
    assert conn.execute("select count(*) from issues").fetchone()[0] == 1
    assert conn.execute("select median_first_response_hours from repos where full_name='o/good'").fetchone()[0] is None

def test_run_harvest_rerun_only_touches_last_seen_on_dismissed(tmp_path):
    from oss_scout import transitions
    cfg = _cfg()
    q = harvest.build_queries(cfg, NOW.date())[0]
    gh = FakeGH({q: [_item("I1", "o/good")]}, {"o/good": GOOD})
    conn = db.connect(tmp_path / "t.db")
    harvest.run_harvest(conn, gh, cfg, NOW)
    transitions.apply(conn, "I1", "dismissed", "user")
    gh.items[q][0]["title"] = "changed"
    s = harvest.run_harvest(conn, gh, cfg, NOW + timedelta(hours=1))
    row = conn.execute("select state, title, last_seen_at from issues where id='I1'").fetchone()
    assert row["state"] == "dismissed" and row["title"] == "t"
    assert row["last_seen_at"] == "2026-10-01T13:00:00Z"
    assert gh.signal_calls == 1  # signals refreshed only after 7 days

def test_run_harvest_updates_live_issue_and_marks_stale_on_closing_pr(tmp_path):
    cfg = _cfg()
    q = harvest.build_queries(cfg, NOW.date())[0]
    gh = FakeGH({q: [_item("I1", "o/good")]}, {"o/good": GOOD})
    conn = db.connect(tmp_path / "t.db")
    harvest.run_harvest(conn, gh, cfg, NOW)
    gh.items[q][0]["title"] = "changed"
    s = harvest.run_harvest(conn, gh, cfg, NOW + timedelta(hours=1))
    assert s.updated_issues == 1
    assert conn.execute("select title from issues where id='I1'").fetchone()[0] == "changed"
    gh.closing["I1"] = True
    s = harvest.run_harvest(conn, gh, cfg, NOW + timedelta(hours=2))
    assert s.marked_stale == 1
    assert conn.execute("select state from issues where id='I1'").fetchone()[0] == "stale"

def test_run_harvest_skips_pull_requests_and_survives_query_errors(tmp_path):
    cfg = Config(languages=["Python"], good_labels=["good first issue", "help wanted"], category_keywords={}, bounty_labels=[])
    q1, q2 = harvest.build_queries(cfg, NOW.date())
    class GH(FakeGH):
        def search_issues(self, q, pages, per_page=100):
            if q == q1:
                raise RuntimeError("boom")
            yield from super().search_issues(q, pages, per_page)
    pr = {**_item("P1", "o/good", 9), "pull_request": {"url": "x"}}
    gh = GH({q2: [pr, _item("I1", "o/good")]}, {"o/good": GOOD})
    conn = db.connect(tmp_path / "t.db")
    s = harvest.run_harvest(conn, gh, cfg, NOW)
    assert s.new_issues == 1 and len(s.errors) == 1 and "boom" in s.errors[0]

def test_run_harvest_prefilters_repos_in_batch_before_signal_calls(tmp_path):
    cfg = _cfg()
    q = harvest.build_queries(cfg, NOW.date())[0]
    class GH(FakeGH):
        def __init__(self, *a, **k):
            super().__init__(*a, **k); self.basics_calls = []
        def repo_basics(self, names):
            self.basics_calls.append(list(names))
            return {n: ({"stars": self.signals[n]["stars"], "pushed_at": self.signals[n]["pushed_at"]} if n in self.signals else None) for n in names}
    gh = GH({q: [_item("I1", "o/good"), _item("I2", "o/small"), _item("I3", "o/gone", 3)]},
            {"o/good": GOOD, "o/small": {**GOOD, "stars": 5}})
    conn = db.connect(tmp_path / "t.db")
    s = harvest.run_harvest(conn, gh, cfg, NOW)
    assert gh.basics_calls == [["o/good", "o/small", "o/gone"]]
    assert gh.signal_calls == 1  # only o/good got the expensive fetch
    assert s.new_issues == 1 and s.excluded_repos == 2
    assert conn.execute("select excluded_reason from repos where full_name='o/small'").fetchone()[0] == "stars<200"
    assert conn.execute("select excluded_reason from repos where full_name='o/gone'").fetchone()[0] == "not_found"

def test_build_repo_queries_use_star_floor_idle_cutoff_and_qualifiers():
    cfg = Config(languages=["Python"], min_stars=200, repo_idle_days=60)
    qs = harvest.build_repo_queries(cfg, date(2026, 10, 1))
    assert qs == ["stars:>=200 pushed:>=2026-08-02 language:Python good-first-issues:>=1",
                  "stars:>=200 pushed:>=2026-08-02 language:Python help-wanted-issues:>=1"]

def test_run_harvest_pulls_issues_from_repo_first_source_and_dedupes(tmp_path):
    cfg = _cfg()
    q = harvest.build_queries(cfg, NOW.date())[0]
    rq = harvest.build_repo_queries(cfg, NOW.date())[0]
    class GH(FakeGH):
        def __init__(self, *a, **k):
            super().__init__(*a, **k); self.list_calls = []
        def search_repos(self, q, pages, per_page=100):
            if q == rq:
                yield {"full_name": "big/repo", "stargazers_count": 5000, "pushed_at": "2026-09-30T00:00:00Z"}
        def list_issues(self, owner, name, label, since, pages=2, per_page=100):
            self.list_calls.append((f"{owner}/{name}", label, since))
            if label == "good first issue":
                return [_item("I1", "big/repo", 1), {**_item("P9", "big/repo", 9), "pull_request": {"url": "x"}}]
            return []
    gh = GH({q: [_item("I1", "big/repo", 1), _item("I2", "o/good", 2)]}, {"big/repo": {**GOOD, "stars": 5000}, "o/good": GOOD})
    conn = db.connect(tmp_path / "t.db")
    s = harvest.run_harvest(conn, gh, cfg, NOW)
    assert s.repos_discovered == 1
    assert gh.list_calls == [("big/repo", "good first issue", "2026-09-01T00:00:00Z")]
    assert s.new_issues == 2 and s.seen == 2  # I1 seen once despite two sources; the PR is skipped
    assert {r[0] for r in conn.execute("select id from issues")} == {"I1", "I2"}

def test_rerun_never_refetches_a_prefilter_excluded_repo(tmp_path):
    cfg = _cfg()
    q = harvest.build_queries(cfg, NOW.date())[0]
    class GH(FakeGH):
        def __init__(self, *a, **k):
            super().__init__(*a, **k); self.contrib_calls = 0
        def contributing_text(self, owner, name):
            self.contrib_calls += 1
            return super().contributing_text(owner, name)
    gh = GH({q: [_item("I2", "o/small")]}, {"o/small": {**GOOD, "stars": 5}})
    conn = db.connect(tmp_path / "t.db")
    harvest.run_harvest(conn, gh, cfg, NOW)
    harvest.run_harvest(conn, gh, cfg, NOW + timedelta(hours=1))
    assert gh.signal_calls == 0 and gh.contrib_calls == 0
    assert conn.execute("select count(*) from issues").fetchone()[0] == 0

def test_ensure_repo_skips_contributing_fetch_when_already_excluded(tmp_path):
    cfg = _cfg()
    class GH(FakeGH):
        def __init__(self, *a, **k):
            super().__init__(*a, **k); self.contrib_calls = 0
        def contributing_text(self, owner, name):
            self.contrib_calls += 1
            return None
    gh = GH({}, {"o/small": {**GOOD, "stars": 5}, "o/good": GOOD})
    conn = db.connect(tmp_path / "t.db")
    small = harvest._ensure_repo(conn, gh, "o/small", cfg, NOW)
    assert small["excluded_reason"] == "stars<200" and gh.contrib_calls == 0
    good = harvest._ensure_repo(conn, gh, "o/good", cfg, NOW)
    assert good["excluded_reason"] is None and gh.contrib_calls == 1

def test_closing_pr_does_not_stale_an_issue_ryan_is_working_on(tmp_path):
    from oss_scout import transitions
    cfg = _cfg()
    q = harvest.build_queries(cfg, NOW.date())[0]
    gh = FakeGH({q: [_item("I1", "o/good")]}, {"o/good": GOOD})
    conn = db.connect(tmp_path / "t.db")
    harvest.run_harvest(conn, gh, cfg, NOW)
    for to, actor in [("classified", "classifier"), ("shortlisted", "scorer"), ("analyzed", "agent:1"), ("discussing", "user")]:
        transitions.apply(conn, "I1", to, actor)
    gh.closing["I1"] = True
    s = harvest.run_harvest(conn, gh, cfg, NOW + timedelta(hours=1))
    assert s.marked_stale == 0
    assert conn.execute("select state from issues where id='I1'").fetchone()[0] == "discussing"

def test_repo_fetch_error_is_recorded_once_not_per_issue(tmp_path):
    cfg = _cfg()
    q = harvest.build_queries(cfg, NOW.date())[0]
    class GH(FakeGH):
        def __init__(self, *a, **k):
            super().__init__(*a, **k); self.signal_calls = 0
        def repo_signals(self, owner, name):
            self.signal_calls += 1
            raise RuntimeError("repository o/gone not found")
    gh = GH({q: [_item("I1", "o/gone", 1), _item("I2", "o/gone", 2), _item("I3", "o/gone", 3)]},
            {"o/gone": GOOD})  # passes the cheap pre-filter, then the full fetch fails
    conn = db.connect(tmp_path / "t.db")
    s = harvest.run_harvest(conn, gh, cfg, NOW)
    assert gh.signal_calls == 1 and len(s.errors) == 1 and s.new_issues == 0

def test_harvest_closes_the_loop_on_open_prs(tmp_path):
    from oss_scout import transitions
    cfg = _cfg()
    gh = FakeGH({}, {})
    conn = db.connect(tmp_path / "t.db")
    conn.execute("insert into repos(full_name, stars) values ('o/r', 1000)")
    for iid, n in (("I1", 1), ("I2", 2), ("I3", 3)):
        conn.execute("insert into issues(id, repo, number, title, url, state, harvested_at, last_seen_at) values (?,'o/r',?,'t','u','pr_open','x','x')", (iid, n))
        conn.execute("insert into drafts(issue_id, work_dir, worktree, branch, base_branch, brief_path, created_at, pr_url) values (?,'w','wt','b','main','p','x',?)", (iid, f"https://github.com/o/r/pull/{n}"))
    conn.commit()
    states = {"1": {"state": "MERGED", "merged": True}, "2": {"state": "CLOSED", "merged": False}, "3": {"state": "OPEN", "merged": False}}
    def shell(cmd, cwd=None, timeout=600):
        assert cmd[:3] == ["gh", "pr", "view"]
        import json as _j
        return 0, _j.dumps(states[cmd[3].rsplit("/", 1)[1]]), ""
    s = harvest.run_harvest(conn, gh, cfg, NOW, shell=shell)
    got = {r[0]: r[1] for r in conn.execute("select id, state from issues")}
    assert got == {"I1": "merged", "I2": "closed", "I3": "pr_open"} and s.prs_closed == 2
