"""methoddep doctor — diagnose external tool availability.

Each check is a (name, probe) pair. Probes return CheckResult and never
raise. The doctor prints a summary table and exits non-zero if any
REQUIRED check fails.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Callable

from rich.console import Console
from rich.table import Table


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    detail: str
    required: bool = True


def _try_run(cmd: list[str], *, timeout: float = 10.0) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError:
        return 127, ""
    except (subprocess.TimeoutExpired, OSError) as exc:
        return 1, str(exc)
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, out


def check_python() -> CheckResult:
    import sys

    version = sys.version.split()[0]
    parts = tuple(int(x) for x in version.split(".")[:2])
    ok = parts >= (3, 11)
    return CheckResult("python", ok, f"Python {version} (>= 3.11 required)")


def check_libclang() -> CheckResult:
    try:
        import clang.cindex  # type: ignore[import-not-found]
    except ImportError as exc:
        return CheckResult("libclang (python)", False, f"import failed: {exc}")
    try:
        version = clang.cindex.Config.library_file  # type: ignore[attr-defined]
    except Exception:
        version = "libclang module loaded"
    return CheckResult("libclang (python)", True, f"bindings present ({version})")


def check_ctags() -> CheckResult:
    exe = shutil.which("ctags")
    if not exe:
        return CheckResult("ctags", False, "not on PATH (install Universal Ctags)")
    rc, out = _try_run([exe, "--version"])
    if rc != 0:
        return CheckResult("ctags", False, f"exit {rc}")
    first = out.splitlines()[0] if out else ""
    ok = "Universal Ctags" in first
    return CheckResult(
        "ctags",
        ok,
        first if ok else f"not Universal Ctags: {first!r}",
    )


def check_msbuild() -> CheckResult:
    exe = shutil.which("msbuild")
    if not exe:
        # Try vswhere fallback
        vswhere = shutil.which("vswhere") or r"C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe"
        if os.path.exists(vswhere):
            rc, out = _try_run([vswhere, "-latest", "-find", r"MSBuild\**\Bin\MSBuild.exe"])
            if rc == 0 and out.strip():
                return CheckResult("msbuild", True, out.strip().splitlines()[0])
        return CheckResult("msbuild", False, "not on PATH; install VS Build Tools")
    rc, out = _try_run([exe, "-version"])
    first = (out.splitlines() or [""])[-1]
    return CheckResult("msbuild", rc == 0, f"{exe}  ({first})")


def check_dotnet_structured_logger() -> CheckResult:
    dotnet = shutil.which("dotnet")
    if not dotnet:
        return CheckResult(
            "StructuredLogger.Cli (dotnet tool)",
            False,
            "dotnet not on PATH",
            required=False,
        )
    rc, out = _try_run([dotnet, "tool", "list", "-g"])
    if rc != 0:
        return CheckResult(
            "StructuredLogger.Cli (dotnet tool)",
            False,
            "dotnet tool list -g failed",
            required=False,
        )
    ok = bool(re.search(r"msbuild\.structuredlogger\.cli", out, re.IGNORECASE))
    hint = (
        "ok" if ok
        else "install: dotnet tool install -g MSBuild.StructuredLogger.Cli"
    )
    return CheckResult(
        "StructuredLogger.Cli (dotnet tool)",
        ok,
        hint,
        required=False,
    )


def check_lizard() -> CheckResult:
    try:
        import lizard  # type: ignore[import-not-found]
    except ImportError as exc:
        return CheckResult("lizard", False, f"import failed: {exc}")
    version = getattr(lizard, "VERSION", getattr(lizard, "version", "unknown"))
    return CheckResult("lizard", True, f"version={version}")


def check_tree_sitter() -> CheckResult:
    # Must match how treesitter_index.py actually loads the grammar:
    #   tree_sitter.Language(tree_sitter_cpp.language())
    try:
        from tree_sitter import Language  # type: ignore[import-not-found]
        import tree_sitter_cpp as _ts_cpp  # type: ignore[import-not-found]
    except ImportError as exc:
        return CheckResult("tree-sitter", False, f"import failed: {exc}")
    try:
        lang = Language(_ts_cpp.language())
    except Exception as exc:
        return CheckResult("tree-sitter", False, f"cpp grammar unavailable: {exc}")
    return CheckResult("tree-sitter", bool(lang), "cpp grammar loaded")


def check_git_longpaths() -> CheckResult:
    exe = shutil.which("git")
    if not exe:
        return CheckResult("git core.longpaths", False, "git not on PATH")
    rc, out = _try_run([exe, "config", "--global", "--get", "core.longpaths"])
    ok = rc == 0 and out.strip().lower() == "true"
    hint = "enabled" if ok else "run: git config --global core.longpaths true"
    return CheckResult("git core.longpaths", ok, hint, required=(os.name == "nt"))


CHECKS: list[Callable[[], CheckResult]] = [
    check_python,
    check_libclang,
    check_ctags,
    check_msbuild,
    check_dotnet_structured_logger,
    check_lizard,
    check_tree_sitter,
    check_git_longpaths,
]


def run_doctor() -> bool:
    console = Console()
    table = Table(title="methoddep doctor", show_lines=False)
    table.add_column("Check")
    table.add_column("Status", justify="center")
    table.add_column("Detail", overflow="fold")

    all_required_ok = True
    for probe in CHECKS:
        result = probe()
        badge_style = (
            "bold green" if result.ok
            else ("bold red" if result.required else "bold yellow")
        )
        badge = "OK" if result.ok else ("FAIL" if result.required else "WARN")
        table.add_row(
            result.name,
            f"[{badge_style}]{badge}[/{badge_style}]",
            result.detail,
        )
        if result.required and not result.ok:
            all_required_ok = False

    console.print(table)
    if not all_required_ok:
        console.print("[bold red]one or more required checks failed[/bold red]")
    return all_required_ok
