"""tree-sitter index: verify it picks up method decls and defs in the
fixture projects."""

from __future__ import annotations

from pathlib import Path

import pytest

from methoddep.index.treesitter_index import index_tree, parse_file

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "cpp-samples"


def _qualified_names(methods) -> set[str]:
    return {m.qualified_name for m in methods}


def test_interface_impl_header_finds_methods() -> None:
    header = FIXTURES / "interface_impl" / "include" / "foo" / "Bar.h"
    symbols, methods = parse_file(header)
    names = _qualified_names(methods)
    assert "foo::Bar::Bar" in names
    assert "foo::Bar::doWork" in names
    # IService is pure virtual — should also appear.
    iservice = FIXTURES / "interface_impl" / "include" / "foo" / "IService.h"
    _, iservice_methods = parse_file(iservice)
    inames = _qualified_names(iservice_methods)
    assert "foo::IService::fetch" in inames
    assert "foo::IService::commit" in inames
    # Pure virtual flag should trip for at least one of them.
    assert any(m.is_pure for m in iservice_methods if m.qualified_name.endswith("fetch"))


def test_interface_impl_cpp_finds_definitions() -> None:
    cpp = FIXTURES / "interface_impl" / "src" / "acme" / "foo" / "Bar.cpp"
    _, methods = parse_file(cpp)
    names = _qualified_names(methods)
    assert "foo::Bar::doWork" in names
    # Definition should have `definition` set, not `declaration`.
    doWork = next(m for m in methods if m.qualified_name == "foo::Bar::doWork")
    assert doWork.definition is not None
    assert doWork.declaration is None


def test_free_functions_fixture() -> None:
    header = FIXTURES / "free_functions" / "include" / "util" / "Hash.h"
    _, methods = parse_file(header)
    names = _qualified_names(methods)
    assert "util::hash" in names
    assert "util::clamp" in names


def test_templated_fixture() -> None:
    header = FIXTURES / "templated" / "include" / "tpl" / "Container.h"
    _, methods = parse_file(header)
    names = _qualified_names(methods)
    # Template methods — at least `add`, `size`, `at`, and `pair_sum`.
    assert any(n.endswith("::add") for n in names)
    assert any(n.endswith("::size") for n in names)
    assert any(n.endswith("::at") for n in names)
    assert any(n.endswith("::pair_sum") for n in names)


def test_index_tree_covers_interface_impl() -> None:
    root = FIXTURES / "interface_impl"
    symbols, methods = index_tree(root)
    names = _qualified_names(methods)
    # Header decls + two cpp definitions for Bar::doWork.
    doWork_entries = [m for m in methods if m.qualified_name == "foo::Bar::doWork"]
    assert len(doWork_entries) >= 2  # header decl + at least one cpp def
    assert "foo::IService::fetch" in names
    # Classes / structs must show up as symbols.
    classes = {s.qualified_name for s in symbols if s.kind in {"class", "struct"}}
    assert "foo::Bar" in classes
    assert "foo::Config" in classes
    assert "foo::IService" in classes


def test_with_deps_fixture_symbols() -> None:
    root = FIXTURES / "with_deps"
    symbols, methods = index_tree(root)
    names = _qualified_names(methods)
    assert "svc::Pipeline::process" in names
    classes = {s.qualified_name for s in symbols if s.kind in {"class", "struct"}}
    assert "svc::Pipeline" in classes
    assert "svc::Cache" in classes
    assert "svc::Reporter" in classes
