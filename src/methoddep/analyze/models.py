"""Analyzer output data classes.

These are the L1/L2 fact records before the merger combines them with
tree-sitter, ctags, lizard, and mock data into a MethodRecord for emit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class SourceLoc:
    path: Path
    line: int
    column: int = 1


@dataclass
class DependencyClass:
    qualified_name: str
    kind: Literal["class", "struct", "interface", "union", "enum", "template"] = "class"
    header: str | None = None  # "include/foo/X.h:12" style
    used_as: set[str] = field(default_factory=set)  # {"parameter","member","call_target","local","return"}
    used_methods: set[str] = field(default_factory=set)
    is_interface: bool = False


@dataclass
class DependencyDataStruct:
    qualified_name: str
    kind: Literal["struct", "class", "union"] = "struct"
    header: str | None = None
    fields: list[dict[str, str]] = field(default_factory=list)


@dataclass
class DependencyFunction:
    qualified_name: str
    header: str | None = None
    signature: str | None = None


@dataclass
class DependencyEnum:
    qualified_name: str
    header: str | None = None
    members_used: set[str] = field(default_factory=set)


@dataclass
class GlobalRef:
    qualified_name: str
    header: str | None = None


@dataclass
class StaticLocal:
    name: str
    type: str


@dataclass
class CallSite:
    target: str
    call_site_line: int
    in_branch: bool = False


@dataclass
class Parameter:
    name: str
    type: str
    direction: Literal["in", "out", "in_out"] = "in"
    default_value: str | None = None


@dataclass
class MethodSpecifiers:
    virtual: bool = False
    override: bool = False
    final: bool = False
    const: bool = False
    static: bool = False
    noexcept: bool = False
    pure: bool = False
    inline: bool = False
    constexpr: bool = False
    deleted: bool = False
    defaulted: bool = False


@dataclass
class ExceptionSpec:
    declared: list[str] = field(default_factory=list)
    observed_throws: list[str] = field(default_factory=list)


@dataclass
class AnalyzedMethod:
    qualified_name: str
    class_name: str | None
    namespace: str | None
    signature: str
    raw_signature: str
    return_type: str | None
    parameters: list[Parameter]
    specifiers: MethodSpecifiers
    access: Literal["public", "protected", "private"] = "public"
    declaration: SourceLoc | None = None
    definition: SourceLoc | None = None
    defined_in_header: bool = False
    template_params: list[str] = field(default_factory=list)
    exception_spec: ExceptionSpec = field(default_factory=ExceptionSpec)
    friends_of_class: list[str] = field(default_factory=list)

    dep_classes: list[DependencyClass] = field(default_factory=list)
    dep_data_structures: list[DependencyDataStruct] = field(default_factory=list)
    dep_free_functions: list[DependencyFunction] = field(default_factory=list)
    dep_enums: list[DependencyEnum] = field(default_factory=list)
    dep_globals_read: list[GlobalRef] = field(default_factory=list)
    dep_globals_written: list[GlobalRef] = field(default_factory=list)
    dep_static_locals: list[StaticLocal] = field(default_factory=list)
    dep_std_types: list[str] = field(default_factory=list)

    call_graph: list[CallSite] = field(default_factory=list)

    sources: list[str] = field(default_factory=list)  # "libclang"/"tree-sitter"
