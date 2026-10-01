from __future__ import annotations
import shutil
import subprocess
from pathlib import Path


class CloneError(Exception):
    pass


def clone_dir(full_name: str, clones_dir: Path) -> Path:
    return Path(clones_dir) / full_name.replace("/", "__")


def ensure_clone(full_name: str, clones_dir: Path, runner=subprocess.run, timeout: float = 180.0) -> Path:
    """Shallow partial clone, reused when it already exists. A directory without .git is a crashed clone: redo it."""
    target = clone_dir(full_name, clones_dir)
    if (target / ".git").exists():
        return target
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["git", "clone", "--depth", "1", "--filter=blob:limit=200k", "--quiet",
           f"https://github.com/{full_name}", str(target)]
    try:
        r = runner(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        shutil.rmtree(target, ignore_errors=True)
        raise CloneError(f"clone of {full_name} timed out after {timeout}s") from e
    if r.returncode != 0:
        shutil.rmtree(target, ignore_errors=True)
        raise CloneError(f"clone of {full_name} failed: {(r.stderr or '')[-300:]}")
    return target
