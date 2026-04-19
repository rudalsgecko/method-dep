"""git worktree creation/refresh."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def _git(args: list[str], cwd: Path | None = None) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.stdout


def is_worktree(path: Path) -> bool:
    return path.exists() and (path / ".git").exists()


def create(repo_root: Path, dest: Path, branch: str, *, refresh: bool = False) -> None:
    """Create or refresh a worktree at `dest` pointing at `branch`.

    `branch` may also be a commit/tag. If `dest` already exists and is a
    valid worktree, the call is a no-op unless `refresh=True`.
    """
    if dest.exists():
        if refresh:
            remove(repo_root, dest)
        elif is_worktree(dest):
            return
        else:
            raise RuntimeError(
                f"{dest} exists but is not a worktree; rerun with --refresh"
            )

    dest.parent.mkdir(parents=True, exist_ok=True)
    # `--detach` avoids collisions when multiple worktrees reference the
    # same branch; analysis is read-only so branch identity is irrelevant.
    _git(["worktree", "add", "--detach", str(dest), branch], cwd=repo_root)


def remove(repo_root: Path, dest: Path) -> None:
    """Remove a worktree, tolerating prior manual deletion."""
    if dest.exists():
        try:
            _git(["worktree", "remove", "--force", str(dest)], cwd=repo_root)
        except subprocess.CalledProcessError:
            shutil.rmtree(dest, ignore_errors=True)
    _git(["worktree", "prune"], cwd=repo_root)
