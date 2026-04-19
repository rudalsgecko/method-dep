"""Smoke tests for doctor. Probes must never raise regardless of
system state; each returns a CheckResult."""

from __future__ import annotations

from methoddep import doctor


def test_every_probe_returns_a_checkresult_without_raising() -> None:
    for probe in doctor.CHECKS:
        result = probe()
        assert isinstance(result, doctor.CheckResult)
        assert isinstance(result.ok, bool)
        assert isinstance(result.required, bool)
        assert result.name
        assert isinstance(result.detail, str)


def test_python_check_always_ok_here() -> None:
    # This suite requires python >= 3.11 to run, so the probe must report ok.
    assert doctor.check_python().ok is True


def test_run_doctor_returns_bool() -> None:
    assert isinstance(doctor.run_doctor(), bool)
