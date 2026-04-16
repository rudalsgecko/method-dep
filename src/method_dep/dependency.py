"""Dependency analysis: resolve types and symbols used by each method."""
from __future__ import annotations
import json
import re
import subprocess
from pathlib import Path
from typing import Optional

import tree_sitter_cpp as tscpp
from tree_sitter import Language, Parser, Node

from .models import MethodInfo, SymbolDependency, SymbolKind

CPP_LANGUAGE = Language(tscpp.language())


class DependencyAnalyzer:
    """Analyze dependencies for C++ methods using ctags + tree-sitter."""

    def __init__(self, project_root: Path, compile_commands_path: Optional[Path] = None):
        self.project_root = project_root
        self.compile_commands_path = compile_commands_path
        self.parser = Parser(CPP_LANGUAGE)

        # Symbol table: name -> list of (kind, file, line, definition)
        self._symbol_table: dict[str, list[dict]] = {}
        self._include_map: dict[str, list[str]] = {}  # file -> included headers
        self._file_cache: dict[str, str] = {}

    def build_symbol_table(self) -> None:
        """Build global symbol table using ctags."""
        print("  Building symbol table with ctags...")
        try:
            result = subprocess.run(
                [
                    "ctags", "--output-format=json",
                    "--kinds-C++=csgefdtum",  # class,struct,enum,func,member,typedef,union,macro
                    "--fields=+nSKl",          # line, signature, kind full, language
                    "--extras=+q",             # qualified tags
                    "-R", ".",
                ],
                capture_output=True, text=True, cwd=self.project_root,
                timeout=120,
            )
        except FileNotFoundError:
            print("  [WARN] ctags not found, falling back to tree-sitter only")
            self._build_symbol_table_treesitter()
            return
        except subprocess.TimeoutExpired:
            print("  [WARN] ctags timed out, falling back to tree-sitter only")
            self._build_symbol_table_treesitter()
            return

        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            try:
                tag = json.loads(line)
            except json.JSONDecodeError:
                continue

            name = tag.get("name", "")
            kind_str = tag.get("kind", "").lower()
            file_path = tag.get("path", "")
            line_no = tag.get("line", 0)

            kind = _map_ctags_kind(kind_str)
            if kind is None:
                continue

            if name not in self._symbol_table:
                self._symbol_table[name] = []

            self._symbol_table[name].append({
                "kind": kind,
                "file": file_path,
                "line": line_no,
                "signature": tag.get("signature", ""),
                "scope": tag.get("scope", ""),
                "scopeKind": tag.get("scopeKind", ""),
            })

        print(f"  Found {sum(len(v) for v in self._symbol_table.values())} symbols")

    def _build_symbol_table_treesitter(self) -> None:
        """Fallback: build symbol table by parsing all headers with tree-sitter."""
        header_exts = {".h", ".hpp", ".hxx"}
        src_exts = {".cpp", ".cc", ".cxx", ".c"}
        all_exts = header_exts | src_exts

        for f in self.project_root.rglob("*"):
            if f.suffix not in all_exts:
                continue
            rel = str(f.relative_to(self.project_root)).replace("\\", "/")
            if any(p in rel for p in ["build/", "third_party/", ".method-dep/"]):
                continue
            try:
                source = f.read_text(encoding="utf-8", errors="replace")
                tree = self.parser.parse(source.encode())
                self._extract_symbols_from_tree(tree.root_node, source, rel)
            except Exception:
                continue

    def _extract_symbols_from_tree(self, node: Node, source: str, file_path: str) -> None:
        """Extract type definitions from a tree-sitter AST."""
        for child in node.children:
            if child.type in ("class_specifier", "struct_specifier"):
                name = _get_type_name(child, source)
                if name:
                    kind = SymbolKind.CLASS if child.type == "class_specifier" else SymbolKind.STRUCT
                    self._add_symbol(name, kind, file_path, child.start_point[0] + 1)

            elif child.type == "enum_specifier":
                name = _get_type_name(child, source)
                if name:
                    self._add_symbol(name, SymbolKind.ENUM, file_path, child.start_point[0] + 1)

            elif child.type == "type_definition":
                # typedef
                for c in child.children:
                    if c.type == "type_identifier":
                        name = source[c.start_byte:c.end_byte]
                        self._add_symbol(name, SymbolKind.TYPEDEF, file_path, child.start_point[0] + 1)

            elif child.type == "function_definition":
                name = _get_func_name(child, source)
                if name:
                    self._add_symbol(name, SymbolKind.FUNCTION, file_path, child.start_point[0] + 1)

            elif child.type == "namespace_definition":
                body = None
                for c in child.children:
                    if c.type == "declaration_list":
                        body = c
                        break
                if body:
                    self._extract_symbols_from_tree(body, source, file_path)

            elif child.type == "declaration":
                self._extract_symbols_from_tree(child, source, file_path)

    def _add_symbol(self, name: str, kind: SymbolKind, file_path: str, line: int) -> None:
        if name not in self._symbol_table:
            self._symbol_table[name] = []
        self._symbol_table[name].append({
            "kind": kind,
            "file": file_path,
            "line": line,
            "signature": "",
            "scope": "",
            "scopeKind": "",
        })

    def analyze_method(self, method: MethodInfo) -> list[SymbolDependency]:
        """Find all type dependencies for a given method."""
        # Parse the method body to find type references
        type_refs = self._extract_type_references(method)

        deps: list[SymbolDependency] = []
        seen: set[str] = set()

        for type_name in type_refs:
            if type_name in seen:
                continue
            seen.add(type_name)

            # Look up in symbol table
            base_name = type_name.split("::")[-1]
            candidates = self._symbol_table.get(base_name, []) + self._symbol_table.get(type_name, [])

            if not candidates:
                continue

            # Pick the best candidate (prefer same file, then headers)
            best = self._pick_best_candidate(candidates, method.file_path)
            if best is None:
                continue

            # Skip self-references (same method appearing as its own dependency)
            if (best.get("kind") in (SymbolKind.FUNCTION, "function")
                    and best["file"] == method.file_path
                    and best["line"] == method.line_start):
                continue

            # Read the definition source
            definition = self._read_definition(best["file"], best["line"], best.get("kind", SymbolKind.CLASS))

            kind = best["kind"] if isinstance(best["kind"], SymbolKind) else _map_ctags_kind(str(best["kind"])) or SymbolKind.CLASS

            is_ext = _is_external(best["file"])

            deps.append(SymbolDependency(
                name=type_name,
                kind=kind,
                file_path=best["file"],
                line=best["line"],
                definition=definition,
                is_external=is_ext,
            ))

        return deps

    def _extract_type_references(self, method: MethodInfo) -> list[str]:
        """Extract type names referenced in a method body using tree-sitter."""
        types: list[str] = []
        source = method.body.encode()
        tree = self.parser.parse(source)

        self._collect_type_nodes(tree.root_node, method.body, types)

        # Also extract from parameter types
        for param in method.parameters:
            # Extract type part from "const SomeType& name" etc.
            cleaned = re.sub(r'[&*]', '', param)
            cleaned = re.sub(r'\bconst\b', '', cleaned)
            cleaned = re.sub(r'\bvolatile\b', '', cleaned)
            cleaned = re.sub(r'\bunsigned\b', '', cleaned)
            cleaned = re.sub(r'\bsigned\b', '', cleaned)
            parts = cleaned.strip().split()
            for p in parts:
                p = p.strip()
                if p and not _is_primitive(p) and re.match(r'^[A-Z_]', p):
                    types.append(p)

        # From return type
        ret = method.return_type.strip()
        ret = re.sub(r'[&*]', '', ret)
        ret = re.sub(r'\bconst\b', '', ret).strip()
        if ret and not _is_primitive(ret) and re.match(r'^[A-Z_]', ret):
            types.append(ret)

        # From class member types (if this is a class method, scan the class definition
        # to find member variable types that this method might use, e.g. ILogger* logger_)
        if method.class_name:
            class_name = method.class_name.split("::")[-1]
            class_entries = self._symbol_table.get(class_name, [])
            for entry in class_entries:
                f = entry.get("file", "")
                line = entry.get("line", 0)
                class_def = self._read_definition(f, line, SymbolKind.CLASS)
                if class_def:
                    # Find member declarations: Type* name_; or Type& name_;
                    for m in re.finditer(r'(\w+)\s*[*&]+\s*\w+\s*;', class_def):
                        member_type = m.group(1)
                        if not _is_primitive(member_type) and re.match(r'^[A-Z_]', member_type):
                            types.append(member_type)
                    # Also find value members: Type name_;
                    for m in re.finditer(r'^\s*(\w+)\s+\w+_\s*;', class_def, re.MULTILINE):
                        member_type = m.group(1)
                        if not _is_primitive(member_type) and re.match(r'^[A-Z_]', member_type):
                            types.append(member_type)
                    break  # use first class definition found

        # Deduplicate preserving order
        seen = set()
        unique = []
        for t in types:
            if t not in seen:
                seen.add(t)
                unique.append(t)
        return unique

    def _collect_type_nodes(self, node: Node, source: str, types: list[str]) -> None:
        """Recursively collect type identifier nodes."""
        if node.type == "type_identifier":
            name = source[node.start_byte:node.end_byte]
            if not _is_primitive(name):
                types.append(name)
        elif node.type == "qualified_identifier":
            name = source[node.start_byte:node.end_byte]
            types.append(name)
        elif node.type == "template_type":
            # Get the base type
            for child in node.children:
                if child.type == "type_identifier":
                    name = source[child.start_byte:child.end_byte]
                    if not _is_primitive(name) and name not in ("std", "vector", "map", "set", "string",
                                                                  "shared_ptr", "unique_ptr", "optional",
                                                                  "pair", "tuple", "array", "list", "deque"):
                        types.append(name)
                    break
            # Also check template arguments for user types
            for child in node.children:
                if child.type == "template_argument_list":
                    self._collect_type_nodes(child, source, types)
                    break

        for child in node.children:
            if child.type not in ("template_type",):  # avoid double-counting
                self._collect_type_nodes(child, source, types)

    def _pick_best_candidate(self, candidates: list[dict], method_file: str) -> Optional[dict]:
        """Pick best symbol definition from candidates."""
        if not candidates:
            return None

        # Score candidates
        scored = []
        for c in candidates:
            score = 0
            f = c["file"]
            if f == method_file:
                score += 10
            elif f.endswith(".h") or f.endswith(".hpp"):
                score += 5
            if not _is_external(f):
                score += 3
            scored.append((score, c))

        scored.sort(key=lambda x: -x[0])
        return scored[0][1]

    def _read_definition(self, file_path: str, start_line: int, kind) -> str:
        """Read the full definition of a symbol from source."""
        abs_path = self.project_root / file_path
        if not abs_path.exists():
            return ""

        if file_path not in self._file_cache:
            try:
                self._file_cache[file_path] = abs_path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                return ""

        source = self._file_cache[file_path]
        lines = source.split("\n")

        if start_line < 1 or start_line > len(lines):
            return ""

        # Use tree-sitter to find the exact node at this line
        tree = self.parser.parse(source.encode())
        target_node = self._find_definition_node(tree.root_node, start_line - 1, source)

        if target_node:
            return source[target_node.start_byte:target_node.end_byte]

        # Fallback: grab lines from start until closing brace
        result_lines = []
        brace_depth = 0
        started = False
        for i in range(start_line - 1, min(start_line + 200, len(lines))):
            line = lines[i]
            result_lines.append(line)
            brace_depth += line.count("{") - line.count("}")
            if "{" in line:
                started = True
            if started and brace_depth <= 0:
                break
            if not started and line.rstrip().endswith(";"):
                break

        return "\n".join(result_lines)

    def _find_definition_node(self, node: Node, target_line: int, source: str) -> Optional[Node]:
        """Find the AST node that defines something at the target line."""
        definition_types = {
            "class_specifier", "struct_specifier", "enum_specifier",
            "function_definition", "type_definition", "declaration",
        }

        if node.type in definition_types and node.start_point[0] == target_line:
            # For declarations that are part of a larger statement, get the parent
            return node

        for child in node.children:
            if child.start_point[0] <= target_line <= child.end_point[0]:
                result = self._find_definition_node(child, target_line, source)
                if result:
                    return result

        return None

    def find_mock_candidates(self, method: MethodInfo, deps: list[SymbolDependency]) -> list[SymbolDependency]:
        """Identify dependencies that should be mocked (interfaces, abstract classes, external deps)."""
        mocks = []
        for dep in deps:
            if dep.is_external:
                continue
            # Check if it's a class with virtual methods (potential interface)
            if dep.kind in (SymbolKind.CLASS, SymbolKind.STRUCT):
                if "virtual" in dep.definition or "= 0" in dep.definition:
                    mocks.append(dep)
                elif dep.kind == SymbolKind.CLASS and _has_methods(dep.definition):
                    mocks.append(dep)
        return mocks


