"""Shared glob filtering used by both symlink-tree and copy-tree."""

from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Iterator


def _expand_pattern(pattern: str) -> list[str]:
    """Expand a methoddep-style glob into fnmatch-compatible patterns.

    "src/acme/**" -> matches anything under src/acme. We produce two
    patterns: one for the recursive case, one for the exact path.
    """
    normalized = pattern.replace("\\", "/").rstrip("/")
    if normalized.endswith("/**"):
        base = normalized[:-3]
        return [f"{base}/*", f"{base}/**"]
    if normalized.endswith("**"):
        base = normalized[:-2]
        return [f"{base}*", f"{base}**"]
    return [normalized]


def _match_any(rel_posix: str, expanded: list[str]) -> bool:
    for pat in expanded:
        if fnmatch.fnmatchcase(rel_posix, pat):
            return True
        # fnmatch doesn't treat "**" specially, so also try prefix match
        # for the "base/**" case.
        if pat.endswith("/**") and rel_posix.startswith(pat[:-3] + "/"):
            return True
        if pat.endswith("**") and rel_posix.startswith(pat[:-2]):
            return True
    return False


def iter_matching_files(
    root: Path, globs: tuple[str, ...]
) -> Iterator[Path]:
    """Yield files under `root` matching any of the given glob patterns.

    Skips `.git` and the worktree output directory by default.
    """
    expanded: list[str] = []
    for g in globs:
        expanded.extend(_expand_pattern(g))

    root = root.resolve()
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        parts = path.relative_to(root).parts
        if parts and parts[0] == ".git":
            continue
        rel = "/".join(parts)
        if _match_any(rel, expanded):
            yield path
