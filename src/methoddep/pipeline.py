"""End-to-end pipeline: workspace → index → analyze → complexity → mocks → emit.

One function (`run_customer`) drives the whole flow. The CLI `run`
subcommand is a thin wrapper around this.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import time

from methoddep.analyze import analyze_file, analyze_file_l2
from methoddep.analyze.models import AnalyzedMethod
from methoddep.build import (
    BuildIntel,
    export_binlog_xml,
    find_msbuild,
    parse_binlog_xml,
    parse_msbuild_text_log,
    run_msbuild_with_binlog,
    structured_logger_cli_available,
)
from methoddep.build.msbuild_driver import run_msbuild_with_text_log
from methoddep.complexity.lizard_runner import analyze_file as lizard_analyze_file, find_match
from methoddep.config import Config
from methoddep.determinism import compute_input_fingerprint
from methoddep.index import build_index
from methoddep.index.treesitter_index import _SOURCE_SUFFIXES
from methoddep.mocks.resolver import resolve_mocks
from methoddep.schema.emit import emit_method, write_index
from methoddep.workspace.composer import ComposedWorkspace, compose_workspace

log = logging.getLogger(__name__)


@dataclass
class RunResult:
    customer: str
    workspace: ComposedWorkspace
    method_count: int
    index_path: Path
    output_dir: Path
    warnings: list[str]


def _iter_cpp(workspace: Path, customer: str) -> list[Path]:
    src = workspace / "src" / customer
    if not src.exists():
        # workspace may be flat (single-customer) — scan all cpp files.
        src = workspace
    sources: list[Path] = []
    for ext in _SOURCE_SUFFIXES:
        sources.extend(sorted(src.rglob(f"*{ext}")))
    return sources


def _include_dirs(workspace: Path, config: Config) -> list[Path]:
    out: list[Path] = []
    for inc in config.analysis.include_dirs:
        inc_path = Path(inc)
        candidate = inc_path if inc_path.is_absolute() else workspace / inc_path
        if candidate.exists():
            out.append(candidate.resolve())
    default_inc = workspace / "include"
    if default_inc.exists() and default_inc.resolve() not in out:
        out.append(default_inc.resolve())
    return out


def _tool_versions() -> dict[str, str]:
    import platform
    try:
        import lizard
        lizard_version = getattr(lizard, "VERSION", "unknown")
    except ImportError:
        lizard_version = "missing"
    return {
        "libclang": _safe_libclang_version(),
        "lizard": str(lizard_version),
        "python": platform.python_version(),
    }


def _safe_libclang_version() -> str:
    try:
        import clang.cindex
        return str(clang.cindex.Config.library_file or "bundled")
    except Exception:
        return "unknown"


def _normalize_source(path: str) -> str:
    """Normalize a path to `lower-posix` form for build-intel lookup keys."""
    return str(path).replace("\\", "/").lower()


def _missing_binlog_guidance(config: Config, binlog_path: Path) -> str:
    """Actionable error for the cached-only mode when no binlog exists."""
    sln = config.target.solution or "<your-solution.sln>"
    return (
        f"build_intel: binlog not found at {binlog_path}. "
        "methoddep does not build your project in cached-only mode. "
        "Produce one with your normal build command plus '/bl:':\n"
        f"    msbuild {sln} /bl:{binlog_path}\n"
        "or set [build_intel].mode = \"build-once\" to let methoddep "
        "build it for you."
    )


def _gather_build_intel(
    config: Config, workspace_path: Path
) -> tuple[BuildIntel | None, list[str]]:
    """Run/parse the MSBuild binlog when `[build_intel].enabled`.

    Returns `(intel, warnings)`. `intel` is None when the feature is
    disabled, binlog is missing and cannot be produced, or the
    StructuredLogger export fails.
    """
    warnings: list[str] = []
    cfg = config.build_intel
    if not cfg.enabled:
        return None, warnings

    binlog_path = Path(cfg.binlog)
    if not binlog_path.is_absolute():
        binlog_path = workspace_path / cfg.binlog
    binlog_path = binlog_path.resolve()

    binlog_exists = binlog_path.exists()
    stale = False
    if binlog_exists:
        age_h = (time.time() - binlog_path.stat().st_mtime) / 3600.0
        stale = age_h > cfg.max_age_h

    # Decide whether to trigger a build based on the configured mode.
    should_build = False
    if cfg.mode == "cached-only":
        should_build = False
        if not binlog_exists:
            warnings.append(_missing_binlog_guidance(config, binlog_path))
            return None, warnings
        if stale:
            warnings.append(
                f"build_intel: binlog is older than {cfg.max_age_h}h "
                f"(mode=cached-only, using it anyway): {binlog_path}"
            )
    elif cfg.mode == "build-once":
        should_build = not binlog_exists
    elif cfg.mode == "always-build":
        should_build = (not binlog_exists) or stale

    if should_build:
        if find_msbuild() is None:
            warnings.append("build_intel: msbuild not on PATH; skipping L0")
            return None, warnings
        solution_attr = config.target.solution
        if not solution_attr:
            warnings.append("build_intel: target.solution not set; cannot produce binlog")
            return None, warnings
        sln = Path(solution_attr)
        if not sln.is_absolute():
            sln = Path(config.target.repo_root) / solution_attr
        sln = sln.resolve()
        if not sln.exists():
            warnings.append(f"build_intel: solution not found: {sln}")
            return None, warnings
        binlog_path.parent.mkdir(parents=True, exist_ok=True)
        log.info("build_intel: running msbuild on %s → %s", sln, binlog_path)
        proc = run_msbuild_with_binlog(sln, binlog=binlog_path)
        if proc.returncode != 0:
            warnings.append(
                f"build_intel: msbuild exited {proc.returncode}; "
                "continuing without L0"
            )
            if not binlog_path.exists():
                return None, warnings

    # Preferred path: structured-logger dotnet tool → XML → parse.
    if structured_logger_cli_available():
        xml_path = binlog_path.with_suffix(".xml")
        if not xml_path.exists() or xml_path.stat().st_mtime < binlog_path.stat().st_mtime:
            if not export_binlog_xml(binlog_path, xml_path):
                warnings.append(f"build_intel: StructuredLogger export failed for {binlog_path}")
            else:
                intel = _try_read_binlog_xml(xml_path, warnings)
                if intel is not None:
                    return intel, warnings
        else:
            intel = _try_read_binlog_xml(xml_path, warnings)
            if intel is not None:
                return intel, warnings

    # Fallback: MSBuild diagnostic text log — parsed directly.
    log_path = binlog_path.with_suffix(".log")
    log_exists = log_path.exists()
    log_stale = False
    if log_exists:
        age_h = (time.time() - log_path.stat().st_mtime) / 3600.0
        log_stale = age_h > cfg.max_age_h

    log_needs_build = False
    if cfg.mode == "build-once":
        log_needs_build = not log_exists
    elif cfg.mode == "always-build":
        log_needs_build = (not log_exists) or log_stale
    # cached-only: never trigger a rebuild of the text log either.

    if log_needs_build:
        solution_attr = config.target.solution
        if solution_attr and find_msbuild() is not None:
            sln = Path(solution_attr)
            if not sln.is_absolute():
                sln = Path(config.target.repo_root) / solution_attr
            sln = sln.resolve()
            if sln.exists():
                log.info("build_intel: regenerating diagnostic text log → %s", log_path)
                run_msbuild_with_text_log(sln, log_path=log_path)

    if log_path.exists():
        try:
            intel = parse_msbuild_text_log(log_path.read_text(encoding="utf-8", errors="replace"))
        except OSError as exc:
            warnings.append(f"build_intel: failed to read {log_path}: {exc}")
            return None, warnings
        if intel.translation_units:
            log.info(
                "build_intel: loaded %d TU records from %s (text log)",
                len(intel.translation_units),
                log_path.name,
            )
            return intel, warnings

    warnings.append(
        "build_intel: no usable source (StructuredLogger tool missing and "
        f"diagnostic text log absent at {log_path}); L0 disabled for this run"
    )
    return None, warnings


def _try_read_binlog_xml(xml_path: Path, warnings: list[str]) -> BuildIntel | None:
    try:
        intel = parse_binlog_xml(xml_path.read_text(encoding="utf-8", errors="replace"))
    except OSError as exc:
        warnings.append(f"build_intel: failed to read {xml_path}: {exc}")
        return None
    if not intel.translation_units:
        warnings.append("build_intel: binlog XML had no CL task records")
        return None
    log.info(
        "build_intel: loaded %d TU records from %s",
        len(intel.translation_units),
        xml_path.name,
    )
    return intel


def _merge_includes(base: list[Path], extra_paths: list[str]) -> list[Path]:
    seen = {p.resolve() for p in base}
    out = list(base)
    for raw in extra_paths:
        p = Path(raw)
        if not p.exists():
            continue
        r = p.resolve()
        if r in seen:
            continue
        seen.add(r)
        out.append(r)
    return out


def run_customer(config: Config, customer: str, *, refresh: bool = False) -> RunResult:
    """Compose workspace, analyze every TU, and emit JSON for `customer`."""
    workspace = compose_workspace(config, customer, refresh=refresh)
    workspace_path = workspace.path

    # L2+L3 index is gathered eagerly (used for future cross-validation).
    merged = build_index(workspace_path)
    warnings = list(merged.warnings)

    # L0: optional MSBuild binlog intel — auto-populates includes/defines.
    build_intel, bi_warnings = _gather_build_intel(config, workspace_path)
    warnings.extend(bi_warnings)

    includes = _include_dirs(workspace_path, config)
    extra_defines: list[str] = list(config.analysis.defines)
    if build_intel is not None:
        includes = _merge_includes(includes, build_intel.include_dirs())
        for d in build_intel.defines():
            if d not in extra_defines:
                extra_defines.append(d)
        log.info(
            "build_intel: merged %d include dirs, %d defines",
            len(build_intel.include_dirs()),
            len(build_intel.defines()),
        )

    sources = _iter_cpp(workspace_path, customer)

    # Fingerprint only workspace-internal files (determinism rule 6/10).
    fingerprint_inputs: list[Path] = list(sources)
    for inc in includes:
        for ext in (".h", ".hpp", ".hh", ".hxx"):
            fingerprint_inputs.extend(sorted(inc.rglob(f"*{ext}")))
    fingerprint = compute_input_fingerprint(fingerprint_inputs, workspace_path)

    tool_versions = _tool_versions()
    output_dir = (Path(config.output.dir) if Path(config.output.dir).is_absolute()
                  else workspace_path / config.output.dir).resolve()

    # Collect all methods first (single-threaded merger — determinism rule 9).
    all_methods: list[tuple[AnalyzedMethod, Path]] = []
    lizard_cache: dict[Path, list] = {}
    scope_root = (Path(config.target.scope_root).resolve()
                  if config.target.scope_root else workspace_path)

    # Build a per-TU lookup keyed on normalized path so PCH / TU-specific
    # include_dirs / defines can be applied when analyzing each cpp.
    tu_by_source: dict[str, object] = {}
    if build_intel is not None:
        for src_key, facts in build_intel.translation_units.items():
            tu_by_source[_normalize_source(src_key)] = facts

    for cpp in sources:
        tu_facts = tu_by_source.get(_normalize_source(cpp))
        clang_flags = list(config.analysis.clang_flags)
        tu_includes = includes
        tu_defines = extra_defines
        if tu_facts is not None:
            # Layer per-TU include dirs/defines on top of the merged set.
            tu_includes = _merge_includes(includes, tu_facts.include_dirs)
            tu_defines = list(dict.fromkeys(extra_defines + tu_facts.defines))
            if config.analysis.pch_autodetect and tu_facts.pch_header:
                clang_flags += ["-include", tu_facts.pch_header]

        methods = analyze_file(
            cpp,
            include_dirs=tu_includes,
            defines=tu_defines,
            extra_args=clang_flags,
            workspace_root=workspace_path,
            scope_root=scope_root,
        )
        if not methods:
            rel = cpp.relative_to(workspace_path).as_posix()
            warnings.append(f"libclang: no methods parsed from {rel}; falling back to tree-sitter")
            methods = analyze_file_l2(cpp, workspace_root=workspace_path)
            if not methods:
                warnings.append(f"tree-sitter: also produced no methods for {rel}")
        for m in methods:
            all_methods.append((m, cpp))

    # Gather virtual-method specs per class (for gmock skeletons on missing mocks).
    virtuals_by_class = _collect_virtuals(merged.methods, workspace_path)

    records: list[tuple[Path, object]] = []
    for method, cpp in all_methods:
        lz_list = lizard_cache.get(cpp)
        if lz_list is None:
            lz_list = lizard_analyze_file(cpp)
            lizard_cache[cpp] = lz_list
        lz_match = find_match(
            lz_list,
            name=method.qualified_name.rsplit("::", 1)[-1],
            class_name=method.class_name,
            definition_line=method.definition.line if method.definition else None,
        )
        interface_targets = [
            d.qualified_name for d in method.dep_classes if d.is_interface
        ]
        mocks = resolve_mocks(
            interface_targets,
            workspace_root=workspace_path,
            mock_dirs=config.test.mock_dirs,
            name_patterns=config.test.mock_name_patterns,
            gmock_virtual_methods={
                q: virtuals_by_class.get(q, []) for q in interface_targets
            },
        )
        path, record = emit_method(
            method,
            customer=customer,
            output_dir=output_dir,
            workspace_root=workspace_path,
            tool_versions=tool_versions,
            input_fingerprint=fingerprint,
            lizard_match=lz_match,
            mocks=mocks,
            warnings=[],
        )
        records.append((path, record))

    index_path = write_index(
        records,
        customer=customer,
        output_dir=output_dir,
        tool_versions=tool_versions,
        input_fingerprint=fingerprint,
        warnings=warnings,
    )

    return RunResult(
        customer=customer,
        workspace=workspace,
        method_count=len(records),
        index_path=index_path,
        output_dir=output_dir,
        warnings=sorted(set(warnings)),
    )


def _collect_virtuals(indexed_methods, workspace_root: Path) -> dict[str, list[dict[str, str]]]:
    """For each class with pure-virtual methods, gather gmock-compatible specs.

    Destructors are excluded: gmock does not require mocking them and
    trying to emit MOCK_METHOD for `~Class()` produces malformed output.
    """
    out: dict[str, list[dict[str, str]]] = {}
    for m in indexed_methods:
        if not m.is_virtual or not m.class_name:
            continue
        bare = m.qualified_name.rsplit("::", 1)[-1]
        if bare.startswith("~"):
            continue  # skip virtual destructors
        ret = (m.return_type or "void").strip()
        if not ret or ret in {"virtual", "= 0"}:
            ret = "void"
        entry = out.setdefault(m.class_name, [])
        args = ", ".join(
            f"{p['type']} {p['name']}" if p.get("name") else p["type"] for p in m.parameters
        )
        extras = "(const, override)" if m.is_const else "(override)"
        entry.append(
            {
                "name": bare,
                "return_type": ret,
                "args": args,
                "extras": extras,
            }
        )
    # Deduplicate by method name while preserving order.
    for qname, items in out.items():
        seen: set[str] = set()
        deduped: list[dict[str, str]] = []
        for it in items:
            if it["name"] in seen:
                continue
            seen.add(it["name"])
            deduped.append(it)
        out[qname] = deduped
    return out
