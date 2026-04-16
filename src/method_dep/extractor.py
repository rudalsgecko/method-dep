"""Extract methods from C++ source files using tree-sitter."""
from __future__ import annotations
import json
import re
from pathlib import Path
from typing import Optional

import tree_sitter_cpp as tscpp
from tree_sitter import Language, Parser, Node

from .models import MethodInfo

CPP_LANGUAGE = Language(tscpp.language())


def create_parser() -> Parser:
    parser = Parser(CPP_LANGUAGE)
    return parser


def extract_methods_from_file(file_path: Path, project_root: Path) -> list[MethodInfo]:
    """Extract all method/function definitions from a C++ source file."""
    parser = create_parser()
    source = file_path.read_bytes()
    tree = parser.parse(source)
    source_text = source.decode("utf-8", errors="replace")
    lines = source_text.split("\n")

    rel_path = str(file_path.relative_to(project_root)).replace("\\", "/")
    methods: list[MethodInfo] = []

    _walk_for_functions(tree.root_node, source_text, lines, rel_path, methods, namespace_stack=[])
    return methods


def _walk_for_functions(
    node: Node,
    source: str,
    lines: list[str],
    file_path: str,
    methods: list[MethodInfo],
    namespace_stack: list[str],
    class_name: str = "",
) -> None:
    """Recursively walk AST to find function/method definitions."""

    if node.type == "namespace_definition":
        ns_name = ""
        for child in node.children:
            if child.type == "namespace_identifier" or child.type == "identifier":
                ns_name = _node_text(child, source)
                break
        body = _find_child(node, "declaration_list")
        if body:
            new_stack = namespace_stack + ([ns_name] if ns_name else [])
            for child in body.children:
                _walk_for_functions(child, source, lines, file_path, methods, new_stack, class_name)
        return

    if node.type in ("class_specifier", "struct_specifier"):
        cname = ""
        for child in node.children:
            if child.type == "type_identifier" or child.type == "name":
                cname = _node_text(child, source)
                break
        body = _find_child(node, "field_declaration_list")
        if body:
            for child in body.children:
                _walk_for_functions(child, source, lines, file_path, methods, namespace_stack, cname)
        return

    if node.type == "function_definition":
        method = _parse_function_definition(node, source, lines, file_path, namespace_stack, class_name)
        if method:
            methods.append(method)
        return

    # For top-level or other containers, recurse into children
    for child in node.children:
        _walk_for_functions(child, source, lines, file_path, methods, namespace_stack, class_name)


