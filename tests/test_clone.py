import subprocess
from pathlib import Path
import pytest
from oss_scout.agents import clone

class R:
    def __init__(self, code=0, err=""): self.returncode, self.stderr, self.stdout = code, err, ""

def test_clone_runs_partial_shallow_clone_into_named_dir(tmp_path):
    calls = []
    def runner(cmd, **kw):
        calls.append(cmd); (tmp_path / "o__r" / ".git").mkdir(parents=True); return R()
    p = clone.ensure_clone("o/r", tmp_path, runner=runner)
    assert p == tmp_path / "o__r"
    assert calls[0][:2] == ["git", "clone"] and "--depth" in calls[0] and "--filter=blob:limit=200k" in calls[0]
    assert calls[0][-2:] == ["https://github.com/o/r", str(tmp_path / "o__r")]

def test_existing_git_dir_is_reused_without_running_git(tmp_path):
    (tmp_path / "o__r" / ".git").mkdir(parents=True)
    calls = []
    clone.ensure_clone("o/r", tmp_path, runner=lambda cmd, **kw: calls.append(cmd) or R())
    assert calls == []

def test_half_finished_dir_is_removed_and_recloned(tmp_path):
    (tmp_path / "o__r").mkdir(); (tmp_path / "o__r" / "junk").write_text("x")
    def runner(cmd, **kw):
        assert not (tmp_path / "o__r").exists()
        (tmp_path / "o__r" / ".git").mkdir(parents=True); return R()
    clone.ensure_clone("o/r", tmp_path, runner=runner)

def test_failure_and_timeout_raise_clone_error(tmp_path):
    with pytest.raises(clone.CloneError):
        clone.ensure_clone("o/r", tmp_path, runner=lambda cmd, **kw: R(128, "fatal: not found"))
    def slow(cmd, **kw): raise subprocess.TimeoutExpired(cmd, 1)
    with pytest.raises(clone.CloneError):
        clone.ensure_clone("o/r", tmp_path, runner=slow)
