"""Mock resolver.

Given a target class name and a workspace root, finds mock classes that
inherit from it (via libclang or tree-sitter fallback) in the configured
mock directories. Filename pattern match is *candidate selection* only —
inheritance verification is authoritative.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Literal

from methoddep.index import treesitter_index
from methoddep.index.models import IndexedSymbol


@dataclass
class MockMatch:
    target_class: str
    status: Literal["found", "missing"]
    mock_class: str | None = None
    header: str | None = None
    framework: Literal["gmock"] = "gmock"
    verified_inheritance: bool = False
    resolved_by: Literal["inheritance_parse", "glob", "none"] = "none"
    suggested_pattern: Literal["fake", "stub"] | None = None
    suggested_path: str | None = None
    gmock_stub_skeleton: str | None = None


def _expand_patterns(patterns: Iterable[str], class_name: str) -> list[str]:
    bare = class_name.rsplit("::", 1)[-1]
    out = []
    for pat in patterns:
        out.append(pat.replace("{Class}", bare))
    return out


def _iter_mock_headers(root: Path, mock_dirs: Iterable[str]) -> list[Path]:
    found: list[Path] = []
    for rel in mock_dirs:
        base = (root / rel).resolve() if not Path(rel).is_absolute() else Path(rel)
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if path.is_file() and treesitter_index.is_indexable(path):
                found.append(path)
    return found


def _parse_base_class(header: Path, mock_class_name: str) -> str | None:
    """Use tree-sitter to find `class MockX : public Foo::Bar` and return
    the fully-qualified base spelling if it inherits publicly."""
    source = header.read_bytes()
    if source.startswith(b"\xef\xbb\xbf"):
        source = source[3:]
    tree = treesitter_index._PARSER.parse(source)

    def walk(node):
        if node.type in {"class_specifier", "struct_specifier"}:
            name_node = None
            for c in node.named_children:
                if c.type == "type_identifier":
                    name_node = c
                    break
            if name_node is None:
                return None
            name = source[name_node.start_byte : name_node.end_byte].decode("utf-8", errors="replace")
            if name == mock_class_name:
                for c in node.named_children:
                    if c.type == "base_class_clause":
                        text = source[c.start_byte : c.end_byte].decode("utf-8", errors="replace")
                        # "base_class_clause" includes the leading ':'.
                        # Extract the first qualified identifier that
                        # follows `public`/`protected` (we only accept
                        # public inheritance for mocks).
                        return _first_public_base(text)
        for child in node.named_children:
            result = walk(child)
            if result:
                return result
        return None

    return walk(tree.root_node)


def _first_public_base(base_clause_text: str) -> str | None:
    parts = base_clause_text.lstrip(":").split(",")
    for part in parts:
        tokens = part.strip().split()
        if not tokens:
            continue
        if tokens[0] == "public":
            tokens = tokens[1:]
        elif tokens[0] in {"protected", "private"}:
            return None  # mocks should inherit publicly
        if tokens:
            base = tokens[0].split("<", 1)[0]
            return base
    return None


def _resolve_ns(bare_base: str, target_class: str) -> bool:
    """Return True if the (possibly-unqualified) base spelling matches
    the target class name."""
    if "::" in bare_base:
        return bare_base == target_class
    return bare_base == target_class.rsplit("::", 1)[-1]


def resolve_mocks(
    target_classes: Iterable[str],
    *,
    workspace_root: Path,
    mock_dirs: Iterable[str],
    name_patterns: Iterable[str],
    gmock_virtual_methods: dict[str, list[dict[str, str]]] | None = None,
) -> list[MockMatch]:
    """Resolve all target classes; returns a MockMatch per target."""
    headers = _iter_mock_headers(workspace_root, mock_dirs)
    gmock_virtual_methods = gmock_virtual_methods or {}

    results: list[MockMatch] = []
    for target in target_classes:
        candidates = _expand_patterns(name_patterns, target)
        match = _find_match(target, candidates, headers, workspace_root)
        if match is None:
            # Emit a missing record with a stub skeleton (if virtuals known).
            vm = gmock_virtual_methods.get(target, [])
            skeleton = None
            if vm:
                bare = target.rsplit("::", 1)[-1]
                skeleton = _render_skeleton(bare, target, vm)
            results.append(
                MockMatch(
                    target_class=target,
                    status="missing",
                    suggested_pattern="fake",
                    suggested_path=f"tests/mocks/Mock{target.rsplit('::', 1)[-1]}.h",
                    gmock_stub_skeleton=skeleton,
                )
            )
        else:
            results.append(match)
    return results


def _find_match(target: str, candidate_names: list[str], headers: list[Path], root: Path) -> MockMatch | None:
    for header in headers:
        source = header.read_bytes()
        if source.startswith(b"\xef\xbb\xbf"):
            source = source[3:]
        tree = treesitter_index._PARSER.parse(source)
        for cand in candidate_names:
            # Only inspect classes with the candidate name — cheap filter.
            if cand.encode("utf-8") not in source:
                continue
            base = _parse_base_class(header, cand)
            if base is None:
                continue
            if _resolve_ns(base, target):
                try:
                    rel = header.relative_to(root).as_posix()
                except ValueError:
                    rel = header.as_posix()
                return MockMatch(
                    target_class=target,
                    status="found",
                    mock_class=f"test::{cand}",
                    header=rel,
                    verified_inheritance=True,
                    resolved_by="inheritance_parse",
                )
    return None


def _render_skeleton(class_name: str, target: str, virtuals: list[dict[str, str]]) -> str:
    lines = [f"class Mock{class_name} : public {target} {{", "public:"]
    for vm in virtuals:
        ret = vm.get("return_type", "void")
        name = vm["name"]
        args = vm.get("args", "")
        extras = vm.get("extras", "(override)")
        lines.append(f"    MOCK_METHOD({ret}, {name}, ({args}), {extras});")
    lines.append("};")
    return "\n".join(lines)
