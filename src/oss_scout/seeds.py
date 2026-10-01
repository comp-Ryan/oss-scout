"""Seed lists: places that point at repos worth a look. Each parser returns [{full_name, label?}].
Everything is cached for a day so the hourly run costs these sites nothing."""
from __future__ import annotations
import io
import json
import re
import tarfile
import time
import urllib.request
from pathlib import Path
from oss_scout.config import Config

GITTREND_URL = "https://gittrend.io/{page}"
GFI_URL = "https://raw.githubusercontent.com/DeepSourceCorp/good-first-issue/master/data/repositories.toml"
AWESOME_URL = "https://raw.githubusercontent.com/MunGell/awesome-for-beginners/main/README.md"
UFG_TARBALL = "https://codeload.github.com/up-for-grabs/up-for-grabs.net/tar.gz/gh-pages"

_SLUG = r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+"


def default_fetcher(url: str, timeout: float = 30.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "oss-scout (personal issue finder)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _dedupe(rows: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out = []
    for r in rows:
        key = r["full_name"].lower()
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def parse_gittrend(html: str) -> list[dict]:
    return _dedupe([{"full_name": m} for m in re.findall(rf'href="/repo/({_SLUG})"', html)])


def parse_good_first_issue(toml_text: str) -> list[dict]:
    return _dedupe([{"full_name": m} for m in re.findall(rf"github\.com/({_SLUG})", toml_text)])


def parse_awesome_for_beginners(md: str) -> list[dict]:
    rows = []
    for m in re.finditer(rf"\[[^\]]+\]\(https://github\.com/({_SLUG})/?\)\s*_\(label:\s*([^)]+)\)_", md):
        rows.append({"full_name": m.group(1), "label": m.group(2).strip()})
    return _dedupe(rows)


def parse_up_for_grabs(tarball: bytes) -> list[dict]:
    rows = []
    with tarfile.open(fileobj=io.BytesIO(tarball), mode="r:gz") as t:
        for member in t.getmembers():
            if "/_data/projects/" not in member.name or not member.name.endswith((".yml", ".yaml")):
                continue
            text = t.extractfile(member).read().decode("utf-8", errors="replace")
            site = re.search(rf"^site:\s*https://github\.com/({_SLUG})", text, re.M)
            if not site:
                continue
            label = re.search(r"^upforgrabs:\s*\n\s+name:\s*(.+)$", text, re.M)
            rows.append({"full_name": site.group(1).rstrip("/"), "label": label.group(1).strip() if label else None})
    return _dedupe(rows)


def _source(name: str, cfg: Config, fetcher) -> list[dict]:
    if name == "gittrend":
        rows = []
        for page in cfg.gittrend_pages:
            rows += parse_gittrend(fetcher(GITTREND_URL.format(page=page)).decode("utf-8", errors="replace"))
        return _dedupe(rows)
    if name == "good_first_issue":
        return parse_good_first_issue(fetcher(GFI_URL).decode("utf-8", errors="replace"))
    if name == "awesome_for_beginners":
        return parse_awesome_for_beginners(fetcher(AWESOME_URL).decode("utf-8", errors="replace"))
    if name == "up_for_grabs":
        return parse_up_for_grabs(fetcher(UFG_TARBALL))
    raise ValueError(f"unknown seed source {name}")


def fetch_all(cfg: Config, *, fetcher=default_fetcher, now: float | None = None, log=None) -> list[dict]:
    """Every seed repo from every configured source, each tagged source='seed:<name>'. One failing source is skipped."""
    now = now if now is not None else time.time()
    cfg.seeds_dir.mkdir(parents=True, exist_ok=True)
    out: list[dict] = []
    for name in cfg.seed_sources:
        cache = cfg.seeds_dir / f"{name}.json"
        rows = None
        if cache.exists():
            try:
                blob = json.loads(cache.read_text())
                if now - blob.get("fetched_at", 0) < cfg.seed_refresh_hours * 3600:
                    rows = blob["rows"]
            except (ValueError, KeyError):
                rows = None
        if rows is None:
            try:
                rows = _source(name, cfg, fetcher)
                cache.write_text(json.dumps({"fetched_at": now, "rows": rows}))
            except Exception as e:
                if log:
                    log(f"seed source {name} failed: {e}")
                continue
        out += [{**r, "source": f"seed:{name}"} for r in rows]
    return _dedupe(out)
