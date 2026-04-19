"""Config loader and scaffold writer for methoddep.toml."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class TargetConfig(BaseModel):
    repo_root: Path
    # Broader "project scope" — dependencies whose source lives outside this
    # root are treated as external (system/SDK/vendor) and filtered from
    # the emitted JSON. Defaults to repo_root when unset.
    scope_root: Path | None = None
    is_git: Literal["auto", "true", "false"] | bool = "auto"
    solution: str | None = None
    cmake_dir: str | None = None


class WorkspaceConfig(BaseModel):
    strategy: Literal[
        "auto", "git-worktree-sparse", "symlink-tree", "copy-tree", "in-place"
    ] = "auto"
    # `in-place` skips composition entirely — libclang reads straight from repo_root.
    # Use this when the repo has no customer variants OR scope_root points above repo_root.
    worktree_base: Path | None = None
    headers_glob: list[str] = Field(default_factory=lambda: ["include/**", "public/**"])
    tests_glob: list[str] = Field(default_factory=lambda: ["tests/**"])
    long_paths: bool = True


class CustomerConfig(BaseModel):
    branches: list[str] = Field(default_factory=lambda: ["main"])
    extra_paths: list[str] = Field(default_factory=list)


class AnalysisConfig(BaseModel):
    libclang_path: str | None = None
    msvc_toolset: str = "v143"
    include_dirs: list[str] = Field(default_factory=list)
    defines: list[str] = Field(default_factory=list)
    analyzer_chain: list[str] = Field(
        default_factory=lambda: ["msbuild", "libclang", "tree-sitter", "ctags"]
    )
    clang_flags: list[str] = Field(
        default_factory=lambda: [
            "-fms-extensions",
            "-fms-compatibility",
            "-fdelayed-template-parsing",
            "-std=c++20",
            "-target",
            "x86_64-pc-windows-msvc",
            "-finput-charset=utf-8",
        ]
    )
    pch_autodetect: bool = True
    skip_generated: bool = True
    skip_unity: bool = True
    encoding_fallback: str = "cp949"


class BuildIntelConfig(BaseModel):
    enabled: bool = True
    # How methoddep sources the binlog:
    #   cached-only (default) — never triggers msbuild; if binlog is missing,
    #                            emit guidance and continue without L0.
    #   build-once            — run msbuild only when binlog is absent.
    #   always-build          — re-run msbuild whenever max_age_h is exceeded.
    mode: Literal["cached-only", "build-once", "always-build"] = "cached-only"
    binlog: str = "artifacts/msbuild.binlog"
    max_age_h: int = 24
    parser_backend: str = "msbuild-structured-logger"


class ComplexityConfig(BaseModel):
    tool: str = "lizard"
    lizard_path: str = "lizard"


class TestConfig(BaseModel):
    framework: Literal["gtest"] = "gtest"
    mock_dirs: list[str] = Field(default_factory=lambda: ["tests/mocks"])
    mock_name_patterns: list[str] = Field(
        default_factory=lambda: ["Mock{Class}", "{Class}Mock", "Fake{Class}"]
    )
    verify_inheritance: bool = True


class OutputConfig(BaseModel):
    dir: str = "artifacts/methoddep"
    index_file: str = "index.json"
    per_method_dir: str = "methods"


class Config(BaseModel):
    target: TargetConfig
    workspace: WorkspaceConfig
    customers: dict[str, CustomerConfig] = Field(default_factory=dict)
    analysis: AnalysisConfig = Field(default_factory=AnalysisConfig)
    build_intel: BuildIntelConfig = Field(default_factory=BuildIntelConfig)
    complexity: ComplexityConfig = Field(default_factory=ComplexityConfig)
    test: TestConfig = Field(default_factory=TestConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)


def load_config(path: Path) -> Config:
    with path.open("rb") as f:
        raw = tomllib.load(f)

    customer_meta = raw.pop("customers", {}) or {}
    src_pattern = customer_meta.pop("src_pattern", None)
    _ = src_pattern  # reserved for customer discovery

    customers: dict[str, CustomerConfig] = {
        name: CustomerConfig(**cfg) for name, cfg in customer_meta.items()
    }

    return Config(
        target=TargetConfig(**raw["target"]),
        workspace=WorkspaceConfig(**raw["workspace"]),
        customers=customers,
        analysis=AnalysisConfig(**raw.get("analysis", {})),
        build_intel=BuildIntelConfig(**raw.get("build_intel", {})),
        complexity=ComplexityConfig(**raw.get("complexity", {})),
        test=TestConfig(**raw.get("test", {})),
        output=OutputConfig(**raw.get("output", {})),
    )


SCAFFOLD = '''\
[target]
repo_root  = "D:/work/your-project/sub-module"
# scope_root: 관심사 루트. 이 경로 밖에서 선언된 타입/함수(시스템 SDK, 서드파티 등)는
# emit에서 전부 제외됨. 생략 시 repo_root로 폴백.
# scope_root = "D:/work/your-project"
is_git     = "auto"

[workspace]
strategy      = "auto"
worktree_base = "D:/work/.methoddep-worktrees"
headers_glob  = ["include/**", "public/**"]
tests_glob    = ["tests/**"]
long_paths    = true

[customers]
src_pattern = "src/{customer}/**/*.cpp"

[customers.acme]
branches = ["main"]

[analysis]
libclang_path  = "C:/Program Files/LLVM/bin/libclang.dll"
msvc_toolset   = "v143"
include_dirs   = ["include", "third_party"]
defines        = ["_WIN32", "UNICODE", "_CRT_SECURE_NO_WARNINGS", "_MSC_VER=1939"]
analyzer_chain = ["msbuild", "libclang", "tree-sitter", "ctags"]

[build_intel]
# 활성화 시 MSBuild binlog에서 include_dirs/defines/PCH를 자동 추출해
# libclang에 주입. methoddep 자신은 빌드하지 않음 — 아래 커맨드로 먼저 만들 것:
#    msbuild <your-solution>.sln /bl:artifacts/msbuild.binlog
# (StructuredLogger.Cli 설치 또는 msbuild /fl 생성한 diagnostic log 둘 다 지원)
enabled   = true
mode      = "cached-only"      # cached-only | build-once | always-build
binlog    = "artifacts/msbuild.binlog"
max_age_h = 168                # cached-only에선 오래된 binlog도 사용

[complexity]
tool = "lizard"

[test]
framework          = "gtest"
mock_dirs          = ["tests/mocks"]
mock_name_patterns = ["Mock{Class}", "{Class}Mock", "Fake{Class}"]

[output]
dir            = "artifacts/methoddep"
index_file     = "index.json"
per_method_dir = "methods"
'''


def scaffold_config(dest: Path | None = None, *, force: bool = False) -> Path:
    path = (dest or Path.cwd()) / "methoddep.toml"
    if path.exists() and not force:
        raise FileExistsError(f"{path} already exists; pass --force to overwrite")
    path.write_text(SCAFFOLD, encoding="utf-8")
    return path
