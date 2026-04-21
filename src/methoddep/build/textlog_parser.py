"""Parse MSBuild plaintext diagnostic logs for CL invocations.

Alternative to the `.binlog` → XML path when `MSBuild.StructuredLogger.Cli`
(dotnet global tool) is unavailable. MSBuild can emit a diagnostic text
log via:

    msbuild <solution> /fl /flp:LogFile=msbuild.log;Verbosity=diagnostic

This parser extracts every CL.exe invocation from such a log and reuses
`binlog_parser._parse_cl_args` to produce a `BuildIntel` with the same
shape as the binlog path.
"""

from __future__ import annotations

import re
from pathlib import Path

from methoddep.build.binlog_parser import BuildIntel, _parse_cl_args


# Lines produced by the CL task look like:
#   Task "CL"
#       C:\...\cl.exe /c /nologo /I"..." /DUNICODE Bar.cpp
# or inline:
#       CL.exe /c /nologo /I"..." Bar.cpp
# We match anywhere "cl.exe" or "CL.exe" appears with trailing arguments.
_CL_LINE_RE = re.compile(
    r"(?i)\b(?:cl\.exe|cl)\b(?P<rest>\s+[^\r\n]+\.(?:cpp|cc|cxx|c\+\+)[^\r\n]*)"
)


def parse_msbuild_text_log(text: str) -> BuildIntel:
    intel = BuildIntel()
    for match in _CL_LINE_RE.finditer(text):
        tail = match.group("rest").strip()
        facts = _parse_cl_args(tail)
        if facts.source:
            intel.translation_units[facts.source] = facts
    return intel


def read_msbuild_text_log(path: Path) -> BuildIntel:
    return parse_msbuild_text_log(path.read_text(encoding="utf-8", errors="replace"))
