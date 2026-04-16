"""Generate context markdown files for LLM consumption."""
from __future__ import annotations
from pathlib import Path
from typing import Optional

from .models import MethodInfo, MethodContext, SymbolDependency, SymbolKind


def generate_context_markdown(ctx: MethodContext) -> str:
    """Generate a markdown document with all context needed for test generation."""
    m = ctx.method
    sections = []

    # Header
    sections.append(f"# Test Context: `{m.qualified_name}`\n")

    # Method under test
    sections.append("## Method Under Test\n")
    sections.append(f"**File:** `{m.file_path}` (lines {m.line_start}-{m.line_end})")
    sections.append(f"**Class:** `{m.class_name}`" if m.class_name else "**Type:** Free function")
    sections.append(f"**Namespace:** `{m.namespace}`" if m.namespace else "")
    sections.append(f"**Signature:** `{m.signature}`\n")
    sections.append("```cpp")
    sections.append(m.body)
    sections.append("```\n")

    # Parameters
    if m.parameters:
        sections.append("## Parameters\n")
        for p in m.parameters:
            sections.append(f"- `{p}`")
        sections.append("")

    # Type Dependencies
    type_deps = [d for d in ctx.dependencies if d.kind in (SymbolKind.CLASS, SymbolKind.STRUCT, SymbolKind.ENUM, SymbolKind.TYPEDEF)]
    if type_deps:
        sections.append("## Type Dependencies\n")
        for dep in type_deps:
            ext_marker = " *(external)*" if dep.is_external else ""
            sections.append(f"### `{dep.name}` ({dep.kind.value}){ext_marker}\n")
            sections.append(f"**Defined in:** `{dep.file_path}:{dep.line}`\n")
            if dep.definition and not dep.is_external:
                sections.append("```cpp")
                sections.append(dep.definition.strip())
                sections.append("```\n")

    # Function Dependencies
    func_deps = [d for d in ctx.dependencies if d.kind in (SymbolKind.FUNCTION, SymbolKind.METHOD)]
    if func_deps:
        sections.append("## Function Dependencies\n")
        for dep in func_deps:
            sections.append(f"### `{dep.name}` ({dep.kind.value})\n")
            sections.append(f"**Defined in:** `{dep.file_path}:{dep.line}`\n")
            if dep.definition:
                sections.append("```cpp")
                sections.append(dep.definition.strip())
                sections.append("```\n")

    # Mock Candidates
    if ctx.mock_candidates:
        sections.append("## Mock Candidates\n")
        sections.append("These classes have virtual methods and should be mocked for isolated testing:\n")
        for dep in ctx.mock_candidates:
            sections.append(f"### `{dep.name}`\n")
            sections.append("```cpp")
            sections.append(dep.definition.strip())
            sections.append("```\n")
            # Generate mock suggestion
            mock_code = _generate_mock_suggestion(dep)
            if mock_code:
                sections.append("**Suggested GMock class:**\n")
                sections.append("```cpp")
                sections.append(mock_code)
                sections.append("```\n")

    # Include paths
    if ctx.include_paths:
        sections.append("## Required Includes\n")
        for inc in ctx.include_paths:
            sections.append(f"- `{inc}`")
        sections.append("")

    return "\n".join(sections)


def save_context(ctx: MethodContext, output_dir: Path) -> Path:
    """Save context markdown to file."""
    md_content = generate_context_markdown(ctx)

    # Organize by class or file
    if ctx.method.class_name:
        subdir = output_dir / ctx.method.class_name.replace("::", "_")
    else:
        # Use source file name
        subdir = output_dir / Path(ctx.method.file_path).stem

    subdir.mkdir(parents=True, exist_ok=True)
    out_path = subdir / f"{ctx.method.slug}.md"
    out_path.write_text(md_content, encoding="utf-8")
    return out_path


def _generate_mock_suggestion(dep: SymbolDependency) -> str:
    """Generate a GMock class skeleton from a class definition."""
    import re

    if not dep.definition:
        return ""

    # Find virtual methods (skip destructors explicitly)
    virtual_pattern = re.compile(
        r'virtual\s+(?!~)([\w:]+(?:\s*[*&])?(?:\s+[\w:]+(?:\s*[*&])?)*?)\s+(\w+)\s*\(([^)]*)\)\s*(const)?\s*(?:=\s*0)?\s*;',
        re.MULTILINE,
    )

    matches = virtual_pattern.findall(dep.definition)
    if not matches:
        return ""

    lines = [f"class Mock{dep.name} : public {dep.name} {{", "public:"]

    for ret_type, name, params, const_qual in matches:

        const_str = " const" if const_qual else ""

        if const_str:
            lines.append(f"    MOCK_METHOD({ret_type}, {name}, ({params}), (const, override));")
        else:
            lines.append(f"    MOCK_METHOD({ret_type}, {name}, ({params}), (override));")

    lines.append("};")
    return "\n".join(lines)


def _extract_type_only(param: str) -> str:
    """Extract just the type from a parameter declaration."""
    # Remove default values
    param = param.split("=")[0].strip()
    # Remove the parameter name (last identifier)
    parts = param.rsplit(" ", 1)
    if len(parts) > 1 and not parts[-1].endswith("*") and not parts[-1].endswith("&"):
        return parts[0]
    return param
