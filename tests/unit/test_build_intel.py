"""L0 build-intel: showincludes parser + binlog XML parser + driver stubs."""

from __future__ import annotations

from pathlib import Path

from methoddep.build import (
    BuildIntel,
    ShowIncludesRecord,
    find_msbuild,
    parse_binlog_xml,
    parse_showincludes_log,
)


_SHOWINCLUDES_SAMPLE = """
1>------ Build started ------
Bar.cpp
Note: including file: D:\\work\\acme\\include\\foo/Bar.h
Note: including file:  D:\\work\\acme\\include\\foo/IService.h
Note: including file:   C:\\VS\\include\\string
Note: including file: D:\\work\\acme\\include\\foo/Config.h

Pipeline.cpp
Note: including file: D:\\work\\acme\\include\\svc/Pipeline.h
Note: including file:  D:\\work\\acme\\include\\svc/Cache.h
"""


def test_showincludes_groups_by_translation_unit() -> None:
    records = parse_showincludes_log(_SHOWINCLUDES_SAMPLE)
    assert len(records) == 2
    bar = records[0]
    assert bar.translation_unit == "Bar.cpp"
    assert any("foo/Bar.h" in inc for inc in bar.includes)
    assert any("foo/IService.h" in inc for inc in bar.includes)
    pipeline = records[1]
    assert pipeline.translation_unit == "Pipeline.cpp"
    assert any("Cache.h" in inc for inc in pipeline.includes)


def test_showincludes_normalizes_paths() -> None:
    records = parse_showincludes_log(_SHOWINCLUDES_SAMPLE)
    for inc in records[0].includes:
        assert "\\" not in inc  # backslashes normalized to forward slashes
    # Drive letter lowercased.
    assert any(inc.startswith("d:/") or inc.startswith("c:/") for inc in records[0].includes)


_BINLOG_XML_SAMPLE = """<?xml version='1.0' encoding='utf-8'?>
<Build>
  <Task Name="CL" CommandLine="cl.exe /nologo /c /I D:/work/acme/include /I D:/work/acme/third_party /DUNICODE /D_WIN32 /Yustdafx.h D:/work/acme/src/acme/foo/Bar.cpp" />
  <Task Name="CL" CommandLine="cl.exe /nologo /c /I D:/work/acme/include /D_MSC_VER=1939 D:/work/acme/src/acme/util/Hash.cpp" />
  <Task Name="Noop" />
</Build>
"""


def test_binlog_xml_extracts_includes_and_defines() -> None:
    intel = parse_binlog_xml(_BINLOG_XML_SAMPLE)
    assert isinstance(intel, BuildIntel)
    assert len(intel.translation_units) == 2
    bar = next(tu for tu in intel.translation_units.values() if tu.source.endswith("Bar.cpp"))
    assert "D:/work/acme/include" in bar.include_dirs
    assert "UNICODE" in bar.defines
    assert bar.pch_header == "stdafx.h"
    hash_tu = next(tu for tu in intel.translation_units.values() if tu.source.endswith("Hash.cpp"))
    assert "_MSC_VER=1939" in hash_tu.defines
    assert "D:/work/acme/include" in intel.include_dirs()


def test_binlog_xml_handles_garbage_gracefully() -> None:
    assert parse_binlog_xml("<not really xml").translation_units == {}


def test_find_msbuild_is_best_effort() -> None:
    # Cannot assert a specific value — just that the call returns a str or None.
    result = find_msbuild()
    assert result is None or isinstance(result, str)
