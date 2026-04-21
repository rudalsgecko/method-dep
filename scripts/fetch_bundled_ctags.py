#!/usr/bin/env python3
"""Populate `src/methoddep/_bundled/ctags/` with platform binaries.

Downloads pinned Universal Ctags releases, optionally verifies SHA-256,
extracts the executable, and places it exactly where the resolver in
`methoddep._bundled.ctags` expects it. Idempotent — rerun to refresh.

Run from the repo root on a build machine with internet access:

    python scripts/fetch_bundled_ctags.py

To bump versions: edit `RELEASES` below. After the first fetch run with
`--print-sha`, copy the printed digests into the `sha256` fields so
future runs verify.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BUNDLE_DIR = REPO_ROOT / "src" / "methoddep" / "_bundled" / "ctags"

# Pin a known Universal Ctags release per platform.
# Bumping: update `url`, run with --print-sha, paste the printed digest.
RELEASES: dict[str, dict[str, str | None]] = {
    "windows-x64": {
        # Stable semver release from ctags-win32 (MSVC build).
        "url": "https://github.com/universal-ctags/ctags-win32/releases/"
               "download/v6.1.0/ctags-v6.1.0-x64.zip",
        "sha256": (
            "ed1bdabe977980db2f5bd8f0a0fa11963f14604b5af92f31db26875bf342efd8"
        ),
        "archive": "zip",
        "member_suffix": "ctags.exe",
        "dest_name": "ctags.exe",
    },
    "linux-x64": {
        # ctags has no stable Linux binary release; use the dated nightly build.
        # Bumping: find the desired tag on
        #   https://github.com/universal-ctags/ctags-nightly-build/releases
        # and update both the tag segment (with `+<sha>` URL-encoded as %2B)
        # and the date in the filename.
        "url": "https://github.com/universal-ctags/ctags-nightly-build/releases/"
               "download/2026.04.20%2B0498b5983b38f835ece70890ea171d1c1204f284/"
               "uctags-2026.04.20-linux-x86_64.release.tar.xz",
        "sha256": (
            "ddb19582b107cad2f60e3b4933c4fd3a3133c92193bf0d9f703fedc2cd877a31"
        ),
        "archive": "tar.xz",
        "member_suffix": "/bin/ctags",
        "dest_name": "ctags",
    },
}


def _download(url: str) -> bytes:
    print(f"  GET {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "methoddep-fetch/1.0"})
    with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310 (pinned URL)
        return resp.read()


def _extract_member(data: bytes, archive_kind: str, suffix: str) -> bytes:
    if archive_kind == "zip":
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = [n for n in zf.namelist() if n.endswith(suffix.lstrip("/"))]
            if not names:
                names = [n for n in zf.namelist() if n.endswith(suffix)]
            if not names:
                raise SystemExit(f"no member matching {suffix!r} in zip "
                                 f"(candidates: {zf.namelist()[:10]})")
            return zf.read(names[0])
    if archive_kind == "tar.xz":
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:xz") as tf:
            names = [m for m in tf.getmembers()
                     if m.isfile() and m.name.endswith(suffix.lstrip("/"))]
            if not names:
                names = [m for m in tf.getmembers()
                         if m.isfile() and m.name.endswith(suffix)]
            if not names:
                raise SystemExit(
                    f"no member matching {suffix!r} in tarball "
                    f"(candidates: {[m.name for m in tf.getmembers()][:10]})"
                )
            f = tf.extractfile(names[0])
            assert f is not None
            return f.read()
    raise SystemExit(f"unsupported archive kind: {archive_kind}")


def _verify_runs(binary: Path) -> None:
    """Best-effort sanity check. On the fetch machine the Linux binary
    may not be executable here (e.g. running on Windows), so only run
    if we're on a compatible platform."""
    host = sys.platform
    if binary.name == "ctags.exe" and host != "win32":
        return
    if binary.name == "ctags" and host.startswith("linux") is False:
        return
    try:
        proc = subprocess.run(
            [str(binary), "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        print(f"  warn: could not run {binary}: {exc}")
        return
    first = (proc.stdout or "").splitlines()[:1]
    if not first or "Universal Ctags" not in first[0]:
        print(f"  warn: {binary} --version did not report Universal Ctags: "
              f"{first!r}")
    else:
        print(f"  verified: {first[0]}")


def fetch(platform_key: str, *, print_sha: bool) -> None:
    spec = RELEASES[platform_key]
    url = str(spec["url"])
    archive = str(spec["archive"])
    suffix = str(spec["member_suffix"])
    dest_name = str(spec["dest_name"])
    expected_sha = spec["sha256"]

    dest_dir = BUNDLE_DIR / platform_key
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / dest_name

    data = _download(url)
    actual_sha = hashlib.sha256(data).hexdigest()
    if print_sha:
        print(f"  sha256[{platform_key}] = {actual_sha}")
    if expected_sha and expected_sha != actual_sha:
        raise SystemExit(
            f"sha256 mismatch for {platform_key}: expected {expected_sha}, "
            f"got {actual_sha}. Re-run with --print-sha after verifying the "
            f"upstream release you really want."
        )

    binary_bytes = _extract_member(data, archive, suffix)
    dest.write_bytes(binary_bytes)
    if dest_name == "ctags":
        dest.chmod(0o755)
    print(f"  wrote {dest} ({len(binary_bytes):,} bytes)")
    _verify_runs(dest)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--only",
        choices=list(RELEASES),
        help="Fetch only one platform (default: all).",
    )
    parser.add_argument(
        "--print-sha",
        action="store_true",
        help="Print observed sha256 of each download so you can pin it.",
    )
    args = parser.parse_args()

    targets = [args.only] if args.only else list(RELEASES)
    for key in targets:
        print(f"[{key}]")
        fetch(key, print_sha=args.print_sha)

    print("done.")


if __name__ == "__main__":
    main()
