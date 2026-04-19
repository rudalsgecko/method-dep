"""Symlink-tree workspace strategy.

Builds a mirror directory whose files are symlinks back to the source
repo. On Windows, directory junctions are used for efficiency (no admin
rights required). File-level symlinks on Windows require developer mode;
if they fail, the caller (composer) falls back to copy-tree.
"""

from __future__ import annotations

import os
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
        if target.exists() or target.is_symlink():
            if refresh:
                target.unlink()
            else:
                continue
        try:
            os.symlink(src_file, target)
        except OSError:
            # Bubble up so composer can fall back to copy-tree.
            raise
