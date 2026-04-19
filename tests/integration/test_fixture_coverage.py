"""Aggregate coverage across all annotated fixtures — verifies the
plan's §Verification quality gate (≥ 0.95 AND ≥ 20 annotated methods).
"""

from __future__ import annotations

from pathlib import Path

from methoddep.verify_fixtures import verify

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "cpp-samples"


def test_aggregate_coverage_meets_plan_gate() -> None:
    targets = [
        ("interface_impl", "acme"),
        ("interface_impl", "globex"),
        ("with_deps", "acme"),
        ("free_functions", "acme"),
        ("templated", "acme"),
        ("gmock_pattern", "acme"),
    ]
    total_annotated = 0
    total_covered = 0
    all_failures = []
    for fixture, customer in targets:
        report = verify(FIXTURES / fixture, customer=customer)
        total_annotated += report.annotated_methods
        total_covered += report.covered_methods
        all_failures.extend(report.failures)

    rate = total_covered / total_annotated if total_annotated else 0.0
    assert total_annotated >= 20, (
        f"only {total_annotated} annotated methods — plan requires ≥ 20"
    )
    assert rate >= 0.95, (
        f"aggregate coverage {rate:.3f} < 0.95; failures={all_failures}"
    )
