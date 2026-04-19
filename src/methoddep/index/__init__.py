"""Index layer — ctags + tree-sitter symbol discovery (L2+L3)."""

from methoddep.index.models import IndexedMethod, IndexedSymbol, Location
from methoddep.index.merge import build_index

__all__ = ["IndexedMethod", "IndexedSymbol", "Location", "build_index"]
