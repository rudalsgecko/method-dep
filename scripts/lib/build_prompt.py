#!/usr/bin/env python3
"""Render an LLM prompt for a single methoddep per-method JSON.

The ralph loop pipes stdout of this script straight into the configured LLM CLI.
Keep the output token-tight: most of the context already lives inside the JSON
produced by methoddep, so we avoid duplicating information unnecessarily.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable


def _posix(p: str | os.PathLike[str]) -> str:
    return str(p).replace("\\", "/")


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError):
        return None


def extract_source_snippet(source_root: Path, rel_path: str, line: int,
                           before: int = 15, after: int = 30) -> str:
    """Return an annotated listing of the method body (best-effort)."""
    rel = rel_path.replace("\\", "/")
    # Try both: relative to source_root, and as an absolute path already.
    candidates = []
    if rel:
        candidates.append(source_root / rel)
        candidates.append(Path(rel))
    text: str | None = None
    picked: Path | None = None
    for cand in candidates:
        text = _read_text(cand)
        if text is not None:
            picked = cand
            break
    if text is None:
        return f"(source file not found: {rel})"
    lines = text.splitlines()
    if not lines:
        return f"(empty source file: {picked})"
    start = max(1, line - before)
    end = min(len(lines), line + after)
    width = len(str(end))
    out: list[str] = [f"// {_posix(picked)}  (lines {start}-{end}, target line {line})"]
    for i in range(start, end + 1):
        marker = ">>" if i == line else "  "
        out.append(f"{marker}{i:>{width}}| {lines[i - 1]}")
    return "\n".join(out)


def format_deps(deps: dict[str, Any]) -> str:
    """Flat bullet list of the dependency cross-reference."""
    lines: list[str] = []

    def _emit(section: str, items: Iterable[dict[str, Any]]) -> None:
        for item in items:
            qn = item.get("qualified_name") or item.get("name") or "<unknown>"
            hdr = item.get("header") or ""
            used = item.get("used_methods") or []
            sig = item.get("signature") or ""
            parts = [f"- [{section}] {qn}"]
            if hdr:
                parts.append(f"@ {hdr}")
            if sig:
                parts.append(f"sig=`{sig}`")
            if used:
                parts.append(f"uses={','.join(used)}")
            lines.append(" ".join(parts))

    if isinstance(deps, dict):
        _emit("class", deps.get("classes") or [])
        _emit("free_fn", deps.get("free_functions") or [])
        _emit("enum", [{"qualified_name": e} if isinstance(e, str) else e
                        for e in (deps.get("enums_referenced") or [])])
        _emit("global", deps.get("globals") or [])
        _emit("typedef", deps.get("typedefs") or [])
    return "\n".join(lines) if lines else "(no recorded dependencies)"


def format_call_graph(edges: list[dict[str, Any]]) -> str:
    if not edges:
        return "(no recorded calls)"
    out = []
    for e in edges:
        loc = e.get("call_site_line")
        target = e.get("target") or "?"
        branch = " [in_branch]" if e.get("in_branch") else ""
        out.append(f"- line {loc}: -> {target}{branch}")
    return "\n".join(out)


def render(args: argparse.Namespace) -> str:
    method_json_path = Path(args.method_json).resolve()
    try:
        data = json.loads(method_json_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise SystemExit(f"failed to read {method_json_path}: {exc}")

    method = data.get("method") or {}
    qualified = method.get("qualified_name") or "(unknown)"
    class_name = method.get("class") or ""
    signature = method.get("signature") or method.get("qualified_name") or ""
    parameters = method.get("parameters") or []
    specifiers = method.get("specifiers") or []
    return_type = method.get("return_type") or ""

    complexity = data.get("complexity") or {}
    cyclomatic = int(complexity.get("cyclomatic") or 1)
    nloc = complexity.get("nloc")

    location = (data.get("location") or {}).get("definition") or {}
    def_path = location.get("path") or ""
    def_line = int(location.get("line") or 0)

    deps = data.get("dependencies") or {}
    call_graph = data.get("call_graph") or []

    source_root = Path(args.source_root).resolve() if args.source_root else Path.cwd()
    snippet = extract_source_snippet(source_root, def_path, def_line) if def_line else "(no definition line)"

    pretty_json = json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False)

    prev_test_block = ""
    if args.previous_test and Path(args.previous_test).is_file():
        prev = _read_text(Path(args.previous_test)) or ""
        # Clip if huge.
        if len(prev) > 8000:
            prev = prev[:8000] + "\n// ... (truncated)\n"
        prev_test_block = (
            "## Previous attempt (this code failed — see error below, REPLACE it)\n"
            f"```cpp\n{prev}\n```\n"
        )

    prev_error_block = ""
    if args.previous_error:
        err = args.previous_error
        if Path(err).is_file():
            err_text = _read_text(Path(err)) or ""
        else:
            err_text = err
        if len(err_text) > 4000:
            err_text = err_text[-4000:]
        if err_text.strip():
            prev_error_block = (
                "## Previous failure (fix THIS)\n"
                "```\n"
                f"{err_text.strip()}\n"
                "```\n"
            )

    # Minimum-viable test count advice: at least cyclomatic (branches + 1 happy path)
    min_tests = max(2, cyclomatic)

    qualifier_hints = []
    if "static" in specifiers:
        qualifier_hints.append("method is static — call via `{class}::{name}(...)`".format(
            **{"class": class_name or "?", "name": qualified.rsplit("::", 1)[-1]}))
    if "const" in specifiers:
        qualifier_hints.append("method is const-qualified")
    if "noexcept" in specifiers:
        qualifier_hints.append("method is noexcept — no exception assertions required")
    if "override" in specifiers or "virtual" in specifiers:
        qualifier_hints.append("method is virtual/override — can be mocked via derived MOCK_METHOD")
    hints_line = "\n".join(f"- {h}" for h in qualifier_hints) or "(none)"

    bare_name = qualified.rsplit("::", 1)[-1] if qualified else "Target"
    _bare_cls = class_name.rsplit("::", 1)[-1] if class_name else bare_name
    # Strip template args and punctuation to get a valid C++ identifier.
    _sanitized = re.sub(r"<[^>]*>", "", _bare_cls)
    _sanitized = re.sub(r"[^A-Za-z0-9_]", "_", _sanitized)
    fixture_name = (_sanitized or "Global") + "Test"

    # Assemble the placeholder map once and let the template decide the layout.
    placeholders: dict[str, str] = {
        "QUALIFIED_NAME": qualified,
        "SIGNATURE": signature,
        "RETURN_TYPE": return_type,
        "PARAMETERS_JSON": json.dumps(parameters, ensure_ascii=False),
        "SPECIFIERS_JSON": json.dumps(specifiers, ensure_ascii=False),
        "CYCLOMATIC": str(cyclomatic),
        "NLOC": str(nloc) if nloc is not None else "?",
        "DEF_PATH": def_path,
        "DEF_LINE": str(def_line),
        "FIXTURE_NAME": fixture_name,
        "MIN_TESTS": str(min_tests),
        "METHOD_JSON": pretty_json,
        "SOURCE_SNIPPET": snippet,
        "DEPENDENCIES": format_deps(deps),
        "CALL_GRAPH": format_call_graph(call_graph),
        "SPECIFIER_HINTS": hints_line,
        "PREVIOUS_TEST_BLOCK": prev_test_block.rstrip("\n"),
        "PREVIOUS_ERROR_BLOCK": prev_error_block.rstrip("\n"),
    }

    template = _load_template(args.prompt_template)
    rendered = _apply_template(template, placeholders)
    # Drop runs of 3+ blank lines left behind when optional blocks are empty.
    rendered = re.sub(r"\n{3,}", "\n\n", rendered)
    if not rendered.endswith("\n"):
        rendered += "\n"
    return rendered


_DEFAULT_TEMPLATE_REL = Path("templates") / "prompts" / "default.md"


def _load_template(explicit: str | None) -> str:
    """Return the prompt template text.

    Search order:
      1. --prompt-template <path> (absolute or relative to cwd)
      2. scripts/templates/prompts/default.md (relative to this file)
    """
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).resolve())
    here = Path(__file__).resolve().parent  # .../scripts/lib
    candidates.append((here.parent / _DEFAULT_TEMPLATE_REL).resolve())

    for path in candidates:
        if path.is_file():
            return path.read_text(encoding="utf-8")
    tried = ", ".join(str(p) for p in candidates)
    raise SystemExit(f"prompt template not found; tried: {tried}")


_PLACEHOLDER_RE = re.compile(r"\{\{([A-Z0-9_]+)\}\}")


def _apply_template(template: str, values: dict[str, str]) -> str:
    """Substitute `{{NAME}}` placeholders. Unknown placeholders are preserved
    as-is so template authors get visible feedback instead of silent drops."""
    def _replace(match: "re.Match[str]") -> str:
        key = match.group(1)
        return values.get(key, match.group(0))
    return _PLACEHOLDER_RE.sub(_replace, template)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Build a methoddep test-generation prompt")
    ap.add_argument("--method-json", required=True, help="path to per-method methoddep JSON")
    ap.add_argument("--source-root", default=".", help="project source root (for snippet extraction)")
    ap.add_argument("--previous-test", default=None, help="path to previously generated .cpp (optional)")
    ap.add_argument("--previous-error", default=None,
                    help="last-attempt error text or path to a file containing it")
    ap.add_argument("--prompt-template", default=None,
                    help="path to a prompt template (.md). defaults to scripts/templates/prompts/default.md")
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    # Force UTF-8 stdout so Windows cp949/cp1252 default locales don't choke on
    # non-ASCII characters (em-dash, Korean comments in source snippets, etc).
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass
    sys.stdout.write(render(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
