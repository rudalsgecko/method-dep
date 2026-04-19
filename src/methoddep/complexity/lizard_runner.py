"""lizard-based complexity analyzer.

Runs `lizard` as a Python library (no subprocess). Produces a map from
function signature to Complexity; callers match by signature first, then
line range.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import lizard


@dataclass(frozen=True)
class Complexity:
    cyclomatic: int
    nloc: int
    token_count: int
    parameter_count: int
    start_line: int
    end_line: int
    name: str
    long_name: str
    source: Literal["lizard"] = "lizard"


def analyze_file(path: Path) -> list[Complexity]:
    """Return per-function complexity records for a single file.

    Returns an empty list for files lizard can't parse.
    """
    try:
        result = lizard.analyze_file(str(path))
    except Exception:
        return []
    out: list[Complexity] = []
    for fn in result.function_list:
        out.append(
            Complexity(
                cyclomatic=fn.cyclomatic_complexity,
                nloc=fn.nloc,
                token_count=fn.token_count,
                parameter_count=len(fn.parameters or []),
                start_line=fn.start_line,
                end_line=fn.end_line,
                name=fn.name,
                long_name=fn.long_name,
            )
        )
    return out


def _bare_name(qualified: str) -> str:
    """Lizard emits fully-qualified names; reduce to the trailing
    identifier for comparison with tree-sitter's bare method name."""
    return qualified.rsplit("::", 1)[-1]


def find_match(
    complexities: list[Complexity],
    *,
    name: str,
    class_name: str | None = None,
    definition_line: int | None = None,
) -> Complexity | None:
    """Match a Complexity record against a known method.

    Lizard-reported names are fully qualified (e.g., `svc::Pipeline::process`).
    Match priority:
    1) `long_name` contains `Class::method`.
    2) `name` ends with `::method` (or equals `method` for free functions)
       and `definition_line` is inside the function's line range.
    3) Any trailing-name match when only one candidate is left.
    """
    candidates = list(complexities)
    if class_name:
        bare_class = class_name.rsplit("::", 1)[-1]
        suffix = f"::{bare_class}::{name}"
        for c in candidates:
            # Either `long_name` has `Class::method` or `name` ends with it.
            if suffix in f"::{c.name}" or suffix in f"::{c.long_name}":
                return c
            if c.name == f"{bare_class}::{name}" or c.name.endswith(f"::{bare_class}::{name}"):
                return c
    # Range match against trailing name
    if definition_line is not None:
        ranged = [
            c for c in candidates
            if _bare_name(c.name) == name
            and c.start_line <= definition_line <= c.end_line + 1
        ]
        if ranged:
            return ranged[0]
    # Trailing-name fallback
    named = [c for c in candidates if _bare_name(c.name) == name]
    if len(named) == 1:
        return named[0]
    return None
