"""Render gmock MOCK_METHOD() declarations for a class's virtual methods."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VirtualMethodSpec:
    name: str
    return_type: str
    args: str  # "int x, std::string const& s"
    is_const: bool = False
    is_noexcept: bool = False


def render_mock_class(mock_class: str, target: str, methods: list[VirtualMethodSpec]) -> str:
    lines = [f"class {mock_class} : public {target} {{", "public:"]
    for m in methods:
        quals = ["override"]
        if m.is_const:
            quals.insert(0, "const")
        if m.is_noexcept:
            quals.insert(0, "noexcept")
        qual_str = ", ".join(quals)
        lines.append(
            f"    MOCK_METHOD({m.return_type}, {m.name}, ({m.args}), ({qual_str}));"
        )
    lines.append("};")
    return "\n".join(lines) + "\n"
