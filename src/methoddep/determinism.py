"""Determinism helpers — sorting, path normalization, JSON serialization.

Single source of truth for all emit-time ordering rules described in
the plan's §Determinism Contract.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path, PureWindowsPath, PurePosixPath


_WIN_LONG_PATH_PREFIX = "\\\\?\\"
_WIN_LONG_UNC_PREFIX = "\\\\?\\UNC\\"


def normalize_path_for_fingerprint(path: Path | str) -> str:
    r"""Normalize a filesystem path for input_fingerprint purposes.

    Windows: strip ``\\?\`` / ``\\?\UNC\`` prefixes, convert to posix
    separators, lowercase. POSIX: posix separators, preserve case.
    """
    p = str(path)
    if os.name == "nt":
        if p.startswith(_WIN_LONG_UNC_PREFIX):
            p = "\\\\" + p[len(_WIN_LONG_UNC_PREFIX):]
        elif p.startswith(_WIN_LONG_PATH_PREFIX):
            p = p[len(_WIN_LONG_PATH_PREFIX):]
        posix = PureWindowsPath(p).as_posix().lower()
    else:
        posix = PurePosixPath(p).as_posix()
    return posix


def path_normalization_tag() -> str:
    return "win-lower-relative" if os.name == "nt" else "posix-relative"


def compute_input_fingerprint(paths: list[Path], workspace_root: Path) -> str:
    """SHA-256 hash over the sorted list of (normalized_relative_path, sha256(content))."""
    entries: list[tuple[str, str]] = []
    root = workspace_root.resolve()
    for raw in paths:
        abspath = Path(raw).resolve()
        try:
            rel = abspath.relative_to(root)
        except ValueError:
            continue  # external (system / SDK) header — represented via tool_versions.
        normalized = normalize_path_for_fingerprint(rel)
        content_hash = hashlib.sha256(abspath.read_bytes()).hexdigest()
        entries.append((normalized, content_hash))
    entries.sort()

    outer = hashlib.sha256()
    for normalized, h in entries:
        outer.update(normalized.encode("utf-8"))
        outer.update(b"\x00")
        outer.update(h.encode("ascii"))
        outer.update(b"\x0a")
    return outer.hexdigest()


def dump_json(obj: object) -> str:
    """Canonical JSON serialization."""
    return json.dumps(
        obj,
        sort_keys=True,
        indent=2,
        ensure_ascii=False,
        separators=(",", ": "),
    )


def write_json_deterministically(path: Path, obj: object) -> None:
    text = dump_json(obj)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Always LF line endings, UTF-8 no BOM.
    path.write_bytes(text.encode("utf-8") + b"\n")


def sort_sources(sources: list[Path]) -> list[Path]:
    """Deterministic file iteration order."""
    return sorted(sources, key=lambda p: p.as_posix().lower() if sys.platform.startswith("win") else p.as_posix())
