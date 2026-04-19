"""verify-fixtures end-to-end against the annotated fixtures."""

from __future__ import annotations

from pathlib import Path

from methoddep.verify_fixtures import verify

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "cpp-samples"


def test_interface_impl_passes() -> None:
    report = verify(FIXTURES / "interface_impl", customer="acme")
    assert report.annotated_methods >= 2
    assert report.coverage_rate == 1.0, f"failures: {report.failures}"


def test_with_deps_passes() -> None:
    report = verify(FIXTURES / "with_deps", customer="acme")
    assert report.annotated_methods >= 2
    assert report.coverage_rate == 1.0, f"failures: {report.failures}"


def test_free_functions_passes() -> None:
    report = verify(FIXTURES / "free_functions", customer="acme")
    assert report.annotated_methods >= 2
    assert report.coverage_rate == 1.0, f"failures: {report.failures}"
