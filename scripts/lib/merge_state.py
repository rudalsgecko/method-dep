#!/usr/bin/env python3
"""Atomic, racy-safe update of the ralph test-generation state file.

Each invocation:
    1. Opens the file under an exclusive cross-platform lock.
    2. Reads the current JSON (if any).
    3. Applies the merge described by ``--patch`` (or a full ``--entry``).
    4. Atomically rewrites the file.

Invocation modes:

    merge_state.py --state PATH --method-id ID --patch '{"status":"passed"}'
        Merge the patch into ``state["methods"][ID]`` (shallow dict merge).

    merge_state.py --state PATH --method-id ID --entry @file.json
        Replace ``state["methods"][ID]`` wholesale with the contents of file.json.

    merge_state.py --state PATH --dump
        Print state JSON to stdout (also acquires the lock to wait out concurrent writers).
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

try:
    import msvcrt  # Windows
    _HAVE_MSVCRT = True
except ImportError:
    _HAVE_MSVCRT = False

try:
    import fcntl  # POSIX
    _HAVE_FCNTL = True
except ImportError:
    _HAVE_FCNTL = False


DEFAULT_SCHEMA_VERSION = 1


@contextlib.contextmanager
def _lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    # Open/create the lock file.
    with open(lock_path, "a+b") as lf:
        acquired = False
        start = time.time()
        while not acquired:
            try:
                if _HAVE_MSVCRT:
                    msvcrt.locking(lf.fileno(), msvcrt.LK_NBLCK, 1)
                    acquired = True
                elif _HAVE_FCNTL:
                    fcntl.flock(lf.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                else:
                    acquired = True  # No locking primitive; best effort.
            except OSError:
                if time.time() - start > 30:
                    raise RuntimeError(f"timed out waiting for lock on {lock_path}")
                time.sleep(0.05)
        try:
            yield
        finally:
            try:
                if _HAVE_MSVCRT:
                    try:
                        lf.seek(0)
                        msvcrt.locking(lf.fileno(), msvcrt.LK_UNLCK, 1)
                    except OSError:
                        pass
                elif _HAVE_FCNTL:
                    fcntl.flock(lf.fileno(), fcntl.LOCK_UN)
            finally:
                pass


def _read_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": DEFAULT_SCHEMA_VERSION, "methods": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        # Corrupt file — back it up and start fresh.
        backup = path.with_suffix(path.suffix + f".corrupt-{int(time.time())}")
        path.rename(backup)
        print(f"warn: state file was corrupt, moved to {backup}", file=sys.stderr)
        return {"version": DEFAULT_SCHEMA_VERSION, "methods": {}}
    if not isinstance(data, dict):
        return {"version": DEFAULT_SCHEMA_VERSION, "methods": {}}
    data.setdefault("version", DEFAULT_SCHEMA_VERSION)
    data.setdefault("methods", {})
    if not isinstance(data["methods"], dict):
        data["methods"] = {}
    return data


def _write_state(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)


def _load_payload(raw: str) -> dict[str, Any]:
    if raw.startswith("@"):
        src = Path(raw[1:])
        # utf-8-sig transparently strips any BOM PowerShell/VS Code might add.
        text = src.read_text(encoding="utf-8-sig")
    else:
        text = raw.lstrip("\ufeff")
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"invalid JSON payload: {exc}")
    if not isinstance(obj, dict):
        raise SystemExit("payload must be a JSON object")
    return obj


def apply_patch(state: dict[str, Any], method_id: str, patch: dict[str, Any]) -> None:
    methods = state.setdefault("methods", {})
    current = methods.get(method_id) or {}
    current.update(patch)
    current["last_updated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    methods[method_id] = current


def replace_entry(state: dict[str, Any], method_id: str, entry: dict[str, Any]) -> None:
    methods = state.setdefault("methods", {})
    entry = dict(entry)
    entry["last_updated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    methods[method_id] = entry


def run(args: argparse.Namespace) -> int:
    state_path = Path(args.state)
    with _lock(state_path):
        state = _read_state(state_path)
        if args.dump:
            json.dump(state, sys.stdout, indent=2, sort_keys=True, ensure_ascii=False)
            sys.stdout.write("\n")
            return 0
        if not args.method_id:
            raise SystemExit("--method-id is required unless --dump is used")
        if args.patch is not None:
            apply_patch(state, args.method_id, _load_payload(args.patch))
        elif args.entry is not None:
            replace_entry(state, args.method_id, _load_payload(args.entry))
        else:
            raise SystemExit("either --patch or --entry (or --dump) is required")
        _write_state(state_path, state)
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Atomically update the ralph test-generation state")
    ap.add_argument("--state", required=True, help="path to .ralph-state.json")
    ap.add_argument("--method-id", help="method identifier (filename sha1 hex)")
    ap.add_argument("--patch", help='JSON object to shallow-merge into methods[id] (or @file.json)')
    ap.add_argument("--entry", help='Full JSON object replacing methods[id] (or @file.json)')
    ap.add_argument("--dump", action="store_true", help="print the full state to stdout")
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    return run(parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
