from __future__ import annotations
import base64
import json
import logging
import subprocess
import time
import urllib.parse
import urllib.request
from typing import Any, Callable, Iterator
from urllib.error import HTTPError

log = logging.getLogger(__name__)
API = "https://api.github.com"
DEFAULT_SECONDARY_SLEEP = 60.0
MAX_5XX_RETRIES = 3


class GitHubError(Exception):
    pass


def github_token() -> str:
    out = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, check=True)
    return out.stdout.strip()


class GitHubClient:
    """Read-only GitHub client. Stage 1 never sends a mutation."""

    def __init__(self, token: str, sleep: Callable[[float], None] = time.sleep,
                 opener: Callable = urllib.request.urlopen, now: Callable[[], float] = time.time,
                 timeout: float = 30.0, min_interval: float = 0.0, search_min_interval: float | None = None):
        self.token = token
        self.sleep = sleep
        self.opener = opener
        self.now = now
        self.timeout = timeout
        self.min_interval = min_interval
        self.search_min_interval = search_min_interval if search_min_interval is not None else min_interval
        self._last_request: float | None = None
        self.requests_made = 0

    def _pace(self, search: bool = False) -> None:
        interval = self.search_min_interval if search else self.min_interval
        if interval and self._last_request is not None:
            gap = self._last_request + interval - self.now()
            if gap > 0:
                self.sleep(gap)
        self._last_request = self.now()
        self.requests_made += 1
        if self.requests_made % 100 == 0:
            log.info("github requests made: %d", self.requests_made)

    # ---- low level -------------------------------------------------------
    def _request(self, method: str, url: str, body: bytes | None = None, search: bool = False) -> tuple[Any, dict]:
        retries_5xx = 0
        while True:
            self._pace(search)
            req = urllib.request.Request(url, data=body, method=method, headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "oss-scout",
                **({"Content-Type": "application/json"} if body else {}),
            })
            try:
                with self.opener(req, timeout=self.timeout) as resp:
                    raw = resp.read()
                    headers = {k: v for k, v in resp.headers.items()}
                    return (json.loads(raw) if raw else None), headers
            except HTTPError as e:
                status = e.code
                try:
                    msg = json.loads(e.read() or b"{}").get("message", "")
                except Exception:
                    msg = ""
                if status in (403, 429) and ("rate limit" in msg.lower() or e.headers.get("X-RateLimit-Remaining") == "0"):
                    retry_after = e.headers.get("Retry-After")
                    reset = e.headers.get("X-RateLimit-Reset")
                    if retry_after:
                        wait = max(1.0, float(retry_after))
                    elif reset:
                        wait = max(1.0, float(reset) - self.now() + 1.0)
                    else:
                        wait = DEFAULT_SECONDARY_SLEEP
                    log.warning("GitHub %s on %s: %s; sleeping %.0fs", status, url.split("?")[0], msg, wait)
                    self.sleep(wait)
                    continue
                if 500 <= status < 600 and retries_5xx < MAX_5XX_RETRIES:
                    retries_5xx += 1
                    self.sleep(2.0 ** retries_5xx)
                    continue
                raise GitHubError(f"{method} {url} -> {status} {msg}") from e

    def rest(self, path: str, params: dict | None = None) -> tuple[Any, dict]:
        url = f"{API}/{path.lstrip('/')}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        return self._request("GET", url, search=path.startswith("search/"))

    def graphql(self, query: str, variables: dict) -> dict:
        body = json.dumps({"query": query, "variables": variables}).encode()
        data, _ = self._request("POST", f"{API}/graphql", body)
        # A partial response (data plus NOT_FOUND errors for some aliases) is still usable.
        if not data or data.get("data") is None:
            raise GitHubError(f"graphql: {data.get('errors') if data else 'empty response'}")
        return data["data"]

    # ---- high level ------------------------------------------------------
    def search_issues(self, q: str, pages: int, per_page: int = 100) -> Iterator[dict]:
        for page in range(1, pages + 1):
            data, _ = self.rest("search/issues", {"q": q, "per_page": per_page, "page": page, "advanced_search": "true"})
            items = data.get("items", []) if data else []
            yield from items
            if len(items) < per_page:
                return

    def search_repos(self, q: str, pages: int, per_page: int = 100, sort: str = "stars") -> Iterator[dict]:
        for page in range(1, pages + 1):
            data, _ = self.rest("search/repositories", {"q": q, "sort": sort, "order": "desc", "per_page": per_page, "page": page})
            items = data.get("items", []) if data else []
            yield from items
            if len(items) < per_page:
                return

    def list_issues(self, owner: str, name: str, label: str | None, since: str, pages: int = 2, per_page: int = 100) -> Iterator[dict]:
        """Open, unassigned issues, optionally carrying one label, updated since the cutoff. Items share the search-item shape."""
        for page in range(1, pages + 1):
            params = {"state": "open", "assignee": "none", "since": since, "per_page": per_page, "page": page}
            if label:
                params["labels"] = label
            data, _ = self.rest(f"repos/{owner}/{name}/issues", params)
            items = data or []
            yield from items
            if len(items) < per_page:
                return

    REPO_SIGNALS_QUERY = """
    query($owner:String!,$name:String!){
      repository(owner:$owner,name:$name){
        stargazerCount pushedAt description primaryLanguage{name}
        pullRequests(last:30,states:MERGED){ nodes{ authorAssociation mergedAt } }
        issues(last:20,states:[OPEN,CLOSED]){ nodes{ createdAt comments(first:5){ nodes{ createdAt authorAssociation } } } }
      }
    }"""

    def repo_signals(self, owner: str, name: str) -> dict:
        data = self.graphql(self.REPO_SIGNALS_QUERY, {"owner": owner, "name": name})
        r = data["repository"]
        if r is None:
            raise GitHubError(f"repository {owner}/{name} not found")
        return {
            "stars": r["stargazerCount"],
            "pushed_at": r["pushedAt"],
            "description": r.get("description") or "",
            "language": (r.get("primaryLanguage") or {}).get("name"),
            "merged_prs": r["pullRequests"]["nodes"],
            "issues": [{"createdAt": i["createdAt"], "comments": i["comments"]["nodes"]} for i in r["issues"]["nodes"]],
        }

    BASICS_BATCH = 50

    def repo_basics(self, names: list[str]) -> dict[str, dict | None]:
        """Stars and push date for many repos in one call per 50. None for a repo that no longer exists."""
        out: dict[str, dict | None] = {}
        names = list(names)
        for start in range(0, len(names), self.BASICS_BATCH):
            chunk = names[start:start + self.BASICS_BATCH]
            parts = []
            for i, full in enumerate(chunk):
                owner, name = full.split("/", 1)
                parts.append(f'r{i}: repository(owner:"{owner}", name:"{name}"){{ nameWithOwner stargazerCount pushedAt }}')
            data = self.graphql("query { " + " ".join(parts) + " }", {})
            for i, full in enumerate(chunk):
                r = data.get(f"r{i}")
                out[full] = {"stars": r["stargazerCount"], "pushed_at": r["pushedAt"]} if r else None
        return out

    CLOSING_PR_QUERY = """
    query($id:ID!){ node(id:$id){ ... on Issue {
      timelineItems(itemTypes:[CROSS_REFERENCED_EVENT],last:20){ nodes{ ... on CrossReferencedEvent {
        willCloseTarget source{ ... on PullRequest { number state title url } } } } } } } }"""

    def related_prs(self, issue_node_id: str) -> list[dict]:
        """Every PR cross-referenced from the issue's timeline, open or closed."""
        data = self.graphql(self.CLOSING_PR_QUERY, {"id": issue_node_id})
        nodes = (data.get("node") or {}).get("timelineItems", {}).get("nodes", [])
        out = []
        for n in nodes:
            src = n.get("source") or {}
            if "number" not in src:
                continue
            out.append({"number": src["number"], "state": src.get("state"), "title": src.get("title"),
                        "url": src.get("url"), "will_close": bool(n.get("willCloseTarget"))})
        return out

    def has_closing_pr(self, issue_node_id: str) -> bool:
        return any(p["will_close"] and p["state"] == "OPEN" for p in self.related_prs(issue_node_id))

    def contributing_text(self, owner: str, name: str) -> str | None:
        for path in ("CONTRIBUTING.md", ".github/CONTRIBUTING.md"):
            try:
                data, _ = self.rest(f"repos/{owner}/{name}/contents/{path}")
            except GitHubError as e:
                if "-> 404" in str(e):
                    continue
                raise
            if data and data.get("encoding") == "base64":
                return base64.b64decode(data["content"]).decode("utf-8", errors="replace")
        return None
