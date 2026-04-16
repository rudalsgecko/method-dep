"""Configuration management."""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import yaml


@dataclass
class Config:
    # Target C++ project
    project_path: str = ""
    compile_commands: str = "compile_commands.json"  # relative to project_path

    # Source filtering
    source_extensions: list[str] = field(default_factory=lambda: [".cpp", ".cc", ".cxx", ".c"])
    header_extensions: list[str] = field(default_factory=lambda: [".h", ".hpp", ".hxx"])
    exclude_patterns: list[str] = field(default_factory=lambda: ["test/*", "tests/*", "third_party/*", "build/*"])

    # Output
    output_dir: str = ".method-dep"  # relative to project_path
    context_dir: str = "context"     # under output_dir
    tests_dir: str = "generated_tests"  # under output_dir

    # Test framework
    test_framework: str = "gtest"
    test_build_command: str = ""  # custom build command, auto-detected if empty
    cmake_build_dir: str = "build"

    # LLM
    llm_tool: str = "claude"  # "claude" or "opencode"
    claude_command: str = "claude"
    opencode_command: str = "opencode"
    max_attempts: int = 3

    # Coverage
    coverage_tool: str = "OpenCppCoverage"
    coverage_threshold: float = 60.0
    opencppcoverage_path: str = "OpenCppCoverage"

    # Workflow
    parallel: bool = False  # reserved for future use
    skip_external_deps: bool = True

    @classmethod
    def load(cls, path: str | Path) -> Config:
        p = Path(path)
        if not p.exists():
            return cls()
        with open(p, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    def save(self, path: str | Path) -> None:
        from dataclasses import asdict
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(asdict(self), f, default_flow_style=False, allow_unicode=True)

    @property
    def project_root(self) -> Path:
        return Path(self.project_path).resolve()

    @property
    def output_root(self) -> Path:
        return self.project_root / self.output_dir

    @property
    def context_root(self) -> Path:
        return self.output_root / self.context_dir

    @property
    def tests_root(self) -> Path:
        return self.output_root / self.tests_dir

    @property
    def methods_json(self) -> Path:
        return self.output_root / "methods.json"

    @property
    def compile_commands_path(self) -> Path:
        return self.project_root / self.compile_commands

    def ensure_dirs(self) -> None:
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.context_root.mkdir(parents=True, exist_ok=True)
        self.tests_root.mkdir(parents=True, exist_ok=True)
