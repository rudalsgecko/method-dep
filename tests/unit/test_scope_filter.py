"""Scope-root filtering — external types (system/SDK/vendor) dropped."""

from __future__ import annotations

from pathlib import Path

from methoddep.analyze import analyze_file

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "cpp-samples"


def test_scope_root_inside_fixture_keeps_everything() -> None:
    root = FIXTURES / "interface_impl"
    cpp = root / "src" / "acme" / "foo" / "Bar.cpp"
    methods = analyze_file(cpp, include_dirs=[root / "include"], scope_root=root)
    dowork = next(m for m in methods if m.qualified_name == "foo::Bar::doWork")
    dep_names = {d.qualified_name for d in dowork.dep_classes}
    assert "foo::IService" in dep_names  # internal → kept


def test_scope_root_excludes_project_treats_deps_as_external() -> None:
    root = FIXTURES / "interface_impl"
    cpp = root / "src" / "acme" / "foo" / "Bar.cpp"
    # Point scope_root at an unrelated directory — nothing should qualify.
    methods = analyze_file(
        cpp,
        include_dirs=[root / "include"],
        scope_root=FIXTURES / "free_functions",  # different subtree
    )
    dowork = next(m for m in methods if m.qualified_name == "foo::Bar::doWork")
    # IService is defined inside interface_impl/include, which is outside
    # the scope_root we passed. It must NOT appear.
    assert not any(d.qualified_name == "foo::IService" for d in dowork.dep_classes)
    # Call graph targets whose definition is out-of-scope must also drop.
    assert not any(c.target.startswith("foo::IService::") for c in dowork.call_graph)


def test_scope_root_none_defaults_to_workspace_root() -> None:
    root = FIXTURES / "interface_impl"
    cpp = root / "src" / "acme" / "foo" / "Bar.cpp"
    # Omit scope_root — should fall through to workspace_root behavior.
    methods = analyze_file(
        cpp,
        include_dirs=[root / "include"],
        workspace_root=root,
    )
    dowork = next(m for m in methods if m.qualified_name == "foo::Bar::doWork")
    assert any(d.qualified_name == "foo::IService" for d in dowork.dep_classes)
