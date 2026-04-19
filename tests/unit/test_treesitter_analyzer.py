"""L2 tree-sitter analyzer fallback."""

from __future__ import annotations

from pathlib import Path

from methoddep.analyze import analyze_file_l2

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "cpp-samples"


def test_l2_emits_partial_methods_for_interface_impl_cpp() -> None:
    cpp = FIXTURES / "interface_impl" / "src" / "acme" / "foo" / "Bar.cpp"
    methods = analyze_file_l2(cpp)
    names = {m.qualified_name for m in methods}
    assert "foo::Bar::doWork" in names

    dowork = next(m for m in methods if m.qualified_name == "foo::Bar::doWork")
    # L2 now extracts parameter/return type names lexically — Config and Input
    # are referenced in the signature, so they should show up as deps with
    # header=None (header resolution needs L1).
    dep_names = {d.qualified_name for d in dowork.dep_classes}
    assert "Config" in dep_names
    assert "Input" in dep_names
    assert all(d.header is None for d in dowork.dep_classes)
    # L2 still doesn't do call-graph analysis.
    assert dowork.call_graph == []
    assert dowork.sources == ["tree-sitter"]
    assert dowork.definition is not None
    assert dowork.parameters


def test_l2_handles_unparseable_cpp_gracefully(tmp_path: Path) -> None:
    # Create a file with syntactically broken content; tree-sitter
    # always produces *some* parse tree, so we just assert the call
    # returns a list without raising.
    broken = tmp_path / "broken.cpp"
    broken.write_text("class { unterminated\n", encoding="utf-8")
    assert isinstance(analyze_file_l2(broken), list)
