"""Parse MSBuild binlog XML exports.

v0.1 contract: methoddep ships a tiny C# shim
(`methoddep/_shim/binlog2xml/`) that wraps the `MSBuild.StructuredLogger`
library to convert a `.binlog` into XML. We then walk the XML with
`xml.etree.ElementTree` to extract:
    - per-TU include paths (from `/I` arguments and `/showIncludes`)
    - preprocessor defines (from `/D`)
    - precompiled header flag (`/Yu` header name)

If the .NET SDK isn't installed (so the shim can't be built), callers
fall back to `textlog_parser`. This module does not shell out on its
own; the driver module owns subprocess invocation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET


@dataclass
class BuildIntel:
    translation_units: dict[str, "TUFacts"] = field(default_factory=dict)

    def include_dirs(self) -> list[str]:
        out: set[str] = set()
        for tu in self.translation_units.values():
            out.update(tu.include_dirs)
        return sorted(out)

    def defines(self) -> list[str]:
        out: set[str] = set()
        for tu in self.translation_units.values():
            out.update(tu.defines)
        return sorted(out)


@dataclass
class TUFacts:
    source: str
    include_dirs: list[str] = field(default_factory=list)
    defines: list[str] = field(default_factory=list)
    pch_header: str | None = None
    extra_flags: list[str] = field(default_factory=list)


_FLAG_I = re.compile(r"[/-]I[:\s]?(?P<path>[^\s]+)")
_FLAG_D = re.compile(r"[/-]D[:\s]?(?P<macro>[^\s]+)")
_FLAG_YU = re.compile(r"/Yu(?P<hdr>\S+)")


def _split_commandline(cmdline: str) -> list[str]:
    """MSBuild emits CL arguments as a single string. Split respecting
    quoted segments."""
    out: list[str] = []
    buf: list[str] = []
    in_quote = False
    for ch in cmdline:
        if ch == '"':
            in_quote = not in_quote
            continue
        if ch.isspace() and not in_quote:
            if buf:
                out.append("".join(buf))
                buf = []
            continue
        buf.append(ch)
    if buf:
        out.append("".join(buf))
    return out


def _parse_cl_args(cmdline: str) -> TUFacts:
    source = ""
    include_dirs: list[str] = []
    defines: list[str] = []
    pch: str | None = None
    extras: list[str] = []
    tokens = _split_commandline(cmdline)
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        # Space-separated flag/value forms (`/I PATH`, `/D MACRO`).
        if tok in {"/I", "-I"} and i + 1 < len(tokens):
            include_dirs.append(tokens[i + 1])
            i += 2
            continue
        if tok in {"/D", "-D"} and i + 1 < len(tokens):
            defines.append(tokens[i + 1])
            i += 2
            continue
        m_i = _FLAG_I.match(tok)
        if m_i and m_i.group("path"):
            include_dirs.append(m_i.group("path"))
            i += 1
            continue
        m_d = _FLAG_D.match(tok)
        if m_d and m_d.group("macro"):
            defines.append(m_d.group("macro"))
            i += 1
            continue
        m_yu = _FLAG_YU.match(tok)
        if m_yu:
            pch = m_yu.group("hdr")
            i += 1
            continue
        if tok.startswith("/") or tok.startswith("-"):
            extras.append(tok)
            i += 1
            continue
        if tok.lower().endswith((".cpp", ".cc", ".cxx", ".c++")):
            source = tok
        i += 1
    return TUFacts(
        source=source,
        include_dirs=sorted(set(include_dirs)),
        defines=sorted(set(defines)),
        pch_header=pch,
        extra_flags=extras,
    )


def parse_binlog_xml(xml_text: str) -> BuildIntel:
    """Parse a StructuredLogger.Cli XML export.

    The XML structure is an approximation — this parser walks every
    element whose tag ends in `Task` looking for CL invocations, using
    the `CommandLine` attribute when present.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return BuildIntel()

    intel = BuildIntel()
    for elem in root.iter():
        tag = elem.tag.rsplit("}", 1)[-1]
        if tag not in {"Task", "CompilerInvocation", "CL", "Cl"}:
            continue
        cmdline = elem.attrib.get("CommandLine") or elem.attrib.get("Arguments")
        if not cmdline:
            # Look for a child <Arguments>.
            arg_child = elem.find(".//Arguments")
            if arg_child is not None:
                cmdline = arg_child.text or ""
        if not cmdline or ("cl.exe" not in cmdline.lower() and "CL " not in cmdline):
            continue
        facts = _parse_cl_args(cmdline)
        if facts.source:
            intel.translation_units[facts.source] = facts
    return intel


def read_binlog_xml(path: Path) -> BuildIntel:
    return parse_binlog_xml(path.read_text(encoding="utf-8", errors="replace"))
