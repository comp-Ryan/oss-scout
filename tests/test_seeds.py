import io, json, tarfile, time
from pathlib import Path
from oss_scout import seeds
from oss_scout.config import Config

GITTREND_HTML = '''<a href="/repo/dmtrKovalenko/fframes">fframes</a> <a href="/repo/ninjahawk/livenerf">x</a>
<a href="/trending/ai-agent">topic</a> <a href="/repo/dmtrKovalenko/fframes">again</a>'''
GFI_TOML = """repositories = [
  'github.com/4paradigm/OpenMLDB',
  'github.com/abpframework/abp',
]
"""
AWESOME_MD = """# Awesome
- [Legerity](https://github.com/MADE-Apps/legerity) _(label: good first issue)_ <br> A framework.
- [MvvmCross](https://github.com/MvvmCross/MvvmCross) _(label: first-timers-only)_ <br> MVVM.
- [NotGitHub](https://gitlab.com/x/y) _(label: good first issue)_
"""

def _ufg_tarball() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for name, body in (("up-for-grabs.net-gh-pages/_data/projects/a.yml", "name: A\nsite: https://github.com/josharsh/100LinesOfCode\nupforgrabs:\n  name: good first issue\n  link: x\n"),
                           ("up-for-grabs.net-gh-pages/_data/projects/b.yml", "name: B\nsite: https://example.org/not-github\nupforgrabs:\n  name: help wanted\n")):
            data = body.encode(); ti = tarfile.TarInfo(name); ti.size = len(data); t.addfile(ti, io.BytesIO(data))
    return buf.getvalue()

def test_parsers():
    assert seeds.parse_gittrend(GITTREND_HTML) == [{"full_name": "dmtrKovalenko/fframes"}, {"full_name": "ninjahawk/livenerf"}]
    assert seeds.parse_good_first_issue(GFI_TOML) == [{"full_name": "4paradigm/OpenMLDB"}, {"full_name": "abpframework/abp"}]
    assert seeds.parse_awesome_for_beginners(AWESOME_MD) == [{"full_name": "MADE-Apps/legerity", "label": "good first issue"}, {"full_name": "MvvmCross/MvvmCross", "label": "first-timers-only"}]
    assert seeds.parse_up_for_grabs(_ufg_tarball()) == [{"full_name": "josharsh/100LinesOfCode", "label": "good first issue"}]

def test_fetch_all_uses_a_daily_cache(tmp_path):
    calls = []
    def fetcher(url):
        calls.append(url)
        if "gittrend.io" in url: return GITTREND_HTML.encode()
        if "good-first-issue" in url: return GFI_TOML.encode()
        if "awesome-for-beginners" in url: return AWESOME_MD.encode()
        if "codeload" in url: return _ufg_tarball()
        raise AssertionError(url)
    cfg = Config(data_dir=tmp_path)
    out = seeds.fetch_all(cfg, fetcher=fetcher, now=1000.0)
    names = {r["full_name"] for r in out}
    assert {"dmtrKovalenko/fframes", "4paradigm/OpenMLDB", "MADE-Apps/legerity", "josharsh/100LinesOfCode"} <= names
    assert all(r["source"].startswith("seed:") for r in out)
    n = len(calls)
    out2 = seeds.fetch_all(cfg, fetcher=fetcher, now=1000.0 + 3600)
    assert len(calls) == n and len(out2) == len(out)  # cached
    seeds.fetch_all(cfg, fetcher=fetcher, now=1000.0 + 25 * 3600)
    assert len(calls) > n  # refreshed after a day

def test_one_broken_source_does_not_break_the_rest(tmp_path):
    def fetcher(url):
        if "gittrend.io" in url: raise RuntimeError("down")
        if "good-first-issue" in url: return GFI_TOML.encode()
        if "awesome-for-beginners" in url: return AWESOME_MD.encode()
        return _ufg_tarball()
    out = seeds.fetch_all(Config(data_dir=tmp_path, seed_sources=["gittrend", "good_first_issue"]), fetcher=fetcher, now=1.0)
    assert {r["full_name"] for r in out} == {"4paradigm/OpenMLDB", "abpframework/abp"}
