"""Bundled Universal Ctags binary resolver.

Supported platforms: `windows-x64`, `linux-x64`. The actual binaries
are committed under `windows-x64/ctags.exe` and `linux-x64/ctags` and
shipped as wheel package-data (see `pyproject.toml`).

Resolution order:
    1. `shutil.which("ctags")` on the user's PATH (honour upgrades).
    2. Copy the platform-matching bundled binary to
       `~/.methoddep/bundled/ctags/<subdir>/` (or `$METHODDEP_BUNDLED_CACHE`)
       and return that path. Copied because `importlib.resources`
       entries may live inside zip-installed site-packages.
    3. Return `None` — caller must handle the missing-ctags case.
"""

from __future__ import annotations

import os
import platform
import shutil
import stat
from importlib import resources
from pathlib import Path

# Map (system, machine) → (subdir, executable filename).
_PLATFORM_MAP: dict[tuple[str, str], tuple[str, str]] = {
    ("Windows", "AMD64"):  ("windows-x64", "ctags.exe"),
    ("Windows", "x86_64"): ("windows-x64", "ctags.exe"),
    ("Linux",   "x86_64"): ("linux-x64",   "ctags"),
    ("Linux",   "AMD64"):  ("linux-x64",   "ctags"),
}


def _cache_root() -> Path:
    override = os.environ.get("METHODDEP_BUNDLED_CACHE")
    if override:
        return Path(override).expanduser().resolve() / "ctags"
    return Path.home() / ".methoddep" / "bundled" / "ctags"


def _platform_entry() -> tuple[str, str] | None:
    return _PLATFORM_MAP.get((platform.system(), platform.machine()))


def bundled_path() -> Path | None:
    """Filesystem path to the committed bundled binary for this platform,
    or None if unsupported / not populated.

    The returned path may be read-only (inside a zipped site-packages)
    — do NOT chmod or exec it directly; use `resolve_ctags()` which
    stages a writable copy.
    """
    entry = _platform_entry()
    if entry is None:
        return None
    subdir, exe = entry
    try:
        root = resources.files("methoddep._bundled.ctags")
    except (ModuleNotFoundError, FileNotFoundError):
        return None
    res = root.joinpath(subdir, exe)
    if not res.is_file():
        return None
    # Traversable doesn't expose an os.fspath guarantee for non-file-backed
    # resources (e.g. zip), but importlib.resources.files returns a Path
    # when the package lives on the filesystem, which is our normal case.
    try:
        return Path(str(res))
    except TypeError:
        return None


def resolve_ctags() -> str | None:
    """Return a path to a runnable `ctags` executable, or None."""
    sys_ctags = shutil.which("ctags")
    if sys_ctags:
        return sys_ctags

    entry = _platform_entry()
    if entry is None:
        return None
    subdir, exe = entry

    try:
        root = resources.files("methoddep._bundled.ctags")
    except (ModuleNotFoundError, FileNotFoundError):
        return None
    res = root.joinpath(subdir, exe)
    if not res.is_file():
        return None

    src_bytes = res.read_bytes()
    cache_dir = _cache_root() / subdir
    cache_dir.mkdir(parents=True, exist_ok=True)
    dest = cache_dir / exe

    needs_write = (
        not dest.exists()
        or dest.stat().st_size != len(src_bytes)
        or dest.read_bytes() != src_bytes
    )
    if needs_write:
        dest.write_bytes(src_bytes)
    mode = dest.stat().st_mode
    dest.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return str(dest)
