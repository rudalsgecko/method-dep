"""methoddep doctor — diagnose external tool availability.

Each check is a (name, probe) pair. Probes return CheckResult and never
raise. The doctor prints a summary table and exits non-zero if any
REQUIRED check fails.
"""

from __future__ import annotations

import os
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
    from methoddep._bundled.ctags import bundled_path, resolve_ctags

    exe = resolve_ctags()
    if not exe:
        hint = "not on PATH and no bundled binary; install Universal Ctags"
        if bundled_path() is None:
            hint += (
                " or populate src/methoddep/_bundled/ctags/<platform>/"
                " (see scripts/fetch_bundled_ctags.py)"
            )
        return CheckResult("ctags", False, hint)
    rc, out = _try_run([exe, "--version"])
    if rc != 0:
        return CheckResult("ctags", False, f"exit {rc} from {exe}")
    first = out.splitlines()[0] if out else ""
    ok = "Universal Ctags" in first
    on_path = shutil.which("ctags") == exe
    source = "PATH" if on_path else "bundled"
    return CheckResult(
        "ctags",
        ok,
        f"{first} [{source}: {exe}]" if ok else f"not Universal Ctags: {first!r}",
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
    """L0 binlog→XML conversion path.

    methoddep ships its own small C# shim (at `methoddep/_shim/binlog2xml/`)
    that wraps the `MSBuild.StructuredLogger` library. The only thing
    the user has to install is the .NET SDK — the NuGet package is
    pulled in automatically when the shim is built.
    """
    from methoddep.build.msbuild_driver import (
        _shim_cache_root,
        _shim_source_exists,
        _built_shim_dll,
    )

    name = "binlog→XML shim (.NET, bundled)"
    dotnet = shutil.which("dotnet")
    if not dotnet:
        return CheckResult(
            name,
            False,
            "install .NET SDK 8+ (https://dotnet.microsoft.com/download); "
            "without it methoddep falls back to diagnostic text-log parsing",
            required=False,
        )
    if not _shim_source_exists():
        return CheckResult(
            name,
            False,
            "shim source missing from install — reinstall methoddep "
            "(expected at methoddep/_shim/binlog2xml/)",
            required=False,
        )
    rc, _ = _try_run([dotnet, "--version"])
    if rc != 0:
        return CheckResult(
            name,
            False,
            "`dotnet --version` failed — check .NET SDK install",
            required=False,
        )
    built = _built_shim_dll(_shim_cache_root() / "src")
    detail = (
        f"ready (cached build: {built})"
        if built.exists()
        else f"ready (shim will build on first run → {built})"
    )
    return CheckResult(name, True, detail, required=False)


def check_lizard() -> CheckResult:
    try:
        import lizard  # type: ignore[import-not-found]
    except ImportError as exc:
        return CheckResult("lizard", False, f"import failed: {exc}")
    version = getattr(lizard, "VERSION", getattr(lizard, "version", "unknown"))
    return CheckResult("lizard", True, f"version={version}")


def check_tree_sitter() -> CheckResult:
    try:
        import tree_sitter  # type: ignore[import-not-found]
        import tree_sitter_languages  # type: ignore[import-not-found]
    except ImportError as exc:
        return CheckResult("tree-sitter", False, f"import failed: {exc}")
    try:
        lang = tree_sitter_languages.get_language("cpp")
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
