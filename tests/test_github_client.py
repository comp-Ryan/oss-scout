import io, json
from urllib.error import HTTPError
from email.message import Message
import pytest
from oss_scout.github_client import GitHubClient, GitHubError

class FakeResp(io.BytesIO):
    def __init__(self, body, headers=None):
        super().__init__(json.dumps(body).encode())
        self.headers = Message()
        for k, v in (headers or {}).items():
            self.headers[k] = v
    def __enter__(self): return self
    def __exit__(self, *a): return False

def make_client(responses):
    """responses: list of FakeResp/HTTPError to return in order."""
    calls = []
    slept = []
    def opener(req, timeout=0):
        calls.append(req)
        r = responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r
    c = GitHubClient("tok", sleep=slept.append, opener=opener, now=lambda: 1000.0)
    return c, calls, slept

def _http_error(code, body=b"{}"):
    return HTTPError("u", code, "err", Message(), io.BytesIO(body))

def test_rest_sends_token_and_params():
    c, calls, _ = make_client([FakeResp({"ok": 1}, {"X-RateLimit-Remaining": "10"})])
    body, headers = c.rest("search/issues", {"q": "a b", "per_page": 3})
    assert body == {"ok": 1}
    assert calls[0].full_url == "https://api.github.com/search/issues?q=a+b&per_page=3"
    assert calls[0].get_header("Authorization") == "Bearer tok"

def test_rate_limit_sleeps_until_reset_then_retries():
    err = _http_error(403, b'{"message":"API rate limit exceeded"}')
    err.headers["X-RateLimit-Remaining"] = "0"; err.headers["X-RateLimit-Reset"] = "1030"
    c, calls, slept = make_client([err, FakeResp({"ok": 1})])
    c.rest("rate_limit")
    assert slept == [31.0]  # reset - now + 1
    assert len(calls) == 2

def test_secondary_limit_without_reset_sleeps_default():
    err = _http_error(403, b'{"message":"You have exceeded a secondary rate limit"}')
    c, calls, slept = make_client([err, FakeResp({"ok": 1})])
    c.rest("x")
    assert slept == [60.0]

def test_5xx_retries_three_times_then_raises():
    errs = [_http_error(502, b"") for _ in range(4)]
    c, calls, slept = make_client(errs)
    with pytest.raises(GitHubError):
        c.rest("x")
    assert len(calls) == 4

def test_422_raises_without_retry():
    err = _http_error(422, b'{"message":"Validation Failed"}')
    c, calls, _ = make_client([err])
    with pytest.raises(GitHubError):
        c.rest("search/issues", {"q": "bad"})
    assert len(calls) == 1

def test_search_issues_paginates_and_stops_on_short_page():
    page1 = {"total_count": 3, "items": [{"id": 1}, {"id": 2}]}
    page2 = {"total_count": 3, "items": [{"id": 3}]}
    c, calls, _ = make_client([FakeResp(page1), FakeResp(page2)])
    items = list(c.search_issues("q", pages=5, per_page=2))
    assert [i["id"] for i in items] == [1, 2, 3]
    assert len(calls) == 2
    assert "advanced_search=true" in calls[0].full_url

def test_graphql_raises_on_errors():
    c, _, _ = make_client([FakeResp({"errors": [{"message": "nope"}]})])
    with pytest.raises(GitHubError):
        c.graphql("query { x }", {})

def test_has_closing_pr_true_only_for_open_will_close_target():
    data = {"data": {"node": {"timelineItems": {"nodes": [
        {"willCloseTarget": False, "source": {"number": 1, "state": "OPEN"}},
        {"willCloseTarget": True, "source": {"number": 2, "state": "OPEN"}},
    ]}}}}
    c, _, _ = make_client([FakeResp(data)])
    assert c.has_closing_pr("I_x") is True
    data2 = {"data": {"node": {"timelineItems": {"nodes": [
        {"willCloseTarget": True, "source": {"number": 2, "state": "MERGED"}}]}}}}
    c2, _, _ = make_client([FakeResp(data2)])
    assert c2.has_closing_pr("I_x") is False

def test_repo_signals_normalizes_graphql_shape():
    data = {"data": {"repository": {
        "stargazerCount": 5, "pushedAt": "p", "description": None, "primaryLanguage": None,
        "pullRequests": {"nodes": [{"authorAssociation": "NONE", "mergedAt": "m"}]},
        "issues": {"nodes": [{"createdAt": "c", "comments": {"nodes": [{"createdAt": "d", "authorAssociation": "MEMBER"}]}}]},
    }}}
    c, _, _ = make_client([FakeResp(data)])
    sig = c.repo_signals("o", "r")
    assert sig["stars"] == 5 and sig["description"] == "" and sig["language"] is None
    assert sig["merged_prs"] == [{"authorAssociation": "NONE", "mergedAt": "m"}]
    assert sig["issues"] == [{"createdAt": "c", "comments": [{"createdAt": "d", "authorAssociation": "MEMBER"}]}]

