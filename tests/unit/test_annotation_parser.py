"""Annotation parser — BNF + depth-aware splitter coverage."""

from __future__ import annotations

from pathlib import Path

from methoddep.fixtures.annotation_parser import (
    ExpectBlock,
    _split_depth_aware,
    parse_annotations,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "cpp-samples"


def test_depth_aware_split_respects_angle_brackets() -> None:
    raw = "Vec<int,int>; Map<std::string,int>; foo::Bar"
    assert _split_depth_aware(raw) == ["Vec<int,int>", "Map<std::string,int>", "foo::Bar"]


def test_depth_aware_split_respects_braces() -> None:
    raw = "foo::Status{OK,Retry}; bar::Mode{A,B}"
    assert _split_depth_aware(raw) == ["foo::Status{OK,Retry}", "bar::Mode{A,B}"]


def test_parse_interface_impl_bar_cpp() -> None:
    cpp = FIXTURES / "interface_impl" / "src" / "acme" / "foo" / "Bar.cpp"
    blocks = parse_annotations([cpp])
    assert len(blocks) == 2
    # Bar::Bar ctor — empty classes expected.
    assert blocks[0].classes == set()
    # Bar::doWork
    dowork = blocks[1]
    assert dowork.classes == {"foo::IService"}
    assert dowork.calls == {"foo::IService::fetch", "foo::IService::commit"}


def test_parse_with_deps_pipeline_cpp() -> None:
    cpp = FIXTURES / "with_deps" / "src" / "acme" / "svc" / "Pipeline.cpp"
    blocks = parse_annotations([cpp])
    # Two blocks: ctor, process
    process = blocks[1]
    assert process.classes == {"svc::Cache", "svc::Reporter"}
    assert "svc::Cache::has" in process.calls
    assert "svc::Reporter::log" in process.calls
    assert "svc::g_processed" in process.globals_read
    assert "seen" in process.static_locals


def test_parse_free_functions_cpp() -> None:
    cpp = FIXTURES / "free_functions" / "src" / "acme" / "util" / "Hash.cpp"
    blocks = parse_annotations([cpp])
    assert len(blocks) == 2
    clamp = blocks[1]
    assert clamp.cc_max == 3


def test_parse_is_deterministic() -> None:
    cpps = [
        FIXTURES / "with_deps" / "src" / "acme" / "svc" / "Pipeline.cpp",
        FIXTURES / "interface_impl" / "src" / "acme" / "foo" / "Bar.cpp",
    ]
    first = parse_annotations(cpps)
    second = parse_annotations(cpps)
    first_sig = [(str(b.method_path), b.method_line, sorted(b.classes), sorted(b.calls)) for b in first]
    second_sig = [(str(b.method_path), b.method_line, sorted(b.classes), sorted(b.calls)) for b in second]
    assert first_sig == second_sig
