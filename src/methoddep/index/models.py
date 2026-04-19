"""Data classes shared across the index layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class Location:
    path: Path
    line: int
    column: int = 1

    def as_rel(self, root: Path) -> str:
        try:
            return f"{self.path.relative_to(root).as_posix()}:{self.line}"
        except ValueError:
            return f"{self.path.as_posix()}:{self.line}"


SymbolKind = Literal[
    "namespace",
    "class",
    "struct",
    "enum",
    "method_decl",
    "method_def",
    "free_function",
    "field",
]


@dataclass
class IndexedSymbol:
    name: str
    qualified_name: str
    kind: SymbolKind
    location: Location
    parent_class: str | None = None
    namespace: str | None = None


@dataclass
class IndexedMethod:
    """A method (declaration + optional definition) with enough info to
    locate it in source.

    Populated primarily by tree-sitter; ctags serves as a cross-check.
    """

    qualified_name: str
    signature: str
    parameters: list[dict[str, str]]
    return_type: str | None
    class_name: str | None
    namespace: str | None
    declaration: Location | None = None
    definition: Location | None = None
    is_virtual: bool = False
    is_pure: bool = False
    is_static: bool = False
    is_const: bool = False
    access: Literal["public", "protected", "private"] = "public"
    defined_in_header: bool = False
    sources: list[str] = field(default_factory=list)  # "tree-sitter" / "ctags"
