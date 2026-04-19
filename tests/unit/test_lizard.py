"""Complexity via lizard."""

from __future__ import annotations

from pathlib import Path

from methoddep.complexity.lizard_runner import analyze_file, find_match

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "cpp-samples"


def test_lizard_returns_functions_for_with_deps() -> None:
    cpp = FIXTURES / "with_deps" / "src" / "acme" / "svc" / "Pipeline.cpp"
    results = analyze_file(cpp)
    names = {c.long_name.split("(")[0] for c in results}
    assert any("Pipeline::process" in n for n in names)


def test_find_match_by_class_prefix() -> None:
    cpp = FIXTURES / "with_deps" / "src" / "acme" / "svc" / "Pipeline.cpp"
    results = analyze_file(cpp)
    m = find_match(results, name="process", class_name="Pipeline")
    assert m is not None
    # "process" contains if/for/if → CC should be ≥ 3.
    assert m.cyclomatic >= 3


def test_find_match_free_function_by_name() -> None:
    cpp = FIXTURES / "free_functions" / "src" / "acme" / "util" / "Hash.cpp"
    results = analyze_file(cpp)
    clamp = find_match(results, name="clamp")
    assert clamp is not None
    assert clamp.cyclomatic >= 2
