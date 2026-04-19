"""L2 tree-sitter analyzer — fallback for TUs libclang cannot parse.

Produces partial `AnalyzedMethod` records. Without libclang we cannot
resolve header locations or canonical types, but we can still extract
the NAMES of types referenced in parameters and return types by
lexically scanning the type strings. This gives downstream LLMs a
"this method depends on X" signal even when the precise declaration
site is unknown (`header=None`).
"""

from __future__ import annotations

import re
from pathlib import Path

from methoddep.analyze.models import (
    AnalyzedMethod,
    CallSite,
    DependencyClass,
    DependencyEnum,
    ExceptionSpec,
    MethodSpecifiers,
    Parameter,
    SourceLoc,
)
from methoddep.index.treesitter_index import parse_file as ts_parse_file


# C++ builtin types — excluded from dep extraction (no info for tests).
_BUILTIN_TYPES: set[str] = {
    "void", "bool", "char", "signed", "unsigned",
    "short", "int", "long", "float", "double",
    "wchar_t", "char8_t", "char16_t", "char32_t",
    "size_t", "ptrdiff_t", "nullptr_t", "auto",
    "int8_t", "int16_t", "int32_t", "int64_t",
    "uint8_t", "uint16_t", "uint32_t", "uint64_t",
    "intptr_t", "uintptr_t",
}
_TYPE_QUALIFIERS: set[str] = {
    "const", "volatile", "mutable", "constexpr",
    "static", "inline", "extern", "register", "thread_local",
    "typename", "class", "struct", "enum", "union",
}
_QUALIFIED_ID_RE = re.compile(r"[A-Za-z_]\w*(?:\s*::\s*[A-Za-z_]\w*)*")


def _extract_type_names(type_str: str) -> list[str]:
    """Pull qualified identifiers out of a raw type string.

    Drops builtins, qualifiers, and std::* (std types live in their own
    bucket; here we only care about project-local symbols).
    """
    if not type_str:
        return []
    names: list[str] = []
    for match in _QUALIFIED_ID_RE.findall(type_str):
        cleaned = match.replace(" ", "")
        if cleaned in _BUILTIN_TYPES or cleaned in _TYPE_QUALIFIERS:
            continue
        if cleaned.startswith("std::") or cleaned == "std":
            continue
        if cleaned not in names:
            names.append(cleaned)
    return names


def _parameter_records(params: list[dict[str, str]]) -> list[Parameter]:
    return [
        Parameter(
            name=p.get("name") or "",
            type=p.get("type") or "",
            direction="in",
            default_value=None,
        )
        for p in params
    ]


def _deps_from_signature(
    parameters: list[dict[str, str]], return_type: str | None
) -> list[DependencyClass]:
    """Build DependencyClass entries from parameter/return type strings.

    L2 cannot resolve declarations, so `header` is left None. The LLM
    treats this as "project symbol, look it up if needed".
    """
    seen: dict[str, DependencyClass] = {}
    for p in parameters:
        for name in _extract_type_names(p.get("type", "")):
            dep = seen.setdefault(
                name,
                DependencyClass(qualified_name=name, kind="class", header=None),
            )
            dep.used_as.add("parameter")
    if return_type:
        for name in _extract_type_names(return_type):
            dep = seen.setdefault(
                name,
                DependencyClass(qualified_name=name, kind="class", header=None),
            )
            dep.used_as.add("return")
    return sorted(seen.values(), key=lambda d: d.qualified_name)


def analyze_file(path: Path, *, workspace_root: Path | None = None) -> list[AnalyzedMethod]:
    """Extract partial method facts from a single file using tree-sitter.

    Only methods with a body (definition) are emitted; declaration-only
    methods are left to libclang/L1 or the index layer.
    """
    _, indexed = ts_parse_file(path)
    out: list[AnalyzedMethod] = []
    for im in indexed:
        definition = im.definition
        if definition is None:
            continue
        method = AnalyzedMethod(
            qualified_name=im.qualified_name,
            class_name=im.class_name,
            namespace=im.namespace,
            signature=im.signature,
            raw_signature=im.signature,
            return_type=im.return_type,
            parameters=_parameter_records(im.parameters),
            specifiers=MethodSpecifiers(
                virtual=im.is_virtual,
                pure=im.is_pure,
                static=im.is_static,
                const=im.is_const,
            ),
            access=im.access,
            declaration=None,
            definition=SourceLoc(path=definition.path, line=definition.line, column=definition.column),
            defined_in_header=im.defined_in_header,
            exception_spec=ExceptionSpec(),
            friends_of_class=[],
            dep_classes=_deps_from_signature(im.parameters, im.return_type),
            dep_data_structures=[],
            dep_free_functions=[],
            dep_enums=[],
            dep_globals_read=[],
            dep_globals_written=[],
            dep_static_locals=[],
            dep_std_types=[],
            call_graph=[],
            sources=["tree-sitter"],
        )
        out.append(method)
    return out
