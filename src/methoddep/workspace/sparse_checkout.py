"""sparse-checkout configuration for the composed worktree."""

from __future__ import annotations

import subprocess
from pathlib import Path


def _git(args: list[str], cwd: Path) -> str:
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


def _globs_are_cone_friendly(globs: tuple[str, ...]) -> bool:
    """Cone mode only supports directory-prefix patterns. Any `**` past
    the first segment or `?`/`[` disqualifies cone."""
    for g in globs:
        if "?" in g or "[" in g:
            return False
        if "**" in g and not g.endswith("/**") and not g.endswith("**"):
            return False
    return True


def configure(worktree: Path, globs: tuple[str, ...]) -> None:
    """Enable sparse-checkout and apply the given glob patterns.

    Uses `--cone` mode when patterns are cone-friendly (faster on
    Windows); falls back to non-cone when the patterns use wildcards.
    """
    cone = _globs_are_cone_friendly(globs)
    _git(
        [
            "sparse-checkout",
            "init",
            "--cone" if cone else "--no-cone",
        ],
        cwd=worktree,
    )

    if cone:
        # Cone mode takes directory prefixes without trailing globs.
        dirs = sorted({_strip_glob_suffix(g) for g in globs})
        _git(["sparse-checkout", "set", *dirs], cwd=worktree)
    else:
        _git(["sparse-checkout", "set", *globs], cwd=worktree)


def _strip_glob_suffix(glob: str) -> str:
    # "include/**" -> "include"; "src/acme/**/*.cpp" -> "src/acme".
    out = glob
    for suffix in ("/**/*.cpp", "/**/*.h", "/**/*", "/**"):
        if out.endswith(suffix):
            out = out[: -len(suffix)]
            break
    return out
