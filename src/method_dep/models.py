"""Data models for method-dep."""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional
import json
import hashlib


class SymbolKind(str, Enum):
    FUNCTION = "function"
    METHOD = "method"
    CLASS = "class"
    STRUCT = "struct"
    ENUM = "enum"
    TYPEDEF = "typedef"
    MACRO = "macro"
    NAMESPACE = "namespace"


@dataclass
class MethodInfo:
    """Extracted method information."""
    file_path: str          # source file (relative to project root)
    class_name: str         # empty string for free functions
    method_name: str
    qualified_name: str     # namespace::class::method
    signature: str          # full signature string
    return_type: str
    parameters: list[str]   # list of "type name" strings
    line_start: int
    line_end: int
    body: str               # full method body source
    namespace: str = ""

    @property
    def method_id(self) -> str:
        """Unique identifier: file::qualified_name(param_types)."""
        param_types = ", ".join(p.rsplit(" ", 1)[0] if " " in p else p for p in self.parameters)
        raw = f"{self.file_path}::{self.qualified_name}({param_types})"
        return raw

    @property
    def slug(self) -> str:
        """Filesystem-safe short name."""
        name = self.qualified_name.replace("::", "_")
        h = hashlib.md5(self.method_id.encode()).hexdigest()[:6]
        return f"{name}_{h}"


@dataclass
class SymbolDependency:
    """A type/symbol that a method depends on."""
    name: str
    kind: SymbolKind
    file_path: str          # where defined
    line: int
    definition: str         # full source of the definition
    is_external: bool = False  # from system/third-party header


@dataclass
class MethodTestStatus:
    """Test tracking status for a single method."""
    method_id: str
    name: str               # human-readable name
    slug: str
    file_path: str
    class_name: str
    created: bool = False
    compiled: bool = False
    passed: bool = False
    coverage: float = 0.0
    test_file: str = ""
    error_message: str = ""
    attempts: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> MethodTestStatus:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class MethodContext:
    """Full context package for LLM test generation."""
    method: MethodInfo
    dependencies: list[SymbolDependency] = field(default_factory=list)
    include_paths: list[str] = field(default_factory=list)
    related_methods: list[MethodInfo] = field(default_factory=list)
    mock_candidates: list[SymbolDependency] = field(default_factory=list)
