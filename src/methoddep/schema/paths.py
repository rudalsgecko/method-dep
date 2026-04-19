"""Path Encoding Algorithm for per-method JSON artifact paths.

Produces filesystem-safe directory/file stems from C++ qualified names,
class names, and method names — including operators, destructors, and
templated types.

Round-tripping is NOT supported. The SHA-1 hash in `schema/hash.py` is
the authoritative key; these encodings exist only for human-readable
directory hierarchies.
"""

from __future__ import annotations

import re
from urllib.parse import quote

# Operator substitution table — ORDER MATTERS (longest match first).
# Applied only when the input component begins with "operator".
_OPERATOR_TABLE: list[tuple[str, str]] = [
    # Placement forms — match before plain "new"/"delete"
    ("operator new[]", "operator__newarr"),
    ("operator delete[]", "operator__delarr"),
    ("operator new", "operator__new"),
    ("operator delete", "operator__del"),
    # 3-char operators (longest among the symbol families)
    ("operator<<=", "operator__shleq"),
    ("operator>>=", "operator__shreq"),
    ("operator<=>", "operator__spaceship"),
    ("operator->*", "operator__arrowstar"),
    # 2-char operators
    ("operator+=", "operator__addeq"),
    ("operator-=", "operator__subeq"),
    ("operator*=", "operator__muleq"),
    ("operator/=", "operator__diveq"),
    ("operator%=", "operator__modeq"),
    ("operator^=", "operator__xoreq"),
    ("operator&=", "operator__bitandeq"),
    ("operator|=", "operator__bitoreq"),
    ("operator<<", "operator__shl"),
    ("operator>>", "operator__shr"),
    ("operator==", "operator__eq"),
    ("operator!=", "operator__neq"),
    ("operator<=", "operator__le"),
    ("operator>=", "operator__ge"),
    ("operator&&", "operator__land"),
    ("operator||", "operator__lor"),
    ("operator++", "operator__inc"),
    ("operator--", "operator__dec"),
    ("operator->", "operator__arrow"),
    ("operator()", "operator__call"),
    ("operator[]", "operator__subscript"),
    # 1-char operators
    ("operator+", "operator__add"),
    ("operator-", "operator__sub"),
    ("operator*", "operator__mul"),
    ("operator/", "operator__div"),
    ("operator%", "operator__mod"),
    ("operator^", "operator__xor"),
    ("operator&", "operator__bitand"),
    ("operator|", "operator__bitor"),
    ("operator~", "operator__bitnot"),
    ("operator!", "operator__lnot"),
    ("operator=", "operator__assign"),
    ("operator<", "operator__lt"),
    ("operator>", "operator__gt"),
    ("operator,", "operator__comma"),
]

_UDL_RE = re.compile(r'^operator\s*""_?(\w+)$')
_CONVERSION_RE = re.compile(r"^operator\s+(.+)$")


def _punct_and_encode(s: str) -> str:
    """Stage 1 tail + Stage 2: replace template/namespace punctuation and
    URL-encode residual Windows-reserved characters."""
    # "::" must be replaced before "<"/">" to avoid colon leaking into Stage 2.
    s = s.replace("::", "_colon__colon_")
    s = (
        s.replace("<", "_lt_")
        .replace(">", "_gt_")
        .replace(",", "_comma_")
        .replace(" ", "_")
    )
    # Stage 2: URL-encode whatever remains. `safe="._-"` keeps common
    # filename-safe characters untouched.
    return quote(s, safe="._-")


def _encode_operator(component: str) -> str:
    """Apply UDL, conversion, and table rules for components beginning
    with 'operator'. Returns the Stage-1 symbolic result (still needs
    punctuation pass)."""
    udl = _UDL_RE.match(component)
    if udl:
        return f"operator__udl_{udl.group(1)}"

    # Exact table match (e.g. "operator new[]" with embedded space).
    for op, rep in _OPERATOR_TABLE:
        if component == op:
            return rep

    # Conversion operator: "operator <type...>" with type that is NOT in
    # the table (e.g. "operator int", "operator const Foo&").
    conv = _CONVERSION_RE.match(component)
    if conv:
        rest = conv.group(1)
        # Type portion gets recursive encoding so templates work.
        return "operator__cvt_" + encode_component(rest)

    # Prefix match (handles symbol operators without trailing whitespace).
    for op, rep in _OPERATOR_TABLE:
        if component.startswith(op):
            tail = component[len(op) :]
            # Guard against spurious matches like "operator_foo" for
            # hypothetical future tokens. In practice this cannot match
            # any real C++ name because identifiers after "operator"
            # would be whitespace-separated (handled by the conversion
            # rule above).
            if not tail or not tail[0].isalnum():
                return rep + tail

    # Starts with "operator" but not matched — fall through unmodified.
    return component


def encode_component(component: str) -> str:
    """Encode a single path component (namespace segment, class name, or
    method name)."""
    if not component:
        return component

    # Destructor prefix — applies only at component start.
    if component.startswith("~"):
        return _punct_and_encode("_dtor_" + component[1:])

    if component.startswith("operator"):
        stage1 = _encode_operator(component)
        return _punct_and_encode(stage1)

    # General name: Stage 1 rules do not apply; go straight to Stage 2.
    return _punct_and_encode(component)


def encode_namespace(qualified: str) -> list[str]:
    """Split a C++ qualified namespace/class name into path segments.

    `::` separates segments at the top level only. Template arguments
    (which may contain their own `::`) are kept intact in the final
    segment via the `encode_component` punctuation pass.
    """
    if not qualified:
        return ["_global_"]

    segments: list[str] = []
    depth = 0
    buf: list[str] = []
    i = 0
    while i < len(qualified):
        ch = qualified[i]
        if ch == "<":
            depth += 1
            buf.append(ch)
        elif ch == ">":
            depth = max(0, depth - 1)
            buf.append(ch)
        elif ch == ":" and depth == 0 and i + 1 < len(qualified) and qualified[i + 1] == ":":
            segments.append("".join(buf))
            buf = []
            i += 2
            continue
        else:
            buf.append(ch)
        i += 1
    if buf:
        segments.append("".join(buf))

    return [encode_component(seg) for seg in segments if seg]
