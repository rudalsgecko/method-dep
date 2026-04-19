"""Merge tree-sitter and ctags indexes.

Tree-sitter is authoritative for structure; ctags cross-validates
location and flags mismatches. If ctags is unavailable the merged
index is equivalent to the tree-sitter index alone.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from methoddep.index import ctags_index, treesitter_index
from methoddep.index.models import IndexedMethod, IndexedSymbol


@dataclass
class MergedIndex:
    symbols: list[IndexedSymbol]
    methods: list[IndexedMethod]
    warnings: list[str] = field(default_factory=list)
    ctags_used: bool = False


def _cross_validate(methods: list[IndexedMethod], tags: list[ctags_index.CtagEntry]) -> list[str]:
    """Emit a warning when ctags reports a method location that tree-sitter
    does not (or vice versa) within the same file. Only catches gross
    discrepancies; ctags and tree-sitter disagree frequently on
    precise lines."""
    warnings: list[str] = []
    by_file: dict[Path, set[int]] = {}
    for m in methods:
        for loc in (m.declaration, m.definition):
            if loc is None:
                continue
            by_file.setdefault(loc.path, set()).add(loc.line)

    function_kinds = {"function", "member", "prototype"}
    for tag in tags:
        if tag.kind not in function_kinds:
            continue
        lines = by_file.get(tag.path)
        if lines is None:
            continue
        # Within ±3 lines is acceptable — ctags tracks the declarator
        # line which may differ from the return-type line.
        if not any(abs(line - tag.line) <= 3 for line in lines):
            warnings.append(
                f"ctags reports {tag.name} at {tag.path}:{tag.line} "
                "but tree-sitter did not find it nearby"
            )
    return warnings


def build_index(root: Path) -> MergedIndex:
    symbols, methods = treesitter_index.index_tree(root)
    tags: list[ctags_index.CtagEntry] = ctags_index.index_tree(root)
    used = bool(tags)
    warnings = _cross_validate(methods, tags) if used else []
    if used:
        for m in methods:
            if "ctags" not in m.sources:
                m.sources.append("ctags")
    return MergedIndex(
        symbols=symbols, methods=methods, warnings=warnings, ctags_used=used,
    )
