"""Driver for invoking MSBuild to capture binlogs.

Minimal surface:
    - `find_msbuild()` — returns the MSBuild.exe path (uses vswhere when
      msbuild is not on PATH).
    - `run_msbuild_with_binlog()` — runs `msbuild /bl:<out>` for a given
      solution.
    - `run_msbuild_with_text_log()` — runs `msbuild /fl ...` for a
      diagnostic text log (used as the no-.NET-SDK fallback path).
    - `structured_logger_cli_available()` — returns True iff we can
      convert `.binlog` → XML. That requires the .NET SDK (`dotnet` on
      PATH) plus the binlog2xml shim shipped with methoddep
      (`methoddep/_shim/binlog2xml/`). The shim depends on the
      `MSBuild.StructuredLogger` NuGet package, which it pulls in
      automatically at build time — users do NOT install any NuGet
      package or dotnet global tool themselves.
    - `export_binlog_xml()` — invokes the cached shim build to produce
      `<binlog>.xml`. Builds the shim into a user cache dir on first
      use (`~/.methoddep/binlog2xml/`) so site-packages stays read-only.

No module-level side effects. The pipeline imports these lazily from
the L0 phase when `build_intel.enabled = true`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from importlib import resources
from pathlib import Path


def find_msbuild() -> str | None:
    exe = shutil.which("msbuild")
    if exe:
        return exe
    vswhere = shutil.which("vswhere") or (
        r"C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe"
    )
    if os.path.exists(vswhere):
        try:
            out = subprocess.run(
                [vswhere, "-latest", "-find", r"MSBuild\**\Bin\MSBuild.exe"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        for line in (out.stdout or "").splitlines():
            path = line.strip()
            if path and os.path.exists(path):
                return path
    return None


def run_msbuild_with_binlog(
    solution: Path,
    *,
    binlog: Path,
    configuration: str = "Debug",
    platform: str = "x64",
    extra_args: list[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    exe = find_msbuild()
    if not exe:
        raise RuntimeError("msbuild.exe not found — install VS Build Tools or run 'methoddep doctor'")
    args = [
        exe,
        str(solution),
        "/restore",
        f"/bl:{binlog}",
        f"/p:Configuration={configuration}",
        f"/p:Platform={platform}",
        "/m",
    ]
    if extra_args:
        args.extend(extra_args)
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def run_msbuild_with_text_log(
    solution: Path,
    *,
    log_path: Path,
    configuration: str = "Debug",
    platform: str = "x64",
    extra_args: list[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run msbuild with a diagnostic fileLogger.

    Produces a text log containing every CL.exe command line. Used as
    the fallback path when the binlog2xml shim is unavailable — the
    plaintext parser reads this directly.
    """
    exe = find_msbuild()
    if not exe:
        raise RuntimeError("msbuild.exe not found — install VS Build Tools or run 'methoddep doctor'")
    args = [
        exe,
        str(solution),
        "/restore",
        "/fl",
        f"/flp:LogFile={log_path};Verbosity=diagnostic;Encoding=UTF-8",
        f"/p:Configuration={configuration}",
        f"/p:Platform={platform}",
        "/m",
    ]
    if extra_args:
        args.extend(extra_args)
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


# --- binlog → XML shim ------------------------------------------------------

_SHIM_PACKAGE = "methoddep._shim.binlog2xml"
_SHIM_SOURCES = ("binlog2xml.csproj", "Program.cs")


def _shim_cache_root() -> Path:
    """Writable cache dir for the built shim.

    Kept under the user's home so pip-installed site-packages stays
    read-only. Overridable via `METHODDEP_SHIM_CACHE` for CI.
    """
    override = os.environ.get("METHODDEP_SHIM_CACHE")
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".methoddep" / "binlog2xml"


def _shim_source_exists() -> bool:
    """True iff the shim source files are present in the installed
    package (they're shipped as package-data in the wheel)."""
    try:
        root = resources.files(_SHIM_PACKAGE)
    except (ModuleNotFoundError, FileNotFoundError):
        return False
    return all(root.joinpath(name).is_file() for name in _SHIM_SOURCES)


def _sync_shim_sources(src_dir: Path) -> bool:
    """Copy shim sources into the cache dir if the cached copy is
    missing or stale. Returns True on success."""
    if not _shim_source_exists():
        return False
    src_dir.mkdir(parents=True, exist_ok=True)
    root = resources.files(_SHIM_PACKAGE)
    for name in _SHIM_SOURCES:
        dest = src_dir / name
        src_bytes = root.joinpath(name).read_bytes()
        if dest.exists() and dest.read_bytes() == src_bytes:
            continue
        dest.write_bytes(src_bytes)
    return True


def _built_shim_dll(src_dir: Path) -> Path:
    return src_dir / "bin" / "Release" / "net8.0" / "binlog2xml.dll"


def _ensure_shim_built() -> Path | None:
    """Build the shim if necessary and return the DLL path, or None if
    the shim isn't available (no dotnet, no source, or build failed)."""
    if shutil.which("dotnet") is None:
        return None
    cache_root = _shim_cache_root()
    src_dir = cache_root / "src"
    if not _sync_shim_sources(src_dir):
        return None

    dll = _built_shim_dll(src_dir)
    if dll.exists():
        dll_mtime = dll.stat().st_mtime
        source_mtimes = (
            (src_dir / name).stat().st_mtime for name in _SHIM_SOURCES
        )
        if all(mt <= dll_mtime for mt in source_mtimes):
            return dll

    csproj = src_dir / "binlog2xml.csproj"
    try:
        proc = subprocess.run(
            [
                "dotnet", "build", str(csproj),
                "-c", "Release",
                "--nologo",
                "-v", "minimal",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0 or not dll.exists():
        return None
    return dll


def structured_logger_cli_available() -> bool:
    """True iff we can convert `.binlog` → XML on this machine.

    Requires `dotnet` (the .NET SDK) on PATH and the bundled shim
    source inside the installed methoddep package. Does NOT require
    any dotnet global tool or a pre-installed NuGet package —
    `MSBuild.StructuredLogger` is resolved by the shim's own csproj.
    """
    return shutil.which("dotnet") is not None and _shim_source_exists()


def export_binlog_xml(binlog: Path, out_xml: Path) -> bool:
    dll = _ensure_shim_built()
    if dll is None:
        return False
    try:
        proc = subprocess.run(
            ["dotnet", "exec", str(dll), str(binlog), str(out_xml)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0 and out_xml.exists()
