"""libclang analyzer — exercise the fixture projects."""

from __future__ import annotations

from pathlib import Path

from methoddep.analyze import analyze_file

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "cpp-samples"


def _by_name(methods, name: str):
    matches = [m for m in methods if m.qualified_name == name]
    assert matches, f"no method named {name!r} — got {[m.qualified_name for m in methods]}"
    return matches[0]


def test_analyze_interface_impl_acme() -> None:
    cpp = FIXTURES / "interface_impl" / "src" / "acme" / "foo" / "Bar.cpp"
    inc = FIXTURES / "interface_impl" / "include"
    methods = analyze_file(cpp, include_dirs=[inc], workspace_root=FIXTURES / "interface_impl")
    names = {m.qualified_name for m in methods}
    assert "foo::Bar::doWork" in names

    doWork = _by_name(methods, "foo::Bar::doWork")
    # Dependencies: IService (call_target), Config (parameter), Input (parameter).
    dep_names = {d.qualified_name for d in doWork.dep_classes}
    assert "foo::IService" in dep_names
    # Interface detection — IService is pure-virtual only.
    iservice = next(d for d in doWork.dep_classes if d.qualified_name == "foo::IService")
    assert iservice.is_interface is True
    assert "fetch" in iservice.used_methods
    assert "commit" in iservice.used_methods

    # Ordered call graph should include fetch before commit.
    targets = [c.target for c in doWork.call_graph if c.target.startswith("foo::IService::")]
    assert targets.index("foo::IService::fetch") < targets.index("foo::IService::commit")


def test_analyze_free_functions() -> None:
    cpp = FIXTURES / "free_functions" / "src" / "acme" / "util" / "Hash.cpp"
    inc = FIXTURES / "free_functions" / "include"
    methods = analyze_file(cpp, include_dirs=[inc])
    names = {m.qualified_name for m in methods}
    assert "util::hash" in names
    assert "util::clamp" in names

    clamp = _by_name(methods, "util::clamp")
    assert clamp.return_type == "int"
    assert [p.type for p in clamp.parameters] == ["int", "int", "int"]


def test_analyze_with_deps_detects_globals_and_static_local() -> None:
    cpp = FIXTURES / "with_deps" / "src" / "acme" / "svc" / "Pipeline.cpp"
    inc = FIXTURES / "with_deps" / "include"
    methods = analyze_file(cpp, include_dirs=[inc], workspace_root=FIXTURES / "with_deps")
    process = _by_name(methods, "svc::Pipeline::process")
    # static local `seen`
    assert any(sl.name == "seen" for sl in process.dep_static_locals)
    # global g_processed should be seen as read.
    global_names = {g.qualified_name for g in process.dep_globals_read}
    assert "svc::g_processed" in global_names
    # Reporter + Cache should both be recognized as dependencies.
    dep_names = {d.qualified_name for d in process.dep_classes}
    assert "svc::Cache" in dep_names
    assert "svc::Reporter" in dep_names
    # Packet / map / vector appear in the std types or data structures.
    assert any(t.startswith("std::") for t in process.dep_std_types)


def test_analyze_bogus_include_does_not_crash() -> None:
    cpp = FIXTURES / "free_functions" / "src" / "acme" / "util" / "Hash.cpp"
    # Deliberately omit include dirs — parser may still partial-parse.
    methods = analyze_file(cpp)
    assert isinstance(methods, list)
