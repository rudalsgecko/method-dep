"""Parse MSBuild `/showIncludes` text output.

When `cl.exe` runs with `/showIncludes`, every `#include` directive it
processes is emitted on stderr prefixed with `Note: including file:`
plus leading whitespace indicating nesting depth.

This module extracts, per translation unit:
    - the included files (absolute paths, deduplicated, sorted)
    - the nested include tree (optional; not emitted by default)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


_INCLUDE_RE = re.compile(
    r"^(?P<indent>\s*)Note: including file:\s+(?P<path>.+?)\s*$"
)
# cl.exe prints the TU source name on its own line, before include notes.
_TU_RE = re.compile(r"^(?P<name>[A-Za-z0-9_.\-]+\.(?:cpp|cc|cxx|c\+\+))\s*$")


@dataclass
class ShowIncludesRecord:
    translation_unit: str
    includes: list[str] = field(default_factory=list)


def parse_showincludes_log(text: str) -> list[ShowIncludesRecord]:
    """Parse a raw text dump from `cl.exe /showIncludes`.

    Returns one record per translation unit seen, in source order.
    """
    records: list[ShowIncludesRecord] = []
    current: ShowIncludesRecord | None = None
    seen_for_current: set[str] = set()

    for line in text.splitlines():
        if not line.strip():
            continue

        tu_match = _TU_RE.match(line)
        if tu_match:
            if current is not None:
                current.includes = sorted(seen_for_current)
                records.append(current)
            current = ShowIncludesRecord(translation_unit=tu_match.group("name"))
            seen_for_current = set()
            continue

        inc_match = _INCLUDE_RE.match(line)
        if inc_match and current is not None:
            path = inc_match.group("path").strip()
            seen_for_current.add(_normalize_include_path(path))

    if current is not None:
        current.includes = sorted(seen_for_current)
        records.append(current)

    return records


def _normalize_include_path(path: str) -> str:
    """Normalize paths for deterministic comparison — forward slashes +
    lowercased drive letter on Windows."""
    p = path.replace("\\", "/")
    if len(p) >= 2 and p[1] == ":":
        p = p[0].lower() + p[1:]
    return p


def include_dirs_from_records(records: list[ShowIncludesRecord]) -> list[str]:
    """Collect the deepest common include directories across all records.

    Best-effort: returns parent directories of every included header,
    deduplicated + sorted. Callers use this to feed libclang `-I` flags.
    """
    out: set[str] = set()
    for rec in records:
        for inc in rec.includes:
            parent = str(Path(inc).parent).replace("\\", "/")
            if parent:
                out.add(parent)
    return sorted(out)
