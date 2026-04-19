"""Pydantic models for the emitted MethodRecord JSON schema."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Location(BaseModel):
    path: str
    line: int
    column: int | None = None


class ParameterRecord(BaseModel):
    name: str
    type: str
    qualifiers: list[str] = Field(default_factory=list)
    direction: Literal["in", "out", "in_out"] = "in"
    default_value: str | None = None


class Specifiers(BaseModel):
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


class ExceptionSpec(BaseModel):
    declared: list[str] = Field(default_factory=list)
    observed_throws: list[str] = Field(default_factory=list)


class MethodBlock(BaseModel):
    qualified_name: str
    class_name: str | None = Field(default=None, alias="class")
    namespace: str | None = None
    signature: str
    raw_signature: str
    return_type: str | None = None
    parameters: list[ParameterRecord] = Field(default_factory=list)
    specifiers: Specifiers = Field(default_factory=Specifiers)
    template_params: list[str] = Field(default_factory=list)
    access: Literal["public", "protected", "private"] = "public"
    exception_spec: ExceptionSpec = Field(default_factory=ExceptionSpec)
    defined_in_header: bool = False
    is_header_only: bool = False
    friends_of_class: list[str] = Field(default_factory=list)

    class Config:
        populate_by_name = True


class LocationBlock(BaseModel):
    header: Location | None = None
    definition: Location | None = None
    customer: str


class Complexity(BaseModel):
    cyclomatic: int | None = None
    nloc: int | None = None
    token_count: int | None = None
    parameter_count: int | None = None
    source: str = "lizard"
    match: Literal["signature", "line-range", "missing"] = "signature"


class DependencyClassBlock(BaseModel):
    qualified_name: str
    kind: str = "class"
    header: str | None = None
    used_as: list[str] = Field(default_factory=list)
    used_methods: list[str] = Field(default_factory=list)
    is_interface: bool = False
    construction: dict = Field(default_factory=dict)


class DependencyStructBlock(BaseModel):
    qualified_name: str
    kind: str = "struct"
    header: str | None = None
    construction: dict = Field(default_factory=dict)


class DependencyFunctionBlock(BaseModel):
    qualified_name: str
    header: str | None = None
    signature: str | None = None


class DependencyEnumBlock(BaseModel):
    qualified_name: str
    header: str | None = None
    members_used: list[str] = Field(default_factory=list)


class GlobalRefBlock(BaseModel):
    qualified_name: str
    header: str | None = None


class StaticLocalBlock(BaseModel):
    name: str
    type: str


class DependenciesBlock(BaseModel):
    classes: list[DependencyClassBlock] = Field(default_factory=list)
    data_structures: list[DependencyStructBlock] = Field(default_factory=list)
    free_functions: list[DependencyFunctionBlock] = Field(default_factory=list)
    globals_read: list[GlobalRefBlock] = Field(default_factory=list)
    globals_written: list[GlobalRefBlock] = Field(default_factory=list)
    static_locals: list[StaticLocalBlock] = Field(default_factory=list)
    enums_referenced: list[DependencyEnumBlock] = Field(default_factory=list)
    std_types: list[str] = Field(default_factory=list)


class CallSiteBlock(BaseModel):
    target: str
    call_site_line: int
    in_branch: bool = False


class MockBlock(BaseModel):
    target_class: str
    status: Literal["found", "missing"]
    mock_class: str | None = None
    header: str | None = None
    framework: Literal["gmock"] = "gmock"
    verified_inheritance: bool | None = None
    resolved_by: str | None = None
    suggested_pattern: str | None = None
    suggested_path: str | None = None
    gmock_stub_skeleton: str | None = None


class TestHints(BaseModel):
    framework: Literal["gtest"] = "gtest"
    suggested_fixture: str | None = None
    required_includes: list[str] = Field(default_factory=list)
    side_effects_observed: list[str] = Field(default_factory=list)
    pure_function: bool = False
    boundary_inputs: list[str] = Field(default_factory=list)


class Provenance(BaseModel):
    layers: dict[str, bool] = Field(default_factory=dict)
    generated_at: str
    tool_version: str = "0.1.0"
    tool_versions: dict[str, str] = Field(default_factory=dict)
    python_version: str
    input_fingerprint: str
    path_normalization: str
    warnings: list[str] = Field(default_factory=list)


class MethodRecord(BaseModel):
    schema_version: str = "1.0"
    id: str
    method: MethodBlock
    location: LocationBlock
    complexity: Complexity | None = None
    dependencies: DependenciesBlock = Field(default_factory=DependenciesBlock)
    call_graph: list[CallSiteBlock] = Field(default_factory=list)
    mocks: list[MockBlock] = Field(default_factory=list)
    test_hints: TestHints = Field(default_factory=TestHints)
    provenance: Provenance


class IndexEntry(BaseModel):
    schema_version: str = "1.0"
    customer: str
    generated_at: str
    tool_versions: dict[str, str] = Field(default_factory=dict)
    by_class: dict[str, list[str]] = Field(default_factory=dict)
    by_method: dict[str, str] = Field(default_factory=dict)
    by_mock: dict[str, list[str]] = Field(default_factory=dict)