def test_contributing_text_tries_two_paths_and_decodes_base64():
    import base64
    found = FakeResp({"content": base64.b64encode(b"Please no AI PRs").decode(), "encoding": "base64"})
    c, calls, _ = make_client([_http_error(404), found])
    assert c.contributing_text("o", "r") == "Please no AI PRs"
    assert calls[0].full_url.endswith("/contents/CONTRIBUTING.md")
    assert calls[1].full_url.endswith("/contents/.github/CONTRIBUTING.md")

def test_contributing_text_none_when_absent():
    c, _, _ = make_client([_http_error(404), _http_error(404)])
    assert c.contributing_text("o", "r") is None

def test_repo_basics_batches_with_aliases_and_handles_missing():
    data = {"data": {"r0": {"nameWithOwner": "o/a", "stargazerCount": 10, "pushedAt": "p"}, "r1": None}}
    c, calls, _ = make_client([FakeResp(data)])
    out = c.repo_basics(["o/a", "o/gone"])
    assert out == {"o/a": {"stars": 10, "pushed_at": "p"}, "o/gone": None}
    body = json.loads(calls[0].data)
    assert 'r0: repository(owner:"o", name:"a")' in body["query"]
    assert len(calls) == 1

def test_search_repos_sorts_by_stars_and_paginates():
    page1 = {"items": [{"full_name": "a/b", "stargazers_count": 5}]}
    c, calls, _ = make_client([FakeResp(page1)])
    items = list(c.search_repos("stars:>=200", pages=3, per_page=2))
    assert [i["full_name"] for i in items] == ["a/b"]
    assert "search/repositories?" in calls[0].full_url and "sort=stars" in calls[0].full_url and "order=desc" in calls[0].full_url

def test_list_issues_filters_open_unassigned_label_since():
    c, calls, _ = make_client([FakeResp([{"number": 1}, {"number": 2}]), FakeResp([{"number": 3}])])
    items = list(c.list_issues("o", "r", "good first issue", since="2026-09-01T00:00:00Z", pages=2, per_page=2))
    assert [i["number"] for i in items] == [1, 2, 3]
    url = calls[0].full_url
    assert url.startswith("https://api.github.com/repos/o/r/issues?")
    for part in ("state=open", "assignee=none", "labels=good+first+issue", "since=2026-09-01T00%3A00%3A00Z", "per_page=2", "page=1"):
        assert part in url, part

def test_secondary_limit_honors_retry_after_and_logs(caplog):
    import logging
    err = _http_error(403, b'{"message":"You have exceeded a secondary rate limit"}')
    err.headers["Retry-After"] = "7"
    c, calls, slept = make_client([err, FakeResp({"ok": 1})])
    with caplog.at_level(logging.WARNING, logger="oss_scout.github_client"):
        c.rest("x")
    assert slept == [7.0]
    assert any("secondary rate limit" in r.message and "7" in r.message for r in caplog.records)

def test_requests_are_paced_by_min_interval():
    responses = [FakeResp({"ok": 1}), FakeResp({"ok": 2})]
    calls, slept, clock = [], [], {"t": 1000.0}
    def opener(req, timeout=0):
        calls.append(req); return responses.pop(0)
    def sleep(s):
        slept.append(s); clock["t"] += s
    c = GitHubClient("tok", sleep=sleep, opener=opener, now=lambda: clock["t"], min_interval=0.5)
    c.rest("a"); c.rest("b")
    assert slept == [0.5]  # second call waited out the interval; first did not

def test_search_requests_use_their_own_slower_interval():
    responses = [FakeResp({"items": [{"id": 1}]}), FakeResp({"items": [{"id": 2}]}), FakeResp({"ok": 1})]
    slept, clock = [], {"t": 1000.0}
    def opener(req, timeout=0): return responses.pop(0)
    def sleep(s): slept.append(s); clock["t"] += s
    c = GitHubClient("tok", sleep=sleep, opener=opener, now=lambda: clock["t"], min_interval=0.25, search_min_interval=2.0)
    list(c.search_issues("a", pages=1, per_page=1)); list(c.search_repos("b", pages=1, per_page=1)); c.rest("x")
    assert slept == [2.0, 0.25]  # search waits the search gap; a plain request only the ordinary gap

def test_related_prs_returns_every_cross_referenced_pr():
    data = {"data": {"node": {"timelineItems": {"nodes": [
        {"willCloseTarget": True, "source": {"number": 2, "state": "OPEN", "title": "Fix it", "url": "u2"}},
        {"willCloseTarget": False, "source": {"number": 5, "state": "MERGED", "title": "Related", "url": "u5"}},
        {"willCloseTarget": False, "source": {}},
    ]}}}}
    c, _, _ = make_client([FakeResp(data)])
    prs = c.related_prs("I_x")
    assert prs == [{"number": 2, "state": "OPEN", "title": "Fix it", "url": "u2", "will_close": True},
                   {"number": 5, "state": "MERGED", "title": "Related", "url": "u5", "will_close": False}]
