"""Pipeline ↔ L0 build-intel integration.

Exercises `_gather_build_intel` without actually running MSBuild or the
dotnet structured-logger tool. We fabricate a `.binlog` (empty file)
plus a matching `.xml` sitting on disk and patch the availability check.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from methoddep import pipeline
from methoddep.build import binlog_parser
from methoddep.config import (
    AnalysisConfig,
    BuildIntelConfig,
    ComplexityConfig,
    Config,
    CustomerConfig,
    OutputConfig,
    TargetConfig,
    TestConfig,
    WorkspaceConfig,
)


_SAMPLE_XML = """<?xml version='1.0' encoding='utf-8'?>
<Build>
  <Task Name="CL" CommandLine="cl.exe /nologo /c /I D:/repo/include /I D:/repo/third_party /DUNICODE /D_WIN32 /YuStdafx.h D:/repo/src/foo/Bar.cpp" />
  <Task Name="CL" CommandLine="cl.exe /nologo /c /I D:/repo/include /D_MSC_VER=1939 D:/repo/src/util/Hash.cpp" />
</Build>
"""


def _cfg(tmp_path: Path, *, enabled: bool, mode: str = "cached-only") -> Config:
    return Config(
        target=TargetConfig(repo_root=tmp_path),
        workspace=WorkspaceConfig(strategy="in-place"),
        customers={"acme": CustomerConfig()},
        analysis=AnalysisConfig(),
        build_intel=BuildIntelConfig(
            enabled=enabled,
            mode=mode,  # type: ignore[arg-type]
            binlog=str(tmp_path / "artifacts" / "msbuild.binlog"),
            max_age_h=24,
        ),
        complexity=ComplexityConfig(),
        test=TestConfig(),
        output=OutputConfig(dir=str(tmp_path / "out")),
    )


def test_cached_only_mode_without_binlog_emits_guidance(tmp_path: Path) -> None:
    """Default cached-only mode never triggers a build — missing binlog
    must produce an actionable guidance warning and continue."""
    cfg = _cfg(tmp_path, enabled=True, mode="cached-only")
    intel, warnings = pipeline._gather_build_intel(cfg, tmp_path)
    assert intel is None
    msg = "\n".join(warnings)
    assert "not found" in msg
    assert "/bl:" in msg          # the suggested flag
    assert "msbuild" in msg       # the suggested command
    assert "cached-only" in msg   # mentions the mode


def test_gather_build_intel_disabled_returns_none(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, enabled=False)
    intel, warnings = pipeline._gather_build_intel(cfg, tmp_path)
    assert intel is None
    assert warnings == []


def test_gather_build_intel_reads_existing_xml(tmp_path: Path, monkeypatch) -> None:
    # Lay out a fresh binlog + xml; ensure xml is newer so no regeneration.
    binlog = tmp_path / "artifacts" / "msbuild.binlog"
    binlog.parent.mkdir(parents=True)
    binlog.write_bytes(b"\x00" * 10)  # dummy binlog contents
    xml = binlog.with_suffix(".xml")
    xml.write_text(_SAMPLE_XML, encoding="utf-8")
    # Make xml newer than binlog so the re-export branch is skipped.
    now = time.time()
    import os
    os.utime(binlog, (now - 10, now - 10))
    os.utime(xml, (now, now))

    # Claim the dotnet tool is present; we don't actually need it because
    # the xml exists and is newer.
    monkeypatch.setattr(pipeline, "structured_logger_cli_available", lambda: True)

    cfg = _cfg(tmp_path, enabled=True)
    intel, warnings = pipeline._gather_build_intel(cfg, tmp_path)
    assert intel is not None, f"warnings: {warnings}"
    assert len(intel.translation_units) == 2
    assert "D:/repo/include" in intel.include_dirs()
    assert "UNICODE" in intel.defines()
    assert "_MSC_VER=1939" in intel.defines()
    bar = next(tu for tu in intel.translation_units.values() if tu.source.endswith("Bar.cpp"))
    assert bar.pch_header == "Stdafx.h"


def test_gather_build_intel_falls_back_to_text_log(tmp_path: Path, monkeypatch) -> None:
    """When the dotnet tool is absent, a diagnostic text log next to the
    binlog must be parsed instead."""
    binlog = tmp_path / "artifacts" / "msbuild.binlog"
    binlog.parent.mkdir(parents=True)
    binlog.write_bytes(b"\x00")
    # Companion .log present with a CL command line.
    log_path = binlog.with_suffix(".log")
    log_path.write_text(
        "Task \"CL\"\n"
        "  CL.exe /c /nologo /I D:/repo/include /DUNICODE "
        "D:/repo/src/foo/Bar.cpp\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(pipeline, "structured_logger_cli_available", lambda: False)
    # Pretend msbuild is unavailable so we don't try to regenerate the log.
    monkeypatch.setattr(pipeline, "find_msbuild", lambda: None)

    cfg = _cfg(tmp_path, enabled=True)
    intel, warnings = pipeline._gather_build_intel(cfg, tmp_path)
    assert intel is not None, f"warnings: {warnings}"
    assert any(tu.source.endswith("Bar.cpp") for tu in intel.translation_units.values())
    assert "D:/repo/include" in intel.include_dirs()


def test_gather_build_intel_gives_up_when_no_source(tmp_path: Path, monkeypatch) -> None:
    """No dotnet tool AND no text log → graceful None + explanatory warning."""
    binlog = tmp_path / "artifacts" / "msbuild.binlog"
    binlog.parent.mkdir(parents=True)
    binlog.write_bytes(b"\x00")
    monkeypatch.setattr(pipeline, "structured_logger_cli_available", lambda: False)
    monkeypatch.setattr(pipeline, "find_msbuild", lambda: None)
    cfg = _cfg(tmp_path, enabled=True)
    intel, warnings = pipeline._gather_build_intel(cfg, tmp_path)
    assert intel is None
    assert any("L0 disabled" in w for w in warnings)


def test_merge_includes_preserves_order_and_dedupes(tmp_path: Path) -> None:
    a = tmp_path / "a"
    a.mkdir()
    b = tmp_path / "b"
    b.mkdir()
    c = tmp_path / "c"
    c.mkdir()
    base = [a, b]
    extra = [str(b), str(c), str(tmp_path / "missing")]
    merged = pipeline._merge_includes(base, extra)
    resolved = [p.resolve() for p in merged]
    assert resolved == [a.resolve(), b.resolve(), c.resolve()]
