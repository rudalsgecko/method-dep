"""Index merge: works with or without ctags on the host."""

from __future__ import annotations

from pathlib import Path

from methoddep.index import build_index
from methoddep.index import ctags_index

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "cpp-samples"


def test_build_index_returns_methods_even_without_ctags() -> None:
    result = build_index(FIXTURES / "interface_impl")
    assert len(result.methods) >= 4
    if not ctags_index.ctags_available():
        assert result.ctags_used is False
    # Cross-validation should not crash regardless.
    assert isinstance(result.warnings, list)


def test_build_index_marks_ctags_when_present() -> None:
    result = build_index(FIXTURES / "free_functions")
    if ctags_index.ctags_available():
        assert result.ctags_used is True
        assert all("ctags" in m.sources for m in result.methods)
    else:
        assert result.ctags_used is False


def test_ctags_index_returns_empty_when_missing(monkeypatch) -> None:
    monkeypatch.setattr(ctags_index, "ctags_available", lambda: False)
    assert ctags_index.index_tree(FIXTURES / "interface_impl") == []
