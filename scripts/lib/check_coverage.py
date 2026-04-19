#!/usr/bin/env python3
"""Verify that a specific source range was actually executed by the last test run.

Supports two coverage report formats out of the box:

* Cobertura XML   (OpenCppCoverage --export_type=cobertura:<file>.xml).
* gcov textual    (llvm-cov gcov <obj> -> foo.cpp.gcov next to the source).
* LLVM JSON        (llvm-cov export --format=text <binary> -instr-profile=X.profdata).

If no supported report is found the script exits with code 2 and prints a
warning that the caller can treat as non-blocking. Exit code 0 means the
requested line range was covered at least once; exit code 1 means the tool
found reports but the range was not covered; exit code 2 means tooling is
missing or unusable.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def _posix(p: str | os.PathLike[str]) -> str:
    return str(p).replace("\\", "/")


def _path_eq(a: str, b: str) -> bool:
    """Compare two file paths case-insensitively with slashes normalised."""
    return os.path.normcase(_posix(a)) == os.path.normcase(_posix(b))


def _path_endswith(haystack: str, needle: str) -> bool:
    h = os.path.normcase(_posix(haystack))
    n = os.path.normcase(_posix(needle))
    return h.endswith(n)


def _overlap(lo1: int, hi1: int, lo2: int, hi2: int) -> bool:
    return not (hi1 < lo2 or hi2 < lo1)


# -------------------- Cobertura (OpenCppCoverage / generic) --------------------

def parse_cobertura(xml_path: Path, source_file: str,
                    line_lo: int, line_hi: int) -> bool | None:
    """Return True/False if matching source found, None if no match."""
    try:
        tree = ET.parse(xml_path)
    except ET.ParseError as exc:
        print(f"warn: cobertura parse error in {xml_path}: {exc}", file=sys.stderr)
        return None
    root = tree.getroot()
    found_any = False
    for cls in root.iter("class"):
        fname = cls.get("filename") or ""
        if not (_path_eq(fname, source_file) or _path_endswith(source_file, fname) or _path_endswith(fname, source_file)):
            continue
        found_any = True
        for ln in cls.iter("line"):
            num = int(ln.get("number") or 0)
            hits = int(ln.get("hits") or 0)
            if line_lo <= num <= line_hi and hits > 0:
                return True
    return False if found_any else None


# -------------------- gcov textual (llvm-cov gcov / gcc gcov) --------------------

def parse_gcov(gcov_path: Path, line_lo: int, line_hi: int) -> bool:
    try:
        text = gcov_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    for raw in text.splitlines():
        # Format: "  count:  lineno: source"
        parts = raw.split(":", 2)
        if len(parts) < 3:
            continue
        count_str = parts[0].strip()
        line_str = parts[1].strip()
        if not line_str.isdigit():
            continue
        lineno = int(line_str)
        if not (line_lo <= lineno <= line_hi):
            continue
        if count_str in ("-", "#####", "====", ""):
            continue
        # Numbers may contain '*' for unexecuted-but-reached branches.
        count_str = count_str.replace("*", "")
        try:
            count = int(count_str)
        except ValueError:
            continue
        if count > 0:
            return True
    return False


def find_gcov_for(coverage_dir: Path, source_basename: str) -> Path | None:
    for p in coverage_dir.rglob(f"{source_basename}.gcov"):
        return p
    return None


# -------------------- LLVM JSON coverage export --------------------

def parse_llvm_json(json_path: Path, source_file: str,
                    line_lo: int, line_hi: int) -> bool | None:
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"warn: llvm json parse error in {json_path}: {exc}", file=sys.stderr)
        return None
    found = False
    for export in data.get("data", []):
        for fobj in export.get("files", []):
            fname = fobj.get("filename", "")
            if not (_path_eq(fname, source_file) or _path_endswith(source_file, fname) or _path_endswith(fname, source_file)):
                continue
            found = True
            # Segments: [line, col, count, has_count, is_region_entry, is_gap_region]
            for seg in fobj.get("segments", []):
                if len(seg) < 4:
                    continue
                line = seg[0]
                count = seg[2]
                has_count = seg[3]
                if has_count and count > 0 and line_lo <= line <= line_hi:
                    return True
    return False if found else None


# -------------------- Entry point --------------------

def run(args: argparse.Namespace) -> int:
    coverage_dir = Path(args.coverage_dir).resolve()
    source_file = _posix(args.source_file)
    line_lo = max(1, int(args.line) - int(args.before))
    line_hi = int(args.line) + int(args.after)

    if not coverage_dir.exists():
        print(f"warn: coverage dir does not exist: {coverage_dir}", file=sys.stderr)
        return 2

    report_seen = False

    # 1) Cobertura
    for xml_file in coverage_dir.rglob("*.xml"):
        result = parse_cobertura(xml_file, source_file, line_lo, line_hi)
        if result is True:
            print(f"covered (cobertura: {_posix(xml_file)})")
            return 0
        if result is False:
            report_seen = True

    # 2) LLVM JSON
    for j in coverage_dir.rglob("*.json"):
        result = parse_llvm_json(j, source_file, line_lo, line_hi)
        if result is True:
            print(f"covered (llvm-json: {_posix(j)})")
            return 0
        if result is False:
            report_seen = True

    # 3) gcov sidecars
    source_basename = Path(source_file).name
    gcov_path = find_gcov_for(coverage_dir, source_basename)
    if gcov_path is not None:
        if parse_gcov(gcov_path, line_lo, line_hi):
            print(f"covered (gcov: {_posix(gcov_path)})")
            return 0
        report_seen = True

    if not report_seen:
        print(
            "warn: no coverage report found (checked cobertura/llvm-json/gcov under "
            f"{_posix(coverage_dir)}); treating as non-blocking",
            file=sys.stderr,
        )
        return 2

    print(
        f"uncovered: {source_file} lines {line_lo}-{line_hi} had zero hits",
        file=sys.stderr,
    )
    return 1


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Verify that a method body was executed")
    ap.add_argument("--coverage-dir", required=True, help="directory containing coverage reports")
    ap.add_argument("--source-file", required=True, help="source file the method is defined in")
    ap.add_argument("--line", required=True, type=int, help="definition line from methoddep JSON")
    ap.add_argument("--before", type=int, default=2, help="lines above to treat as part of body")
    ap.add_argument("--after", type=int, default=30, help="lines below to treat as part of body (nloc approx)")
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    return run(parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
