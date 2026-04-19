"""verify-fixtures — run the analyzer over annotated fixtures and
measure coverage against `@methoddep:expect` annotations."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from methoddep.analyze import analyze_file
from methoddep.analyze.models import AnalyzedMethod
from methoddep.complexity.lizard_runner import analyze_file as lizard_file, find_match
from methoddep.fixtures.annotation_parser import ExpectBlock, parse_annotations


@dataclass
class CoverageFailure:
    path: Path
    line: int
    qualified_name: str
    missing: dict[str, list[str]] = field(default_factory=dict)
    # 'missing' key example: {"calls": ["svc::Cache::has"]}


@dataclass
class CoverageReport:
    annotated_methods: int
    covered_methods: int
    failures: list[CoverageFailure]
    coverage_rate: float

    def passes(self, *, min_rate: float = 0.95, min_count: int = 1) -> bool:
        return self.coverage_rate >= min_rate and self.annotated_methods >= min_count


def _sources_for(root: Path) -> list[Path]:
    exts = {".cpp", ".cc", ".cxx", ".h", ".hpp", ".hxx", ".hh"}
    return [p for p in sorted(root.rglob("*")) if p.is_file() and p.suffix in exts]


def _locate_method(methods: list[AnalyzedMethod], block: ExpectBlock) -> AnalyzedMethod | None:
    for m in methods:
        loc = m.definition or m.declaration
        if loc is None:
            continue
        if Path(loc.path).name != block.method_path.name:
            continue
        # Annotation points at the method's first line — libclang gives
        # us the return-type line, so allow ±3 lines.
        if abs(loc.line - block.method_line) <= 3:
            return m
    return None


def _check_block(method: AnalyzedMethod, block: ExpectBlock, *, strict: bool = True) -> dict[str, list[str]]:
    """Return a map {key: [missing_items]} if the method's facts fail to
    cover the expectations."""
    missing: dict[str, list[str]] = {}

    produced_classes = {d.qualified_name for d in method.dep_classes}
    produced_structs = {d.qualified_name for d in method.dep_data_structures}
    produced_freefn = {d.qualified_name for d in method.dep_free_functions}
    produced_calls = {c.target for c in method.call_graph}
    produced_gr = {g.qualified_name for g in method.dep_globals_read}
    produced_gw = {g.qualified_name for g in method.dep_globals_written}
    produced_sl = {sl.name for sl in method.dep_static_locals}
    produced_enums = {e.qualified_name for e in method.dep_enums}
    produced_throws = set(method.exception_spec.observed_throws)

    def _cmp(name: str, expected: set[str], produced: set[str]) -> None:
        if not expected:
            return
        diff = sorted(expected - produced)
        if diff:
            missing[name] = diff

    _cmp("classes", block.classes, produced_classes)
    _cmp("data_structures", block.data_structures, produced_structs)
    _cmp("free_functions", block.free_functions, produced_freefn)
    _cmp("calls", block.calls, produced_calls)
    _cmp("globals_read", block.globals_read, produced_gr)
    _cmp("globals_written", block.globals_written, produced_gw)
    _cmp("static_locals", block.static_locals, produced_sl)
    _cmp("enums", set(block.enums.keys()), produced_enums)
    _cmp("throws", block.throws, produced_throws)

    return missing


def verify(
    fixture_root: Path,
    *,
    customer: str = "acme",
    include_dir: Path | None = None,
    strict: bool = True,
) -> CoverageReport:
    src_root = fixture_root / "src" / customer
    header_root = include_dir or (fixture_root / "include")
    cpp_sources = [p for p in sorted(src_root.rglob("*.cpp")) if p.is_file()] if src_root.exists() else []
    header_sources = [p for p in sorted(header_root.rglob("*.h")) if p.is_file()] if header_root.exists() else []

    # Parse annotations from both header decls and cpp defs.
    blocks = parse_annotations(cpp_sources + header_sources)
    total = len(blocks)

    # Analyze every cpp source to gather facts, plus any header-only
    # methods the caller expects us to cover.
    all_methods: list[AnalyzedMethod] = []
    for cpp in cpp_sources:
        all_methods.extend(
            analyze_file(cpp, include_dirs=[header_root], workspace_root=fixture_root)
        )

    failures: list[CoverageFailure] = []
    for block in blocks:
        method = _locate_method(all_methods, block)
        if method is None:
            failures.append(
                CoverageFailure(
                    path=block.method_path,
                    line=block.method_line,
                    qualified_name="<unresolved>",
                    missing={"__binding__": ["analyzer did not find a method at this location"]},
                )
            )
            continue
        # Complexity check (cc_max).
        if block.cc_max is not None:
            lizards = lizard_file(method.definition.path if method.definition else method.declaration.path)
            lz = find_match(
                lizards,
                name=method.qualified_name.rsplit("::", 1)[-1],
                class_name=method.class_name,
                definition_line=method.definition.line if method.definition else None,
            )
            if lz is None or lz.cyclomatic > block.cc_max:
                failures.append(
                    CoverageFailure(
                        path=block.method_path,
                        line=block.method_line,
                        qualified_name=method.qualified_name,
                        missing={"cc_max": [f"cyclomatic={lz.cyclomatic if lz else '?'} > {block.cc_max}"]},
                    )
                )
                continue

        missing = _check_block(method, block, strict=strict)
        if missing:
            failures.append(
                CoverageFailure(
                    path=block.method_path,
                    line=block.method_line,
                    qualified_name=method.qualified_name,
                    missing=missing,
                )
            )

    covered = total - len(failures)
    rate = (covered / total) if total else 0.0
    return CoverageReport(
        annotated_methods=total,
        covered_methods=covered,
        failures=failures,
        coverage_rate=rate,
    )
