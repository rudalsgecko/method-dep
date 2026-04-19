"""ctags-based symbol index (L3).

Uses Universal Ctags with JSON output (`--output-format=json`) when the
binary is available. When ctags is absent or returns an error, the
module returns an empty index — callers treat it as optional
cross-validation data.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CtagEntry:
    name: str
    path: Path
    line: int
    kind: str
    scope: str | None
    signature: str | None


def ctags_available() -> bool:
    exe = shutil.which("ctags")
    if not exe:
        return False
    try:
        proc = subprocess.run(
            [exe, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return "Universal Ctags" in (proc.stdout or "")


def index_tree(root: Path) -> list[CtagEntry]:
    if not ctags_available():
        return []
    exe = shutil.which("ctags")
    assert exe is not None
    try:
        proc = subprocess.run(
            [
                exe,
                "--languages=C++",
                "--output-format=json",
                "--fields=+neKSt",
                "--c++-kinds=+p",
                "--recurse",
                str(root),
            ],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0:
        return []

    entries: list[CtagEntry] = []
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("_type") != "tag":
            continue
        path_str = rec.get("path")
        if not path_str:
            continue
        entries.append(
            CtagEntry(
                name=rec.get("name", ""),
                path=Path(path_str),
                line=int(rec.get("line", 0) or 0),
                kind=rec.get("kind", ""),
                scope=rec.get("scope"),
                signature=rec.get("signature"),
            )
        )
    return entries