def _get_type_name(node: Node, source: str) -> str:
    for child in node.children:
        if child.type in ("type_identifier", "name", "identifier"):
            return source[child.start_byte:child.end_byte]
    return ""


def _get_func_name(node: Node, source: str) -> str:
    for child in node.children:
        if child.type == "function_declarator":
            for c in child.children:
                if c.type in ("identifier", "qualified_identifier", "field_identifier"):
                    return source[c.start_byte:c.end_byte]
    return ""


def _map_ctags_kind(kind_str: str) -> Optional[SymbolKind]:
    mapping = {
        "class": SymbolKind.CLASS,
        "struct": SymbolKind.STRUCT,
        "enum": SymbolKind.ENUM,
        "function": SymbolKind.FUNCTION,
        "member": SymbolKind.METHOD,
        "typedef": SymbolKind.TYPEDEF,
        "macro": SymbolKind.MACRO,
        "namespace": SymbolKind.NAMESPACE,
        "enumerator": SymbolKind.ENUM,
        "union": SymbolKind.STRUCT,
    }
    return mapping.get(kind_str)


def _is_primitive(name: str) -> bool:
    primitives = {
        "int", "float", "double", "char", "bool", "void", "long", "short",
        "unsigned", "signed", "size_t", "int8_t", "int16_t", "int32_t", "int64_t",
        "uint8_t", "uint16_t", "uint32_t", "uint64_t", "auto", "string",
        "wchar_t", "char16_t", "char32_t", "ptrdiff_t", "nullptr_t",
    }
    return name in primitives


def _is_external(file_path: str) -> bool:
    external_indicators = ["third_party/", "external/", "vendor/", "/usr/", "C:/Program"]
    return any(ind in file_path for ind in external_indicators)


def _has_methods(definition: str) -> bool:
    return bool(re.search(r'\w+\s*\([^)]*\)\s*(const)?\s*[{;]', definition))