def _parse_function_definition(
    node: Node,
    source: str,
    lines: list[str],
    file_path: str,
    namespace_stack: list[str],
    class_name: str,
) -> Optional[MethodInfo]:
    """Parse a function_definition node into MethodInfo."""
    # Extract return type
    return_type = ""
    type_node = _find_child(node, "type_identifier") or _find_child(node, "primitive_type") \
        or _find_child(node, "qualified_identifier") or _find_child(node, "template_type") \
        or _find_child(node, "auto")
    # More robust: get the type from the first type-like child
    for child in node.children:
        if child.type in (
            "type_identifier", "primitive_type", "qualified_identifier",
            "template_type", "auto", "sized_type_specifier", "placeholder_type_specifier",
        ):
            return_type = _node_text(child, source)
            break
        if child.type == "type_qualifier":
            return_type = _node_text(child, source) + " "
            continue
        if child.type == "storage_class_specifier":
            continue
        if child.type in ("virtual", "inline", "static", "explicit", "constexpr"):
            continue
        if child.type == "function_declarator":
            break

    # Extract declarator
    declarator = _find_child(node, "function_declarator")
    if not declarator:
        # Could be a pointer_declarator wrapping function_declarator
        ptr_decl = _find_child(node, "pointer_declarator")
        if ptr_decl:
            declarator = _find_child(ptr_decl, "function_declarator")
        if not declarator:
            return None

    # Get method name from declarator
    name_node = _find_child(declarator, "identifier") \
        or _find_child(declarator, "qualified_identifier") \
        or _find_child(declarator, "field_identifier") \
        or _find_child(declarator, "destructor_name") \
        or _find_child(declarator, "operator_name") \
        or _find_child(declarator, "template_method")

    if not name_node:
        return None

    raw_name = _node_text(name_node, source)

    # Determine class name from qualified identifier (e.g., ClassName::methodName)
    detected_class = class_name
    method_name = raw_name
    if name_node.type == "qualified_identifier":
        parts = raw_name.split("::")
        if len(parts) >= 2:
            detected_class = "::".join(parts[:-1])
            method_name = parts[-1]
    elif "::" in raw_name:
        parts = raw_name.split("::")
        detected_class = "::".join(parts[:-1])
        method_name = parts[-1]

    # Skip destructors, operators (optional: could include them)
    if method_name.startswith("~"):
        return None

    # Build qualified name
    ns = "::".join(namespace_stack) if namespace_stack else ""
    parts = []
    if ns:
        parts.append(ns)
    if detected_class:
        parts.append(detected_class)
    parts.append(method_name)
    qualified_name = "::".join(parts)

    # Extract parameters
    params_node = _find_child(declarator, "parameter_list")
    parameters = _extract_parameters(params_node, source) if params_node else []

    # Signature
    param_str = _node_text(params_node, source) if params_node else "()"
    signature = f"{return_type} {raw_name}{param_str}".strip()

    # Body
    body_node = _find_child(node, "compound_statement")
    body = _node_text(body_node, source) if body_node else ""

    # Full source including signature
    full_source = _node_text(node, source)

    line_start = node.start_point[0] + 1
    line_end = node.end_point[0] + 1

    return MethodInfo(
        file_path=file_path,
        class_name=detected_class,
        method_name=method_name,
        qualified_name=qualified_name,
        signature=signature,
        return_type=return_type,
        parameters=parameters,
        line_start=line_start,
        line_end=line_end,
        body=full_source,
        namespace=ns,
    )


def _extract_parameters(params_node: Node, source: str) -> list[str]:
    """Extract parameter list as ['type name', ...] strings."""
    params = []
    for child in params_node.children:
        if child.type == "parameter_declaration":
            params.append(_node_text(child, source).strip())
        elif child.type == "optional_parameter_declaration":
            params.append(_node_text(child, source).strip())
        elif child.type == "variadic_parameter_declaration":
            params.append(_node_text(child, source).strip())
    return params


def _find_child(node: Node, type_name: str) -> Optional[Node]:
    """Find first direct child of given type."""
    for child in node.children:
        if child.type == type_name:
            return child
    return None


def _node_text(node: Node, source: str) -> str:
    """Get source text for a node."""
    return source[node.start_byte:node.end_byte]


def scan_project(project_root: Path, extensions: list[str], exclude_patterns: list[str]) -> list[Path]:
    """Find all C++ source files in the project."""
    files = []
    for ext in extensions:
        for f in project_root.rglob(f"*{ext}"):
            rel = str(f.relative_to(project_root)).replace("\\", "/")
            if not any(_match_pattern(rel, pat) for pat in exclude_patterns):
                files.append(f)
    return sorted(files)


def _match_pattern(path: str, pattern: str) -> bool:
    """Simple glob pattern matching."""
    import fnmatch
    return fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(path, f"**/{pattern}")


def extract_all_methods(project_root: Path, extensions: list[str], exclude_patterns: list[str]) -> list[MethodInfo]:
    """Extract all methods from all source files in the project."""
    all_methods = []
    source_files = scan_project(project_root, extensions, exclude_patterns)
    for f in source_files:
        try:
            methods = extract_methods_from_file(f, project_root)
            all_methods.extend(methods)
        except Exception as e:
            print(f"  [WARN] Failed to parse {f}: {e}")
    return all_methods
