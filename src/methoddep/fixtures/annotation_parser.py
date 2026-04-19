"""Parse `@methoddep:expect` fixture annotations.

Grammar (see plan §Fixture Annotation Grammar):

    block       = "// @methoddep:expect" NL line+
    line        = "//" SP{2,} key ":" SP* value NL
    key         = "classes" | "data_structures" | "free_functions"
                | "calls" | "globals_read" | "globals_written"
                | "static_locals" | "enums" | "throws" | "cc_max"
    value       = item (";" SP* item)*     ; depth-aware split
    item        = qualified_name [ "{" members "}" ]    ; {} only for enums
    members     = ident ("," SP* ident)*

The splitter tracks `<>` and `{}` depth so template commas and enum
member commas never collide with item separators.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from tree_sitter import Node

from methoddep.index.treesitter_index import _PARSER, parse_file  # noqa: F401


ALLOWED_KEYS: set[str] = {
    "classes",
    "data_structures",
    "free_functions",
    "calls",
    "globals_read",
    "globals_written",
    "static_locals",
    "enums",
    "throws",
    "cc_max",
}


@dataclass
class ExpectBlock:
    """Parsed annotation attached to one method declaration/definition."""

    method_line: int                           # line of the bound method cursor
    method_path: Path                          # source file
    classes: set[str] = field(default_factory=set)
    data_structures: set[str] = field(default_factory=set)
    free_functions: set[str] = field(default_factory=set)
    calls: set[str] = field(default_factory=set)
    globals_read: set[str] = field(default_factory=set)
    globals_written: set[str] = field(default_factory=set)
    static_locals: set[str] = field(default_factory=set)
    enums: dict[str, set[str]] = field(default_factory=dict)
    throws: set[str] = field(default_factory=set)
    cc_max: int | None = None

    def is_empty(self) -> bool:
        return not any(
            [
                self.classes,
                self.data_structures,
                self.free_functions,
                self.calls,
                self.globals_read,
                self.globals_written,
                self.static_locals,
                self.enums,
                self.throws,
                self.cc_max is not None,
            ]
        )


_LINE_RE = re.compile(r"^\s*//\s{2,}(?P<key>[a-z_]+)\s*:\s*(?P<value>.*?)\s*$")
_BLOCK_START_RE = re.compile(r"^\s*//\s*@methoddep:expect\s*$")
_COMMENT_BODY_RE = re.compile(r"^\s*//(?:\s.*)?$")


def _split_depth_aware(value: str) -> list[str]:
    """Split on `;` at depth 0 (outside `<>` and `{}`)."""
    items: list[str] = []
    buf: list[str] = []
    angle = 0
    brace = 0
    for ch in value:
        if ch == "<":
            angle += 1
        elif ch == ">":
            angle = max(0, angle - 1)
        elif ch == "{":
            brace += 1
        elif ch == "}":
            brace = max(0, brace - 1)
        if ch == ";" and angle == 0 and brace == 0:
            items.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    tail = "".join(buf).strip()
    if tail:
        items.append(tail)
    return items


def _parse_enum_item(item: str) -> tuple[str, set[str]]:
    """Return (qualified_name, members) for `Status{OK,Retry}` syntax."""
    if "{" in item and item.endswith("}"):
        qname, members_part = item.split("{", 1)
        members = {m.strip() for m in members_part[:-1].split(",") if m.strip()}
        return qname.strip(), members
    return item.strip(), set()


def _parse_block_text(lines: list[str]) -> dict[str, str]:
    """Convert raw comment lines (without the block marker) into a key-map.

    Duplicate keys are merged by joining with `;`.
    """
    parsed: dict[str, str] = {}
    for raw in lines:
        m = _LINE_RE.match(raw)
        if not m:
            continue
        key = m.group("key")
        if key not in ALLOWED_KEYS:
            raise ValueError(f"unknown annotation key: {key!r}")
        value = m.group("value")
        if key in parsed and value:
            parsed[key] = parsed[key] + "; " + value
        else:
            parsed[key] = value
    return parsed


def _apply_to_block(block: ExpectBlock, parsed: dict[str, str]) -> None:
    for key, value in parsed.items():
        items = _split_depth_aware(value) if value else []
        if key == "classes":
            block.classes.update(items)
        elif key == "data_structures":
            block.data_structures.update(items)
        elif key == "free_functions":
            block.free_functions.update(items)
        elif key == "calls":
            block.calls.update(items)
        elif key == "globals_read":
            block.globals_read.update(items)
        elif key == "globals_written":
            block.globals_written.update(items)
        elif key == "static_locals":
            block.static_locals.update(items)
        elif key == "throws":
            block.throws.update(items)
        elif key == "enums":
            for item in items:
                qname, members = _parse_enum_item(item)
                block.enums.setdefault(qname, set()).update(members)
        elif key == "cc_max":
            block.cc_max = int(value)


_FUNCTION_BINDING_TYPES = {
    "function_definition",
    "field_declaration",
    "declaration",
    "template_declaration",
}


def _next_function_sibling(start_node: Node) -> Node | None:
    """Walk forward from `start_node` looking for the next named sibling
    that is a function-ish declaration."""
    node = start_node.next_named_sibling
    while node is not None:
        if node.type in _FUNCTION_BINDING_TYPES:
            return node
        if node.type == "comment":
            node = node.next_named_sibling
            continue
        # Other siblings (unexpected) break the binding.
        return None
    return None


def _locate_blocks(path: Path) -> list[ExpectBlock]:
    """Scan `path` for annotation blocks and bind each to the next
    function-like cursor. Returns one ExpectBlock per block found.
    """
    source_bytes = path.read_bytes()
    if source_bytes.startswith(b"\xef\xbb\xbf"):
        source_bytes = source_bytes[3:]
    text_lines = source_bytes.decode("utf-8", errors="replace").splitlines()

    # Find consecutive-comment runs that start with @methoddep:expect.
    blocks: list[tuple[int, list[str]]] = []
    i = 0
    while i < len(text_lines):
        line = text_lines[i]
        if _BLOCK_START_RE.match(line):
            start_line = i + 1  # 1-based
            body: list[str] = []
            i += 1
            while i < len(text_lines) and _COMMENT_BODY_RE.match(text_lines[i]):
                body.append(text_lines[i])
                i += 1
            blocks.append((start_line, body))
            continue
        i += 1

    if not blocks:
        return []

    # Use tree-sitter to map each block to a function cursor.
    tree = _PARSER.parse(source_bytes)
    root = tree.root_node

    # Gather comment nodes with @methoddep:expect by line number.
    comment_nodes: dict[int, Node] = {}

    def walk(node: Node) -> None:
        if node.type == "comment":
            text = source_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
            if "@methoddep:expect" in text:
                comment_nodes[node.start_point[0] + 1] = node
        for child in node.named_children:
            walk(child)

    walk(root)

    out: list[ExpectBlock] = []
    for start_line, body in blocks:
        node = comment_nodes.get(start_line)
        if node is None:
            raise ValueError(
                f"{path}:{start_line}: @methoddep:expect block not locatable in tree-sitter parse"
            )
        fn_node = _next_function_sibling(node)
        if fn_node is None:
            raise ValueError(
                f"{path}:{start_line}: @methoddep:expect block is not followed by a function declaration"
            )
        block = ExpectBlock(method_line=fn_node.start_point[0] + 1, method_path=path)
        parsed = _parse_block_text(body)
        _apply_to_block(block, parsed)
        out.append(block)
    return out


def parse_annotations(paths: Iterable[Path]) -> list[ExpectBlock]:
    """Parse annotations from the given files in deterministic order."""
    all_blocks: list[ExpectBlock] = []
    for path in sorted(paths, key=lambda p: p.as_posix()):
        if not path.is_file():
            continue
        blocks = _locate_blocks(path)
        all_blocks.extend(blocks)
    return all_blocks
