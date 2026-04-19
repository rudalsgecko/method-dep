"""Copy-tree fallback — file-level copy of matched sources."""

from __future__ import annotations

import shutil
from pathlib import Path

from methoddep.workspace._filter import iter_matching_files


def build(repo_root: Path, dest: Path, globs: tuple[str, ...], *, refresh: bool = False) -> None:
    if dest.exists() and refresh:
        shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True, exist_ok=True)

    for src_file in iter_matching_files(repo_root, globs):
        rel = src_file.relative_to(repo_root)
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and not refresh:
            continue
        shutil.copy2(src_file, target)
